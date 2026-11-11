"""
Document Ingestion Reconciliation Service for Pwanimate.

Provides unified detection of ingestion gaps, incomplete embeddings, and health states,
with distributed locking to prevent duplicate concurrent task dispatches and hot-loop retries.
"""

import logging
from typing import Dict, Any, List, Optional
from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.db.models import Count, Q

from documents.models import DocumentVersion

logger = logging.getLogger("pwanimate.reconciliation")

# Lock keys and timeouts
GLOBAL_RECONCILIATION_LOCK_KEY = "pwanimate:reconciliation:global_lock"
GLOBAL_RECONCILIATION_LOCK_TIMEOUT = 300  # 5 minutes

INGEST_TASK_LOCK_PREFIX = "pwanimate:reconciliation:ingest_lock"
EMBED_TASK_LOCK_PREFIX = "pwanimate:reconciliation:embed_lock"
TASK_LOCK_TIMEOUT = 900  # 15 minutes to allow slow/queued worker processing

FAIL_COUNT_KEY_PREFIX = "pwanimate:reconciliation:fail_count"
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_LOCK_TIMEOUT = 86400  # 24 hours backoff for chronically broken files


def classify_version_health(
    total_chunks: int,
    completed_chunks: int,
    pending_chunks: int,
    failed_chunks: int,
    has_file: bool,
) -> str:
    """
    Standardized classification of DocumentVersion ingestion health.

    Returns one of:
      - 'NO FILE': Version has no attached source file.
      - 'NOT INGESTED': Version has 0 chunks in database.
      - 'READY': All chunks have completed embeddings and are active.
      - 'FAILED': All chunks encountered embedding failure.
      - 'EMBEDDING INCOMPLETE': Chunks exist, but some are pending or failed.
    """
    if not has_file:
        return "NO FILE"
    if total_chunks == 0:
        return "NOT INGESTED"
    if total_chunks > 0 and completed_chunks == total_chunks:
        return "READY"
    if total_chunks > 0 and failed_chunks == total_chunks:
        return "FAILED"
    if pending_chunks > 0 or failed_chunks > 0:
        return "EMBEDDING INCOMPLETE"
    return "EMBEDDING INCOMPLETE"


