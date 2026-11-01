"""Private, page-aware processing for Pwanimate chat attachments."""

import logging
import os
import tempfile
from typing import Any, Dict, List

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from pwanimate.ai.gateway import AIGateway, ChatMessage, LLMRequest
from pwanimate.ai.gateway.types import AttachmentData
from pwanimate.orchestrator.service import SPARK_VISION_UNAVAILABLE_NOTICE
from pwanimate.ingestion.chunker import chunk_document
from pwanimate.ingestion.exceptions import EmptyDocumentError
from pwanimate.ingestion.extractors.factory import extract_document
from pwanimate.ingestion.types import (
    ElementType,
    ExtractedDocument,
    ExtractedElement,
    LocationMetadata,
)
from pwanimate.models import PwanimateAttachment, PwanimateAttachmentChunk

logger = logging.getLogger(__name__)


def _copy_attachment_to_temp(attachment: PwanimateAttachment) -> str:
    suffix = os.path.splitext(attachment.file_name)[1]
    handle = tempfile.NamedTemporaryFile(prefix="pwanimate-attachment-", suffix=suffix, delete=False)
    try:
        attachment.file.open("rb")
        try:
            for block in attachment.file.chunks():
                handle.write(block)
        finally:
            attachment.file.close()
        handle.flush()
        return handle.name
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    finally:
        if not handle.closed:
            handle.close()


def _transcribe_pdf_page(page, attachment: PwanimateAttachment, page_number: int) -> tuple[str, str, str]:
    """Use the image-capable route for one scanned or image-heavy PDF page."""
    from django.conf import settings
    import fitz

    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    page_bytes = pixmap.tobytes("png")
    page_image = AttachmentData(
        id=f"{attachment.id}-page-{page_number}",
        name=f"{attachment.file_name} page {page_number}",
        mime_type="image/png",
        attachment_type="image",
        data_bytes=page_bytes,
    )
    model = getattr(settings, "PWANIMATE_VISION_MODEL", "gemini-3.8-flash")
    result = AIGateway().generate(LLMRequest(
        task="attachment_vision_extraction",
        messages=[ChatMessage(
            role="user",
            content=(
                f"Transcribe the meaningful text on page {page_number} faithfully. "
                "Preserve equations, labels, table structure, and reading order. "
                "Briefly describe any diagram or chart that contains useful information. "
                "Do not guess text that is unreadable; say when a region is unclear."
            ),
            attachments=[page_image],
        )],
        provider="gemini",
        model=model,
        temperature=0.1,
        max_tokens=1600,
        attachments=[page_image],
        metadata={"required_capability": "image"},
    ))
    return result.content.strip(), result.provider, result.model


class VisionBudgetExhausted(RuntimeError):
    """The account-wide image budget (or the student's own allowance) is gone.

    This is a *degradation*, not a failure: the image is marked as
    unavailable, the turn continues on the text-only path, and the student is
    told plainly that Spark Vision could not look at the image.
    """


