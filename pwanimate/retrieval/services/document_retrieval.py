"""
Document Semantic Retrieval Service for Pwanimate.

Performs vector similarity search against DocumentChunk embeddings using pgvector,
with strict pre-retrieval authorization candidate filtering, version integrity,
and citation generation.
"""

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from django.db.models import Q
from pgvector.django import CosineDistance

from pwanimate.models import DocumentChunk
from pwanimate.retrieval.types import RetrievalRequest, RetrievalResult, SourceType
from pwanimate.ai.embeddings.base import BaseEmbeddingProvider, EmbeddingProviderError
from pwanimate.ai.embeddings.factory import get_embedding_provider

# Title words too generic to count as "the student named this document".
_GENERIC_TITLE_TOKENS = {
    'document', 'documents', 'notes', 'note', 'summary', 'assignment', 'homework',
    'uploaded', 'upload', 'final', 'copy', 'file', 'files', 'page', 'pages',
    'part', 'chapter', 'untitled', 'document1', 'document2', 'scan', 'scan1',
    'image', 'images', 'photo', 'photos', 'book', 'books', 'pdf', 'docx', 'text',
    'question', 'questions', 'answers', 'answer', 'lecture', 'slides', 'paper',
}

logger = logging.getLogger(__name__)


class DocumentSemanticRetrievalService:
    """Service handling semantic retrieval over authorized DocumentChunks."""

    def __init__(self, embedding_provider: Optional[BaseEmbeddingProvider] = None):
        self._provider = embedding_provider

    @property
    def provider(self) -> BaseEmbeddingProvider:
        """Lazily resolve active embedding provider."""
        if self._provider is None:
            self._provider = get_embedding_provider()
        return self._provider

    def build_authorized_chunk_queryset(self, user: Optional[Any] = None, filters: Optional[dict] = None):
        """
        Build pre-retrieval chunk queryset enforcing strict authorization and availability
        invariants WITHOUT requiring vector embeddings to be completed.

        Used directly by deterministic SQL lookups (e.g., `get_page_chunks` and
        `get_document_overview_chunks`) and extended by `build_candidate_queryset`
        for vector similarity search.
        """
        qs = DocumentChunk.objects.filter(
            is_active=True,
            document__status='ready',
            document__is_available=True,
        )

        # Authorization filter
        if user and getattr(user, 'is_authenticated', False):
            # Staff, superuser, and executive student leaders have administrative read access
            global_role = getattr(user, 'global_role', None)
            is_admin_or_leader = (
                user.is_staff or
                user.is_superuser or
                global_role in ['PRESIDENT', 'DELEGATE']
            )

            if is_admin_or_leader:
                qs = qs.filter(Q(document__is_ai_generated=False) | Q(document__uploaded_by=user))
            else:
                # Build student visibility query
                visibility_q = Q(document__visibility='public', document__is_ai_generated=False) | Q(document__uploaded_by=user)

                # Restricted visibility check
                restricted_q = Q(document__visibility='restricted', document__is_ai_generated=False)
                has_restricted_match = False

                # 1. Match student's enrolled programme units or direct unit enrollments
                programme = getattr(user, 'programme', None)
                if programme:
                    visibility_q |= (
                        restricted_q &
                        Q(document__academic_units__academic_unit__programme_units__programme=programme)
                    )
                    has_restricted_match = True

                # 2. Match direct StudentUnitEnrollment
                visibility_q |= (
                    restricted_q &
                    Q(
                        document__academic_units__academic_unit__student_enrollments__user=user,
                        document__academic_units__academic_unit__student_enrollments__is_active=True,
                    )
                )

                # 3. Legacy fallback to course
                course = getattr(user, 'course', None)
                if course and not has_restricted_match:
                    visibility_q |= (
                        restricted_q &
                        Q(document__academic_units__academic_unit__code__icontains=course.name)
                    )

                qs = qs.filter(visibility_q).distinct()
        else:
            # Anonymous users can only view public documents
            qs = qs.filter(document__visibility='public', document__is_ai_generated=False)

        # Domain metadata filters
        if filters:
            if filters.get('category'):
                cat = filters['category']
                qs = qs.filter(
                    Q(document__category__code=cat) |
                    Q(document__category__id=cat)
                )

            if filters.get('academic_unit'):
                unit = filters['academic_unit']
                qs = qs.filter(
                    Q(document__academic_units__academic_unit__code=unit) |
                    Q(document__academic_units__academic_unit__id=unit)
                )

            if filters.get('academic_units'):
                units = filters['academic_units']
                qs = qs.filter(
                    document__academic_units__academic_unit_id__in=units
                )

            if filters.get('document_id'):
                qs = qs.filter(document_id=filters['document_id'])

            if filters.get('document_ids'):
                qs = qs.filter(document_id__in=filters['document_ids'])

            if filters.get('file_type'):
                ft = str(filters['file_type']).lower().strip()
                qs = qs.filter(document__versions__files__extension=ft)

        return qs

    def build_candidate_queryset(self, user: Optional[Any] = None, filters: Optional[dict] = None):
        """
        Build pre-retrieval candidate queryset enforcing strict authorization invariants
        plus completed vector embeddings for active embedding model.
        """
        model_name = self.provider.get_model_name()
        return self.build_authorized_chunk_queryset(user=user, filters=filters).filter(
            embedding_status='completed',
            embedding_model=model_name,
            embedding__isnull=False,
        )

    def _resolve_student_academic_sets(self, user: Optional[Any]):
        """Resolve sets of unit IDs for tiered student-native academic boosting."""
        enrolled_unit_ids = set()
        programme_level_unit_ids = set()
        programme_unit_ids = set()

        if not user or not getattr(user, 'is_authenticated', False):
            return enrolled_unit_ids, programme_level_unit_ids, programme_unit_ids

        try:
            from documents.academic.models import ProgrammeUnit
            from documents.academic.services import StudentAcademicEnrollmentService

            programme, academic_level, _, _ = StudentAcademicEnrollmentService.resolve_student_programme_and_level(user)
            active_enrollments = StudentAcademicEnrollmentService.get_active_enrollments(user)
            enrolled_unit_ids = {enr.academic_unit_id for enr in active_enrollments}

            if programme:
                programme_unit_ids = set(
                    ProgrammeUnit.objects.filter(programme=programme)
                    .values_list('academic_unit_id', flat=True)
                )
                if academic_level:
                    programme_level_unit_ids = set(
                        ProgrammeUnit.objects.filter(programme=programme, academic_level=academic_level)
                        .values_list('academic_unit_id', flat=True)
                    )
        except Exception as exc:
            logger.debug("Could not resolve student academic sets for semantic boost: %s", exc)

        return enrolled_unit_ids, programme_level_unit_ids, programme_unit_ids

    def retrieve(self, request: RetrievalRequest) -> List[RetrievalResult]:
        """
        Execute semantic retrieval for the given request with soft student-native academic boosting.

        Args:
            request: RetrievalRequest containing query, user, limit, and filters.

        Returns:
            List of normalized RetrievalResult objects ranked by composite similarity + academic alignment.
        """
        query = (request.query or "").strip()
        if not query:
            return []

        # 1. Generate query embedding vector
        try:
            query_vector = self.provider.embed_query(query)
        except EmbeddingProviderError as exc:
            logger.error("Failed to generate query embedding: %s", exc)
            raise

        expected_dim = self.provider.get_dimensions()
        if len(query_vector) != expected_dim:
            logger.error(
                "Query embedding dimension mismatch: got %d, expected %d",
                len(query_vector), expected_dim
            )
            return []

        # 2. Get authorized candidates
        candidate_qs = self.build_candidate_queryset(
            user=request.user,
            filters=request.filters
        )

        # 3. Annotate pgvector cosine distance and order by similarity
        ranked_qs = (
            candidate_qs
            .annotate(distance=CosineDistance('embedding', query_vector))
            .filter(distance__isnull=False)
            .select_related('document', 'document_version')
            .prefetch_related('document__academic_units__academic_unit')
            .order_by('distance')
        )

        limit = max(1, int(request.limit or 10))
        enrolled_unit_ids, programme_level_unit_ids, programme_unit_ids = self._resolve_student_academic_sets(request.user)
        has_student_academic_scope = bool(enrolled_unit_ids or programme_unit_ids)

        # An explicitly named document must not be buried by the academic
        # boost, so work out which title tokens are distinctive first.
        query_tokens = set(re.findall(r"[a-z0-9]{4,}", query.lower()))
        token_frequency, tokens_by_document = self._title_token_frequency(candidate_qs)
        candidate_document_count = len(tokens_by_document)
        mentioned_document_ids = {
            document_id
            for document_id, tokens in tokens_by_document.items()
            if any(
                token in query_tokens
                and self._is_distinctive(token, token_frequency, candidate_document_count)
                for token in tokens
            )
        }

        # Fetch a wider candidate pool when applying student-native soft re-ranking
        candidate_pool_size = max(limit * 3, 20) if has_student_academic_scope else limit
        chunks = list(ranked_qs[:candidate_pool_size])

        # A named document is always in the pool, even if its raw similarity
        # sits below the cut: the student asked for it by name.
        if mentioned_document_ids:
            seen_ids = {chunk.id for chunk in chunks}
            for chunk in (
                ranked_qs.filter(document_id__in=mentioned_document_ids)[:12]
            ):
                if chunk.id not in seen_ids:
                    seen_ids.add(chunk.id)
                    chunks.append(chunk)

        results = []
        for chunk in chunks:
            dist = float(chunk.distance) if chunk.distance is not None else 1.0
            similarity = max(0.0, min(1.0, 1.0 - dist))

            if request.min_score > 0.0 and similarity < request.min_score:
                continue

            res = self._format_chunk_result(chunk, similarity)

            # Enrich with academic unit provenance and apply soft student-native boost
            doc_units = list(chunk.document.academic_units.all()) if hasattr(chunk.document, 'academic_units') else []
            doc_unit_ids = {du.academic_unit_id for du in doc_units if du.academic_unit_id}
            unit_codes = [du.academic_unit.code for du in doc_units if getattr(du, 'academic_unit', None)]
            unit_names = [du.academic_unit.name for du in doc_units if getattr(du, 'academic_unit', None)]

            res.metadata['unit_codes'] = unit_codes
            res.metadata['unit_names'] = unit_names
            res.metadata['raw_similarity'] = round(similarity, 4)

            academic_boost = 0.0
            academic_match_tier = 'general'
            if doc_unit_ids:
                if enrolled_unit_ids and (doc_unit_ids & enrolled_unit_ids):
                    academic_boost = 0.18
                    academic_match_tier = 'enrolled_unit'
                elif programme_level_unit_ids and (doc_unit_ids & programme_level_unit_ids):
                    academic_boost = 0.12
                    academic_match_tier = 'programme_level'
                elif programme_unit_ids and (doc_unit_ids & programme_unit_ids):
                    academic_boost = 0.06
                    academic_match_tier = 'programme'
                elif has_student_academic_scope:
                    academic_match_tier = 'other_programme'

            res.metadata['academic_match_tier'] = academic_match_tier
            res.metadata['matches_student_unit'] = (academic_match_tier == 'enrolled_unit')
            res.metadata['matches_student_programme'] = academic_match_tier in ('enrolled_unit', 'programme_level', 'programme')

            mention_boost = self._explicit_mention_boost(
                chunk, query_tokens, token_frequency, candidate_document_count
            )
            if mention_boost:
                res.metadata['explicit_mention'] = True
            res.score = round(min(1.0, similarity + academic_boost + mention_boost), 4)
            results.append(res)

        # Always re-rank by score: the mention boost (and the academic boost)
        # are meaningless unless the order follows the score. Previously the
        # sort only ran for students with an academic scope, so a named
        # document could be boosted and still sit below the pool order.
        results.sort(key=lambda r: (-r.score, -r.metadata.get('raw_similarity', 0.0)))

        return results[:limit]

    def get_page_chunks(
        self,
        user: Any,
        document_share_id: Any,
        page_number: int,
        document_version_id: Optional[int] = None,
    ) -> List[RetrievalResult]:
        """
        Deterministic, authorized retrieval of chunks spanning a specific page.

        Enforces strict authorization invariants via `build_authorized_chunk_queryset(user)`
        WITHOUT requiring vector embeddings to be completed (since page lookup is an exact
        SQL filter on `page_number`).
        Handles multi-page spanning chunks where page_number <= P <= page_end.
        """
        # 1. Validate user authentication
        if not user or not getattr(user, 'is_authenticated', False):
            return []

        # 2. Validate document_share_id
        if not document_share_id:
            return []
        if isinstance(document_share_id, uuid.UUID):
            share_uuid = document_share_id
        else:
            try:
                share_uuid = uuid.UUID(str(document_share_id).strip())
            except (ValueError, TypeError, AttributeError):
                return []

        # 3. Validate page_number (must be positive integer, not bool or float)
        if isinstance(page_number, bool) or isinstance(page_number, float):
            return []
        try:
            page_num = int(page_number)
            if page_num <= 0:
                return []
        except (ValueError, TypeError):
            return []

        # 4. Validate optional document_version_id
        version_id = None
        if document_version_id is not None:
            if isinstance(document_version_id, bool) or isinstance(document_version_id, float):
                return []
            try:
                version_id = int(document_version_id)
                if version_id <= 0:
                    return []
            except (ValueError, TypeError):
                return []

        # 5. Anchor to authorized chunk queryset (independent of embedding completion)
        candidate_qs = self.build_authorized_chunk_queryset(user=user)

        # 6. Scope strictly to the requested document share_id
        qs = candidate_qs.filter(document__share_id=share_uuid)

        # 7. Apply version filtering
        if version_id is not None:
            qs = qs.filter(document_version_id=version_id)
        else:
            qs = qs.filter(document_version__is_latest=True)

        # 8. Filter chunks spanning the requested page: page_number <= P <= page_end
        page_filter = (
            Q(page_number__lte=page_num, page_end__gte=page_num) |
            Q(page_number=page_num, page_end__isnull=True)
        )
        qs = qs.filter(page_filter)

        # 9. Deterministic ordering by chunk_index with relation prefetching
        chunks = list(
            qs.select_related('document', 'document_version')
            .prefetch_related('document_version__files', 'document__academic_units__academic_unit')
            .order_by('chunk_index')
        )

        # 10. Format results using standard RetrievalResult
        results = []
        for chunk in chunks:
            res = self._format_chunk_result(chunk, similarity=1.0)
            res.metadata['retrieval_mode'] = 'explicit_page'
            res.metadata['match_type'] = 'exact_page'
            res.metadata['active_viewer_page'] = page_num
            results.append(res)

        return results

    def get_document_overview_chunks(
        self,
        user: Any,
        document_share_id: Any,
        max_chunks: int = 6,
    ) -> List[RetrievalResult]:
        """
        Retrieve a whole-document structural overview (Table of Contents / section headings map,
        page count, and representative opening + sampled chunks across the document).

        Enables PwaniMate to provide a comprehensive book/document summary when a user
        opens a document via "Ask PwaniMate" or asks for a whole-document overview.
        """
        if not user or not getattr(user, 'is_authenticated', False) or not document_share_id:
            return []

        if isinstance(document_share_id, uuid.UUID):
            share_uuid = document_share_id
        else:
            try:
                share_uuid = uuid.UUID(str(document_share_id).strip())
            except (ValueError, TypeError, AttributeError):
                return []

        base_qs = (
            self.build_authorized_chunk_queryset(user=user)
            .filter(document__share_id=share_uuid, document_version__is_latest=True)
            .select_related('document', 'document_version')
            .prefetch_related('document_version__files', 'document__academic_units__academic_unit')
            .order_by('chunk_index')
        )

        all_chunks = list(base_qs[:120])
        if not all_chunks:
            return []

        doc = all_chunks[0].document
        max_page = max((c.page_end or c.page_number or 1) for c in all_chunks)

        # Build structural Table of Contents from section headings and heading chunks
        toc_entries = []
        seen_headings = set()
        for c in all_chunks:
            heading = (c.section_heading or "").strip()
            if not heading and c.chunk_type == 'heading':
                heading = (c.content or "").strip().splitlines()[0][:120]
            if heading and heading.lower() not in seen_headings:
                seen_headings.add(heading.lower())
                page_lbl = f"p. {c.page_number}" if c.page_number else f"chunk {c.chunk_index + 1}"
                toc_entries.append(f"- {heading} ({page_lbl})")
                if len(toc_entries) >= 25:
                    break

        # Select opening chunks + evenly spaced representative chunks across the document
        selected_indices = set()
        for idx in range(min(3, len(all_chunks))):
            selected_indices.add(idx)

        remaining_slots = max(0, max_chunks - len(selected_indices))
        if remaining_slots > 0 and len(all_chunks) > 3:
            step = max(1, (len(all_chunks) - 3) // (remaining_slots + 1))
            for slot in range(1, remaining_slots + 1):
                candidate_idx = min(len(all_chunks) - 1, 2 + slot * step)
                selected_indices.add(candidate_idx)

        ordered_selected = [all_chunks[i] for i in sorted(selected_indices)]
        results: List[RetrievalResult] = []

        for idx, chunk in enumerate(ordered_selected):
            res = self._format_chunk_result(chunk, similarity=0.98)
            res.metadata['retrieval_mode'] = 'document_overview'
            res.metadata['match_type'] = 'document_structure'
            res.metadata['total_pages_estimate'] = max_page
            if idx == 0:
                header_lines = [
                    f"[DOCUMENT STRUCTURE & OVERVIEW — '{doc.title}' (Approx. {max_page} pages)]"
                ]
                if getattr(doc, 'description', None):
                    header_lines.append(f"Document Description: {doc.description.strip()}")
                if toc_entries:
                    header_lines.append("Detected Sections / Table of Contents:")
                    header_lines.extend(toc_entries)
                header_lines.append("\nOpening Content:")
                res.snippet = "\n".join(header_lines) + "\n" + res.snippet
            results.append(res)

        return results

    def _title_token_frequency(self, queryset) -> Tuple[Dict[str, int], Dict[int, set]]:
        """
        Distinctive title tokens per candidate document, plus how many distinct
        titles carry each token.

        Both are counted per TITLE, not per chunk: the queryset carries a
        per-chunk distance annotation, so DISTINCT on it does not dedupe (and
        the same file uploaded twice is one name, not many).
        """
        from documents.models import Document

        document_ids = set(queryset.values_list('document_id', flat=True))
        tokens_by_document: Dict[int, set] = {}
        counts: Dict[str, int] = {}
        for document_id, title in Document.objects.filter(
            id__in=document_ids
        ).values_list('id', 'title'):
            tokens = self._distinctive_tokens(title or "")
            tokens_by_document[document_id] = tokens
        for title in set(
            Document.objects.filter(id__in=document_ids).values_list('title', flat=True)
        ):
            for token in self._distinctive_tokens(title or ""):
                counts[token] = counts.get(token, 0) + 1
        return counts, tokens_by_document

    @staticmethod
    def _distinctive_tokens(text: str) -> set:
        """Tokens worth matching on: long enough, not generic document words."""
        tokens = re.findall(r"[a-z0-9]{4,}", (text or "").lower())
        return {token for token in tokens if token not in _GENERIC_TITLE_TOKENS}

    def _is_distinctive(self, token: str, frequency: Dict[str, int], document_count: int) -> bool:
        """
        Is this title token rare enough to mean "the student named this"?

        Relative, not absolute: a word carried by a quarter of the library is
        a topic, not a name. (An absolute cap also broke the obvious case —
        re-uploading the same file a few times made its own name common.)
        """
        if document_count <= 0:
            return True
        return frequency.get(token, 99) <= max(3, 0.25 * document_count)

    def _explicit_mention_boost(
        self,
        chunk: DocumentChunk,
        query_tokens: set,
        token_frequency: Dict[str, int],
        document_count: int = 0,
    ) -> float:
        """
        Boost a document the student explicitly named.

        Without this, "what does the <their file> say" can be buried by
        programme-aligned material that has nothing to do with the question:
        the academic boost favours enrolled units, but a personal upload (a
        resume, their own notes) has no academic unit at all.
        """
        if not query_tokens:
            return 0.0
        titles = [chunk.document.title or ""]
        try:
            for file_obj in chunk.document_version.files.all():
                titles.append(file_obj.original_filename or "")
        except Exception:  # noqa: BLE001
            pass
        for title in titles:
            tokens = self._distinctive_tokens(title)
            if not tokens:
                continue
            hits = {
                token
                for token in tokens & query_tokens
                if self._is_distinctive(token, token_frequency, document_count)
            }
            if not hits:
                continue
            # Naming most of the title is an explicit request; naming one
            # distinctive word (e.g. a surname) still deserves a lift.
            return 0.30 if len(hits) / len(tokens) >= 0.5 else 0.18
        return 0.0

    def _format_chunk_result(self, chunk: DocumentChunk, similarity: float) -> RetrievalResult:
        """Format DocumentChunk into a normalized RetrievalResult."""
        doc = chunk.document
        version = chunk.document_version

        # Generate deterministic citation string
        citation = self.generate_citation(chunk)

        # Canonical detail URL
        detail_url = f"/documents/document/{doc.share_id}/"

        # Resolve thumbnail, media file URL, and author
        thumbnail_url = getattr(doc, 'thumbnail_url', None) or ""
        first_file = None
        if version and hasattr(version, 'files'):
            first_file = version.files.first()
        elif hasattr(doc, 'latest_version') and doc.latest_version:
            first_file = doc.latest_version.files.first()

        media_url = ""
        file_type = ""
        if first_file:
            try:
                if getattr(first_file, 'file', None):
                    media_url = first_file.file.url
            except Exception:
                media_url = ""
            file_type = getattr(first_file, 'extension', '') or ""

        author_name = ""
        uploader = getattr(doc, 'uploaded_by', None)
        if uploader:
            full_name = f"{uploader.first_name or ''} {uploader.last_name or ''}".strip()
            author_name = full_name or getattr(uploader, "username", "") or ""

        doc_units = list(doc.academic_units.all()) if hasattr(doc, 'academic_units') else []
        unit_codes = [du.academic_unit.code for du in doc_units if getattr(du, 'academic_unit', None)]
        unit_names = [du.academic_unit.name for du in doc_units if getattr(du, 'academic_unit', None)]

        metadata = {
            "document_id": doc.id,
            "document_share_id": str(doc.share_id),
            "document_version_id": version.id if version else None,
            "version_number": version.version_number if version else None,
            "chunk_id": chunk.id,
            "chunk_index": chunk.chunk_index,
            "chunk_type": chunk.chunk_type,
            "page_number": chunk.page_number,
            "page_end": chunk.page_end,
            "slide_number": chunk.slide_number,
            "section_heading": chunk.section_heading,
            "embedding_model": chunk.embedding_model,
            "thumbnail_url": thumbnail_url,
            "media_url": media_url,
            "file_type": file_type,
            "resource_type": "document",
            "author": author_name,
            "unit_codes": unit_codes,
            "unit_names": unit_names,
        }

        return RetrievalResult(
            source=SourceType.DOCUMENT,
            object_id=chunk.id,
            title=doc.title,
            snippet=chunk.content,
            score=round(similarity, 4),
            url=detail_url,
            citation=citation,
            metadata=metadata,
            raw_object=chunk,
        )

    @staticmethod
    def generate_citation(chunk: DocumentChunk) -> str:
        """
        Generate academic citation label from chunk location provenance.

        Format:
        [<Title>, v<N>, p. <page> - <Heading>]
        [<Title>, v<N>, Slide <slide>]
        [<Title>, v<N>, chunk <idx>]
        """
        parts = [chunk.document.title]
        version_str = f"v{chunk.document_version.version_number}"
        parts.append(version_str)

        loc_parts = []
        if chunk.chunk_type == 'slide' or (chunk.slide_number and not chunk.page_number):
            loc_parts.append(f"Slide {chunk.slide_number}")
        elif chunk.page_number:
            if chunk.page_end and chunk.page_end > chunk.page_number:
                loc_parts.append(f"pp. {chunk.page_number}–{chunk.page_end}")
            else:
                loc_parts.append(f"p. {chunk.page_number}")
        elif chunk.slide_number:
            loc_parts.append(f"Slide {chunk.slide_number}")
        else:
            loc_parts.append(f"Chunk {chunk.chunk_index + 1}")

        if chunk.section_heading:
            loc_parts.append(f'"{chunk.section_heading}"')

        citation_loc = ", ".join(loc_parts)
        return f"[{', '.join(parts)}: {citation_loc}]"
