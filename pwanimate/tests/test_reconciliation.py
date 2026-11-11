"""
Unit and integration tests for Pwanimate automatic document ingestion reconciliation.
"""

from unittest.mock import patch, MagicMock
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from documents.models import Document, DocumentVersion, DocumentFile, Category
from pwanimate.models import DocumentChunk
from pwanimate.services.reconciliation import (
    DocumentReconciliationService,
    classify_version_health,
    GLOBAL_RECONCILIATION_LOCK_KEY,
    INGEST_TASK_LOCK_PREFIX,
    EMBED_TASK_LOCK_PREFIX,
    FAIL_COUNT_KEY_PREFIX,
    MAX_CONSECUTIVE_FAILURES,
)
from pwanimate.tasks.reconciliation import reconcile_document_ingestion

User = get_user_model()


@override_settings(PWANIMATE_EMBEDDING_PROVIDER='mock')
class DocumentReconciliationTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="reconcile_tester",
            email="reconcile@example.com",
            password="Password123!",
        )
        self.category = Category.objects.create(
            code="cs_reconcile",
            name="Computer Science Reconcile",
            is_active=True,
        )

        # 1. Eligible for missing ingestion: 0 chunks + valid file
        self.doc_missing = Document.objects.create(
            title="Missing Ingestion Document",
            slug="missing-ingestion-doc",
            category=self.category,
            uploaded_by=self.user,
            status="ready",
        )
        self.v_missing = DocumentVersion.objects.create(
            document=self.doc_missing,
            version_number=1,
            created_by=self.user,
            is_latest=True,
        )
        self.file_missing = DocumentFile.objects.create(
            document_version=self.v_missing,
            original_filename="missing_doc.pdf",
            extension="pdf",
            mime_type="application/pdf",
            storage_path="documents/missing_doc.pdf",
            storage_provider="local",
            processing_status="ready",
            processing_error="",
            size_bytes=1024,
            uploaded_by=self.user,
        )

        # 2. Healthy document: all chunks completed and active
        self.doc_healthy = Document.objects.create(
            title="Healthy Document",
            slug="healthy-doc",
            category=self.category,
            uploaded_by=self.user,
            status="ready",
        )
        self.v_healthy = DocumentVersion.objects.create(
            document=self.doc_healthy,
            version_number=1,
            created_by=self.user,
            is_latest=True,
        )
        self.file_healthy = DocumentFile.objects.create(
            document_version=self.v_healthy,
            original_filename="healthy.pdf",
            extension="pdf",
            mime_type="application/pdf",
            storage_path="documents/healthy.pdf",
            storage_provider="local",
            processing_status="ready",
            processing_error="",
            size_bytes=2048,
            uploaded_by=self.user,
        )
        self.chunk_healthy = DocumentChunk.objects.create(
            document=self.doc_healthy,
            document_version=self.v_healthy,
            chunk_index=0,
            content="Healthy chunk with valid vector embedding.",
            content_hash="hash_h1",
            chunk_type="paragraph",
            page_number=1,
            page_end=1,
            section_heading="Overview",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=[0.01] * 768,
        )

        # 3. Incomplete document: pending chunks
        self.doc_pending = Document.objects.create(
            title="Pending Embedding Document",
            slug="pending-doc",
            category=self.category,
            uploaded_by=self.user,
            status="ready",
        )
        self.v_pending = DocumentVersion.objects.create(
            document=self.doc_pending,
            version_number=1,
            created_by=self.user,
            is_latest=True,
        )
        self.file_pending = DocumentFile.objects.create(
            document_version=self.v_pending,
            original_filename="pending.pdf",
            extension="pdf",
            mime_type="application/pdf",
            storage_path="documents/pending.pdf",
            storage_provider="local",
            processing_status="ready",
            processing_error="",
            size_bytes=4096,
            uploaded_by=self.user,
        )
        self.chunk_pending = DocumentChunk.objects.create(
            document=self.doc_pending,
            document_version=self.v_pending,
            chunk_index=0,
            content="Chunk awaiting embedding generation.",
            content_hash="hash_p1",
            chunk_type="paragraph",
            page_number=1,
            page_end=1,
            section_heading="Intro",
            is_active=False,
            embedding_status="pending",
            embedding_model="",
            embedding=None,
        )

        # 4. Incomplete document: recoverable failed chunks
        self.doc_failed = Document.objects.create(
            title="Failed Embedding Document",
            slug="failed-doc",
            category=self.category,
            uploaded_by=self.user,
            status="ready",
        )
        self.v_failed = DocumentVersion.objects.create(
            document=self.doc_failed,
            version_number=1,
            created_by=self.user,
            is_latest=True,
        )
        self.file_failed = DocumentFile.objects.create(
            document_version=self.v_failed,
            original_filename="failed.pdf",
            extension="pdf",
            mime_type="application/pdf",
            storage_path="documents/failed.pdf",
            storage_provider="local",
            processing_status="ready",
            processing_error="",
            size_bytes=4096,
            uploaded_by=self.user,
        )
        self.chunk_failed = DocumentChunk.objects.create(
            document=self.doc_failed,
            document_version=self.v_failed,
            chunk_index=0,
            content="Chunk that failed embedding due to transient timeout.",
            content_hash="hash_f1",
            chunk_type="paragraph",
            page_number=1,
            page_end=1,
            section_heading="Intro",
            is_active=False,
            embedding_status="failed",
            embedding_model="",
            embedding=None,
        )

        # 5. Invalid document: no source file attached (Document 14 scenario)
        self.doc_orphan = Document.objects.create(
            title="Orphan Document With No File",
            slug="orphan-doc",
            category=self.category,
            uploaded_by=self.user,
            status="ready",
        )
        self.v_orphan = DocumentVersion.objects.create(
            document=self.doc_orphan,
            version_number=1,
            created_by=self.user,
            is_latest=True,
        )

    def tearDown(self):
        cache.clear()

    @patch("pwanimate.tasks.embeddings.embed_document_version.delay")
    @patch("pwanimate.tasks.ingestion.ingest_document_version.delay")
    def test_reconciliation_detects_and_dispatches_missing_and_incomplete(
        self, mock_ingest_delay, mock_embed_delay
    ):
        service = DocumentReconciliationService()
        result = service.reconcile(dry_run=False)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["inspected_count"], 5)
        self.assertEqual(result["missing_ingestion_found"], 1)
        self.assertEqual(result["embedding_incomplete_found"], 2)  # pending + failed

        # Missing ingestion queued
        mock_ingest_delay.assert_called_once_with(self.v_missing.id, force=False)

        # Embedding recovery queued for pending and failed
        expected_embed_calls = [self.v_pending.id, self.v_failed.id]
        actual_embed_calls = [call.kwargs.get("version_id") for call in mock_embed_delay.call_args_list]
        self.assertCountEqual(actual_embed_calls, expected_embed_calls)

        # Healthy and orphan skipped
        self.assertEqual(result["skipped_healthy"], 1)
        self.assertEqual(result["skipped_invalid"], 1)

    @patch("pwanimate.tasks.embeddings.embed_document_version.delay")
    @patch("pwanimate.tasks.ingestion.ingest_document_version.delay")
    def test_reconciliation_dry_run_dispatches_nothing(
        self, mock_ingest_delay, mock_embed_delay
    ):
        service = DocumentReconciliationService()
        result = service.reconcile(dry_run=True)

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["ingest_dispatched"], 1)
        self.assertEqual(result["embed_dispatched"], 2)

        # Zero actual tasks queued
        mock_ingest_delay.assert_not_called()
        mock_embed_delay.assert_not_called()

        # Zero locks set
        self.assertIsNone(cache.get(f"{INGEST_TASK_LOCK_PREFIX}:{self.v_missing.id}"))
        self.assertIsNone(cache.get(f"{EMBED_TASK_LOCK_PREFIX}:{self.v_pending.id}"))

    @patch("pwanimate.tasks.embeddings.embed_document_version.delay")
    @patch("pwanimate.tasks.ingestion.ingest_document_version.delay")
    def test_duplicate_dispatch_lock_prevents_requeuing(
        self, mock_ingest_delay, mock_embed_delay
    ):
        service = DocumentReconciliationService()

        # First run: acquires locks and dispatches
        res1 = service.reconcile(dry_run=False)
        self.assertEqual(res1["ingest_dispatched"], 1)
        self.assertEqual(res1["embed_dispatched"], 2)
        self.assertEqual(res1["skipped_locked"], 0)

        # Verify dispatch locks are present in cache
        self.assertIsNotNone(cache.get(f"{INGEST_TASK_LOCK_PREFIX}:{self.v_missing.id}"))
        self.assertIsNotNone(cache.get(f"{EMBED_TASK_LOCK_PREFIX}:{self.v_pending.id}"))
        self.assertIsNotNone(cache.get(f"{EMBED_TASK_LOCK_PREFIX}:{self.v_failed.id}"))

        # Reset mocks
        mock_ingest_delay.reset_mock()
        mock_embed_delay.reset_mock()

        # Second run while locks are held: skips locked tasks
        res2 = service.reconcile(dry_run=False)
        self.assertEqual(res2["ingest_dispatched"], 0)
        self.assertEqual(res2["embed_dispatched"], 0)
        self.assertEqual(res2["skipped_locked"], 3)
        mock_ingest_delay.assert_not_called()
        mock_embed_delay.assert_not_called()

    @patch("pwanimate.tasks.ingestion.ingest_document_version.delay")
    def test_global_lock_prevents_concurrent_reconciliation(self, mock_ingest_delay):
        # Simulate active reconciliation run
        cache.set(GLOBAL_RECONCILIATION_LOCK_KEY, "1", timeout=300)

        service = DocumentReconciliationService()
        result = service.reconcile(dry_run=False)

        self.assertEqual(result["status"], "skipped_locked")
        self.assertEqual(result["reason"], "concurrent_run_active")
        mock_ingest_delay.assert_not_called()

    @patch("pwanimate.tasks.embeddings.embed_document_version.delay")
    @patch("pwanimate.tasks.ingestion.ingest_document_version.delay")
    def test_batch_limits_respected(self, mock_ingest_delay, mock_embed_delay):
        # Service configured with max_ingest=0, max_embed=1
        service = DocumentReconciliationService(max_ingest=0, max_embed=1)
        result = service.reconcile(dry_run=False)

        self.assertEqual(result["ingest_dispatched"], 0)
        self.assertEqual(result["embed_dispatched"], 1)
        mock_ingest_delay.assert_not_called()
        self.assertEqual(mock_embed_delay.call_count, 1)

    @patch("pwanimate.tasks.ingestion.ingest_document_version.delay")
    def test_hot_loop_backoff_protection(self, mock_ingest_delay):
        service = DocumentReconciliationService()

        # Simulate 3 consecutive failures recorded for this version
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            service.record_attempt_failure(self.v_missing.id)

        self.assertTrue(service.is_backed_off(self.v_missing.id))

        result = service.reconcile(dry_run=False)
        self.assertEqual(result["skipped_backoff"], 1)
        self.assertEqual(result["ingest_dispatched"], 0)
        mock_ingest_delay.assert_not_called()

    @patch("pwanimate.services.reconciliation.DocumentReconciliationService.reconcile")
    def test_celery_reconciliation_task_wrapper(self, mock_reconcile):
        mock_reconcile.return_value = {"status": "success", "inspected_count": 5}

        res = reconcile_document_ingestion()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["inspected_count"], 5)
        mock_reconcile.assert_called_once_with(dry_run=False)

    def test_celery_beat_schedule_configuration(self):
        beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        self.assertIn("pwanimate-reconcile-document-ingestion", beat_schedule)

        config = beat_schedule["pwanimate-reconcile-document-ingestion"]
        self.assertEqual(config["task"], "pwanimate.tasks.reconciliation.reconcile_document_ingestion")
        self.assertEqual(config["schedule"], 600.0)
        self.assertEqual(config["options"]["queue"], "docs_queue")

    def test_completed_but_unusable_vectors_are_recovered(self):
        self.chunk_healthy.is_active = False
        self.chunk_healthy.save(update_fields=['is_active'])
        result = DocumentReconciliationService().reconcile(dry_run=True)
        self.assertGreaterEqual(result['embedding_incomplete_found'], 3)

    def test_old_model_is_not_classified_as_ready(self):
        self.chunk_healthy.embedding_model = 'retired-model'
        self.chunk_healthy.save(update_fields=['embedding_model'])
        version = DocumentReconciliationService().get_annotated_versions_queryset(version_id=self.v_healthy.pk).get()
        self.assertEqual(version.completed_count, 0)
