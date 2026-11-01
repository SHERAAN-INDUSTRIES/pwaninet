"""
Pwanimate Orchestrator Service.

Central coordinator bridging user interactions, upstream retrieval, deterministic
domain tool routing, in-memory context bounding, application prompt policy,
and the AI Gateway.
"""

import logging
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from django.conf import settings

from pwanimate.ai.gateway import (
    AIGateway,
    ChatMessage,
    LLMRequest,
    LLMResponse,
    get_task_policy,
)
from pwanimate.ai.exceptions import AIGatewayError
from pwanimate.ai.gateway.types import AttachmentData
from pwanimate.context import ContextEngine, ContextPackage, ContextRequest, GroundingMode
from pwanimate.context.types import AuthorityLevel, ContextItem
from pwanimate.orchestrator.prompts import (
    STUDY_MODE_ADAPTIVE_TEACHING_ADDENDUM,
    STUDY_MODE_INSTRUCTION_ADDENDUM,
    UNAVAILABLE_SOURCE_INSTRUCTION_ADDENDUM,
    get_system_instruction,
)
from pwanimate.orchestrator.types import OrchestrationRequest, OrchestrationResponse
from pwanimate.retrieval import (
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    RetrievalResponse,
    SourceType,
    UnifiedRetrievalService,
)
from pwanimate.tools import (
    ToolRoute,
    ToolRouter,
    ToolRegistry,
    get_default_tool_registry,
    tool_result_to_context_items,
    build_tool_context_package,
)
from pwanimate.tools.selector import LLMToolSelector

logger = logging.getLogger(__name__)

# The one user-facing sentence for "the vision route could not serve this
# image". Said plainly rather than hidden: if Spark Vision is down, the
# student should be told Spark Vision is down.
SPARK_VISION_UNAVAILABLE_NOTICE = (
    "Spark Vision is down and didn't respond, so I could not look at your image. "
    "Please try again in a little while."
)


def _vision_fallback_instruction(system_instruction: Optional[str]) -> str:
    """Tell the model exactly what to say when the image could not be read."""
    return (
        (system_instruction or "").rstrip()
        + "\n\nImage fallback notice: "
        + SPARK_VISION_UNAVAILABLE_NOTICE
        + " Answer the user's text and available context only, and tell them this plainly."
    )


def _vision_unavailable_warning() -> Dict[str, str]:
    """Kept for callers that want a machine-readable marker. The chat no
    longer renders a banner for this: the model reports it in its answer
    (once, and only when the question actually depends on the image)."""
    return {
        "code": "spark_vision_unavailable",
        "title": "Spark Vision is down",
        "message": SPARK_VISION_UNAVAILABLE_NOTICE,
    }

# Patterns that indicate purely conversational pleasantries without substantive search intent
CONVERSATIONAL_PATTERNS = [
    r"^(hi|hello|hey|hiya|howdy)(\s+(there|Spark|friend))?[\s\.\!\?]*$",
    r"^(good\s+(morning|afternoon|evening|day))(\s+(Spark|there))?[\s\.\!\?]*$",
    r"^(habari|sasa|mambo|niaje|jambo)[\s\.\!\?]*$",
    r"^(thanks|thank\s+you|asante(\s+sana)?)[\s\.\!\?]*$",
    r"^(who\s+are\s+you|what\s+are\s+you|what\s+is\s+Spark)[\s\.\!\?]*$",
    r"^(what\s+can\s+you\s+do|how\s+can\s+you\s+help(\s+me)?)[\s\.\!\?]*$",
    r"^(help|start)[\s\.\!\?]*$",
    r"^(ok|okay|k|kk|alright|all\s+right|got\s+it|understood|makes\s+sense)[\s\.\!\?]*$",
    r"^(cool|great|nice|awesome|perfect|sweet|good|amazing|wonderful)[\s\.\!\?]*$",
    r"^(yes|yeah|yep|sure|yup|no|nope|nah)[\s\.\!\?]*$",
    r"^(how\s+are\s+you(\s+doing)?|how\s+r\s+u|what\'?s\s+up|wassup|sup)[\s\.\!\?]*$",
    r"^(bye|goodbye|see\s+ya|see\s+you(\s+later)?|cya|take\s+care)[\s\.\!\?]*$",
    r"^(test|testing|ping)[\s\.\!\?]*$",
]


def sanitize_llm_response(text: str) -> str:
    """
    Sanitize raw LLM response text, converting any accidental or synthetic tool-call tokens
    (e.g., <|tool_call_start|>[write(path='...', content='...')]<|tool_call_end|>)
    into standard Markdown fenced code blocks.
    """
    if not text:
        return ""

    if "tool_call" not in text and "[write(" not in text and "write(path=" not in text:
        return text

    def parse_tool_call_content(block: str) -> str:
        cleaned = block.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1].strip()

        m_func = re.match(r"([a-zA-Z0-9_]+)\s*\((.*)\)\s*$", cleaned, re.DOTALL)
        if not m_func:
            return block

        func_name = m_func.group(1)
        args_str = m_func.group(2).strip()

        if func_name == "write":
            m_path = re.search(r"path=([\'\"])(.*?)\1", args_str)
            path = m_path.group(2) if m_path else ""
            ext = path.split(".")[-1].lower() if "." in path else "python"
            lang_map = {
                "py": "python",
                "js": "javascript",
                "ts": "typescript",
                "sh": "bash",
                "sql": "sql",
                "json": "json",
                "html": "html",
                "css": "css",
            }
            lang = lang_map.get(ext, ext or "python")

            m_content = re.search(r"content=([\'\"])(.*)", args_str, re.DOTALL)
            if m_content:
                quote = m_content.group(1)
                content = m_content.group(2)
                content = re.sub(rf"{quote}\s*\)?\s*$", "", content)
            else:
                content = args_str

            try:
                content = content.encode("utf-8").decode("unicode_escape")
            except Exception:
                content = (
                    content.replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace('\\"', '"')
                    .replace("\\'", "'")
                )

            filename_header = f"### `{path}`\n\n" if path else ""
            return f"{filename_header}```{lang}\n{content.strip()}\n```"

        return block

    # 1. Transform <|tool_call_start|> ... <|tool_call_end|> blocks
    def tool_call_replacer(match: re.Match) -> str:
        inner = match.group(1)
        return "\n\n" + parse_tool_call_content(inner) + "\n\n"

    result = re.sub(
        r"<\|tool_call_start\|>(.*?)<\|tool_call_end\|>",
        tool_call_replacer,
        text,
        flags=re.DOTALL,
    )

    # 2. Catch naked [write(path=..., content=...)]
    def naked_write_replacer(match: re.Match) -> str:
        return "\n\n" + parse_tool_call_content(match.group(0)) + "\n\n"

    result = re.sub(
        r"\[write\(\s*(?:path=[\'\"].*?[\'\"],\s*)?content=[\'\"].*?[\'\"]\s*\)\]",
        naked_write_replacer,
        result,
        flags=re.DOTALL,
    )

    # 3. Clean up any remaining special tags
    result = re.sub(r"<\/?(?:\|tool_call_start\||\|tool_call_end\||tool_call)>", "", result)
    return result.strip()