def _analyze_image(attachment: PwanimateAttachment) -> tuple[str, str, str]:
    """
    One vision pass over an uploaded image.

    The result is stored on the attachment and reused by every later turn —
    an image is analysed ONCE, not once per question.

    Two budgets guard this call:

    * the student's own daily image allowance, and
    * the account-wide daily image budget, which exists because the shared
      inference capacity (the Workers AI free allocation) is finite even when
      every individual student is within their own limit.

    Both are checked *before* the provider is called, so a refused analysis
    costs nothing.
    """
    from django.conf import settings

    from pwanimate.billing.global_budget import image_budget
    from pwanimate.billing.models import UsageKind
    from pwanimate.billing.services import QuotaService, UsageService

    user = getattr(attachment, "user", None)

    # 1. The student's own allowance.
    if user is not None and getattr(user, "is_authenticated", False):
        decision = QuotaService.check(user, kind=UsageKind.IMAGE)
        if not decision.allowed:
            raise VisionBudgetExhausted(
                "You have used today's image analyses. Upgrade for more, or try again tomorrow."
            )

    # 2. The account-wide budget. Claimed atomically so two concurrent
    #    uploads cannot both take the last unit.
    budget = image_budget()
    if not budget.consume(1):
        raise VisionBudgetExhausted(
            "Spark Vision is busy right now. Try again shortly."
        )

    model = getattr(settings, "PWANIMATE_VISION_MODEL", "gemini-3.8-flash")
    result = AIGateway().generate(LLMRequest(
        task="attachment_vision_analysis",
        messages=[ChatMessage(
            role="user",
            content=(
                "Describe this image for a study assistant. Transcribe any text, "
                "equations, labels or tables faithfully. Explain any diagram, chart, "
                "graph or code shown, and note the key ideas a student might ask about. "
                "Be concrete and complete: this description is what later questions "
                "will be answered from. If something is unreadable, say so plainly."
            ),
            attachments=[attachment],
        )],
        provider="gemini",
        model=model,
        temperature=0.1,
        max_tokens=1600,
        attachments=[attachment],
        metadata={"required_capability": "image"},
    ))

    # 3. Meter the analysis: it spends an image from the student's
    #    allowance, not a chat from their conversation allowance.
    try:
        UsageService.record_image_analysis(
            user=user,
            provider=result.provider,
            model_id=result.model,
            prompt_tokens=int(getattr(result, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(result, "completion_tokens", 0) or 0),
            source="vision_analysis",
        )
    except Exception:  # noqa: BLE001 - accounting must never break analysis
        logger.warning("[BILLING] image analysis usage recording failed", exc_info=True)

    return result.content.strip(), result.provider, result.model


@shared_task(name="pwanimate.tasks.attachments.process_pwanimate_attachment")
def process_pwanimate_attachment(attachment_id: str) -> Dict[str, Any]:
    """Extract, OCR when needed, analyse images once, chunk, and persist a private chat upload."""
    attachment = PwanimateAttachment.objects.filter(id=attachment_id).first()
    if attachment is None:
        return {"status": "missing", "attachment_id": str(attachment_id)}
    if attachment.attachment_type != "document":
        # Images: one vision pass, stored for reuse. Later turns read the
        # stored description instead of paying for the image again.
        if attachment.attachment_type == "image" and (attachment.vision_summary or "").strip():
            return {
                "status": "ready",
                "attachment_id": str(attachment.id),
                "vision_summary": True,
            }
        if attachment.attachment_type == "image":
            # Claim the image atomically. Two workers must never analyse the
            # same image: the vision pass is the single most expensive thing
            # this task does, and a double analysis is a double bill.
            claimed = PwanimateAttachment.objects.filter(
                id=attachment.id,
                processing_status__in=["pending", "not_required", ""],
            ).update(processing_status="processing")
            if not claimed:
                # Someone else has it (or it is already done).
                attachment.refresh_from_db()
                return {
                    "status": attachment.processing_status or "processing",
                    "attachment_id": str(attachment.id),
                    "vision_summary": bool((attachment.vision_summary or "").strip()),
                }
            try:
                summary, provider, model = _analyze_image(attachment)
            except VisionBudgetExhausted as exc:
                # The image was not analysed, and no capacity was spent on
                # it. Say so plainly: the turn continues on the text-only
                # path with the vision notice, and the image is never
                # re-sent (so it is never paid for twice).
                logger.info("Image analysis not performed for %s: %s", attachment.id, exc)
                attachment.processing_status = "failed"
                attachment.processing_error = str(exc)[:2000]
                attachment.vision_error = SPARK_VISION_UNAVAILABLE_NOTICE
                attachment.save(update_fields=[
                    "processing_status", "processing_error", "vision_error",
                ])
                return {"status": "failed", "attachment_id": str(attachment.id)}
            except Exception as exc:
                logger.warning("Image analysis failed for %s: %s", attachment.id, exc)
                attachment.processing_status = "failed"
                attachment.processing_error = f"Image analysis failed: {exc}"[:2000]
                # Remember the failure so the image is never re-sent: later
                # turns carry the note instead of paying for another look.
                attachment.vision_error = SPARK_VISION_UNAVAILABLE_NOTICE
                attachment.save(update_fields=[
                    "processing_status", "processing_error", "vision_error",
                ])
                return {"status": "failed", "attachment_id": str(attachment.id)}
            attachment.vision_summary = summary
            attachment.vision_model = f"{provider}:{model}"
            attachment.processing_status = "ready"
            attachment.processing_error = ""
            attachment.processed_at = timezone.now()
            attachment.save(update_fields=[
                "vision_summary", "vision_model", "processing_status",
                "processing_error", "processed_at",
            ])
            return {
                "status": "ready",
                "attachment_id": str(attachment.id),
                "vision_summary": True,
                "vision_model": attachment.vision_model,
            }
        attachment.processing_status = "not_required"
        attachment.save(update_fields=["processing_status"])
        return {"status": "not_required", "attachment_id": str(attachment.id)}
    if (
        attachment.processing_status == "ready"
        and not attachment.processing_error
        and attachment.chunks.exists()
    ):
        return {
            "status": "ready",
            "attachment_id": str(attachment.id),
            "chunk_count": attachment.chunks.count(),
        }

    attachment.processing_status = "processing"
    attachment.processing_error = ""
    attachment.save(update_fields=["processing_status", "processing_error"])
    temp_path = None
    try:
        temp_path = _copy_attachment_to_temp(attachment)
        try:
            extracted = extract_document(
                temp_path,
                filename=attachment.file_name,
                mime_type=attachment.mime_type,
            )
        except EmptyDocumentError:
            if attachment.extension != ".pdf":
                raise
            import fitz
            with fitz.open(temp_path) as pdf:
                extracted = ExtractedDocument(
                    source_filename=attachment.file_name,
                    format="pdf",
                    metadata={"total_pages": len(pdf), "parsed_pages": len(pdf), "element_count": 0},
                )

        warnings: List[str] = []
        if attachment.extension == ".pdf":
            import fitz
            max_vision_pages = max(0, int(getattr(settings, "PWANIMATE_ATTACHMENT_VISION_MAX_PAGES", 20)))
            existing_text_by_page = {}
            for element in extracted.elements:
                page_no = element.location.page_number
                if page_no:
                    existing_text_by_page[page_no] = existing_text_by_page.get(page_no, 0) + len(element.text.strip())

            vision_count = 0
            with fitz.open(temp_path) as pdf:
                total_pages = len(pdf)
                extracted.metadata["total_pages"] = total_pages
                for page_index, page in enumerate(pdf):
                    page_number = page_index + 1
                    text_chars = existing_text_by_page.get(page_number, 0)
                    has_images = bool(page.get_images(full=True))
                    has_visual_content = has_images or bool(page.get_drawings())
                    if text_chars >= 80 and not has_images and not has_visual_content:
                        continue
                    if text_chars < 80 and not has_visual_content:
                        continue
                    if vision_count >= max_vision_pages:
                        warnings.append(f"Visual extraction limit reached after {max_vision_pages} pages")
                        break
                    vision_count += 1
                    try:
                        text, provider, model = _transcribe_pdf_page(page, attachment, page_number)
                        if text:
                            extracted.elements.append(ExtractedElement(
                                element_type=ElementType.PARAGRAPH,
                                text=text,
                                location=LocationMetadata(page_number=page_number),
                                metadata={
                                    "extraction_method": "vision",
                                    "vision_provider": provider,
                                    "vision_model": model,
                                },
                            ))
                    except Exception as exc:
                        logger.warning(
                            "Vision extraction failed for attachment %s page %s: %s",
                            attachment.id,
                            page_number,
                            exc,
                        )
                        warnings.append(f"Page {page_number} could not be visually processed")

        chunks = chunk_document(extracted)
        if not chunks:
            raise ValueError("No readable text could be extracted from this file.")

        with transaction.atomic():
            PwanimateAttachmentChunk.objects.filter(attachment=attachment).delete()
            PwanimateAttachmentChunk.objects.bulk_create([
                PwanimateAttachmentChunk(
                    attachment=attachment,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    page_number=chunk.page_number,
                    page_end=chunk.page_end,
                    chunk_type=chunk.chunk_type,
                    metadata=chunk.metadata,
                )
                for chunk in chunks
            ])
            attachment.processing_status = "ready"
            attachment.processing_error = "; ".join(warnings)[:2000]
            attachment.processed_at = timezone.now()
            attachment.save(update_fields=["processing_status", "processing_error", "processed_at"])

        return {
            "status": "ready",
            "attachment_id": str(attachment.id),
            "chunk_count": len(chunks),
            "vision_pages": vision_count if attachment.extension == ".pdf" else 0,
            "warnings": warnings,
        }
    except Exception as exc:
        logger.exception("Attachment processing failed for %s", attachment.id)
        attachment.processing_status = "failed"
        attachment.processing_error = str(exc)[:2000]
        attachment.processed_at = timezone.now()
        attachment.save(update_fields=["processing_status", "processing_error", "processed_at"])
        return {"status": "failed", "attachment_id": str(attachment.id), "error": str(exc)[:500]}
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                logger.warning("Could not delete temporary attachment copy: %s", temp_path)
