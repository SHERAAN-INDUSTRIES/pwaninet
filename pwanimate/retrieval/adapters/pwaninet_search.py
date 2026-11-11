"""
pwaninet Search Adapter for Pwanimate.

Thin adapter that consumes the existing pwaninet UnifiedSearchService
and translates domain search results into normalized RetrievalResult objects,
preserving all existing authorization, privacy, and ranking invariants.
"""

import logging
from typing import List, Optional
from search.services.unified_search_service import UnifiedSearchService
from pwanimate.retrieval.types import RetrievalRequest, RetrievalResult, SourceType

logger = logging.getLogger(__name__)


class pwaninetSearchAdapter:
    """Adapter wrapping pwaninet UnifiedSearchService for Pwanimate retrieval."""

    def __init__(self, search_service: Optional[UnifiedSearchService] = None):
        self.search_service = search_service or UnifiedSearchService()

    def search_posts(self, request: RetrievalRequest) -> List[RetrievalResult]:
        """Search posts via existing UnifiedSearchService."""
        query = (request.query or "").strip()
        if not query:
            return []

        limit = max(1, int(request.limit or 10))
        items, _ = self.search_service._search_posts(
            query=query,
            user=request.user,
            limit=limit,
            offset=0
        )

        results = []
        for item in items:
            raw_post = item.get("obj")
            author_info = item.get("author", {})

            # Resolve media, HLS stream, and poster thumbnail
            media_url = ""
            thumbnail_url = ""
            hls_url = ""
            resource_type = "post"

            if raw_post:
                # Video media & HLS
                if getattr(raw_post, "video", None):
                    try:
                        media_url = raw_post.video.url
                    except Exception:
                        media_url = ""
                    resource_type = "video"

                # Video poster
                poster = getattr(raw_post, "get_video_poster", None)
                if poster:
                    thumbnail_url = poster

                # HLS master playlist URL
                post_hls = getattr(raw_post, "get_hls_url", None)
                if post_hls:
                    hls_url = post_hls
                    resource_type = "video"

                # Image attachments fallback
                if not media_url and hasattr(raw_post, "images"):
                    first_img = raw_post.images.first()
                    if first_img and getattr(first_img, "image", None):
                        try:
                            img_url = first_img.image.url
                            media_url = img_url
                            if not thumbnail_url:
                                thumbnail_url = img_url
                            resource_type = "image"
                        except Exception:
                            pass

                # Post thumbnail fallback
                if not thumbnail_url and getattr(raw_post, "thumbnail", None):
                    try:
                        thumbnail_url = raw_post.thumbnail.url
                    except Exception:
                        pass

            # Also check item dict fallbacks
            if not hls_url and item.get("hls_url"):
                hls_url = item.get("hls_url")
                resource_type = "video"

            metadata = {
                "author_id": author_info.get("id"),
                "author_username": author_info.get("username"),
                "author_name": author_info.get("name"),
                "created_at": item.get("created_at"),
                "like_count": item.get("like_count", 0),
                "comment_count": item.get("comment_count", 0),
                "thumbnail_url": thumbnail_url,
                "media_url": media_url,
                "hls_url": hls_url,
                "resource_type": resource_type,
            }
            if raw_post and hasattr(raw_post, 'share_id'):
                canonical_url = f"/post/{raw_post.share_id}/"
            else:
                canonical_url = item.get("detail_url", "")

            post_content = (getattr(raw_post, 'content', None) or item.get("snippet", "")).strip()
            results.append(
                RetrievalResult(
                    source=SourceType.POST,
                    object_id=item.get("id"),
                    title=item.get("title", ""),
                    snippet=post_content,
                    score=float(item.get("relevance_rank", 0.0)),
                    url=canonical_url,
                    citation=f"Post by @{author_info.get('username', 'user')}",
                    metadata=metadata,
                    raw_object=raw_post,
                )
            )

        return results

    def search_people(self, request: RetrievalRequest) -> List[RetrievalResult]:
        """Search people/users via existing UnifiedSearchService."""
        query = (request.query or "").strip()
        limit = max(1, int(request.limit or 10))
        
        # Detect general exploration queries
        exploration_keywords = ['which', 'what', 'available', 'list', 'show', 'find', 'all', 'existing', 'students', 'people']
        is_exploration = any(kw in query.lower() for kw in exploration_keywords) if query else False
        
        # Extract actual search terms from exploration queries
        search_query = query
        if is_exploration:
            # Remove exploration keywords to get actual search terms
            for kw in exploration_keywords:
                search_query = search_query.lower().replace(kw, '').strip()
            if not search_query or search_query in ['students', 'people', 'available']:
                search_query = ""
        
        # For general queries or exploration without specific terms, return suggested students
        if not search_query:
            users_page, _, _, _, _, _ = self.search_service.search_people_models(
                query="",
                user=request.user,
                connection_type=None,
                profile_username=None,
                page=1,
                page_size=limit
            )
            # Convert models to dict format expected by adapter
            items = [
                {
                    "id": u.id,
                    "title": u.get_full_name() or u.username,
                    "subtitle": getattr(u, 'course', {}).name if hasattr(u, 'course') and u.course else "",
                    "username": u.username,
                    "detail_url": f"/users/user/{u.username}/",
                    "relevance_rank": 0.0,
                    "obj": u
                }
                for u in users_page
            ]
        else:
            items, _ = self.search_service._search_people(
                query=search_query,
                user=request.user,
                limit=limit,
                offset=0
            )

        results = []
        for item in items:
            raw_user = item.get("obj")
            username = item.get("username", "")
            results.append(
                RetrievalResult(
                    source=SourceType.USER,
                    object_id=item.get("id"),
                    title=item.get("title", username),
                    snippet=item.get("headline") or item.get("subtitle", f"@{username}"),
                    score=float(item.get("relevance_rank", 0.0)),
                    url=item.get("detail_url", f"/users/user/{username}/"),
                    citation=f"Profile @{username}",
                    metadata={
                        "username": username,
                        "global_role": item.get("global_role"),
                        "headline": item.get("headline"),
                    },
                    raw_object=raw_user,
                )
            )

        return results

    def search_groups(self, request: RetrievalRequest) -> List[RetrievalResult]:
        """Search groups via existing UnifiedSearchService."""
        query = (request.query or "").strip()
        limit = max(1, int(request.limit or 10))
        
        # Detect general exploration queries (keywords that indicate exploration rather than specific search)
        exploration_keywords = ['which', 'what', 'available', 'list', 'show', 'all', 'existing']
        is_exploration = any(kw in query.lower() for kw in exploration_keywords) if query else False
        
        # Extract actual search terms from exploration queries
        search_query = query
        if is_exploration:
            # Remove exploration keywords to get actual search terms
            for kw in exploration_keywords:
                search_query = search_query.lower().replace(kw, '').strip()
            if not search_query or search_query in ['groups', 'available']:
                search_query = ""
        
        # For general queries or exploration without specific terms, return top groups by member count
        if not search_query:
            items, _ = self.search_service._search_groups(
                query="",
                user=request.user,
                limit=limit,
                offset=0
            )
        else:
            items, _ = self.search_service._search_groups(
                query=search_query,
                user=request.user,
                limit=limit,
                offset=0
            )

        results = []
        for item in items:
            raw_group = item.get("obj")
            desc = raw_group.description if raw_group and hasattr(raw_group, 'description') else ""
            results.append(
                RetrievalResult(
                    source=SourceType.GROUP,
                    object_id=item.get("id"),
                    title=item.get("title", ""),
                    snippet=desc[:160] if desc else item.get("subtitle", ""),
                    score=float(item.get("relevance_rank", 0.0)),
                    url=item.get("detail_url", f"/groups/{item.get('id')}/"),
                    citation=f"Group: {item.get('title', '')}",
                    metadata={
                        "member_count": getattr(raw_group, 'member_count', 0) if raw_group else 0,
                        "is_official": item.get("is_official", False),
                    },
                    raw_object=raw_group,
                )
            )

        return results

    def search_documents_lexical(self, request: RetrievalRequest) -> List[RetrievalResult]:
        """Lexical full-text search for documents via existing UnifiedSearchService."""
        query = (request.query or "").strip()
        if not query:
            return []

        limit = max(1, int(request.limit or 10))
        items, _ = self.search_service._search_documents(
            query=query,
            user=request.user,
            limit=limit,
            offset=0
        )

        selected_document_ids = (request.filters or {}).get("document_ids")
        if selected_document_ids:
            selected_ids = {str(value) for value in selected_document_ids}
            items = [item for item in items if str(getattr(item.get("obj"), "id", "")) in selected_ids]

        results = []
        for item in items:
            raw_doc = item.get("obj")
            share_id = getattr(raw_doc, 'share_id', None)
            url = f"/documents/document/{share_id}/" if share_id else item.get("detail_url", "")

            doc_thumb = ""
            doc_media = ""
            doc_ext = item.get("file_type") or ""
            doc_author = item.get("author") or ""
            if raw_doc:
                doc_thumb = getattr(raw_doc, "thumbnail_url", "") or ""
                first_file = None
                if hasattr(raw_doc, "latest_version") and raw_doc.latest_version:
                    first_file = raw_doc.latest_version.files.first()
                if first_file:
                    try:
                        if getattr(first_file, "file", None):
                            doc_media = first_file.file.url
                    except Exception:
                        pass
                    if not doc_ext:
                        doc_ext = getattr(first_file, "extension", "") or ""
                if not doc_author and getattr(raw_doc, "uploaded_by", None):
                    uploader = raw_doc.uploaded_by
                    full_name = f"{uploader.first_name or ''} {uploader.last_name or ''}".strip()
                    doc_author = full_name or getattr(uploader, "username", "") or ""

            unit_codes = []
            unit_names = []
            if raw_doc and hasattr(raw_doc, "academic_units"):
                for du in raw_doc.academic_units.all():
                    if getattr(du, "academic_unit", None):
                        unit_codes.append(du.academic_unit.code)
                        unit_names.append(du.academic_unit.name)

            acad_rel = float(item.get("academic_relevance", 0.0) or 0.0)
            if acad_rel >= 0.99:
                academic_match_tier = "enrolled_unit"
            elif acad_rel >= 0.7:
                academic_match_tier = "programme_level"
            elif acad_rel >= 0.4:
                academic_match_tier = "programme"
            elif unit_codes:
                academic_match_tier = "other_programme"
            else:
                academic_match_tier = "general"

            results.append(
                RetrievalResult(
                    source=SourceType.DOCUMENT,
                    object_id=item.get("id"),
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    score=float(item.get("relevance_rank", 0.0)),
                    url=url,
                    citation=f"Document: {item.get('title', '')}",
                    metadata={
                        "document_id": getattr(raw_doc, 'id', item.get('id')),
                        "category_display": item.get("category_display"),
                        "file_type": doc_ext,
                        "download_count": item.get("download_count"),
                        "rating_average": item.get("rating_average"),
                        "thumbnail_url": doc_thumb,
                        "media_url": doc_media,
                        "resource_type": "document",
                        "author": doc_author,
                        "unit_codes": unit_codes,
                        "unit_names": unit_names,
                        "academic_match_tier": academic_match_tier,
                    },
                    raw_object=raw_doc,
                )
            )

        return results
