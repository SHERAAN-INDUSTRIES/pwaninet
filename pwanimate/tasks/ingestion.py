"""
Asynchronous Document Ingestion Celery Tasks for Pwanimate (Phase 2E).

Connects:
  DocumentFile / DocumentVersion
        ↓
  Phase 2B Structural Extraction (extract_document)
        ↓
  Phase 2D Structure-Aware Chunking (chunk_document)
        ↓
  Phase 2C DocumentChunk Persistence (bulk_create)
        ↓
  Ready for Phase 2F Embeddings (is_active=False, embedding_status='pending')
"""

import logging
from functools import wraps
from typing import Dict, Any, Optional
from celery import shared_task
from celery.exceptions import Retry
from django.db import transaction

from documents.models import Document, DocumentVersion, DocumentFile
from ..models import DocumentChunk
from ..ingestion.extractors.factory import extract_document
from ..ingestion.chunker import chunk_document, create_document_chunks_from_data
from ..ingestion.exceptions import (
    ExtractionError,
    UnsupportedFormatError,
    CorruptFileError,
    EmptyDocumentError,
)

logger = logging.getLogger(__name__)


def _queue_embeddings(task, version_id):
    def dispatch():
        from .embeddings import embed_document_version
        try:
            embed_document_version.delay(version_id=version_id)
        except Exception as exc:
            # A retry finds the committed chunks and resumes embedding dispatch.
            raise task.retry(exc=exc)
    transaction.on_commit(dispatch)


