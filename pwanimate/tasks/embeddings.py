"""
Asynchronous Embedding Celery Tasks for Pwanimate (Phase 2F).

Processes eligible DocumentChunks, generates vector embeddings via
the configured EmbeddingProvider, and stores them in PostgreSQL/pgvector.
"""

import logging
from typing import Dict, Any, Optional, List
from celery import shared_task
from django.conf import settings
from django.db import transaction

from documents.models import Document, DocumentVersion
from ..models import DocumentChunk
from ..ai.embeddings.factory import get_embedding_provider
from ..ai.embeddings.base import (
    BaseEmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingConfigurationError,
    EmbeddingQuotaExhaustedError,
)

logger = logging.getLogger(__name__)


def _chunk_batch_generator(items: List[Any], batch_size: int):
    """Yield successive batches from items."""
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='pwanimate.tasks.embeddings.embed_document_version'
)
def embed_document_version(
    self,
    version_id: int,
    force: bool = False,
    provider_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generates and stores vector embeddings for all eligible chunks in a DocumentVersion.

    Args:
        version_id: Primary key of DocumentVersion to embed.
        force: If True, forces re-embedding even if chunks are already completed.
        provider_name: Optional override for provider type ('gemini' or 'mock').

    Returns:
        Summary dictionary with embedding metrics.
    """
    logger.info(f"[PWANIMATE-EMBEDDINGS] Starting embedding task for DocumentVersion {version_id} (force={force})")

    try:
        version = DocumentVersion.objects.select_related('document').get(id=version_id)
    except DocumentVersion.DoesNotExist:
        logger.error(f"[PWANIMATE-EMBEDDINGS] DocumentVersion {version_id} not found")
        return {
            'status': 'failed',
            'reason': 'version_not_found',
            'version_id': version_id,
        }

    # Resolve provider
    try:
        provider: BaseEmbeddingProvider = get_embedding_provider(provider_name=provider_name)
    except Exception as e:
        logger.error(f"[PWANIMATE-EMBEDDINGS] Could not initialize embedding provider: {e}", exc_info=True)
        return {
            'status': 'failed',
            'reason': 'provider_initialization_error',
            'error': str(e),
        }

    # Check configuration
    if not provider.is_configured():
        logger.warning(
            f"[PWANIMATE-EMBEDDINGS] Provider '{provider.get_model_name()}' is not configured (missing credentials). "
            f"Chunks for version {version_id} remain in pending status."
        )
        return {
            'status': 'provider_unconfigured',
            'version_id': version_id,
            'model': provider.get_model_name(),
        }

    active_model = provider.get_model_name()
    expected_dim = provider.get_dimensions()

    # Query eligible chunks
    # By default, process chunks that are 'pending' or 'failed', or whose model does not match active_model
    chunk_qs = version.chunks.exclude(content='').order_by('chunk_index')
    if not force:
        chunk_qs = chunk_qs.exclude(
            embedding_status='completed', embedding_model=active_model,
            embedding__isnull=False, is_active=True,
        )

    chunks_to_embed = list(chunk_qs)

    if not chunks_to_embed:
        logger.info(f"[PWANIMATE-EMBEDDINGS] Version {version_id} has 0 chunks requiring embedding. Everything up to date.")
        return {
            'status': 'up_to_date',
            'version_id': version_id,
            'embedded_count': 0,
            'model': active_model,
        }

    batch_size = getattr(settings, 'PWANIMATE_EMBEDDING_BATCH_SIZE', 32)
    logger.info(
        f"[PWANIMATE-EMBEDDINGS] Embedding {len(chunks_to_embed)} chunks for version {version_id} "
        f"using model '{active_model}' (batch size: {batch_size}, dim: {expected_dim})"
    )

    # Prepend academic provenance (unit codes/names, level) to the embedded
    # text so generically-titled chunks ("Week 3 notes") still match queries
    # like "linear algebra". Stored chunk.content is left untouched.
    try:
        from documents.academic.services import document_academic_context
        academic_prefix = document_academic_context(version.document)
    except Exception:
        academic_prefix = ''
    total_embedded = 0

    for batch in _chunk_batch_generator(chunks_to_embed, batch_size):
        texts = [f"{academic_prefix} {c.content}" if academic_prefix else c.content
                 for c in batch]

        try:
            vectors = provider.embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

        except EmbeddingConfigurationError as e:
            logger.error(f"[PWANIMATE-EMBEDDINGS] Configuration error during embedding: {e}")
            return {
                'status': 'provider_unconfigured',
                'version_id': version_id,
                'error': str(e),
            }

        except EmbeddingQuotaExhaustedError as e:
            # Quota is transient — leave chunks as 'pending' so they can be picked
            # up on the next attempt. Use exponential back-off:
            #   retry 0 → 5 min, retry 1 → 30 min, retry 2 → 2 hr,
            #   retry 3 → 8 hr,  retry 4 → 24 hr
            attempt = self.request.retries
            backoff = min(300 * (6 ** attempt), 86400)  # cap at 24 hr
            logger.warning(
                f"[PWANIMATE-EMBEDDINGS] Quota exhausted for version {version_id} "
                f"(attempt {attempt + 1}/6). Retrying in {backoff}s. Error: {e}"
            )
            raise self.retry(exc=e, countdown=backoff, max_retries=5)

        except Exception as e:
            logger.error(
                f"[PWANIMATE-EMBEDDINGS] Provider error generating embeddings for version {version_id}: {e}",
                exc_info=True
            )
            # Hard failure — mark this batch as failed so it's not silently stuck
            err_text = str(e)[:250]
            with transaction.atomic():
                for c in batch:
                    c.embedding_status = 'failed'
                    c.is_active = False
                    if c.metadata is None:
                        c.metadata = {}
                    c.metadata['last_embedding_error'] = err_text
                DocumentChunk.objects.bulk_update(batch, fields=['embedding_status', 'is_active', 'metadata'])
            raise self.retry(exc=e)

        if len(vectors) != len(batch):
            err_msg = f"Provider returned {len(vectors)} vectors for {len(batch)} chunks"
            logger.error(f"[PWANIMATE-EMBEDDINGS] {err_msg}")
            with transaction.atomic():
                for c in batch:
                    c.embedding_status = 'failed'
                    c.is_active = False
                    if c.metadata is None:
                        c.metadata = {}
                    c.metadata['last_embedding_error'] = err_msg
                DocumentChunk.objects.bulk_update(batch, fields=['embedding_status', 'is_active', 'metadata'])
            return {'status': 'failed', 'reason': err_msg}

        # Persist vectors and transition lifecycle to completed + active
        with transaction.atomic():
            for chunk, vec in zip(batch, vectors):
                if vec is not None and len(vec) == expected_dim:
                    chunk.embedding = vec
                    chunk.embedding_status = 'completed'
                    chunk.embedding_model = active_model
                    chunk.is_active = True  # Validated and retrieval-ready
                    if chunk.metadata and 'last_embedding_error' in chunk.metadata:
                        chunk.metadata.pop('last_embedding_error', None)
                else:
                    chunk.embedding = None
                    chunk.embedding_status = 'failed'
                    chunk.is_active = False
                    if chunk.metadata is None:
                        chunk.metadata = {}
                    dim_found = len(vec) if vec else 0
                    chunk.metadata['last_embedding_error'] = f"Vector dimension mismatch: expected {expected_dim}, got {dim_found}"

            if academic_prefix:
                for c in batch:
                    c.metadata = {**(c.metadata or {}), 'academic_context': academic_prefix}

            DocumentChunk.objects.bulk_update(
                batch,
                fields=['embedding', 'embedding_status', 'embedding_model', 'is_active', 'metadata']
            )

        successful_in_batch = sum(1 for c in batch if c.embedding_status == 'completed')
        total_embedded += successful_in_batch

    logger.info(
        f"[PWANIMATE-EMBEDDINGS] SUCCESS: Embedded and activated {total_embedded} chunks for "
        f"version {version_id} with model '{active_model}'."
    )

    return {
        'status': 'success',
        'version_id': version_id,
        'document_id': version.document_id,
        'embedded_count': total_embedded,
        'model': active_model,
        'dimensions': expected_dim,
    }


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='pwanimate.tasks.embeddings.embed_document'
)
def embed_document(
    self,
    document_id: int,
    force: bool = False,
    provider_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convenience task to embed the latest version of a Document.
    """
    try:
        doc = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error(f"[PWANIMATE-EMBEDDINGS] Document {document_id} not found")
        return {'status': 'failed', 'reason': 'document_not_found', 'document_id': document_id}

    latest_version = doc.latest_version
    if not latest_version:
        logger.warning(f"[PWANIMATE-EMBEDDINGS] Document {document_id} has no latest_version, skipping.")
        return {'status': 'skipped', 'reason': 'no_latest_version', 'document_id': document_id}

    return embed_document_version(
        version_id=latest_version.id,
        force=force,
        provider_name=provider_name
    )
