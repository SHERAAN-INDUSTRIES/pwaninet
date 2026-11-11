"""
Unit and integration tests for Pwanimate Phase 2E: Async Celery Document Ingestion.
"""

import os
import tempfile
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from documents.models import Document, DocumentVersion, DocumentFile, Category
from pwanimate.models import DocumentChunk
from pwanimate.tasks.ingestion import ingest_document_version, ingest_document
from documents.tasks.processing import process_document

User = get_user_model()


class IngestionPipelineTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='ingestion_tester',
            email='tester@example.com',
            password='Password123!'
        )
        self.category = Category.objects.create(
            code='lecture_notes',
            name='Lecture Notes',
            is_active=True
        )
        self.document = Document.objects.create(
            title="Distributed Systems CSC311",
            slug="distributed-systems-csc311",
            category=self.category,
            uploaded_by=self.user,
            status='ready'
        )
        self.version_v1 = DocumentVersion.objects.create(
            document=self.document,
            version_number=1,
            created_by=self.user,
            is_latest=False
        )
        self.version_v2 = DocumentVersion.objects.create(
            document=self.document,
            version_number=2,
            created_by=self.user,
            is_latest=True
        )

        # Create physical test text files
        self.temp_dir = tempfile.TemporaryDirectory()

        # File for Version 1
        v1_path = os.path.join(self.temp_dir.name, "v1_notes.txt")
        with open(v1_path, "w", encoding="utf-8") as f:
            f.write("# Distributed Systems V1\n\nChapter 1 covers system models and communication primitives.")

        # File for Version 2
        v2_path = os.path.join(self.temp_dir.name, "v2_notes.txt")
        with open(v2_path, "w", encoding="utf-8") as f:
            f.write("# Distributed Systems V2\n\nChapter 1 covers revised consensus protocols including Raft.")

        # Attach DocumentFile records
        self.file_v1 = DocumentFile.objects.create(
            document_version=self.version_v1,
            original_filename="v1_notes.txt",
            storage_path=v1_path,
            mime_type="text/plain",
            extension="txt",
            size_bytes=os.path.getsize(v1_path),
            uploaded_by=self.user,
            processing_status='ready'
        )
        self.file_v1.file.name = v1_path
        self.file_v1.save()

        self.file_v2 = DocumentFile.objects.create(
            document_version=self.version_v2,
            original_filename="v2_notes.txt",
            storage_path=v2_path,
            mime_type="text/plain",
            extension="txt",
            size_bytes=os.path.getsize(v2_path),
            uploaded_by=self.user,
            processing_status='ready'
        )
        self.file_v2.file.name = v2_path
        self.file_v2.save()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_successful_ingestion_lifecycle(self):
        # Run ingestion for Version 1
        result = ingest_document_version(version_id=self.version_v1.id)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['version_id'], self.version_v1.id)
        self.assertGreater(result['chunk_count'], 0)

        chunks = list(self.version_v1.chunks.all())
        self.assertEqual(len(chunks), result['chunk_count'])

        # Verify lifecycle state: all chunks must be inactive and pending embedding
        for chunk in chunks:
            self.assertFalse(chunk.is_active)
            self.assertEqual(chunk.embedding_status, 'pending')
            self.assertEqual(chunk.document, self.document)
            self.assertEqual(chunk.document_version, self.version_v1)
            self.assertIsNotNone(chunk.content_hash)
            self.assertEqual(chunk.section_heading, "Distributed Systems V1")

    def test_version_isolation(self):
        # Ingest both versions
        res_v1 = ingest_document_version(version_id=self.version_v1.id)
        res_v2 = ingest_document_version(version_id=self.version_v2.id)

        self.assertEqual(res_v1['status'], 'success')
        self.assertEqual(res_v2['status'], 'success')

        v1_chunks = self.version_v1.chunks.all()
        v2_chunks = self.version_v2.chunks.all()

        self.assertGreater(v1_chunks.count(), 0)
        self.assertGreater(v2_chunks.count(), 0)

        # Version 1 contains V1 text, Version 2 contains V2 text
        self.assertTrue(any("communication primitives" in c.content for c in v1_chunks))
        self.assertFalse(any("Raft" in c.content for c in v1_chunks))

        self.assertTrue(any("Raft" in c.content for c in v2_chunks))
        self.assertFalse(any("communication primitives" in c.content for c in v2_chunks))

    def test_idempotency_prevents_duplicate_chunks(self):
        # First ingestion
        res1 = ingest_document_version(version_id=self.version_v1.id)
        self.assertEqual(res1['status'], 'success')
        initial_count = self.version_v1.chunks.count()

        # Second ingestion without force -> already_ingested
        res2 = ingest_document_version(version_id=self.version_v1.id)
        self.assertEqual(res2['status'], 'already_ingested')
        self.assertEqual(self.version_v1.chunks.count(), initial_count)

        # Ingestion with force=True -> purges and safely recreates
        res3 = ingest_document_version(version_id=self.version_v1.id, force=True)
        self.assertEqual(res3['status'], 'success')
        self.assertEqual(self.version_v1.chunks.count(), initial_count)

    def test_failure_safety_missing_version_or_files(self):
        # Non-existent version
        res_missing = ingest_document_version(version_id=999999)
        self.assertEqual(res_missing['status'], 'failed')
        self.assertEqual(res_missing['reason'], 'version_not_found')

        # Version with no files
        v_empty = DocumentVersion.objects.create(
            document=self.document,
            version_number=3,
            created_by=self.user,
            is_latest=False
        )
        res_no_files = ingest_document_version(version_id=v_empty.id)
        self.assertEqual(res_no_files['status'], 'skipped')
        self.assertEqual(res_no_files['reason'], 'no_files_found')
        self.assertEqual(v_empty.chunks.count(), 0)

    def test_convenience_ingest_document_task(self):
        # ingest_document delegates to the latest_version (which is v2)
        res = ingest_document(document_id=self.document.id)
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['version_id'], self.version_v2.id)
        self.assertGreater(self.version_v2.chunks.count(), 0)

    def test_downstream_dispatch_from_process_document(self):
        with patch('pwanimate.tasks.ingestion.ingest_document_version.delay') as mock_pwanimate_delay:
            with patch('documents.tasks.processing.process_file.delay'):
                with patch('documents.tasks.processing.extract_ocr_text_for_document', return_value="Sample OCR"):
                    process_document(self.document.id)

            # Confirm that process_document called ingest_document_version.delay(latest_version.id)
            mock_pwanimate_delay.assert_called_once_with(self.version_v2.id)

    def test_existing_chunks_resume_embedding_only_after_commit(self):
        ingest_document_version(version_id=self.version_v1.pk)
        ids = list(self.version_v1.chunks.values_list('pk', flat=True))
        with patch('pwanimate.tasks.embeddings.embed_document_version.delay') as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                result = ingest_document_version(version_id=self.version_v1.pk)
                dispatch.assert_not_called()
            dispatch.assert_called_once_with(version_id=self.version_v1.pk)
        self.assertEqual(result['status'], 'already_ingested')
        self.assertEqual(list(self.version_v1.chunks.values_list('pk', flat=True)), ids)

    def test_broker_failure_is_not_silently_swallowed(self):
        ingest_document_version(version_id=self.version_v1.pk)
        with patch('pwanimate.tasks.embeddings.embed_document_version.delay', side_effect=OSError('broker down')):
            with self.assertRaises(OSError):
                with self.captureOnCommitCallbacks(execute=True):
                    ingest_document_version(version_id=self.version_v1.pk)
        self.assertTrue(self.version_v1.chunks.exists())

    def test_retry_dispatch_is_not_scheduled_twice(self):
        from celery.exceptions import Retry
        with patch('pwanimate.tasks.embeddings.embed_document_version.delay', side_effect=OSError('broker down')):
            with patch.object(ingest_document_version, 'retry', side_effect=Retry('retrying')) as retry:
                with patch('pwanimate.tasks.ingestion.transaction.on_commit', side_effect=lambda callback: callback()):
                    with self.assertRaises(Retry):
                        ingest_document_version(version_id=self.version_v1.pk)
        self.assertEqual(retry.call_count, 1)
        self.assertTrue(self.version_v1.chunks.exists())

    def test_ingestion_includes_all_files_with_stable_source_provenance(self):
        first_file = self.version_v1.files.first()
        second = DocumentFile.objects.create(document_version=self.version_v1,
            original_filename='second.txt', extension='txt', mime_type='text/plain',
            storage_path=self.file_v2.storage_path if hasattr(self, 'file_v2') else first_file.storage_path,
            uploaded_by=self.user, size_bytes=10)
        second.file.name = first_file.file.name
        second.save(update_fields=['file'])
        with patch('pwanimate.tasks.embeddings.embed_document_version.delay'):
            result = ingest_document_version(version_id=self.version_v1.pk)
        self.assertEqual(result['status'], 'success')
        chunks = list(self.version_v1.chunks.order_by('chunk_index'))
        self.assertEqual({c.metadata['document_file_id'] for c in chunks}, {first_file.pk, second.pk})
        self.assertEqual([c.chunk_index for c in chunks], list(range(len(chunks))))

    def test_unreadable_sources_are_backed_off_after_repeated_failures(self):
        from pwanimate.ingestion.exceptions import CorruptFileError
        from pwanimate.services.reconciliation import DocumentReconciliationService
        with patch('pwanimate.tasks.ingestion.extract_document', side_effect=CorruptFileError('missing source')):
            for _ in range(3):
                result = ingest_document_version(version_id=self.version_v1.pk)
                self.assertEqual(result['status'], 'unreadable_file')
        self.assertTrue(DocumentReconciliationService().is_backed_off(self.version_v1.pk))
