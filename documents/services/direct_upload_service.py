"""Create documents from objects that were uploaded straight to object storage.

The browser PUTs the bytes to R2 (never through Django). When the transfer
completes, the upload engine hands us the key of the stored object; this
service writes the document rows and hands the heavy work (preview,
thumbnail, OCR, search indexing) to Celery so the user can carry on with
whatever they were doing.
"""

import logging
import os
import re
import uuid

from django.db import transaction
from django.utils import timezone

from ..documents.models import (
    Category,
    Document,
    DocumentFile,
    DocumentVersion,
    DocumentTag,
    Tag,
)

logger = logging.getLogger(__name__)

# Mirrors UploadService.allowed_extensions / max_file_size. The upload
# engine enforces the same list before a byte is transferred.
ALLOWED_EXTENSIONS = ("pdf", "docx", "doc", "pptx", "ppt", "zip", "rar")
MAX_BYTES = 50 * 1024 * 1024
# Conservative basename budget so full object keys stay well within S3/R2 (1024 bytes)
# and Django storage/db path fields used by this flow.
MAX_STORAGE_BASENAME_LENGTH = 180

MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "zip": "application/zip",
    "rar": "application/vnd.rar",
}


class DocumentUploadError(Exception):
    """Rejected document upload (bad metadata, unknown document, ...)."""


def safe_storage_basename(filename, *, max_length=MAX_STORAGE_BASENAME_LENGTH):
    """Return a storage-safe basename preserving extension where possible.

    Original filenames are kept separately (e.g. DocumentFile.original_filename);
    this value is only for object keys/storage paths and must respect provider and
    database limits without changing the file's identity.
    """
    # Treat the provided name as a filename (not a server path): preserve text
    # like "2025/2026" by replacing separators instead of taking os.path.basename.
    base = str(filename or "upload").replace("\x00", "")
    base = base.replace("/", "_").replace("\\", "_").strip()
    base = re.sub(r"[\r\n\t]", " ", base).strip(" .")
    if not base:
        base = "upload"

    root, ext = os.path.splitext(base)
    # Keep extension (including dot) intact when truncating.
    if len(ext) > max_length - 1:
        ext = ext[: max_length - 1]
    allowed_root = max(1, max_length - len(ext))
    if len(base) > max_length:
        if not root:
            root = base[:allowed_root]
        root = root[:allowed_root].rstrip(" .") or "upload"
        base = f"{root}{ext}"
    if not base:
        base = "upload"
    return base


def storage_path_for(filename):
    """Storage-relative path for a document file, matching the classic flow."""
    from django.utils import timezone as _tz

    safe_name = safe_storage_basename(filename)
    return f"documents/{_tz.now():%Y/%m/%d}/{uuid.uuid4().hex}/{safe_name}"


def _resolve_category(raw_category):
    """Resolve the category the same way the classic upload view does.

    A blank selection (or the literal 'other') falls back to the 'Other'
    category, which is auto-created on demand.
    """
    from django.db.models import Q

    other_cat = Category.objects.filter(
        Q(code__iexact="other") | Q(name__iexact="Other")
    ).first()
    if not other_cat:
        try:
            other_cat = Category.objects.create(
                name="Other",
                code="other",
                description="General announcements, memos, and non-unit documents",
                is_active=True,
            )
        except Exception:
            other_cat = Category.objects.filter(is_active=True).last()

    raw = (raw_category or "").strip()
    if not raw or raw.lower() == "other":
        if other_cat:
            return other_cat.id
        raise DocumentUploadError("Category is required")
    try:
        category_id = int(raw)
    except (TypeError, ValueError):
        category_id = raw
    if not Category.objects.filter(pk=category_id).exists() and not Category.objects.filter(code__iexact=raw).exists():
        raise DocumentUploadError("Category is required")
    return category_id


def _resolve_academic(metadata):
    """Resolve (unit, level, semester, academic_year) from the payload."""
    from ..academic.services import StudentAcademicEnrollmentService

    raw_unit = (metadata.get("academic_unit") or "").strip()
    unit_id = None
    if raw_unit and raw_unit.lower() != "other":
        unit_id = raw_unit

    raw_level = (metadata.get("academic_level") or "").strip()
    resolved_level = None
    if raw_level:
        try:
            resolved_level = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(
                int(raw_level)
            )
        except (TypeError, ValueError):
            resolved_level = None

    raw_sem = (metadata.get("semester") or "").strip()
    resolved_sem = None
    if raw_sem:
        try:
            resolved_sem = StudentAcademicEnrollmentService.resolve_or_ensure_semester(int(raw_sem))
        except (TypeError, ValueError):
            resolved_sem = None

    raw_year = (metadata.get("academic_year") or "").strip()
    year_id = None
    if raw_year:
        try:
            year_id = int(raw_year)
        except (TypeError, ValueError):
            year_id = None
    if not year_id and resolved_sem is not None:
        year_id = getattr(resolved_sem, "academic_year_id", None)

    return unit_id, resolved_level, resolved_sem, year_id


def _unique_title(user, title):
    """Mirror the classic flow: never collide with the user's own titles."""
    base = title
    counter = 1
    while Document.objects.filter(title=title, uploaded_by=user).exists():
        title = f"{base} ({counter})"
        counter += 1
    return title


