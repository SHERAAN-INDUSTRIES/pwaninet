"""
Unified Retrieval Service for Pwanimate.

Orchestrates multi-source retrieval across documents (semantic and lexical),
posts, people, and groups with execution timing and performance metrics.
"""

import time
import logging
from typing import Callable, List, Optional

from pwanimate.retrieval.types import (
    RetrievalRequest,
    RetrievalResponse,
    RetrievalResult,
    SourceType,
    RetrievalMode,
)
from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService
from pwanimate.retrieval.adapters.pwaninet_search import pwaninetSearchAdapter

logger = logging.getLogger(__name__)


class UnifiedRetrievalService:
    """Orchestrator for multi-source knowledge retrieval."""

    def __init__(
        self,
        document_service: Optional[DocumentSemanticRetrievalService] = None,
        search_adapter: Optional[pwaninetSearchAdapter] = None,
    ):
        self.document_service = document_service or DocumentSemanticRetrievalService()
        self.search_adapter = search_adapter or pwaninetSearchAdapter()

    def retrieve(
        self,
        request: RetrievalRequest,
        on_source_start: Optional[Callable[[SourceType], None]] = None,
    ) -> RetrievalResponse:
        """
        Execute unified cross-source retrieval.

        Args:
            request: Standard RetrievalRequest.

        Returns:
            RetrievalResponse with normalized results and performance metrics.
        """
        start_time = time.perf_counter()
        query = (request.query or "").strip()

        if not query:
            return RetrievalResponse(
                query=query,
                results=[],
                total_count=0,
                execution_time_ms=0.0,
                source_metrics={}
            )

        sources = request.get_source_types()
        mode = request.mode if isinstance(request.mode, RetrievalMode) else RetrievalMode(str(request.mode).lower())

        combined_results: List[RetrievalResult] = []
        source_metrics = {}

        for source in sources:
            if on_source_start:
                on_source_start(source)
            source_start = time.perf_counter()
            source_results: List[RetrievalResult] = []
            degraded = False

            try:
                if source == SourceType.DOCUMENT:
                    if mode == RetrievalMode.SEMANTIC:
                        source_results = self.document_service.retrieve(request)
                    elif mode == RetrievalMode.LEXICAL:
                        source_results = self.search_adapter.search_documents_lexical(request)
                    elif mode == RetrievalMode.HYBRID:
                        # Always consult both indexes. Chunk-level semantic hits
                        # can otherwise fill the limit with a couple of documents
                        # and prevent unchunked documents from reaching lexical search.
                        sem_results = []
                        try:
                            sem_results = self.document_service.retrieve(request)
                        except Exception as sem_exc:
                            degraded = True
                            try:
                                sem_results = self.document_service.retrieve_lexical_chunks(request)
                            except Exception:
                                logger.exception('Chunk-text fallback failed; trying document metadata search.')
                            logger.warning(
                                "Semantic document retrieval failed in hybrid mode, continuing with lexical fallback: %s",
                                sem_exc,
                                exc_info=True,
                            )

                        try:
                            lex_results = self.search_adapter.search_documents_lexical(request)
                        except Exception as lex_exc:
                            logger.warning(
                                "Lexical document retrieval failed in hybrid mode: %s",
                                lex_exc,
                                exc_info=True,
                            )
                            lex_results = []

                        limit = max(1, int(request.limit or 10))
                        semantic_budget = max(1, (limit + 1) // 2)
                        seen_document_ids = set()
                        seen_titles = set()
                        seen_chunk_ids = set()
                        semantic_chunks_per_document = {}
                        for result in sem_results:
                            metadata = result.metadata if isinstance(result.metadata, dict) else {}
                            document_id = str(metadata.get("document_id") or "")
                            title_key = (result.title or "").strip().casefold()
                            document_key = document_id or title_key
                            if not document_key:
                                continue
                            count = semantic_chunks_per_document.get(document_key, 0)
                            if count >= 3:
                                continue
                            semantic_chunks_per_document[document_key] = count + 1
                            seen_chunk_ids.add(str(result.object_id))
                            seen_document_ids.add(document_key)
                            if title_key:
                                seen_titles.add(title_key)
                            source_results.append(result)
                            if len(source_results) >= semantic_budget:
                                break

                        # Add distinct lexical documents even if semantic chunks
                        # filled the former limit, including documents not chunked yet.
                        for result in lex_results:
                            metadata = result.metadata if isinstance(result.metadata, dict) else {}
                            document_key = str(metadata.get("document_id") or result.object_id or "")
                            title_key = (result.title or "").strip().casefold()
                            if (document_key and document_key in seen_document_ids) or (
                                title_key and title_key in seen_titles
                            ):
                                continue
                            if document_key:
                                seen_document_ids.add(document_key)
                            if title_key:
                                seen_titles.add(title_key)
                            source_results.append(result)
                            if len(source_results) >= limit:
                                break

                        # If lexical search added few distinct documents, use the
                        # remaining semantic hits to fill unused slots, still
                        # limiting repeated chunks from any one document.
                        if len(source_results) < limit:
                            for result in sem_results:
                                if str(result.object_id) in seen_chunk_ids:
                                    continue
                                metadata = result.metadata if isinstance(result.metadata, dict) else {}
                                document_key = str(metadata.get("document_id") or result.title or "").casefold()
                                count = semantic_chunks_per_document.get(document_key, 0)
                                if not document_key or count >= 3:
                                    continue
                                semantic_chunks_per_document[document_key] = count + 1
                                seen_chunk_ids.add(str(result.object_id))
                                source_results.append(result)
                                if len(source_results) >= limit:
                                    break
                elif source == SourceType.POST:
                    source_results = self.search_adapter.search_posts(request)
                elif source == SourceType.USER:
                    source_results = self.search_adapter.search_people(request)
                elif source == SourceType.GROUP:
                    source_results = self.search_adapter.search_groups(request)

            except Exception as exc:
                logger.error("Error retrieving from source %s: %s", source, exc, exc_info=True)
                source_results = []

            source_duration_ms = round((time.perf_counter() - source_start) * 1000.0, 2)
            source_metrics[source.value] = {
                "count": len(source_results),
                "duration_ms": source_duration_ms,
                "degraded": degraded,
            }
            combined_results.extend(source_results)

        # Multi-source ranking: deterministic merge
        if len(sources) > 1:
            combined_results.sort(key=lambda r: r.score, reverse=True)

        final_results = combined_results[:request.limit]
        total_duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

        return RetrievalResponse(
            query=query,
            results=final_results,
            total_count=len(combined_results),
            execution_time_ms=total_duration_ms,
            source_metrics=source_metrics,
        )