def _track_ingestion_outcome(function):
    @wraps(function)
    def tracked(self, version_id, *args, **kwargs):
        from pwanimate.services.reconciliation import DocumentReconciliationService, INGEST_TASK_LOCK_PREFIX
        service = DocumentReconciliationService()
        try:
            result = function(self, version_id, *args, **kwargs)
            try:
                status = result.get('status') if isinstance(result, dict) else None
                if status in {'unsupported_format', 'unreadable_file', 'extraction_failed', 'chunking_failed', 'empty_chunks'}:
                    service.record_attempt_failure(version_id)
                elif status in {'success', 'already_ingested'}:
                    service.clear_attempt_failure(version_id)
            except Exception:
                logger.exception('Could not record ingestion health for version %s', version_id)
            return result
        finally:
            try:
                service.release_dispatch_lock(INGEST_TASK_LOCK_PREFIX, version_id)
            except Exception:
                logger.exception('Could not release ingestion dispatch lock for version %s', version_id)
    return tracked


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='pwanimate.tasks.ingestion.ingest_document_version'
)
@_track_ingestion_outcome
def ingest_document_version(self, version_id: int, force: bool = False) -> Dict[str, Any]:
    """
    Ingests a specific DocumentVersion into structured, inactive DocumentChunks.

    Args:
        version_id: Primary key of DocumentVersion to ingest.
        force: If True, purges and regenerates existing chunks for this version.

    Returns:
        Dictionary containing ingestion summary metrics.
    """
    logger.info(f"[PWANIMATE-INGESTION] Starting ingestion for DocumentVersion {version_id} (force={force})")

    try:
        version = DocumentVersion.objects.select_related('document').get(id=version_id)
    except DocumentVersion.DoesNotExist:
        logger.error(f"[PWANIMATE-INGESTION] DocumentVersion {version_id} not found")
        return {
            'status': 'failed',
            'reason': 'version_not_found',
            'version_id': version_id,
        }

    doc = version.document

    # Idempotency check: if not forcing, avoid duplicate chunking
    if not force and version.chunks.exists():
        existing_count = version.chunks.count()
        logger.info(
            f"[PWANIMATE-INGESTION] DocumentVersion {version_id} already has {existing_count} chunks. Skipping re-ingestion."
        )
        _queue_embeddings(self, version_id)
        return {
            'status': 'already_ingested',
            'document_id': doc.id,
            'version_id': version_id,
            'chunk_count': existing_count,
        }

    source_files = list(version.files.order_by("id"))
    primary_file = source_files[0] if source_files else None

    if not primary_file:
        logger.warning(f"[PWANIMATE-INGESTION] DocumentVersion {version_id} has no attached files, skipping.")
        return {
            'status': 'skipped',
            'reason': 'no_files_found',
            'version_id': version_id,
            'document_id': doc.id,
        }

    # Phase 2B: Structural Extraction
    try:
        logger.info(f"[PWANIMATE-INGESTION] Extracting structural content from file {primary_file.id} ({primary_file.original_filename})")
        extracted_files = []
        for primary_file in source_files:
            extracted_files.append((primary_file, extract_document(primary_file)))
    except UnsupportedFormatError as e:
        logger.info(f"[PWANIMATE-INGESTION] Unsupported format for file {primary_file.id}: {e}")
        return {
            'status': 'unsupported_format',
            'document_id': doc.id,
            'version_id': version_id,
            'file_id': primary_file.id,
            'error': str(e),
        }
    except (CorruptFileError, EmptyDocumentError) as e:
        logger.warning(f"[PWANIMATE-INGESTION] Unprocessable file {primary_file.id}: {e}")
        return {
            'status': 'unreadable_file',
            'document_id': doc.id,
            'version_id': version_id,
            'file_id': primary_file.id,
            'error': str(e),
        }
    except ExtractionError as e:
        logger.error(f"[PWANIMATE-INGESTION] Extraction error for file {primary_file.id}: {e}", exc_info=True)
        return {
            'status': 'extraction_failed',
            'document_id': doc.id,
            'version_id': version_id,
            'file_id': primary_file.id,
            'error': str(e),
        }
    except Exception as e:
        logger.error(f"[PWANIMATE-INGESTION] Unexpected extraction error for file {primary_file.id}: {e}", exc_info=True)
        raise self.retry(exc=e)

    # Phase 2D: Structure-Aware Chunking
    try:
        chunks_data = []
        element_count = sum(len(extracted.elements) for _, extracted in extracted_files)
        logger.info("[PWANIMATE-INGESTION] Chunking %d elements across %d files", element_count, len(extracted_files))
        for source_file, extracted_doc in extracted_files:
            for chunk in chunk_document(extracted_doc):
                chunk.chunk_index = len(chunks_data)
                chunk.metadata = {**(chunk.metadata or {}), 'document_file_id': source_file.pk,
                                  'source_filename': source_file.original_filename}
                chunks_data.append(chunk)
    except Exception as e:
        logger.error(f"[PWANIMATE-INGESTION] Chunking failed for version {version_id}: {e}", exc_info=True)
        return {
            'status': 'chunking_failed',
            'document_id': doc.id,
            'version_id': version_id,
            'error': str(e),
        }

    if not chunks_data:
        logger.warning(f"[PWANIMATE-INGESTION] Extracted document produced 0 chunks for version {version_id}")
        return {
            'status': 'empty_chunks',
            'document_id': doc.id,
            'version_id': version_id,
            'chunk_count': 0,
        }

    # Validation: PDF documents MUST have page numbers for page-specific retrieval
    if extracted_doc.format == 'pdf':
        chunks_without_pages = sum(1 for chunk in chunks_data if chunk.page_number is None)
        if chunks_without_pages > 0:
            logger.error(
                f"[PWANIMATE-INGESTION] CRITICAL: PDF document {doc.id} (version {version_id}) has "
                f"{chunks_without_pages}/{len(chunks_data)} chunks without page numbers. "
                f"This will break page-specific retrieval."
            )
            # Continue anyway to allow manual intervention, but log the critical issue

    # Phase 2C: Atomic Database Persistence
    try:
        with transaction.atomic():
            # Lock version row to serialize concurrent ingestion requests
            locked_version = DocumentVersion.objects.select_for_update().get(id=version_id)

            if not force and locked_version.chunks.exists():
                _queue_embeddings(self, version_id)
                return {'status': 'already_ingested', 'document_id': doc.id,
                        'version_id': version_id, 'chunk_count': locked_version.chunks.count()}
            if force:
                logger.info(f"[PWANIMATE-INGESTION] Purging existing chunks for version {version_id} prior to re-creation")
                DocumentChunk.objects.filter(document_version=locked_version).delete()

            created_chunks = create_document_chunks_from_data(
                document_version=locked_version,
                chunks=chunks_data,
                document=locked_version.document
            )

        logger.info(
            f"[PWANIMATE-INGESTION] SUCCESS: Created {len(created_chunks)} inactive chunks for "
            f"document {doc.id} (version {version_id}). Status: pending embedding."
        )

        # Auto-tag untagged documents so unit-scoped retrieval and academic
        # ranking can see them. Advisory only — never fail ingestion on it.
        try:
            from documents.academic.services import DocumentAcademicLinkService
            DocumentAcademicLinkService.auto_link_document(doc)
        except Exception:
            logger.warning("[PWANIMATE-INGESTION] Auto academic-unit tagging failed for document %s", doc.id, exc_info=True)

        _queue_embeddings(self, version_id)

        return {
            'status': 'success',
            'document_id': doc.id,
            'version_id': version_id,
            'file_id': primary_file.id,
            'element_count': element_count,
            'file_ids': [file.pk for file in source_files],
            'chunk_count': len(created_chunks),
        }
    except Retry:
        raise
    except Exception as e:
        logger.error(f"[PWANIMATE-INGESTION] Database persistence error for version {version_id}: {e}", exc_info=True)
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='pwanimate.tasks.ingestion.ingest_document'
)
def ingest_document(self, document_id: int, force: bool = False) -> Dict[str, Any]:
    """
    Convenience task to ingest the latest version of a Document.

    Args:
        document_id: Primary key of Document to ingest.
        force: If True, forces re-chunking even if already ingested.
    """
    try:
        doc = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error(f"[PWANIMATE-INGESTION] Document {document_id} not found")
        return {'status': 'failed', 'reason': 'document_not_found', 'document_id': document_id}

    latest_version = doc.latest_version
    if not latest_version:
        logger.warning(f"[PWANIMATE-INGESTION] Document {document_id} has no latest_version, skipping.")
        return {'status': 'skipped', 'reason': 'no_latest_version', 'document_id': document_id}

    return ingest_document_version(version_id=latest_version.id, force=force)