def _apply_tags(document, tags):
    for tag_name in tags or []:
        tag_name = (tag_name or "").strip()
        if not tag_name:
            continue
        tag, _ = Tag.objects.get_or_create(
            name=tag_name,
            defaults={"slug": tag_name.lower().replace(" ", "-")},
        )
        DocumentTag.objects.get_or_create(document=document, tag=tag)


@transaction.atomic
def create_document_from_stored_object(
    *,
    user,
    storage_path,
    filename,
    mime_type="",
    size_bytes=0,
    checksum="",
    metadata=None,
    change_notes="",
):
    """Create a Document for an object that is already in object storage.

    Only rows are written here - the bytes are already at `storage_path`.
    Processing (preview, thumbnail, OCR, indexing) is queued for Celery.
    """
    from ..academic.services import DocumentAcademicLinkService

    metadata = metadata or {}

    extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentUploadError(
            f"File type '{extension or 'unknown'}' is not allowed. "
            f"Supported formats: {', '.join(e.upper() for e in ALLOWED_EXTENSIONS)}"
        )
    if size_bytes and size_bytes > MAX_BYTES:
        raise DocumentUploadError(
            f"File size exceeds maximum of {MAX_BYTES // (1024 * 1024)}MB"
        )

    category_id = _resolve_category(metadata.get("category"))
    unit_id, resolved_level, resolved_sem, year_id = _resolve_academic(metadata)

    title = (metadata.get("title") or "").strip()
    if not title:
        title = (filename or "Document").rsplit(".", 1)[0] if "." in (filename or "") else (filename or "Document")
    title = _unique_title(user, title)

    document = Document.objects.create(
        title=title,
        description=(metadata.get("description") or "").strip(),
        uploaded_by=user,
        status="processing",
        visibility="public",
        category_id=category_id,
    )

    version = DocumentVersion.objects.create(
        document=document,
        version_number=1,
        change_notes=change_notes or "",
        is_latest=True,
        created_by=user,
    )

    _create_file_row(
        version=version,
        user=user,
        storage_path=storage_path,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum=checksum,
    )

    try:
        DocumentAcademicLinkService.link_document_to_unit(
            document=document,
            academic_unit_id=unit_id,
            academic_level_id=getattr(resolved_level, "id", None),
            semester_id=getattr(resolved_sem, "id", None),
            academic_year_id=year_id,
            uploader=user,
            replace_existing=False,
        )
    except Exception:
        logger.warning(
            "Could not link document %s to academic unit %s", document.id, unit_id, exc_info=True
        )

    _apply_tags(document, metadata.get("tags") or [])

    logger.info("Created document %s from direct upload %s", document.id, storage_path)
    return document, version


@transaction.atomic
def create_version_from_stored_object(
    *,
    document,
    user,
    storage_path,
    filename,
    mime_type="",
    size_bytes=0,
    checksum="",
    change_notes="",
):
    """Add a new version to an existing document from an already-stored object."""
    extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentUploadError(
            f"File type '{extension or 'unknown'}' is not allowed. "
            f"Supported formats: {', '.join(e.upper() for e in ALLOWED_EXTENSIONS)}"
        )

    last_version = document.versions.order_by("-version_number").first()
    version_number = (last_version.version_number + 1) if last_version else 1

    document.versions.filter(is_latest=True).update(is_latest=False)
    version = DocumentVersion.objects.create(
        document=document,
        version_number=version_number,
        change_notes=change_notes or "",
        created_by=user,
        is_latest=True,
    )

    _create_file_row(
        version=version,
        user=user,
        storage_path=storage_path,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum=checksum,
    )

    document.status = "processing"
    document.save(update_fields=["status"])

    logger.info("Created version %s for document %s from direct upload", version_number, document.id)
    return version


def _create_file_row(*, version, user, storage_path, filename, mime_type, size_bytes, checksum):
    """Point a DocumentFile at an object that already lives in storage."""
    extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    resolved_mime = mime_type or MIME_BY_EXTENSION.get(extension, "application/octet-stream")
    # Preserve the user-provided filename as much as possible (including
    # characters like '/' in "2025/2026") while preventing null/control issues.
    original = str(filename or "upload").replace("\x00", "")
    original = re.sub(r"[\r\n\t]", " ", original).strip()
    original_max = DocumentFile._meta.get_field("original_filename").max_length or 500
    if len(original) > original_max:
        original = original[:original_max]
    original = original or "upload"

    document_file = DocumentFile(
        document_version=version,
        original_filename=original or "upload",
        storage_path=storage_path,
        mime_type=resolved_mime,
        extension=extension,
        size_bytes=size_bytes or 0,
        checksum=checksum or None,
        storage_provider="r2",
        processing_status="pending",
        uploaded_by=user,
    )
    # Name the field at the stored object instead of uploading it again.
    document_file.file.name = storage_path
    document_file.save()
    return document_file


def queue_processing(document):
    """Hand the document to the background pipeline."""
    from ..tasks.processing import process_document

    transaction.on_commit(lambda: process_document.delay(document.id))