class DocumentReconciliationService:
    """
    Service that evaluates document ingestion completeness and safely
    dispatches required worker tasks.
    """

    def __init__(
        self,
        max_inspect: Optional[int] = None,
        max_ingest: Optional[int] = None,
        max_embed: Optional[int] = None,
    ):
        self.max_inspect = max_inspect if max_inspect is not None else getattr(settings, "PWANIMATE_RECONCILE_MAX_INSPECT", 50)
        self.max_ingest = max_ingest if max_ingest is not None else getattr(settings, "PWANIMATE_RECONCILE_MAX_INGEST", 10)
        self.max_embed = max_embed if max_embed is not None else getattr(settings, "PWANIMATE_RECONCILE_MAX_EMBED", 10)

    def get_annotated_versions_queryset(
        self,
        doc_id: Optional[int] = None,
        version_id: Optional[int] = None,
    ):
        """
        Build an efficient queryset using database-side conditional aggregations.
        Calculates chunk and file counts without loading chunk contents or vectors.
        """
        from pwanimate.ai.embeddings.factory import get_embedding_provider
        active_model = get_embedding_provider().get_model_name()
        qs = DocumentVersion.objects.filter(
            is_latest=True,
            document__status="ready",
            document__is_available=True,
        ).annotate(
            file_count=Count("files", distinct=True),
            chunk_count=Count("chunks", distinct=True),
            pending_count=Count("chunks", filter=Q(chunks__embedding_status="pending"), distinct=True),
            failed_count=Count("chunks", filter=Q(chunks__embedding_status="failed"), distinct=True),
            completed_count=Count("chunks", filter=Q(chunks__embedding_status="completed",
                chunks__is_active=True, chunks__embedding__isnull=False,
                chunks__embedding_model=active_model), distinct=True),
            active_count=Count("chunks", filter=Q(chunks__is_active=True), distinct=True),
        ).select_related("document").prefetch_related("files")

        if doc_id is not None:
            qs = qs.filter(document_id=doc_id)
        if version_id is not None:
            qs = qs.filter(id=version_id)

        return qs.annotate(_repair_priority=models.Case(
            models.When(file_count=0, then=models.Value(2)),
            models.When(chunk_count=models.F('completed_count'), then=models.Value(1)),
            default=models.Value(0), output_field=models.IntegerField(),
        )).order_by('_repair_priority', 'document_id', 'version_number')

    def acquire_dispatch_lock(self, lock_prefix: str, version_id: int, timeout: int = TASK_LOCK_TIMEOUT) -> bool:
        """
        Atomic distributed lock for task dispatch using Django's cache backend.
        Returns True if lock acquired, False if task is already in-flight.
        """
        key = f"{lock_prefix}:{version_id}"
        return bool(cache.add(key, "1", timeout=timeout))

    def release_dispatch_lock(self, lock_prefix: str, version_id: int):
        """Release a task dispatch lock."""
        key = f"{lock_prefix}:{version_id}"
        cache.delete(key)

    def is_backed_off(self, version_id: int) -> bool:
        """Check if this version has exceeded failure threshold and is backed off."""
        fail_count = cache.get(f"{FAIL_COUNT_KEY_PREFIX}:{version_id}", 0)
        return fail_count >= MAX_CONSECUTIVE_FAILURES

    def record_attempt_failure(self, version_id: int):
        """Record an unsuccessful processing cycle for exponential backoff."""
        key = f"{FAIL_COUNT_KEY_PREFIX}:{version_id}"
        current = cache.get(key, 0) + 1
        timeout = BACKOFF_LOCK_TIMEOUT if current >= MAX_CONSECUTIVE_FAILURES else 3600
        cache.set(key, current, timeout=timeout)
        logger.warning(
            "[PWANIMATE-RECONCILER] Version %d recorded failure attempt %d/%d (backoff: %ds)",
            version_id, current, MAX_CONSECUTIVE_FAILURES, timeout
        )

    def clear_attempt_failure(self, version_id: int):
        """Reset failure counter when progress is observed."""
        cache.delete(f"{FAIL_COUNT_KEY_PREFIX}:{version_id}")

    def reconcile(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Main reconciliation execution turn.

        1. Acquires global execution lock.
        2. Evaluates document versions for missing ingestion or pending embeddings.
        3. Respects per-version dispatch locks to prevent duplicate tasks.
        4. Queues existing Celery tasks (ingest_document_version / embed_document_version).
        5. Emits structured observability logs.
        """
        # Step 1: Global Lock Check
        if not dry_run:
            acquired_global = cache.add(
                GLOBAL_RECONCILIATION_LOCK_KEY, "1", timeout=GLOBAL_RECONCILIATION_LOCK_TIMEOUT
            )
            if not acquired_global:
                logger.info("[PWANIMATE-RECONCILER] Reconciliation already in progress. Skipping cycle.")
                return {
                    "status": "skipped_locked",
                    "reason": "concurrent_run_active",
                }

        try:
            logger.info(
                "[PWANIMATE-RECONCILER] Reconciliation started. Limits: max_inspect=%d, max_ingest=%d, max_embed=%d (dry_run=%s)",
                self.max_inspect, self.max_ingest, self.max_embed, dry_run
            )

            versions_qs = self.get_annotated_versions_queryset()[:self.max_inspect]
            versions = list(versions_qs)

            inspected_count = len(versions)
            missing_ingestion_found = 0
            embedding_incomplete_found = 0
            ingest_dispatched = 0
            embed_dispatched = 0
            skipped_locked = 0
            skipped_healthy = 0
            skipped_invalid = 0
            skipped_backoff = 0

            # Import tasks dynamically to allow standard Celery discovery
            from pwanimate.tasks.ingestion import ingest_document_version
            from pwanimate.tasks.embeddings import embed_document_version

            for v in versions:
                has_file = v.file_count > 0
                first_file = v.files.first() if has_file else None

                # Classify health
                health = classify_version_health(
                    total_chunks=v.chunk_count,
                    completed_chunks=v.completed_count,
                    pending_chunks=v.pending_count,
                    failed_chunks=v.failed_count,
                    has_file=has_file,
                )

                if health == "READY":
                    skipped_healthy += 1
                    self.clear_attempt_failure(v.id)
                    continue

                if health == "NO FILE":
                    # Document 14 scenario: missing file is skipped permanently without hot-loop
                    skipped_invalid += 1
                    continue

                # Check if file is permanently unreadable or errored
                if first_file and getattr(first_file, "processing_status", "") == "failed":
                    file_name = getattr(first_file, "file", None)
                    if not file_name or not getattr(file_name, "name", ""):
                        skipped_invalid += 1
                        continue

                # Check backoff state
                if self.is_backed_off(v.id):
                    skipped_backoff += 1
                    logger.info(
                        "[PWANIMATE-RECONCILER] Version %d is backed off due to repeated failures. Skipping.", v.id
                    )
                    continue

                # CASE 1: Missing Ingestion (0 chunks)
                if health == "NOT INGESTED":
                    missing_ingestion_found += 1
                    if ingest_dispatched >= self.max_ingest:
                        continue

                    if dry_run:
                        ingest_dispatched += 1
                        logger.info(
                            "[PWANIMATE-RECONCILER] [DRY RUN] Would queue Pwanimate ingestion document_id=%d version_id=%d reason=no_chunks",
                            v.document_id, v.id
                        )
                    else:
                        # Attempt per-version task lock
                        if not self.acquire_dispatch_lock(INGEST_TASK_LOCK_PREFIX, v.id):
                            skipped_locked += 1
                            continue

                        ingest_document_version.delay(v.id, force=False)
                        ingest_dispatched += 1
                        logger.info(
                            "[PWANIMATE-RECONCILER] Queued Pwanimate ingestion document_id=%d version_id=%d reason=no_chunks",
                            v.document_id, v.id
                        )

                # CASE 2: Incomplete Embeddings (pending or recoverable failed chunks)
                elif health in ("EMBEDDING INCOMPLETE", "FAILED"):
                    embedding_incomplete_found += 1
                    if embed_dispatched >= self.max_embed:
                        continue

                    if dry_run:
                        embed_dispatched += 1
                        logger.info(
                            "[PWANIMATE-RECONCILER] [DRY RUN] Would queue Pwanimate embedding recovery document_id=%d version_id=%d reason=pending_or_failed_embeddings",
                            v.document_id, v.id
                        )
                    else:
                        # Attempt per-version task lock
                        if not self.acquire_dispatch_lock(EMBED_TASK_LOCK_PREFIX, v.id):
                            skipped_locked += 1
                            continue

                        embed_document_version.delay(version_id=v.id)
                        embed_dispatched += 1
                        logger.info(
                            "[PWANIMATE-RECONCILER] Queued Pwanimate embedding recovery document_id=%d version_id=%d reason=pending_or_failed_embeddings",
                            v.document_id, v.id
                        )

            summary = {
                "status": "success",
                "dry_run": dry_run,
                "inspected_count": inspected_count,
                "missing_ingestion_found": missing_ingestion_found,
                "embedding_incomplete_found": embedding_incomplete_found,
                "ingest_dispatched": ingest_dispatched,
                "embed_dispatched": embed_dispatched,
                "skipped_healthy": skipped_healthy,
                "skipped_invalid": skipped_invalid,
                "skipped_locked": skipped_locked,
                "skipped_backoff": skipped_backoff,
            }

            logger.info(
                "[PWANIMATE-RECONCILER] Reconciliation completed. Inspected: %d, missing_ingest: %d, "
                "embed_incomplete: %d, ingest_dispatched: %d, embed_dispatched: %d, "
                "skipped_healthy: %d, skipped_invalid: %d, skipped_locked: %d, skipped_backoff: %d",
                inspected_count, missing_ingestion_found, embedding_incomplete_found,
                ingest_dispatched, embed_dispatched, skipped_healthy, skipped_invalid,
                skipped_locked, skipped_backoff
            )
            return summary

        finally:
            if not dry_run:
                cache.delete(GLOBAL_RECONCILIATION_LOCK_KEY)