def extract_attachment_text(
    attachment: Any,
    max_chars: int = 15000,
    query: str = "",
    active_page: Optional[int] = None,
) -> str:
    """
    Extract bounded textual content from an uploaded document attachment.
    Supports PDF, DOCX, PPTX, TXT, MD, CSV, etc. using Pwanimate extractors.
    Prioritizes the currently open page (`active_page`) from the Right Context Rail
    or an explicit page in `query`, alongside a whole-document structural overview.
    """
    file_obj = getattr(attachment, "file", None)
    file_name = getattr(attachment, "file_name", getattr(attachment, "name", "document"))

    if getattr(attachment, "processing_status", None) == "ready":
        stored_chunks = list(attachment.chunks.all())
        if stored_chunks:
            query_str = query or ""
            query_terms = set(re.findall(r"[a-z0-9]{3,}", query_str.lower()))
            page_match = re.search(r"\b(?:page|p\.)\s*(\d+)\b", query_str, re.IGNORECASE)
            explicit_query_page = int(page_match.group(1)) if page_match else None
            effective_page = explicit_query_page
            if effective_page is None and active_page is not None:
                try:
                    if int(active_page) > 0:
                        effective_page = int(active_page)
                except (ValueError, TypeError):
                    effective_page = None

            is_summary_query = bool(
                re.search(
                    r"\b(?:summar(?:ize|y|ise)|overview|table of contents|chapters?|whole (?:book|document|pdf)|about this (?:book|document|pdf)|structure)\b",
                    query_str,
                    re.IGNORECASE,
                )
            )
            max_page = max((c.page_end or c.page_number or 1) for c in stored_chunks)

            lines = [
                f"[DOCUMENT OVERVIEW — '{file_name}' (Total pages: {max_page}, Total chunks: {len(stored_chunks)})]"
            ]
            remaining = max_chars - len(lines[0])
            included_indices = set()

            # 1. If a page is open in the Right Context Rail (or explicitly requested), include its full content
            if effective_page is not None and not is_summary_query:
                page_matching_chunks = [
                    c for c in stored_chunks
                    if c.page_number is not None
                    and c.page_number <= effective_page <= (c.page_end or c.page_number)
                ]
                if page_matching_chunks:
                    lines.append(f"[CURRENTLY OPEN PAGE IN VIEWER — PAGE {effective_page}]")
                    for chunk in page_matching_chunks:
                        page_lbl = f"page {chunk.page_number}"
                        item = f"[{page_lbl}]\n{chunk.content.strip()}"
                        if len(item) > remaining:
                            item = item[:remaining].rstrip()
                        if item:
                            lines.append(item)
                            remaining -= len(item)
                            included_indices.add(chunk.chunk_index)
                        if remaining <= 200:
                            break
                    # Close the page-scoped block explicitly: the supplementary
                    # section below reuses the same "[page N]" labels, so the
                    # model must be able to tell where the viewed page ends.
                    lines.append("[END OF CURRENT PAGE CONTEXT]")

            # 2. For whole-book summary queries (or supplementary context), include opening + sampled/ranked chunks
            if remaining > 200:
                if is_summary_query:
                    sample_indices = set(range(min(3, len(stored_chunks))))
                    if len(stored_chunks) > 3:
                        step = max(1, (len(stored_chunks) - 3) // 5)
                        for s in range(1, 5):
                            sample_indices.add(min(len(stored_chunks) - 1, 2 + s * step))
                    supplementary = [
                        stored_chunks[i] for i in sorted(sample_indices)
                        if stored_chunks[i].chunk_index not in included_indices
                    ]
                else:
                    remaining_chunks = [c for c in stored_chunks if c.chunk_index not in included_indices]
                    ranked = sorted(
                        remaining_chunks,
                        key=lambda chunk: (
                            int(
                                effective_page is not None
                                and chunk.page_number is not None
                                and abs(chunk.page_number - effective_page) <= 1
                            ),
                            sum(1 for term in query_terms if term in chunk.content.lower()),
                            -(chunk.chunk_index),
                        ),
                        reverse=True,
                    )
                    supplementary = sorted(ranked[:5], key=lambda chunk: chunk.chunk_index)

                if supplementary:
                    lines.append("[ADDITIONAL DOCUMENT CONTEXT]")
                for chunk in supplementary:
                    page = f"page {chunk.page_number}" if chunk.page_number else "page not specified"
                    item = f"[{page}]\n{chunk.content.strip()}"
                    if len(item) > remaining:
                        item = item[:remaining].rstrip()
                    if item:
                        lines.append(item)
                        remaining -= len(item)
                    if remaining <= 0:
                        break

            if getattr(attachment, "processing_error", ""):
                lines.append(f"[Processing note: {attachment.processing_error}]")
            return "\n\n".join(lines)
    if getattr(attachment, "processing_status", None) == "failed":
        return f"[Document processing failed for '{file_name}': {attachment.processing_error or 'no readable text was found'}]"

    file_path = None
    if file_obj:
        try:
            # Try to get the file path - this will fail for cloud storage backends
            potential_path = file_obj.path
            if os.path.exists(potential_path):
                file_path = potential_path
        except (NotImplementedError, AttributeError):
            # Storage backend doesn't support absolute paths (e.g., S3)
            # or path attribute doesn't exist - fall back to direct file reading
            pass

    extracted_text = ""
    if file_path:
        try:
            from pwanimate.ingestion.extractors.factory import extract_document
            extracted_doc = extract_document(file_path, filename=file_name)
            if extracted_doc and extracted_doc.elements:
                extracted_text = "\n\n".join(el.text for el in extracted_doc.elements if el.text)
        except Exception as exc:
            logger.warning("Structural extraction failed for attachment %s: %s", file_name, exc)

    # Fallback to direct text reading if extraction is empty or file has no path
    if not extracted_text and file_obj:
        try:
            file_obj.seek(0)
            raw_bytes = file_obj.read(max_chars * 2)
            file_obj.seek(0)
            text_candidate = raw_bytes.decode("utf-8", errors="replace").replace("\x00", "")
            if text_candidate.strip():
                printable_ratio = sum(c.isprintable() or c in "\n\r\t" for c in text_candidate) / max(1, len(text_candidate))
                if printable_ratio > 0.80:
                    extracted_text = text_candidate
        except Exception as exc:
            logger.warning("Fallback raw read failed for attachment %s: %s", file_name, exc)

    cleaned = (extracted_text or "").strip()
    if not cleaned:
        return f"[No extractable text could be found in '{file_name}']"

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "... [ATTACHMENT TRUNCATED]"
    return cleaned


class PwanimateOrchestrator:
    """
    Main orchestration engine for Pwanimate assistant interactions.
    """

    def __init__(
        self,
        retrieval_service: Optional[UnifiedRetrievalService] = None,
        context_engine: Optional[ContextEngine] = None,
        gateway: Optional[AIGateway] = None,
        tool_router: Optional[ToolRouter] = None,
        tool_registry: Optional[ToolRegistry] = None,
        tool_selector: Optional[LLMToolSelector] = None,
    ):
        self.retrieval_service = retrieval_service or UnifiedRetrievalService()
        self.context_engine = context_engine or ContextEngine()
        self.gateway = gateway or AIGateway()
        self.tool_router = tool_router or ToolRouter()
        self.tool_registry = tool_registry or get_default_tool_registry()
        self.tool_selector = tool_selector or LLMToolSelector(self.gateway, self.tool_registry)

    def _process_attachments(
        self,
        attachments: List[Any],
        query: str = "",
        attachment_pages: Optional[Dict[str, int]] = None,
    ) -> Tuple[List[AttachmentData], Optional[str]]:
        """
        Process user attachments:
        - Image attachments become AttachmentData with raw bytes for multimodal models.
        - Document attachments have text extracted and bounded into an XML context block,
          respecting any active page open in the Right Context Rail viewer.
        """
        if not attachments:
            return [], None

        from django.conf import settings

        max_chars = getattr(settings, "PWANIMATE_MAX_EXTRACT_CHARS", 15000)
        image_attachments: List[AttachmentData] = []
        doc_blocks: List[str] = []
        pages_map = attachment_pages or {}

        for att in attachments:
            att_type = getattr(att, "attachment_type", None) or "document"
            att_id = str(getattr(att, "id", uuid.uuid4()))
            att_name = getattr(att, "file_name", getattr(att, "name", "attachment"))
            att_mime = getattr(att, "mime_type", "application/octet-stream")

            if att_type == "image":
                # The image was analysed ONCE at upload. Later turns reuse
                # that description as context instead of paying for (and
                # waiting on) another vision pass on the same image.
                stored_summary = (getattr(att, "vision_summary", "") or "").strip()
                if stored_summary:
                    doc_blocks.append(
                        f'<attachment_context id="{att_id}" filename="{att_name}" '
                        'type="image" analysed="once">\n'
                        f"  <title>{att_name}</title>\n"
                        "  <content>\n"
                        "[IMAGE ANALYSIS — captured once when the image was uploaded; "
                        "this description is the image's context]\n"
                        f"{stored_summary}\n"
                        "  </content>\n"
                        "</attachment_context>"
                    )
                    continue

                # The analysis failed earlier. The student decides what
                # happens to the image: keep it in the context (so they can
                # reuse it) or drop it. Until they choose, it is offered.
                stored_error = (getattr(att, "vision_error", "") or "").strip()
                if stored_error:
                    policy = (getattr(att, "vision_context_policy", "") or "ask").strip().lower()
                    if policy == "drop":
                        doc_blocks.append(
                            f'<attachment_context id="{att_id}" filename="{att_name}" '
                            'type="image" analysed="failed">\n'
                            f"  <title>{att_name}</title>\n"
                            "  <content>\n"
                            f"{stored_error} The student chose not to keep this image, so it is "
                            "not available. Answer from the rest of the context, and only mention "
                            "the image if the question depends on it.\n"
                            "  </content>\n"
                            "</attachment_context>"
                        )
                        continue
                    if policy == "keep":
                        # The student wants to reuse the image: send it again
                        # so the vision route can pick it up.
                        data_bytes = getattr(att, "data_bytes", None)
                        if not data_bytes and hasattr(att, "file") and att.file:
                            try:
                                att.file.seek(0)
                                data_bytes = att.file.read()
                                att.file.seek(0)
                            except Exception as e:
                                logger.warning("Could not read bytes for image attachment %s: %s", att_id, e)
                        image_attachments.append(
                            AttachmentData(
                                id=att_id,
                                name=att_name,
                                mime_type=att_mime,
                                attachment_type="image",
                                data_bytes=data_bytes,
                                url=getattr(att, "url", f"/api/Spark/attachments/{att_id}/view/"),
                            )
                        )
                        continue
                    # 'ask': send it once more (the vision route may have
                    # recovered) and let the student decide afterwards.
                    data_bytes = getattr(att, "data_bytes", None)
                    if not data_bytes and hasattr(att, "file") and att.file:
                        try:
                            att.file.seek(0)
                            data_bytes = att.file.read()
                            att.file.seek(0)
                        except Exception as e:
                            logger.warning("Could not read bytes for image attachment %s: %s", att_id, e)
                    image_attachments.append(
                        AttachmentData(
                            id=att_id,
                            name=att_name,
                            mime_type=att_mime,
                            attachment_type="image",
                            data_bytes=data_bytes,
                            url=getattr(att, "url", f"/api/Spark/attachments/{att_id}/view/"),
                        )
                    )
                    continue

                # Still being analysed by the upload task. Sending it here
                # would analyse it a second time and bill it twice, so it is
                # left out of the payload and the student is told.
                if (getattr(att, "processing_status", "") or "") in {"pending", "processing"}:
                    doc_blocks.append(
                        f'<attachment_context id="{att_id}" filename="{att_name}" '
                        'type="image" analysed="in_progress">\n'
                        f"  <title>{att_name}</title>\n"
                        "  <content>\n"
                        "This image is still being read by Spark. Its contents are not "
                        "available yet, so answer without it and say the image is still "
                        "being processed if the question depends on it.\n"
                        "  </content>\n"
                        "</attachment_context>"
                    )
                    continue

                data_bytes = getattr(att, "data_bytes", None)
                if not data_bytes and hasattr(att, "file") and att.file:
                    try:
                        att.file.seek(0)
                        data_bytes = att.file.read()
                        att.file.seek(0)
                    except Exception as e:
                        logger.warning("Could not read bytes from image attachment %s: %s", att_id, e)

                image_attachments.append(
                    AttachmentData(
                        id=att_id,
                        name=att_name,
                        mime_type=att_mime,
                        attachment_type="image",
                        data_bytes=data_bytes,
                        url=getattr(att, "url", f"/api/Spark/attachments/{att_id}/view/"),
                    )
                )

            elif att_type == "document":
                active_page = pages_map.get(att_id)
                extracted_text = extract_attachment_text(
                    att,
                    max_chars=max_chars,
                    query=query,
                    active_page=active_page,
                )
                page_attr = f' active_page="{active_page}"' if active_page else ""
                block = [
                    f'<attachment_context id="{att_id}" filename="{att_name}" type="{att_type}"{page_attr}>',
                    f'  <title>{att_name}</title>',
                    '  <content>',
                    f'    {extracted_text}',
                    '  </content>',
                    '</attachment_context>',
                ]
                doc_blocks.append("\n".join(block))

        attachment_context_str = "\n\n".join(doc_blocks) if doc_blocks else None
        return image_attachments, attachment_context_str

    def _resolve_context_attachments(
        self, user: Any, context_resources: List[Dict[str, Any]]
    ) -> Tuple[List[Any], Dict[str, int]]:
        """
        Resolve any uploaded chat attachments (`PwanimateAttachment`) present in the
        Right Context Rail along with their currently open viewer `pageNumber`.
        """
        if not context_resources or not isinstance(context_resources, list):
            return [], {}

        from pwanimate.services.attachment import AttachmentService

        resolved_attachments: List[Any] = []
        attachment_pages: Dict[str, int] = {}
        seen_ids = set()

        for res in context_resources:
            if not isinstance(res, dict):
                continue
            source_type = str(res.get("source_type") or res.get("sourceType") or "").lower()
            res_type = str(res.get("type") or res.get("category") or "").lower()
            att_id = res.get("attachment_id") or res.get("attachmentId")
            if not att_id and source_type == "attachment":
                att_id = res.get("id")
            if not att_id and res_type == "attachment":
                att_id = res.get("id")
            if not att_id:
                continue

            att = AttachmentService.get_authorized_attachment(user=user, attachment_id=att_id)
            if not att:
                continue

            att_key = str(att.id)
            if att_key not in seen_ids:
                resolved_attachments.append(att)
                seen_ids.add(att_key)

            page_raw = res.get("page") or res.get("page_number") or res.get("pageNumber")
            if page_raw is not None:
                try:
                    page_num = int(page_raw)
                    if page_num > 0:
                        attachment_pages[att_key] = page_num
                except (ValueError, TypeError):
                    pass

        return resolved_attachments, attachment_pages

    def _resolve_context_resources(
        self, user: Any, context_resources: List[Dict[str, Any]], query: str = ""
    ) -> Tuple[List[int], List[Any], List[RetrievalResult], List[RetrievalResult]]:
        """
        Authorize and resolve client context resources (documents, pages, and posts).
        Returns: (filter_document_ids, page_chunks, selected_post_results, selected_user_results)
        """
        if not context_resources or not isinstance(context_resources, list):
            return [], [], [], []

        from documents.models import Document
        from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService

        doc_retrieval = DocumentSemanticRetrievalService()
        authorized_chunk_qs = doc_retrieval.build_authorized_chunk_queryset(user=user)
        query_str = query or ""
        is_summary_query = bool(
            re.search(
                r"\b(?:summar(?:ize|y|ise)|overview|table of contents|chapters?|whole (?:book|document|pdf)|about this (?:book|document|pdf)|structure)\b",
                query_str,
                re.IGNORECASE,
            )
        )
        explicit_page_match = re.search(r"\b(?:page|p\.)\s*(\d+)\b", query_str, re.IGNORECASE)
        explicit_query_page = int(explicit_page_match.group(1)) if explicit_page_match else None

        filter_doc_ids: List[int] = []
        page_chunks: List[Any] = []
        selected_post_results: List[RetrievalResult] = []
        selected_user_results: List[RetrievalResult] = []

        for res in context_resources:
            if not isinstance(res, dict):
                continue
            res_type = str(res.get("type") or res.get("category") or "").lower()
            source_type = str(res.get("source_type") or res.get("sourceType") or "").lower()
            # Public web citations and chat attachments are handled separately
            if source_type in {"web", "web_search", "attachment"} or res_type in {"web", "web_search", "attachment"}:
                continue
            resource_id = res.get("id")
            if res_type in ("document", "doc"):
                # Context rail descriptors are produced by the browser in camelCase,
                # while older API clients may still send snake_case fields.
                doc_id = res.get("document_id") or res.get("documentId")
                share_id = (
                    res.get("share_id")
                    or res.get("document_share_id")
                    or res.get("documentShareId")
                )
                if not doc_id and resource_id:
                    try:
                        # Older clients used `id` for a numeric document primary key.
                        doc_id = int(resource_id)
                    except (ValueError, TypeError):
                        # The current context rail uses the document's share UUID as `id`.
                        if not share_id and res_type in ("document", "doc"):
                            share_id = resource_id
                page_raw = (
                    explicit_query_page
                    or res.get("page")
                    or res.get("page_number")
                    or res.get("pageNumber")
                )

                doc_obj = None
                if doc_id:
                    try:
                        doc_obj = Document.objects.filter(id=int(doc_id)).first()
                    except (ValueError, TypeError):
                        pass
                elif share_id:
                    try:
                        share_uuid = uuid.UUID(str(share_id).strip())
                        doc_obj = Document.objects.filter(share_id=share_uuid).first()
                    except (ValueError, TypeError):
                        pass

                if not doc_obj:
                    continue

                # Verify authorized chunk queryset permits this document
                is_authorized = authorized_chunk_qs.filter(document_id=doc_obj.id).exists()
                if not is_authorized:
                    logger.warning(
                        "Context resource document %s not authorized for user %s",
                        doc_obj.id,
                        getattr(user, "id", None),
                    )
                    continue

                if doc_obj.id not in filter_doc_ids:
                    filter_doc_ids.append(doc_obj.id)

                # Retrieve whole-document structural overview + active page chunks
                doc_overview_chunks: List[RetrievalResult] = []
                if doc_obj.share_id and hasattr(doc_retrieval, "get_document_overview_chunks"):
                    try:
                        doc_overview_chunks = doc_retrieval.get_document_overview_chunks(
                            user=user,
                            document_share_id=doc_obj.share_id,
                            max_chunks=(5 if is_summary_query else 2),
                        )
                    except Exception as exc:
                        logger.debug("Could not load document overview chunks for %s: %s", doc_obj.id, exc)

                active_page_chunks: List[RetrievalResult] = []
                if page_raw is not None:
                    try:
                        page_num = int(page_raw)
                        if page_num > 0 and doc_obj.share_id:
                            chunks = doc_retrieval.get_page_chunks(
                                user=user,
                                document_share_id=doc_obj.share_id,
                                page_number=page_num,
                            )
                            if chunks:
                                first_snippet = chunks[0].snippet or ""
                                marker = f"[CURRENTLY OPEN PAGE IN VIEWER — PAGE {page_num}]"
                                if marker not in first_snippet:
                                    chunks[0].snippet = f"{marker}\n{first_snippet}"
                                active_page_chunks.extend(chunks)
                    except (ValueError, TypeError):
                        pass

                if is_summary_query:
                    page_chunks.extend(doc_overview_chunks)
                    page_chunks.extend(active_page_chunks)
                else:
                    page_chunks.extend(active_page_chunks)
                    if doc_overview_chunks:
                        page_chunks.extend(doc_overview_chunks[:1])

            post_id = res.get("post_id") or res.get("postId")
            if post_id or res_type == "post" or source_type == "post":
                post_id = post_id or resource_id
                if not post_id:
                    url = str(res.get("url") or "")
                    match = re.search(r"/post(?:s)?/([0-9a-fA-F-]{36})(?:/|$)", url)
                    post_id = match.group(1) if match else None
                if post_id:
                    try:
                        import uuid
                        from posts.models import Post
                        post_qs = Post.objects.select_related("author")
                        try:
                            post = post_qs.filter(share_id=uuid.UUID(str(post_id))).first()
                        except (ValueError, TypeError, AttributeError):
                            try:
                                post = post_qs.filter(id=int(post_id)).first()
                            except (ValueError, TypeError):
                                post = None
                    except Exception as exc:
                        logger.info("Selected Pwanimate post is unavailable: %s", type(exc).__name__)
                        post = None

                    if post:
                        author = post.author.get_full_name() or post.author.username
                        title = str(res.get("title") or f"Post by {author}")[:255]
                        content_parts = [str(post.content or "").strip()]
                        transcript = str(getattr(post, "video_transcript", "") or "").strip()
                        if transcript:
                            content_parts.append(f"Video transcript: {transcript}")
                        snippet = "\n\n".join(part for part in content_parts if part)
                        if snippet:
                            selected_post_results.append(
                                RetrievalResult(
                                    source=SourceType.POST,
                                    object_id=str(post.share_id),
                                    title=title,
                                    snippet=snippet,
                                    score=1.0,
                                    url=f"/post/{post.share_id}/",
                                    citation=f"Post by @{post.author.username}",
                                    metadata={
                                        "post_id": str(post.share_id),
                                        "author_id": post.author_id,
                                        "author_username": post.author.username,
                                        "selected_resource": True,
                                    },
                                    raw_object=post,
                                )
                            )

            person_data = res.get("person") if isinstance(res.get("person"), dict) else {}
            username = str(
                res.get("username")
                or person_data.get("username")
                or (resource_id if res_type in ("user", "profile") or source_type == "user" else "")
            ).strip()
            if username:
                try:
                    from pwanimate.tools.domain.users import UserProfileTool

                    profile_result = UserProfileTool().execute(user=user, username=username)
                    if profile_result.success and isinstance(profile_result.data, dict):
                        profile = profile_result.data
                        profile_lines = [
                            f"{profile.get('display_name') or username} (@{username})",
                            profile.get("headline") or "",
                            profile.get("academic_level") or "",
                            profile.get("programme_name") or "",
                            profile.get("bio") or "",
                        ]
                        profile_lines.extend(str(value) for value in (profile.get("skills") or [])[:12])
                        profile_lines.extend(str(value) for value in (profile.get("matched_interests") or [])[:12])
                        profile_text = "\n".join(value for value in profile_lines if value).strip()
                        selected_user_results.append(RetrievalResult(
                            source=SourceType.USER,
                            object_id=profile.get("id", username),
                            title=f"Profile: {profile.get('display_name') or username}",
                            snippet=profile_text,
                            score=1.0,
                            url=profile.get("profile_url") or f"/users/user/{username}/",
                            citation=f"Profile @{username}",
                            metadata=profile,
                        ))
                except Exception as exc:
                    logger.info("Selected Pwanimate profile is unavailable: %s", type(exc).__name__)

        return filter_doc_ids, page_chunks, selected_post_results, selected_user_results

    def is_conversational_intent(self, query: str) -> bool:
        """
        Check if query is purely conversational chitchat that does not
        require knowledge base retrieval or domain tools.
        """
        cleaned = query.strip().lower()
        if len(cleaned) > 60:
            # Substantive queries are longer
            return False

        for pattern in CONVERSATIONAL_PATTERNS:
            if re.match(pattern, cleaned, re.IGNORECASE):
                return True
        return False

    def _resolve_effective_budget(self, request: OrchestrationRequest, task_category: str) -> int:
        """
        Resolve effective output token ceiling following strict precedence:
        1. Explicit request-level override (if provided)
        2. Task-aware GenerationPolicy (conversational=512, tool=1024, rag=4096)
        3. Fallback safe default (1024)
        """
        if request.max_tokens is not None and request.max_tokens > 0:
            return request.max_tokens
        policy = get_task_policy(task_category)
        return policy.max_output_tokens

    @staticmethod
    def _with_local_time(instruction: str, request: OrchestrationRequest) -> str:
        """Give Pwanimate the browser's current clock and IANA timezone when available."""
        if not request.local_time:
            return instruction
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime
            requested_zone = request.timezone_name or "Africa/Nairobi"
            if requested_zone in {"UTC", "Etc/UTC"}:
                requested_zone = "Africa/Nairobi"
            zone_obj = ZoneInfo(requested_zone)
            local_dt = datetime.fromisoformat(str(request.local_time).replace("Z", "+00:00"))
            if local_dt.tzinfo is None:
                return instruction
            local_time = local_dt.astimezone(zone_obj).isoformat()
            zone = zone_obj.key
        except Exception:
            return instruction
        return f"{instruction}\n\nCurrent user local time: {local_time} ({zone}). Use this as the current time."

    def _collect_source_warnings(
        self,
        request: OrchestrationRequest,
        effective_attachments: Optional[List[Any]] = None,
        attachment_context: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Inspect currently open documents/pages and attachments for missing or failed
        text extraction so Pwanimate warns the user honestly instead of guessing.
        """
        warnings: List[Dict[str, Any]] = []
        if request.context_resources:
            try:
                from pwanimate.services.study_session import inspect_open_document_availability
                warnings.extend(
                    inspect_open_document_availability(
                        user=request.user,
                        context_resources=request.context_resources,
                    )
                )
            except Exception as exc:
                logger.debug("Could not inspect open document availability: %s", exc)

        if effective_attachments:
            for att in effective_attachments:
                att_type = getattr(att, "attachment_type", None) or "document"
                if att_type != "document":
                    continue
                att_name = getattr(att, "file_name", getattr(att, "name", "attachment"))
                att_id = str(getattr(att, "id", ""))
                status = getattr(att, "processing_status", None)
                if status == "failed":
                    warnings.append({
                        "code": "attachment_extraction_failed",
                        "attachment_id": att_id,
                        "title": att_name,
                        "message": (
                            f"Text extraction failed for uploaded file '{att_name}'. "
                            "Pwanimate cannot read its contents directly."
                        ),
                    })
                elif attachment_context and f"[No extractable text could be found in '{att_name}']" in attachment_context:
                    warnings.append({
                        "code": "attachment_no_extracted_text",
                        "attachment_id": att_id,
                        "title": att_name,
                        "message": (
                            f"No extractable text was found in '{att_name}'. "
                            "If this is a scanned document, please upload a clear page image or paste the text."
                        ),
                    })
        return warnings

    def _apply_study_and_warning_context(
        self,
        request: OrchestrationRequest,
        query: str,
        context_pkg: Optional[ContextPackage],
        system_instruction: str,
        source_warnings: List[Dict[str, Any]],
    ) -> Tuple[Optional[ContextPackage], str]:
        """
        Inject Study Mode checkpoint/session continuity and unavailable-source notices
        into the ContextPackage and system instruction.
        """
        blocks: List[str] = []
        if request.study_mode:
            system_instruction = (
                f"{system_instruction}\n\n"
                f"{STUDY_MODE_INSTRUCTION_ADDENDUM}\n\n"
                f"{STUDY_MODE_ADAPTIVE_TEACHING_ADDENDUM}"
            )
            if request.study_context is not None:
                if hasattr(request.study_context, "format_prompt_block"):
                    block = request.study_context.format_prompt_block()
                else:
                    block = str(request.study_context or "").strip()
                if block:
                    blocks.append(block)

        if source_warnings:
            system_instruction = f"{system_instruction}\n\n{UNAVAILABLE_SOURCE_INSTRUCTION_ADDENDUM}"
            notice_lines = ["<unavailable_source_notice>"]
            for w in source_warnings:
                msg = str(w.get("message") or "").strip()
                if msg:
                    notice_lines.append(f"  - {msg}")
            notice_lines.append("</unavailable_source_notice>")
            blocks.append("\n".join(notice_lines))

        if blocks:
            if context_pkg is None:
                context_pkg = ContextPackage(query=query, user_context=request.user_context)
            combined = "\n\n".join(blocks)
            if context_pkg.study_context_block:
                context_pkg.study_context_block = f"{context_pkg.study_context_block}\n\n{combined}"
            else:
                context_pkg.study_context_block = combined

        return context_pkg, system_instruction

    def run(
        self,
        request: OrchestrationRequest,
        on_progress: Optional[Callable[[str], None]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> OrchestrationResponse:
        """
        Execute an end-to-end orchestration turn.

        1. Inspects query intent for conversational pleasantries (bypassed if attachments/context present).
        2. Evaluates high-confidence deterministic domain tool routes.
           If matched: executes authorized tool and grounds response via ContextPackage.
        3. If broad academic/knowledge query or explicit attachment/context turn:
           a. Performs authorized multi-source retrieval (respecting request.user and context filters).
           b. Binds and formats retrieved context + attachment context into ContextPackage.
           c. Formulates LLMRequest with academic tutor system prompt.
           d. Generates normalized response via Gateway.
        4. Packages citations, sources, timing, and response.
        """
        t_start = time.perf_counter()
        query = request.query.strip()
        from pwanimate.services.social_updates import (
            get_pending_friend_post_updates,
        )
        friend_post_updates = get_pending_friend_post_updates(request.user)

        # Construct current-user context snapshot if not already provided
        if request.user and getattr(request.user, "is_authenticated", False) and request.user_context is None:
            try:
                from pwanimate.context.user_context import UserContextService
                request.user_context = UserContextService().build(request.user)
            except Exception as exc:
                logger.warning("Could not build UserContext for user %s: %s", getattr(request.user, "id", None), exc)

        # Selected resources inform the answer; they do not suppress independent tools.
        has_attachments_or_context = bool(request.attachments or request.context_resources)

        # 1. Use exact deterministic routes for clear requests.
        tool_route = self.tool_router.route(
            query, user=request.user, user_context=request.user_context,
            local_time=request.local_time, timezone_name=request.timezone_name,
        )
        retrieval_sources = None
        # 2. Force web search for clear current-events requests and factual follow-ups.
        # This decision is deterministic so an LLM selector cannot silently skip it.
        required_web_query = self.tool_selector.required_web_query(query, request.history)
        if not tool_route and required_web_query:
            tool_route = ToolRoute(
                tool_name="web_search",
                parameters={"query": required_web_query},
                confidence=1.0,
                matched_intent="required_public_web_lookup",
            )

        # 3. Let the configured model resolve other paraphrases not covered by patterns.
        # Avoid an extra selection call for simple greetings and acknowledgements.
        if (
            not tool_route
            and not self.is_conversational_intent(query)
            and self.tool_selector.might_need_tool(query)
        ):
            selection = self.tool_selector.select(
                query=query,
                history=request.history,
                local_time=request.local_time,
                timezone_name=request.timezone_name,
                selected_resources=request.context_resources,
                attachments=request.attachments,
            )
            tool_route = selection.route
            retrieval_sources = selection.sources
        if tool_route:
            if tool_route.tool_name == "web_search" and on_progress:
                on_progress("searching_web")
            elif tool_route.tool_name == "content_search" and on_progress:
                content_type = str(tool_route.parameters.get("content_type") or "").lower()
                if content_type == "post":
                    on_progress("searching_posts")
                elif content_type == "document":
                    on_progress("searching_documents")
            elif tool_route.tool_name == "group_announcements" and on_progress:
                on_progress("searching_posts")
            response = self._run_tool(request, query, tool_route, t_start, on_progress=on_progress, on_token=on_token)
            self._record_usage(request, response)
            return self._append_friend_post_updates(response, request.user, friend_post_updates)

        # 4. Conversational turns can skip platform retrieval after tool choice.
        is_conversational = not has_attachments_or_context and not request.study_mode and (
            request.task == "general" or self.is_conversational_intent(query)
        )
        if is_conversational:
            response = self._run_conversational(request, query, t_start, on_token=on_token)
            self._record_usage(request, response)
            return self._append_friend_post_updates(response, request.user, friend_post_updates)

        # 5. Standard / Attachment / Context RAG
        response = self._run_rag(
            request,
            query,
            t_start,
            source_override=retrieval_sources,
            on_progress=on_progress,
            on_token=on_token,
        )
        self._record_usage(request, response)
        return self._append_friend_post_updates(response, request.user, friend_post_updates)

    @staticmethod
    def _request_metadata(request) -> Dict[str, Any]:
        """
        Gateway metadata for this turn: the engine's ordered chain (if any),
        the caller's tier rank and the offering they explicitly asked for.

        The tier rank is what lets the router withhold paid models from a
        free-tier student's chain; the offering is the premium capability the
        student selected.
        """
        meta: Dict[str, Any] = {}
        try:
            meta.update(request.engine_metadata())
        except Exception:  # noqa: BLE001
            pass
        try:
            meta.update(request.billing_metadata())
        except Exception:  # noqa: BLE001 - billing must never break a turn
            pass
        if getattr(request, "request_id", None):
            meta["request_id"] = request.request_id
        meta["prompt_version"] = "2026-10-26.1"
        meta["context_version"] = "2.1"
        return meta

    @staticmethod
    def _first_image_id(attachments) -> Optional[str]:
        for attachment in attachments or []:
            if (getattr(attachment, "attachment_type", "") or "") == "image":
                return str(getattr(attachment, "id", "") or "")
        return None

    @staticmethod
    def _first_image_policy(attachments) -> str:
        for attachment in attachments or []:
            if (getattr(attachment, "attachment_type", "") or "") == "image":
                return (getattr(attachment, "vision_context_policy", "") or "ask").strip().lower()
        return "ask"

    @staticmethod
    def _remember_vision_failure(attachments) -> None:
        """
        Stop re-sending an image that could not be analysed.

        The failure is recorded on the attachment, so the next turn carries a
        short note instead of the image: no repeated vision call, no repeated
        "Spark Vision is down" announcement.
        """
        for attachment in attachments or []:
            if (getattr(attachment, "attachment_type", "") or "") != "image":
                continue
            try:
                attachment.vision_error = SPARK_VISION_UNAVAILABLE_NOTICE
                attachment.save(update_fields=["vision_error"])
            except Exception:  # noqa: BLE001 - best effort, never break the turn
                logger.debug("Could not record the vision failure on %s", attachment)

    @staticmethod
    def _record_usage(request, response):
        """
        Record this turn's real usage (model + provider token counts) into the
        billing ledger, so the quota the student sees is the quota we enforce.

        Never fatal: accounting must not break a chat turn.
        """
        try:
            from pwanimate.ai.vision_images import image_will_be_sent
            from pwanimate.billing.models import UsageKind
            from pwanimate.billing.services import UsageService

            # Count the images that actually reached the provider. An image
            # that was already analysed at upload is represented by its
            # stored description, so it was not sent and must not be
            # charged as an image.
            images = sum(
                1
                for attachment in (getattr(request, "attachments", None) or [])
                if image_will_be_sent(attachment)
            )
            UsageService.record_turn(
                user=getattr(request, "user", None),
                provider=getattr(response, "provider", "") or "",
                model_id=getattr(response, "model", "") or "",
                prompt_tokens=int(getattr(response, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(response, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(response, "total_tokens", 0) or 0),
                images=images,
                kind=UsageKind.IMAGE if images else UsageKind.CHAT,
                source="chat",
            )
        except Exception:  # noqa: BLE001 - accounting must never break a turn
            logger.warning("[BILLING] usage recording failed", exc_info=True)

    @staticmethod
    def _append_friend_post_updates(response, user, updates):
        """Surface newly received friend posts once, in the next successful chat reply."""
        if not updates:
            return response
        from pwanimate.services.social_updates import mark_friend_post_updates_surfaced

        lines = ["**New from your network:**"]
        for update in updates:
            author = str(update.get("author") or "Someone you follow").strip()
            excerpt = str(update.get("excerpt") or "").strip()
            label = f"@{author}" if author and not author.startswith("@") else author
            item = f"- {label} posted"
            if excerpt:
                item += f": “{excerpt}”"
            if update.get("url", "").startswith("/") and not update["url"].startswith("//"):
                item += f" ([view post]({update['url']}))"
            lines.append(item)

        response.answer = (response.answer.rstrip() + "\n\n" + "\n".join(lines)).strip()
        mark_friend_post_updates_surfaced(user, updates)
        response.metadata = response.metadata or {}
        response.metadata["friend_post_updates_count"] = len(updates)
        return response

    def _generate(self, llm_request: LLMRequest, on_token: Optional[Callable[[str], None]] = None):
        """Single LLM call site: streams tokens when a callback is provided."""
        if on_token is not None:
            return self.gateway.generate_stream(llm_request, on_token)
        return self.gateway.generate(llm_request)

    def _run_conversational(
        self,
        request: OrchestrationRequest,
        query: str,
        t_start: float,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> OrchestrationResponse:
        """Handle pure conversational dialogue turns without retrieval or tools."""
        messages = list(request.history) + [ChatMessage(role="user", content=query)]

        context_pkg = None
        if request.user_context:
            context_pkg = ContextPackage(
                query=query,
                user_context=request.user_context,
                conversation_memory=request.conversation_memory,
            )
        elif request.conversation_memory:
            context_pkg = ContextPackage(query=query, conversation_memory=request.conversation_memory)

        source_warnings = self._collect_source_warnings(request)
        base_instruction = self._with_local_time(get_system_instruction("conversational"), request)
        context_pkg, system_instruction = self._apply_study_and_warning_context(
            request=request,
            query=query,
            context_pkg=context_pkg,
            system_instruction=base_instruction,
            source_warnings=source_warnings,
        )

        effective_max_tokens = self._resolve_effective_budget(request, "conversational")

        llm_request = LLMRequest(
            task="general",
            messages=messages,
            context=context_pkg,
            system_instruction=system_instruction,
            provider=request.provider,
            model=request.model,
            temperature=request.temperature,
            max_tokens=effective_max_tokens,
            metadata=self._request_metadata(request),
        )

        t_gen = time.perf_counter()
        llm_response = self._generate(llm_request, on_token)
        gen_time_ms = round((time.perf_counter() - t_gen) * 1000.0, 2)
        total_time_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

        quota_info = llm_response.metadata.get("quota_info")
        sanitized_ans = sanitize_llm_response(llm_response.content)
        if not sanitized_ans or not sanitized_ans.strip():
            logger.warning("Sanitized answer is empty for provider=%s model=%s; applying fallback response", llm_response.provider, llm_response.model)
            sanitized_ans = "I'm sorry, I was unable to generate a response. Please try asking again or rephrasing your question."

        thoughts_tokens = llm_response.metadata.get("thoughts_tokens")
        total_output_tokens = llm_response.metadata.get("total_output_tokens")
        max_output_tokens = llm_response.metadata.get("max_output_tokens", llm_request.max_tokens)

        logger.info(
            "Pwanimate Orchestrator [conversational]: provider=%s model=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s thoughts_tokens=%s total_output_tokens=%s total_tokens=%s max_output_tokens=%s raw_len=%d ans_len=%d duration_ms=%.2f",
            llm_response.provider,
            llm_response.model,
            llm_response.finish_reason,
            llm_response.prompt_tokens,
            llm_response.completion_tokens,
            thoughts_tokens,
            total_output_tokens,
            llm_response.total_tokens,
            max_output_tokens,
            len(llm_response.content),
            len(sanitized_ans),
            total_time_ms,
        )

        metadata = {
            "intent": "conversational",
            "thoughts_tokens": thoughts_tokens,
            "total_output_tokens": total_output_tokens,
            "max_output_tokens": max_output_tokens,
            "study_mode": bool(request.study_mode),
            "prompt_version": "2026-10-26.1",
            "context_version": "2.1",
            "memory_count": len(request.conversation_memory),
            "memory_trace": request.conversation_memory,
        }
        if source_warnings:
            metadata["warnings"] = source_warnings

        return OrchestrationResponse(
            answer=sanitized_ans,
            citations=[],
            sources=[],
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_tokens=llm_response.prompt_tokens,
            completion_tokens=llm_response.completion_tokens,
            total_tokens=llm_response.total_tokens,
            retrieval_time_ms=0.0,
            generation_time_ms=gen_time_ms,
            total_time_ms=total_time_ms,
            metadata=metadata,
            quota_info=quota_info,
            finish_reason=llm_response.finish_reason,
            warnings=source_warnings,
        )

    def _run_tool(
        self,
        request: OrchestrationRequest,
        query: str,
        tool_route: ToolRoute,
        t_start: float,
        on_progress: Optional[Callable[[str], None]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> OrchestrationResponse:
        """Handle high-confidence deterministic domain tool execution."""
        t_tool = time.perf_counter()
        tool_result = self.tool_registry.execute(
            tool_route.tool_name,
            user=request.user,
            user_context=request.user_context,
            **tool_route.parameters,
        )
        tool_time_ms = round((time.perf_counter() - t_tool) * 1000.0, 2)

        # Convert tool result to grounded ContextPackage
        context_items = tool_result_to_context_items(tool_route.tool_name, tool_result)
        context_pkg = build_tool_context_package(
            query,
            context_items,
            user_context=request.user_context,
        )
        # Extract people data if people_discovery or user_profile tool ran
        people_results: List[Dict[str, Any]] = []
        if (
            tool_route.tool_name == "people_discovery"
            and tool_result.success
            and isinstance(tool_result.data, list)
        ):
            people_results = list(tool_result.data)
        elif (
            tool_route.tool_name == "user_profile"
            and tool_result.success
            and isinstance(tool_result.data, dict)
        ):
            user_data = tool_result.data
            u_name = user_data.get("username", "")
            d_name = user_data.get("display_name", u_name)
            p_url = user_data.get("profile_url") or f"/users/user/{u_name}/"
            p_avatar = user_data.get("avatar_url") or user_data.get("profile_photo_url")
            p_skills = user_data.get("skills") if isinstance(user_data.get("skills"), list) else []
            p_interests = user_data.get("matched_interests") or [
                i.strip() for i in (user_data.get("interests") or "").split(",") if i.strip()
            ]
            prog_name = (
                user_data.get("programme_name")
                or user_data.get("programme")
                or user_data.get("course")
            )
            acad_level = user_data.get("academic_level") or (
                f"Year {user_data.get('year')}" if user_data.get("year") else None
            )
            people_results = [
                {
                    "user_id": user_data.get("id"),
                    "username": u_name,
                    "display_name": d_name,
                    "headline": user_data.get("headline", ""),
                    "avatar_url": p_avatar,
                    "profile_url": p_url,
                    "programme_name": prog_name,
                    "academic_level": acad_level,
                    "collaboration_status": user_data.get("collaboration_status", ""),
                    "matched_skills": p_skills,
                    "matched_interests": p_interests,
                    "evidence": {},
                    "recommendation_score": 0,
                }
            ]

        sources_summary = self._build_sources_summary(context_pkg.items)

        # Write actions propose; the student confirms. The proposal travels
        # back to the client so it can render a confirm/cancel card.
        pending_actions: List[Dict[str, Any]] = []
        if tool_result.success and isinstance(tool_result.data, dict):
            action_data = tool_result.data.get("action")
            if isinstance(action_data, dict):
                pending_actions.append(action_data)

        messages = list(request.history) + [ChatMessage(role="user", content=query)]

        source_warnings = self._collect_source_warnings(request)
        base_instruction = self._with_local_time(
            get_system_instruction("optional" if tool_route.tool_name == "web_search" else "required"),
            request,
        )
        context_pkg, system_instruction = self._apply_study_and_warning_context(
            request=request,
            query=query,
            context_pkg=context_pkg,
            system_instruction=base_instruction,
            source_warnings=source_warnings,
        )

        effective_max_tokens = self._resolve_effective_budget(request, "tool")

        llm_request = LLMRequest(
            task="tool",
            messages=messages,
            context=context_pkg,
            system_instruction=system_instruction,
            provider=request.provider,
            model=request.model,
            temperature=request.temperature,
            max_tokens=effective_max_tokens,
            metadata=self._request_metadata(request),
        )

        if (
            tool_route.tool_name == "web_search"
            and tool_result.success
            and any(item.source == "web" and item.url for item in context_pkg.items)
            and on_progress
        ):
            on_progress("reading_sources")
        elif tool_route.tool_name in {"content_search", "group_announcements"} and on_progress:
            on_progress("devouring_context" if tool_result.success and tool_result.data else "thinking")

        t_gen = time.perf_counter()
        llm_response = None
        vision_text_fallback = False
        has_image_attachments = any(
            getattr(attachment, "attachment_type", "") == "image"
            for attachment in (getattr(request, "attachments", None) or [])
        )
        try:
            llm_response = self._generate(llm_request, on_token)
        except AIGatewayError as vision_error:
            if not has_image_attachments:
                raise
            # No image-capable model was available. The free tier is never
            # denied service, so answer from the text context instead of
            # failing the turn — and say plainly that the image was not read.
            logger.warning(
                "Pwanimate tool route could not serve the image; retrying text-only: %s",
                type(vision_error).__name__,
            )
            self._remember_vision_failure(getattr(request, "attachments", None))
            vision_text_fallback = True
            text_only_instruction = _vision_fallback_instruction(llm_request.system_instruction)
            text_request = LLMRequest(
                task=llm_request.task,
                messages=llm_request.messages,
                context=llm_request.context,
                system_instruction=text_only_instruction,
                provider=llm_request.provider,
                model=llm_request.model,
                temperature=llm_request.temperature,
                max_tokens=llm_request.max_tokens,
                attachments=[],
                metadata=llm_request.metadata,
            )
            llm_response = self._generate(text_request, on_token)
            llm_response.metadata.update({
                "vision_route": "text_fallback",
                "vision_text_fallback": True,
                "vision_fallback_error": type(vision_error).__name__,
                "vision_attachment_id": self._first_image_id(getattr(request, "attachments", None)),
                "vision_policy": self._first_image_policy(getattr(request, "attachments", None)),
            })
        gen_time_ms = round((time.perf_counter() - t_gen) * 1000.0, 2)
        total_time_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

        citations = llm_response.citations or list(context_pkg.citations)
        quota_info = llm_response.metadata.get("quota_info")
        sanitized_ans = sanitize_llm_response(llm_response.content)
        if not sanitized_ans or not sanitized_ans.strip():
            logger.warning("Sanitized answer is empty for tool route provider=%s model=%s; applying fallback response", llm_response.provider, llm_response.model)
            sanitized_ans = "I'm sorry, I was unable to generate a response. Please try asking again or rephrasing your question."

        thoughts_tokens = llm_response.metadata.get("thoughts_tokens")
        total_output_tokens = llm_response.metadata.get("total_output_tokens")
        max_output_tokens = llm_response.metadata.get("max_output_tokens", llm_request.max_tokens)

        logger.info(
            "Pwanimate Orchestrator [tool:%s]: provider=%s model=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s thoughts_tokens=%s total_output_tokens=%s total_tokens=%s max_output_tokens=%s raw_len=%d ans_len=%d duration_ms=%.2f",
            tool_route.tool_name,
            llm_response.provider,
            llm_response.model,
            llm_response.finish_reason,
            llm_response.prompt_tokens,
            llm_response.completion_tokens,
            thoughts_tokens,
            total_output_tokens,
            llm_response.total_tokens,
            max_output_tokens,
            len(llm_response.content),
            len(sanitized_ans),
            total_time_ms,
        )

        metadata = {
            "intent": "tool",
            "tool_name": tool_route.tool_name,
            "tool_success": tool_result.success,
            "matched_intent": tool_route.matched_intent,
            "context_items_count": context_pkg.total_items,
            "thoughts_tokens": thoughts_tokens,
            "total_output_tokens": total_output_tokens,
            "max_output_tokens": max_output_tokens,
            "study_mode": bool(request.study_mode),
            "pending_actions": pending_actions,
        }
        if source_warnings:
            metadata["warnings"] = source_warnings

        return OrchestrationResponse(
            answer=sanitized_ans,
            citations=citations,
            sources=sources_summary,
            people=people_results,
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_tokens=llm_response.prompt_tokens,
            completion_tokens=llm_response.completion_tokens,
            total_tokens=llm_response.total_tokens,
            retrieval_time_ms=tool_time_ms,
            generation_time_ms=gen_time_ms,
            total_time_ms=total_time_ms,
            metadata=metadata,
            quota_info=quota_info,
            finish_reason=llm_response.finish_reason,
            # No banner: the model says it in the answer, once, and only
            # when the question actually depends on the image.
            warnings=source_warnings,
        )

    def _run_rag(
        self,
        request: OrchestrationRequest,
        query: str,
        t_start: float,
        source_override: Optional[List[str]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> OrchestrationResponse:
        """Handle knowledge/retrieval-augmented dialogue turns."""
        # 0. Resolve any attachments active in the Right Context Rail (including open page numbers)
        rail_attachments, attachment_pages = self._resolve_context_attachments(
            request.user, request.context_resources
        )
        effective_attachments = list(request.attachments or [])
        seen_att_ids = {str(getattr(a, "id", "")) for a in effective_attachments}
        for r_att in rail_attachments:
            r_id = str(getattr(r_att, "id", ""))
            if r_id and r_id not in seen_att_ids:
                effective_attachments.append(r_att)
                seen_att_ids.add(r_id)

        # Process attachments (images for vision, docs for prompt context with active page awareness)
        has_document_attachment = any(
            (getattr(attachment, "attachment_type", None) or "document") == "document"
            for attachment in effective_attachments
        )
        if has_document_attachment and on_progress:
            on_progress("devouring_context")
        image_attachments, attachment_context = self._process_attachments(
            effective_attachments,
            query=query,
            attachment_pages=attachment_pages,
        )

        # 1. Resolve authorized context resources (active document / page filtering)
        try:
            filter_doc_ids, page_chunks, selected_post_results, selected_user_results = self._resolve_context_resources(
                request.user, request.context_resources, query=query
            )
        except TypeError:
            filter_doc_ids, page_chunks, selected_post_results, selected_user_results = self._resolve_context_resources(
                request.user, request.context_resources
            )

        # 2. Upstream Authorized Retrieval
        t_ret = time.perf_counter()
        # Keep searching the requested sources even when a selected document page
        # is present. Previously page chunks replaced retrieval entirely, which
        # made unrelated post searches appear to find nothing.
        requested_sources = request.sources or source_override
        sources = (
            [source if isinstance(source, SourceType) else SourceType(str(source).lower())
             for source in requested_sources]
            if requested_sources
            else [SourceType.DOCUMENT, SourceType.POST, SourceType.USER, SourceType.GROUP]
        )
        source_set = set(sources)
        include_documents = SourceType.DOCUMENT in source_set
        include_posts = SourceType.POST in source_set
        include_users = SourceType.USER in source_set
        filters = {"document_ids": filter_doc_ids} if filter_doc_ids and include_documents else {}
        retrieval_req = RetrievalRequest(
            query=query,
            user=request.user,
            sources=sources,
            mode=RetrievalMode.HYBRID,
            filters=filters,
        )
        if on_progress:
            def report_retrieval_source(source):
                if source == SourceType.DOCUMENT:
                    on_progress("searching_documents")
                elif source == SourceType.POST:
                    on_progress("searching_posts")

            retrieval_resp = self.retrieval_service.retrieve(
                retrieval_req,
                on_source_start=report_retrieval_source,
            )
        else:
            retrieval_resp = self.retrieval_service.retrieve(retrieval_req)
        if selected_post_results and include_posts:
            retrieval_resp.results.extend(selected_post_results)
            retrieval_resp.total_count += len(selected_post_results)
        if selected_user_results and include_users:
            retrieval_resp.results.extend(selected_user_results)
            retrieval_resp.total_count += len(selected_user_results)
        ret_time_ms = round((time.perf_counter() - t_ret) * 1000.0, 2)

        # 3. Context Engine Bounding
        # Mark selected document chunks explicitly so they remain available even
        # while other sources are searched and ranked for the current question.
        explicit_retrieval_results = list(page_chunks) if include_documents else []
        if include_posts:
            explicit_retrieval_results.extend(selected_post_results)
        if include_users:
            explicit_retrieval_results.extend(selected_user_results)
        selected_doc_id_set = set(filter_doc_ids) if include_documents else set()
        for result in retrieval_resp.results:
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            if (
                result.source == SourceType.DOCUMENT
                and (metadata.get("document_id") or result.object_id) in selected_doc_id_set
            ):
                explicit_retrieval_results.append(result)
        explicit_items = []
        seen_explicit_ids = set()
        for result in explicit_retrieval_results:
            explicit_key = (str(result.source), str(result.object_id))
            if explicit_key in seen_explicit_ids:
                continue
            seen_explicit_ids.add(explicit_key)
            if len(explicit_items) >= 10:
                break
            explicit_items.append(ContextItem(
                source=result.source,
                object_id=result.object_id,
                title=result.title,
                content=(result.snippet or "")[:3000],
                citation=result.citation,
                url=result.url,
                relevance_score=result.score,
                metadata=result.metadata,
                explicitly_selected=True,
                authority_level=AuthorityLevel.EXPLICIT,
            ))

        explicit_object_ids = {
            (str(item.source), str(item.object_id)) for item in explicit_items
        }
        retrieval_resp.results = [
            result for result in retrieval_resp.results
            if (str(result.source), str(result.object_id)) not in explicit_object_ids
        ]

        context_req = ContextRequest(
            query=query,
            retrieval_response=retrieval_resp,
            explicit_resources=explicit_items,
            user_context=request.user_context,
            grounding_mode=(
                GroundingMode.EXPLICIT_RESOURCE
                if explicit_items
                else GroundingMode.OPTIONAL
            ),
        )
        context_pkg: ContextPackage = self.context_engine.build_context(context_req)
        context_pkg.conversation_memory = list(request.conversation_memory)
        if attachment_context:
            context_pkg.attachment_context = attachment_context

        source_warnings = self._collect_source_warnings(
            request=request,
            effective_attachments=effective_attachments,
            attachment_context=attachment_context,
        )
        base_instruction = self._with_local_time(get_system_instruction(context_pkg.grounding_mode), request)
        context_pkg, system_instruction = self._apply_study_and_warning_context(
            request=request,
            query=query,
            context_pkg=context_pkg,
            system_instruction=base_instruction,
            source_warnings=source_warnings,
        )

        # Extract source summaries (deduplicated & enriched)
        sources_summary = self._build_sources_summary(context_pkg.items)

        # 4. LLM Generation
        last_user_msg = ChatMessage(role="user", content=query, attachments=image_attachments)
        messages = list(request.history) + [last_user_msg]

        effective_max_tokens = self._resolve_effective_budget(request, "rag")
        is_multimodal_turn = bool(image_attachments)

        llm_request = LLMRequest(
            task="rag",
            messages=messages,
            context=context_pkg,
            system_instruction=system_instruction,
            provider=("gemini" if is_multimodal_turn else request.provider),
            model=(
                getattr(settings, "PWANIMATE_VISION_MODEL", "gemini-3.8-flash")
                if is_multimodal_turn
                else request.model
            ),
            temperature=request.temperature,
            max_tokens=effective_max_tokens,
            attachments=image_attachments,
            metadata={
                **request.engine_metadata(),
                **({"required_capability": "image"} if is_multimodal_turn else {}),
            },
        )

        if on_progress:
            on_progress("devouring_context" if attachment_context or context_pkg.total_items else "thinking")

        t_gen = time.perf_counter()
        vision_text_fallback = False
        if is_multimodal_turn:
            try:
                llm_response = self.gateway.generate(llm_request)
            except AIGatewayError as vision_error:
                logger.warning(
                    "Pwanimate vision route failed; retrying text-only through the normal model chain: %s",
                    type(vision_error).__name__,
                )
                self._remember_vision_failure(image_attachments)
                vision_text_fallback = True
                text_only_messages = [
                    ChatMessage(role=message.role, content=message.content)
                    for message in messages
                ]
                text_only_instruction = _vision_fallback_instruction(llm_request.system_instruction)
                text_request = LLMRequest(
                    task=llm_request.task,
                    messages=text_only_messages,
                    context=llm_request.context,
                    system_instruction=text_only_instruction,
                    provider=request.provider,
                    model=request.model,
                    temperature=llm_request.temperature,
                    max_tokens=llm_request.max_tokens,
                    metadata=self._request_metadata(request),
                    attachments=[],
                )
                llm_response = self.gateway.generate(text_request)
                llm_request = text_request
                llm_response.metadata.update({
                    "vision_route": "text_fallback",
                    "vision_text_fallback": True,
                    "vision_fallback_error": type(vision_error).__name__,
                    "vision_attachment_id": self._first_image_id(image_attachments),
                    "vision_policy": self._first_image_policy(image_attachments),
                    "fallback_used": True,
                    "original_provider": "gemini",
                    "original_model": getattr(settings, "PWANIMATE_VISION_MODEL", "gemini-3.8-flash"),
                })
        else:
            # Text turns stream when the caller supplied a token callback.
            # Multimodal turns stay non-streaming in v1 (vision→text fallback
            # correctness first; the client degrades to final-render UX).
            llm_response = self._generate(llm_request, on_token)
        if is_multimodal_turn:
            if not vision_text_fallback:
                llm_response.metadata["vision_route"] = "fallback" if llm_response.provider != "gemini" else "primary"
            if not vision_text_fallback and llm_response.provider != "gemini":
                llm_response.metadata["fallback_used"] = True
                llm_response.metadata.setdefault("original_provider", "gemini")
                llm_response.metadata.setdefault("original_model", llm_request.model)
        gen_time_ms = round((time.perf_counter() - t_gen) * 1000.0, 2)
        total_time_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

        # Citations are drawn from LLMResponse (or ContextPackage fallback)
        citations = llm_response.citations or list(context_pkg.citations)
        quota_info = llm_response.metadata.get("quota_info")
        sanitized_ans = sanitize_llm_response(llm_response.content)
        if not sanitized_ans or not sanitized_ans.strip():
            logger.warning("Sanitized answer is empty for rag provider=%s model=%s; applying fallback response", llm_response.provider, llm_response.model)
            sanitized_ans = "I'm sorry, I was unable to generate a response. Please try asking again or rephrasing your question."

        thoughts_tokens = llm_response.metadata.get("thoughts_tokens")
        total_output_tokens = llm_response.metadata.get("total_output_tokens")
        max_output_tokens = llm_response.metadata.get("max_output_tokens", llm_request.max_tokens)

        logger.info(
            "Pwanimate Orchestrator [rag]: provider=%s model=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s thoughts_tokens=%s total_output_tokens=%s total_tokens=%s max_output_tokens=%s raw_len=%d ans_len=%d duration_ms=%.2f",
            llm_response.provider,
            llm_response.model,
            llm_response.finish_reason,
            llm_response.prompt_tokens,
            llm_response.completion_tokens,
            thoughts_tokens,
            total_output_tokens,
            llm_response.total_tokens,
            max_output_tokens,
            len(llm_response.content),
            len(sanitized_ans),
            total_time_ms,
        )

        metadata = {
            "intent": "rag",
            "retrieved_count": len(retrieval_resp.results),
            "context_items_count": context_pkg.total_items,
            "thoughts_tokens": thoughts_tokens,
            "total_output_tokens": total_output_tokens,
            "max_output_tokens": max_output_tokens,
            "study_mode": bool(request.study_mode),
            "prompt_version": "2026-10-26.1",
            "context_version": "2.1",
            "memory_count": len(request.conversation_memory),
            "memory_trace": request.conversation_memory,
            "retrieval_trace": [
                {
                    "source": result.source.value if hasattr(result.source, "value") else str(result.source),
                    "object_id": str(result.object_id),
                    "score": round(float(result.score), 4),
                    "citation": result.citation,
                }
                for result in retrieval_resp.results[:50]
            ],
        }
        if source_warnings:
            metadata["warnings"] = source_warnings

        return OrchestrationResponse(
            answer=sanitized_ans,
            citations=citations,
            sources=sources_summary,
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_tokens=llm_response.prompt_tokens,
            completion_tokens=llm_response.completion_tokens,
            total_tokens=llm_response.total_tokens,
            retrieval_time_ms=ret_time_ms,
            generation_time_ms=gen_time_ms,
            total_time_ms=total_time_ms,
            metadata=metadata,
            quota_info=quota_info,
            finish_reason=llm_response.finish_reason,
            # No banner: the model says it in the answer, once, and only
            # when the question actually depends on the image.
            warnings=source_warnings,
        )

    def _build_sources_summary(self, context_items: list) -> List[Dict[str, Any]]:
        """
        Build deduplicated, enriched source summaries from context items.
        Preserves canonical resource metadata (type, media, author, creation date)
        while avoiding redundant duplicate badges from the same document version.
        """
        seen_keys = set()
        summary: List[Dict[str, Any]] = []

        for item in context_items:
            src_val = item.source.value if hasattr(item.source, "value") else str(item.source)
            # Deduplicate by canonical URL or (source, id)
            dedup_key = (src_val, item.url) if item.url else (src_val, str(item.object_id))
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            meta = item.metadata if isinstance(item.metadata, dict) else {}
            res_type = meta.get("resource_type") or meta.get("chunk_type") or meta.get("file_type") or src_val
            file_ext = meta.get("file_type") or meta.get("extension") or ""
            author_val = meta.get("author") or meta.get("author_username") or meta.get("author_name") or ""
            created_val = meta.get("created_at") or meta.get("published_at") or ""

            summary_dict: Dict[str, Any] = {
                "source": src_val,
                "id": item.object_id,
                "title": item.title,
                "citation": item.citation,
                "url": item.url,
                "web_citation_index": meta.get("web_citation_index"),
                "domain": meta.get("source") if src_val == "web" else "",
                "resource_type": res_type,
                "file_extension": file_ext,
                "media_url": meta.get("media_url") or "",
                "thumbnail_url": meta.get("thumbnail_url") or meta.get("thumbnail") or "",
                "hls_url": meta.get("hls_url") or "",
                "author": author_val,
                "created_at": str(created_val) if created_val else "",
                "published_at": str(meta.get("published_at") or ""),
                "page_number": meta.get("page_number"),
                "document_id": meta.get("document_id"),
                "document_share_id": meta.get("document_share_id") or "",
                "file_type": meta.get("file_type") or file_ext or "",
                "post_id": meta.get("post_id") or (item.object_id if src_val == "post" else ""),
                "snippet": (
                    (meta.get("snippet") or item.content[:1200])
                    if src_val in {"post", "web"} and item.content
                    else ""
                ),
            }

            if (
                src_val == "user"
                and meta
                and item.object_id not in ("people_none", "user_none")
            ):
                summary_dict["person"] = meta

            summary.append(summary_dict)

        return summary
