"""
Document Detail Tool for Pwanimate.

Allows deterministic, read-only retrieval of authorized academic document metadata
and engagement statistics by share_id or slug.
"""

from typing import Any, Dict, Optional
import uuid

from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import ToolValidationError, ToolPermissionError, ToolNotFoundError
from documents.models import Document


class DocumentDetailTool(BaseDomainTool):
    """
    Retrieve sanitized metadata and engagement metrics for a vasto document.
    """
    name = "document_detail"
    description = (
        "Retrieve authorized metadata and engagement counts for a specific academic "
        "document using its share_id (UUID) or slug."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "share_id": {
                "type": "string",
                "description": "Document unique UUID identifier (share_id)",
            },
            "slug": {
                "type": "string",
                "description": "Document URL-friendly slug",
            },
        },
        "required": [],
    }

    def execute(
        self,
        user: Any,
        share_id: Optional[str] = None,
        slug: Optional[str] = None,
        **kwargs,
    ) -> ToolResult:
        if not share_id and not slug:
            raise ToolValidationError(
                "Either 'share_id' or 'slug' must be provided.",
                tool_name=self.name,
            )

        # Base query with active/available status
        from documents.access_policy import DocumentAccessPolicy
        qs = DocumentAccessPolicy.authorized_documents(user).select_related("category", "uploaded_by")

        doc = None
        if share_id:
            try:
                parsed_uuid = uuid.UUID(str(share_id).strip())
                doc = qs.filter(share_id=parsed_uuid).first()
            except (ValueError, AttributeError):
                raise ToolValidationError(f"Invalid share_id UUID format: '{share_id}'", tool_name=self.name)
        elif slug:
            doc = qs.filter(slug=str(slug).strip()).first()

        if not doc:
            raise ToolNotFoundError(
                f"Document not found or is currently unavailable (share_id={share_id}, slug={slug}).",
                tool_name=self.name,
            )

        # Aggregate engagement counts
        view_count = doc.views.count()
        download_count = doc.downloads.count()

        # Resolve media and thumbnail
        thumbnail_url = getattr(doc, "thumbnail_url", "") or ""
        first_file = doc.latest_version.files.first() if getattr(doc, "latest_version", None) else None
        media_url = ""
        file_ext = ""
        if first_file:
            try:
                if getattr(first_file, "file", None):
                    media_url = first_file.file.url
            except Exception:
                media_url = ""
            file_ext = getattr(first_file, "extension", "") or ""

        author_name = ""
        uploader = getattr(doc, "uploaded_by", None)
        if uploader:
            full_name = f"{uploader.first_name or ''} {uploader.last_name or ''}".strip()
            author_name = full_name or getattr(uploader, "username", "") or ""

        data = {
            "share_id": str(doc.share_id),
            "slug": doc.slug,
            "title": doc.title,
            "description": doc.description or "",
            "category": doc.category.name if doc.category else None,
            "language": doc.language,
            "visibility": doc.visibility,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "published_at": doc.published_at.isoformat() if doc.published_at else None,
            "view_count": view_count,
            "download_count": download_count,
            "canonical_url": f"/documents/document/{doc.share_id}/",
            "thumbnail_url": thumbnail_url,
            "media_url": media_url,
            "file_type": file_ext,
            "resource_type": "document",
            "author": author_name,
        }

        return ToolResult.ok(data, share_id=str(doc.share_id))
