"""
Shared document-context resolution for Spark (Pwanimate) turns.

Resolves Context Rail / Preview / Sheet `context_resources` document
descriptors into authorized, page-aware retrieval evidence BEFORE intent
routing, so every execution path (RAG, deterministic tools, conversational)
sees the same trusted document context.

Design rules:
- Identity: `document_id` (integer PK) and `document_share_id` (UUID) are
  resolved independently; conflicting references fail closed.
- Authorization: every resolved document must pass the same visibility
  rules used by semantic retrieval before any content is fetched.
- Diagnostics: every resource produces a structured status. User-facing
  messages never reveal whether an inaccessible document exists.
- Laziness: identity + authorization are resolved eagerly (cheap); chunk
  text is fetched only when a route actually consumes it.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page-intent parsing (Phase E)
# ---------------------------------------------------------------------------

PAGE_MENTION_RE = re.compile(r"\b(?:page|p\.)\s*(\d{1,4})\b", re.IGNORECASE)
COMPARISON_RE = re.compile(
    r"\b(?:compare|comparison|versus|vs\.?|alongside|in addition to|both)\b",
    re.IGNORECASE,
)
DOCUMENT_WIDE_RE = re.compile(
    r"\b(?:summar(?:ize|y|ise)|overview|table of contents|chapters?|whole\s+"
    r"(?:book|document|pdf)|entire\s+(?:book|document|pdf)|about\s+this\s+"
    r"(?:book|document|pdf)|structure|all\s+pages|every\s+page)\b",
    re.IGNORECASE,
)
PAGE_SCOPE_RE = re.compile(
    r"\b(?:this\s+page|current\s+page|the\s+page|page\s+\d+|equation|formula|"
    r"figure|diagram|table|graph|chart|step\s+\d+)\b",
    re.IGNORECASE,
)
# Intent verbs that make a "page N" mention a retrieval target. Used only to
# decide whether a mention is a target; absence never removes the viewer page.
_TARGET_VERB_RE = re.compile(
    r"\b(?:explain|describe|summari[sz]e|quiz|discuss|tell|walk|outline|"
    r"detail|elaborate|define|list|give|what(?:'s|\s+is|\s+does|\s+are))\b",
    re.IGNORECASE,
)

MAX_TARGET_PAGES_PER_DOC = 4  # active viewer page + up to 3 explicit targets
MAX_NEIGHBOR_CHUNKS_PER_SIDE = 2


_DOC_LEVEL_RE = re.compile(
    r"\b(?:document|book|pdf|entire|whole|all\s+pages|every\s+page|chapters?|"
    r"table\s+of\s+contents|structure|syllabus)\b",
    re.IGNORECASE,
)
_PAGE_SPECIFIC_RE = re.compile(
    r"\b(?:this|current|that|the)\s+page\b|\bpage\s+\d+\b|\bp\.\s*\d+",
    re.IGNORECASE,
)


def extract_page_intent(query: str) -> Dict[str, Any]:
    """
    Deterministically classify page references in a user query.

    Returns:
        document_wide: whole-document question (overview retrieval). Requires
            a document-level object — "summarize this page" stays page-scoped.
        page_scoped:   question plausibly about page-level content.
        comparison:    explicit comparison construction detected.
        explicit_pages: page numbers the student explicitly targeted
            (excluding the active viewer page, which the resolver adds).
        all_mentions:  every "page N" number found, for diagnostics.
    """
    q = query or ""
    mentions = [(m.start(), int(m.group(1))) for m in PAGE_MENTION_RE.finditer(q)]
    doc_level = bool(_DOC_LEVEL_RE.search(q))
    page_specific = bool(_PAGE_SPECIFIC_RE.search(q))
    # Document-wide only when the question is about the document as a whole.
    # A page-specific phrase ("this page", "page 12") overrides summary verbs
    # unless a document-level noun is also present ("summarize the document").
    document_wide = bool(DOCUMENT_WIDE_RE.search(q)) and (doc_level or not page_specific)
    comparison = bool(COMPARISON_RE.search(q))
    page_scoped = bool(PAGE_SCOPE_RE.search(q)) or bool(mentions)

    explicit_pages: List[int] = []
    if mentions and not document_wide:
        if comparison:
            # "Compare this page with page 20" — every mentioned page is a
            # target; the active page is added separately by the resolver.
            explicit_pages = [p for _, p in mentions]
        else:
            # Conservative single-target rule: only the FIRST page mention
            # can be an explicit target, and only when it is plausibly the
            # subject of the question (an intent verb precedes it, or it
            # leads the query). Later mentions ("On page 12 it mentions
            # page 4") are left to semantic retrieval.
            pos, first_page = mentions[0]
            prefix = q[:pos]
            if _TARGET_VERB_RE.search(prefix) or pos <= 40:
                explicit_pages = [first_page]

    return {
        "document_wide": document_wide,
        "page_scoped": page_scoped,
        "comparison": comparison,
        "explicit_pages": explicit_pages,
        "all_mentions": [p for _, p in mentions],
    }


# ---------------------------------------------------------------------------
# Resource identity resolution (Phase A)
# ---------------------------------------------------------------------------

_DOC_RESOURCE_TYPES = {"document", "doc", ""}
_SKIP_SOURCE_TYPES = {"web", "web_search", "attachment"}
_SKIP_RESOURCE_TYPES = {"web", "web_search", "attachment", "post", "profile", "user", "image", "video"}


def _parse_positive_int(raw: Any) -> Optional[int]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(str(raw).strip())
    except (ValueError, TypeError):
        return None
    return value if value > 0 else None


def _parse_uuid(raw: Any) -> Optional[uuid.UUID]:
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _resource_kind(res: Dict[str, Any]) -> Tuple[str, str]:
    """Return (res_type, source_type) lowercased for a raw resource dict."""
    res_type = str(
        res.get("type")
        or res.get("previewType")
        or res.get("resourceType")
        or res.get("category")
        or ""
    ).lower()
    source_type = str(res.get("source_type") or res.get("sourceType") or "").lower()
    return res_type, source_type


def is_document_resource(res: Dict[str, Any]) -> bool:
    """Whether a context_resources entry describes a repository document."""
    if not isinstance(res, dict):
        return False
    res_type, source_type = _resource_kind(res)
    if source_type in _SKIP_SOURCE_TYPES or res_type in _SKIP_RESOURCE_TYPES:
        return False
    if res_type in {"post", "profile", "user"} or source_type in {"post", "profile", "user"}:
        return False
    # Document-ish if typed as document, carries document identifiers, or
    # has a repository document URL.
    if res_type in _DOC_RESOURCE_TYPES or source_type in {"document", "doc"}:
        return True
    if res.get("documentId") or res.get("document_id") or res.get("documentShareId") or res.get("document_share_id"):
        return True
    url = str(res.get("url") or "")
    return "/documents/" in url


def collect_document_identifiers(res: Dict[str, Any]) -> Tuple[List[int], List[uuid.UUID]]:
    """
    Collect integer PK candidates and UUID candidates from ALL identifier
    fields a client may populate, including legacy payloads that place a
    UUID inside `documentId`/`id`.
    """
    pk_candidates: List[int] = []
    uuid_candidates: List[uuid.UUID] = []

    def _add(raw: Any):
        if raw is None:
            return
        pk = _parse_positive_int(raw)
        if pk is not None:
            pk_candidates.append(pk)
            return
        parsed = _parse_uuid(raw)
        if parsed is not None:
            uuid_candidates.append(parsed)

    # Canonical + legacy fields. `id` historically carried the share UUID.
    for key in (
        "document_id", "documentId", "id",
        "share_id", "document_share_id", "documentShareId",
    ):
        _add(res.get(key))

    # De-duplicate while preserving order.
    pks = list(dict.fromkeys(pk_candidates))
    uuids = list(dict.fromkeys(uuid_candidates))
    return pks, uuids


@dataclass
class DocumentResourceResolution:
    """Resolution result for a single document-type context resource."""

    index: int
    resource: Dict[str, Any]
    status: str = "pending"
    detail: str = ""
    document: Any = None
    document_id: Optional[int] = None
    share_id: Optional[str] = None
    document_version_id: Optional[int] = None
    viewer_page: Optional[int] = None
    target_pages: List[int] = field(default_factory=list)
    document_wide: bool = False
    embeddings_pending: bool = False
    file_type: str = ""
    title: str = ""

    @property
    def resolved(self) -> bool:
        return self.status == "resolved" and self.document is not None

    def to_status_dict(self) -> Dict[str, Any]:
        """Privacy-safe structured status for diagnostics/metadata."""
        return {
            "index": self.index,
            "status": self.status,
            "document_id": self.document_id,
            "share_id": self.share_id,
            "viewer_page": self.viewer_page,
            "target_pages": list(self.target_pages),
            "document_wide": self.document_wide,
            "embeddings_pending": self.embeddings_pending,
            "title": self.title or None,
        }


@dataclass
class DocumentTurnContext:
    """
    Shared, route-independent document context for one orchestration turn.

    Produced once in `run()` before intent routing; consumed by `_run_rag`,
    `_run_tool`, and diagnostics. Chunk text is fetched lazily via
    `collect_page_evidence` / `collect_overview_evidence`.
    """

    page_intent: Dict[str, Any] = field(default_factory=dict)
    resolutions: List[DocumentResourceResolution] = field(default_factory=list)
    document_ids: List[int] = field(default_factory=list)
    _page_chunks: List[Any] = field(default_factory=list)
    _page_chunks_fetched: bool = False
    _page_chunks_partial: bool = False

    # -- construction ------------------------------------------------------

    @classmethod
    def resolve(
        cls,
        user: Any,
        context_resources: Optional[List[Dict[str, Any]]],
        query: str = "",
    ) -> "DocumentTurnContext":
        """Resolve all document-type resources; non-document resources are ignored."""
        ctx = cls(page_intent=extract_page_intent(query))
        if not context_resources or not isinstance(context_resources, list):
            return ctx

        from documents.models import Document
        from pwanimate.services.study_session import is_user_authorized_for_document

        for index, res in enumerate(context_resources):
            if not isinstance(res, dict) or not is_document_resource(res):
                continue
            resolution = cls._resolve_one(
                index, res, user, Document, is_user_authorized_for_document, ctx.page_intent
            )
            ctx.resolutions.append(resolution)
            if resolution.resolved and resolution.document_id not in ctx.document_ids:
                ctx.document_ids.append(resolution.document_id)
        return ctx

    @staticmethod
    def _resolve_one(
        index: int,
        res: Dict[str, Any],
        user: Any,
        Document: Any,
        is_authorized,
        page_intent: Dict[str, Any],
    ) -> DocumentResourceResolution:
        from pwanimate.models import DocumentChunk

        out = DocumentResourceResolution(index=index, resource=res)
        out.title = str(res.get("title") or "")[:300]
        out.file_type = str(
            res.get("fileType") or res.get("file_type") or res.get("extension") or ""
        ).lower().lstrip(".")

        pks, uuids = collect_document_identifiers(res)
        if not pks and not uuids:
            out.status = "invalid_resource"
            out.detail = "no_document_identifier"
            return out

        # Resolve each identifier family independently so a malformed legacy
        # field can never suppress a valid compatible identifier.
        by_pk: Dict[int, Any] = {}
        for pk in pks:
            doc = Document.objects.filter(id=pk).first()
            if doc is not None:
                by_pk[doc.id] = doc
        by_uuid: Dict[Any, Any] = {}
        for uid in uuids:
            doc = Document.objects.filter(share_id=uid).first()
            if doc is not None:
                by_uuid[doc.id] = doc

        distinct = {doc.id: doc for doc in list(by_pk.values()) + list(by_uuid.values())}
        if not distinct:
            # Missing and unauthorized are intentionally indistinguishable
            # to the client (no existence oracle).
            out.status = "inaccessible"
            out.detail = "not_found_or_unauthorized"
            return out
        if len(distinct) > 1:
            out.status = "identifier_conflict"
            out.detail = "conflicting_document_identifiers"
            logger.warning(
                "Spark context resource %s has conflicting document identifiers: %s",
                index, sorted(distinct.keys()),
            )
            return out

        doc = next(iter(distinct.values()))
        out.document = doc
        out.document_id = doc.id
        out.share_id = str(doc.share_id) if doc.share_id else None

        # Authorization gate: same visibility rules as document viewing.
        # require_ready=False lets us distinguish lifecycle states
        # (processing/unavailable) from true inaccessibility; content is only
        # fetched for documents that are fully ready + available below.
        try:
            authorized = bool(is_authorized(user, doc, require_ready=False))
        except TypeError:
            authorized = bool(is_authorized(user, doc))
        except Exception:
            authorized = False
        if not authorized:
            out.status = "inaccessible"
            out.detail = "not_found_or_unauthorized"
            return out

        # Lifecycle / availability states (authorization already established).
        doc_status = getattr(doc, "status", "") or ""
        if doc_status == "archived" or not getattr(doc, "is_available", True):
            out.status = "unavailable"
            out.detail = "document_unavailable"
            return out
        if doc_status != "ready":
            out.status = "awaiting_processing"
            out.detail = f"document_status_{doc_status or 'unknown'}"
            return out

        # Indexing state (distinct from authorization and availability).
        total_chunks = DocumentChunk.objects.filter(document=doc).count()
        active_chunks = DocumentChunk.objects.filter(document=doc, is_active=True).count()
        if total_chunks == 0:
            out.status = "unindexed"
            out.detail = "no_chunks"
            return out
        out.embeddings_pending = active_chunks == 0

        # Version pinning: honor documentVersionId ONLY when it is the
        # document's latest version — the viewer always renders the latest
        # file, so retrieving a superseded version would mismatch evidence.
        version_raw = (
            res.get("document_version_id") or res.get("documentVersionId")
            or res.get("versionId")
        )
        latest_version = getattr(doc, "latest_version", None)
        if version_raw is not None and latest_version is not None:
            try:
                if int(version_raw) == latest_version.id:
                    out.document_version_id = latest_version.id
            except (ValueError, TypeError):
                pass
        if out.document_version_id is None and latest_version is not None:
            out.document_version_id = latest_version.id

        # Viewer page + explicit targets.
        page_raw = (
            res.get("pageNumber") or res.get("page_number") or res.get("page")
        )
        viewer_page = _parse_positive_int(page_raw)
        out.viewer_page = viewer_page

        explicit = list(page_intent.get("explicit_pages") or [])
        targets: List[int] = []
        if viewer_page is not None:
            targets.append(viewer_page)
        for page in explicit:
            if page not in targets:
                targets.append(page)
        out.target_pages = targets[:MAX_TARGET_PAGES_PER_DOC]
        out.document_wide = bool(page_intent.get("document_wide"))

        # Page bounds check against the best available page-count signal.
        # Out-of-bounds does NOT drop the document: the student may still ask
        # document-wide questions; the bad page simply yields no evidence.
        if viewer_page is not None and latest_version is not None:
            try:
                first_file = latest_version.files.first()
                page_count = getattr(first_file, "page_count", None) if first_file else None
                if page_count and viewer_page > int(page_count):
                    out.detail = "page_out_of_bounds"
            except Exception:
                pass

        out.status = "resolved"
        if out.embeddings_pending and not out.detail:
            out.detail = "embeddings_pending"
        return out

    # -- evidence retrieval (lazy) -----------------------------------------

    def collect_page_evidence(self, user: Any, query: str = "") -> List[Any]:
        """
        Fetch bounded page-aware chunks for all resolved documents.

        Priority order: active viewer page → explicit target pages →
        neighboring pages (only for page-scoped questions) → overview chunks
        (document-wide questions). Result is cached per turn.
        """
        if self._page_chunks_fetched:
            return list(self._page_chunks)
        self._page_chunks_fetched = True

        from pwanimate.retrieval.services.document_retrieval import (
            DocumentSemanticRetrievalService,
        )

        service = DocumentSemanticRetrievalService()
        intent = self.page_intent or {}
        document_wide = bool(intent.get("document_wide"))
        page_scoped = bool(intent.get("page_scoped")) and not document_wide
        query_terms = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))

        per_page_cap = int(getattr(settings, "PWANIMATE_PAGE_MAX_CHUNKS", 8) or 8)
        neighbor_radius = int(getattr(settings, "PWANIMATE_NEIGHBOR_PAGE_RADIUS", 1) or 1)
        total_budget = int(getattr(settings, "PWANIMATE_PAGE_EVIDENCE_CHAR_BUDGET", 16000) or 16000)

        ordered: List[Any] = []
        seen_chunk_ids = set()
        chars_used = 0

        def _add_results(results: List[Any], origin: str, active_page: Optional[int], cap: Optional[int] = None):
            nonlocal chars_used
            kept = 0
            for res in results or []:
                if cap is not None and kept >= cap:
                    self._page_chunks_partial = True
                    break
                meta = res.metadata if isinstance(res.metadata, dict) else {}
                chunk_id = meta.get("chunk_id") or res.object_id
                if chunk_id in seen_chunk_ids:
                    continue
                content = res.snippet or ""
                if not content.strip():
                    continue
                if chars_used + len(content) > total_budget:
                    self._page_chunks_partial = True
                    break
                seen_chunk_ids.add(chunk_id)
                meta = dict(meta)
                meta["retrieval_origin"] = origin
                if active_page is not None:
                    meta["active_viewer_page"] = active_page
                res.metadata = meta
                ordered.append(res)
                chars_used += len(content)
                kept += 1

        def _rank_and_cap(chunks: List[Any]) -> List[Any]:
            """Bound oversized pages: keep query-relevant chunks, then order by chunk_index."""
            if len(chunks) <= per_page_cap:
                return chunks
            self._page_chunks_partial = True
            scored = []
            for c in chunks:
                content = (c.snippet or "").lower()
                score = sum(1 for t in query_terms if t in content)
                idx = (c.metadata or {}).get("chunk_index", 0)
                scored.append((-score, idx, c))
            scored.sort(key=lambda t: (t[0], t[1]))
            chosen = sorted((t[2] for t in scored[:per_page_cap]),
                            key=lambda c: (c.metadata or {}).get("chunk_index", 0))
            return chosen

        for resolution in self.resolutions:
            if not resolution.resolved or not resolution.share_id:
                continue
            doc = resolution.document
            share_id = resolution.share_id
            version_id = resolution.document_version_id
            viewer_page = resolution.viewer_page
            targets = [p for p in resolution.target_pages if p]

            if document_wide:
                try:
                    overview = service.get_document_overview_chunks(
                        user=user,
                        document_share_id=share_id,
                        max_chunks=int(getattr(settings, "PWANIMATE_OVERVIEW_MAX_CHUNKS", 6) or 6),
                    )
                    _add_results(overview, "document_overview", viewer_page)
                except Exception as exc:
                    logger.debug("Overview retrieval failed for doc %s: %s", resolution.document_id, exc)
                # Still include the visible page and any explicit targets:
                # the student is looking at them even during overview questions.
                extra_pages = []
                if viewer_page is not None:
                    extra_pages.append(viewer_page)
                extra_pages.extend(p for p in targets if p not in extra_pages)
                for page in extra_pages:
                    try:
                        chunks = service.get_page_chunks(
                            user=user, document_share_id=share_id,
                            page_number=page, document_version_id=version_id,
                            include_unembedded=True,
                        )
                        origin = "active_viewer_page" if page == viewer_page else "explicit_page"
                        _add_results(_rank_and_cap(chunks), origin, viewer_page or page)
                    except Exception as exc:
                        logger.debug("Page retrieval failed for doc %s p%s: %s", resolution.document_id, page, exc)
                continue

            # Page-scoped evidence: viewer page first, then explicit targets.
            primary_page = viewer_page if viewer_page is not None else (targets[0] if targets else None)
            if primary_page is not None:
                try:
                    chunks = service.get_page_chunks(
                        user=user, document_share_id=share_id,
                        page_number=primary_page, document_version_id=version_id,
                        include_unembedded=True,
                    )
                    origin = "active_viewer_page" if primary_page == viewer_page else "explicit_page"
                    _add_results(_rank_and_cap(chunks), origin, viewer_page or primary_page)
                except Exception as exc:
                    logger.debug("Page retrieval failed for doc %s p%s: %s", resolution.document_id, primary_page, exc)

            for page in targets:
                if page == primary_page:
                    continue
                try:
                    chunks = service.get_page_chunks(
                        user=user, document_share_id=share_id,
                        page_number=page, document_version_id=version_id,
                        include_unembedded=True,
                    )
                    _add_results(_rank_and_cap(chunks), "explicit_page", viewer_page)
                except Exception as exc:
                    logger.debug("Explicit page retrieval failed for doc %s p%s: %s", resolution.document_id, page, exc)

            # Neighboring pages only for page-scoped questions, tightly bounded.
            if page_scoped and primary_page is not None and neighbor_radius > 0:
                for delta in range(1, neighbor_radius + 1):
                    for neighbor in (primary_page - delta, primary_page + delta):
                        if neighbor < 1 or neighbor in targets or neighbor == primary_page:
                            continue
                        try:
                            chunks = service.get_page_chunks(
                                user=user, document_share_id=share_id,
                                page_number=neighbor, document_version_id=version_id,
                                include_unembedded=True,
                            )[:MAX_NEIGHBOR_CHUNKS_PER_SIDE]
                            _add_results(chunks, "neighbor_page", viewer_page)
                        except Exception as exc:
                            logger.debug("Neighbor page retrieval failed for doc %s p%s: %s", resolution.document_id, neighbor, exc)

        self._page_chunks = ordered
        return list(ordered)

    @property
    def page_chunks_partial(self) -> bool:
        return self._page_chunks_partial

    @property
    def has_documents(self) -> bool:
        return any(r.resolved for r in self.resolutions)

    @property
    def filter_document_ids(self) -> List[int]:
        """Backward-compatible alias for the resolved document PK list."""
        return list(self.document_ids)

    # -- diagnostics ---------------------------------------------------------

    def to_warnings(self) -> List[Dict[str, Any]]:
        """
        User-facing, privacy-preserving warnings. 'inaccessible' resources
        produce a generic message that does not confirm existence.
        """
        warnings: List[Dict[str, Any]] = []
        for r in self.resolutions:
            title = r.title or "The selected document"
            base = {"type": "document", "document_id": r.document_id, "share_id": r.share_id}
            if r.status == "inaccessible":
                warnings.append({**base, "code": "document_inaccessible",
                                 "message": "I couldn't access one of the selected documents, so I answered without it."})
            elif r.status == "identifier_conflict":
                warnings.append({**base, "code": "identifier_conflict",
                                 "message": "A selected document reference was inconsistent, so I ignored it."})
            elif r.status == "invalid_resource":
                warnings.append({**base, "code": "invalid_resource",
                                 "message": "A selected document reference was incomplete, so I ignored it."})
            elif r.status == "awaiting_processing":
                warnings.append({**base, "code": "document_processing",
                                 "message": f'"{title}" is still being processed, so its contents are not available yet.'})
            elif r.status == "unavailable":
                warnings.append({**base, "code": "document_unavailable",
                                 "message": f'"{title}" is currently unavailable.'})
            elif r.status == "unindexed":
                warnings.append({**base, "code": "document_unindexed",
                                 "message": f'"{title}" has no indexed text yet — if it is a scanned document, its pages cannot be read directly.'})
            elif r.resolved and r.detail == "page_out_of_bounds":
                warnings.append({**base, "code": "page_out_of_bounds", "page_number": r.viewer_page,
                                 "message": f'Page {r.viewer_page} is outside the page range of "{title}".'})
            elif r.resolved and r.embeddings_pending:
                warnings.append({**base, "code": "embeddings_pending",
                                 "message": f'The text of "{title}" is available, but semantic indexing is still processing.'})
            elif r.status == "resolved":
                # Page-level emptiness is detected during evidence fetch; the
                # caller adds 'page_text_unavailable' warnings from fetch results.
                pass
        return warnings

    def page_level_warnings(self, fetched_chunks: List[Any]) -> List[Dict[str, Any]]:
        """Warnings derived from actually-fetched page evidence."""
        warnings: List[Dict[str, Any]] = []
        covered = set()
        for res in fetched_chunks:
            meta = res.metadata if isinstance(res.metadata, dict) else {}
            doc_id = meta.get("document_id")
            page = meta.get("page_number")
            if doc_id is not None and page is not None:
                covered.add((str(doc_id), int(page)))
        for r in self.resolutions:
            if not r.resolved:
                continue
            title = r.title or "The document"
            base = {"type": "document", "document_id": r.document_id, "share_id": r.share_id}
            pages_to_check = [p for p in r.target_pages if p]
            if r.viewer_page is not None and r.viewer_page not in pages_to_check:
                pages_to_check.insert(0, r.viewer_page)
            for page in pages_to_check:
                if (str(r.document_id), int(page)) not in covered:
                    warnings.append({**base, "code": "page_text_unavailable",
                                     "page_number": page,
                                     "message": f'Page {page} of "{title}" has no readable extracted text.'})
        if self.page_chunks_partial:
            warnings.append({"type": "document", "code": "page_context_partial",
                             "message": "Only part of the selected page(s) fit in the context budget; Spark may not see everything on the page."})
        return warnings

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "document_context": {
                "resolved_count": sum(1 for r in self.resolutions if r.resolved),
                "filter_document_ids": self.filter_document_ids,
                "page_intent": dict(self.page_intent),
                "resources": [r.to_status_dict() for r in self.resolutions],
                "page_chunks_partial": self.page_chunks_partial,
            }
        }
