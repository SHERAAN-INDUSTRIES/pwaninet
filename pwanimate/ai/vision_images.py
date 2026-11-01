"""
Preparing images for a vision model.

Two jobs, both of which exist to protect the account:

1. **Strip EXIF.** A student photo carries GPS coordinates, the device
   name and a timestamp. None of that helps a study assistant and all of it
   is personal data we should not hand to a third-party inference provider.
2. **Downscale.** Vision models bill on input tokens, and an image's token
   count grows with its pixel count. A 1600 px phone screenshot costs ~4,800
   input tokens; the same image at 1024 px costs a fraction of that for the
   same OCR accuracy.

The helpers never raise. If an image cannot be prepared, the original bytes
are used -- an optimisation must not be able to fail a student's turn.
"""

import io
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MAX_EDGE = 1024
JPEG_QUALITY = 85


def prepare_image_for_vision(
    data_bytes: bytes,
    mime_type: str = "image/jpeg",
    max_edge: Optional[int] = None,
) -> Tuple[bytes, str]:
    """
    Return ``(bytes, mime_type)`` suitable for a vision model.

    The original bytes and mime type are returned unchanged when the image
    needs no work (already small, no EXIF) or when preparation fails.
    """
    if not data_bytes:
        return data_bytes, mime_type

    try:
        from PIL import Image
    except ImportError:
        logger.debug("Pillow is unavailable; sending the original image bytes")
        return data_bytes, mime_type

    if max_edge is None:
        from django.conf import settings

        max_edge = getattr(settings, "PWANIMATE_VISION_MAX_IMAGE_EDGE", DEFAULT_MAX_EDGE)
    try:
        max_edge = int(max_edge)
    except (TypeError, ValueError):
        max_edge = DEFAULT_MAX_EDGE
    if max_edge <= 0:
        max_edge = DEFAULT_MAX_EDGE

    try:
        image = Image.open(io.BytesIO(data_bytes))
        # Re-opening through load() applies the EXIF orientation, so the
        # pixels we send are the pixels the student sees.
        image.load()

        has_alpha = image.mode in ("RGBA", "LA", "PA")
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        needs_resize = max(image.size) > max_edge
        has_exif = bool(getattr(image, "info", {}).get("exif"))
        if not needs_resize and not has_exif:
            return data_bytes, mime_type

        if needs_resize:
            scale = max_edge / max(image.size)
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        if has_alpha:
            image.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue(), "image/png"
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        logger.warning("Could not prepare image for vision; sending the original", exc_info=True)
        return data_bytes, mime_type


def image_will_be_sent(attachment) -> bool:
    """
    Whether this attachment will actually reach a provider on this turn.

    An image that was already analysed is represented by its stored
    description, so it is never sent again -- and it must not be metered as
    an image, because no image was sent. The rules mirror
    ``PwanimateOrchestrator._process_attachments`` exactly:

    * a stored ``vision_summary`` means the image is context, not payload;
    * a failed analysis the student chose to drop is never sent;
    * an image still being analysed is never sent -- sending it would
      analyse it a second time;
    * anything else is sent.
    """
    if getattr(attachment, "attachment_type", "") != "image":
        return False

    if (getattr(attachment, "vision_summary", "") or "").strip():
        return False

    if (getattr(attachment, "processing_status", "") or "") in {"pending", "processing"}:
        return False

    stored_error = (getattr(attachment, "vision_error", "") or "").strip()
    if stored_error:
        policy = (getattr(attachment, "vision_context_policy", "") or "ask").strip().lower()
        if policy == "drop":
            return False

    return True
