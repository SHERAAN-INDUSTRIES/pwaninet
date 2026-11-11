"""
Unit and integration tests for Pwanimate Phase 2F: Embedding Subsystem.
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from documents.models import Document, DocumentVersion, Category
from pwanimate.models import DocumentChunk
from pwanimate.ai.embeddings.base import (
    BaseEmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingConfigurationError,
    EmbeddingDimensionMismatchError,
)
from pwanimate.ai.embeddings.gemini import GeminiEmbeddingProvider
from pwanimate.ai.embeddings.mock import MockEmbeddingProvider
from pwanimate.ai.embeddings.factory import get_embedding_provider
from pwanimate.tasks.embeddings import embed_document_version, embed_document
from pwanimate.tasks.ingestion import ingest_document_version

User = get_user_model()


class EmbeddingProviderTestCase(TestCase):
    def test_mock_embedding_provider_deterministic_and_dimensions(self):
        provider = MockEmbeddingProvider(dimensions=768)
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.get_dimensions(), 768)
        self.assertEqual(provider.get_model_name(), "mock-embedding-768")

        texts = ["Operating systems manage hardware.", "Distributed consensus via Raft."]
        vectors = provider.embed_texts(texts)

        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 768)
        self.assertEqual(len(vectors[1]), 768)

        # Determinism check
        vectors_repeat = provider.embed_texts(texts)
        self.assertEqual(vectors[0], vectors_repeat[0])

        # Query embedding
        q_vec = provider.embed_query("query text")
        self.assertEqual(len(q_vec), 768)

    def test_gemini_provider_unconfigured_raises_error(self):
        provider = GeminiEmbeddingProvider(api_key="")
        self.assertFalse(provider.is_configured())
        with self.assertRaises(EmbeddingConfigurationError):
            provider.embed_texts(["Test text"])

    @patch("requests.post")
    def test_gemini_provider_successful_batch_embed(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [
                {"values": [0.01] * 768},
                {"values": [-0.02] * 768}
            ]
        }
        mock_post.return_value = mock_response

        provider = GeminiEmbeddingProvider(api_key="fake-test-key", dimensions=768)
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.get_model_name(), "gemini-embedding-2")

        vectors = provider.embed_texts(["First chunk", "Second chunk"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 768)
        self.assertEqual(len(vectors[1]), 768)

        # Verify endpoint, headers, and outputDimensionality
        call_url = mock_post.call_args[0][0]
        call_kwargs = mock_post.call_args[1]
        self.assertIn("models/gemini-embedding-2:batchEmbedContents", call_url)
        self.assertEqual(call_kwargs["headers"]["x-goog-api-key"], "fake-test-key")
        self.assertEqual(call_kwargs["json"]["requests"][0]["outputDimensionality"], 768)

    @patch("requests.post")
    def test_gemini_provider_dimension_mismatch_raises_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [
                {"values": [0.1] * 512}  # 512 instead of expected 768
            ]
        }
        mock_post.return_value = mock_response

        provider = GeminiEmbeddingProvider(api_key="fake-key", dimensions=768)
        with self.assertRaises(EmbeddingDimensionMismatchError):
            provider.embed_texts(["Chunk text"])

    @patch("requests.post")
    def test_gemini_provider_http_error_raises_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"error": {"message": "API key invalid"}}'
        mock_post.return_value = mock_response

        provider = GeminiEmbeddingProvider(api_key="invalid-key")
        with self.assertRaises(EmbeddingProviderError):
            provider.embed_texts(["Chunk text"])

    def test_factory_resolution(self):
        gemini = get_embedding_provider("gemini", api_key="test-key")
        self.assertIsInstance(gemini, GeminiEmbeddingProvider)

        mock_p = get_embedding_provider("mock")
        self.assertIsInstance(mock_p, MockEmbeddingProvider)

        with self.assertRaises(ValueError):
            get_embedding_provider("unsupported-provider")


class EmbeddingPersistenceAndCeleryTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='embed_tester',
            email='embed@example.com',
            password='Password123!'
        )
        self.category = Category.objects.create(
            code='lecture_notes',
            name='Lecture Notes',
            is_active=True
        )
        self.document = Document.objects.create(
            title="Computer Networks CSC312",
            slug="computer-networks-csc312",
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

        # Create sample pending chunks for Version 1
        self.chunk1 = DocumentChunk.objects.create(
            document=self.document,
            document_version=self.version_v1,
            chunk_index=0,
            content="Layer 1 Physical transmission of raw bits over communication channels.",
            chunk_type="paragraph",
            is_active=False,
            embedding_status="pending"
        )
        self.chunk2 = DocumentChunk.objects.create(
            document=self.document,
            document_version=self.version_v1,
            chunk_index=1,
            content="Layer 2 Data link framing, error detection, and medium access control.",
            chunk_type="paragraph",
            is_active=False,
            embedding_status="pending"
        )

    def test_successful_embedding_task_persistence_and_lifecycle(self):
        # Run embedding task using the deterministic mock provider
        result = embed_document_version(
            version_id=self.version_v1.id,
            provider_name='mock'
        )

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['embedded_count'], 2)
        self.assertEqual(result['dimensions'], 768)

        # Refresh from database
        self.chunk1.refresh_from_db()
        self.chunk2.refresh_from_db()

        # Lifecycle check: embedding_status='completed' and is_active=True (retrieval-ready)
        self.assertEqual(self.chunk1.embedding_status, 'completed')
        self.assertTrue(self.chunk1.is_active)
        self.assertEqual(self.chunk1.embedding_model, 'mock-embedding-768')
        self.assertIsNotNone(self.chunk1.embedding)
        self.assertEqual(len(self.chunk1.embedding), 768)

        self.assertEqual(self.chunk2.embedding_status, 'completed')
        self.assertTrue(self.chunk2.is_active)
        self.assertEqual(len(self.chunk2.embedding), 768)

    def test_idempotency_avoids_redundant_embedding(self):
        # First embedding
        embed_document_version(version_id=self.version_v1.id, provider_name='mock')

        # Second embedding without force -> up_to_date (0 embedded)
        res2 = embed_document_version(version_id=self.version_v1.id, provider_name='mock')
        self.assertEqual(res2['status'], 'up_to_date')
        self.assertEqual(res2['embedded_count'], 0)

        # Forced re-embedding -> re-embeds all 2 chunks
        res_forced = embed_document_version(version_id=self.version_v1.id, force=True, provider_name='mock')
        self.assertEqual(res_forced['status'], 'success')
        self.assertEqual(res_forced['embedded_count'], 2)

    def test_version_isolation(self):
        # Create a chunk on Version 2
        chunk_v2 = DocumentChunk.objects.create(
            document=self.document,
            document_version=self.version_v2,
            chunk_index=0,
            content="Version 2 OSI vs TCP/IP model comparison.",
            chunk_type="paragraph",
            is_active=False,
            embedding_status="pending"
        )

        # Embed only Version 2
        res_v2 = embed_document_version(version_id=self.version_v2.id, provider_name='mock')
        self.assertEqual(res_v2['status'], 'success')
        self.assertEqual(res_v2['embedded_count'], 1)

        # Version 2 chunk is completed; Version 1 chunks remain pending
        chunk_v2.refresh_from_db()
        self.chunk1.refresh_from_db()

        self.assertEqual(chunk_v2.embedding_status, 'completed')
        self.assertTrue(chunk_v2.is_active)

        self.assertEqual(self.chunk1.embedding_status, 'pending')
        self.assertFalse(self.chunk1.is_active)
        self.assertIsNone(self.chunk1.embedding)

    def test_failure_safety_unconfigured_provider(self):
        # With unconfigured provider, chunks must remain pending and inactive
        with override_settings(PWANIMATE_GEMINI_API_KEY=''):
            res = embed_document_version(version_id=self.version_v1.id, provider_name='gemini')
            self.assertEqual(res['status'], 'provider_unconfigured')
            self.assertEqual(res['model'], 'gemini-embedding-2')

            self.chunk1.refresh_from_db()
            self.assertEqual(self.chunk1.embedding_status, 'pending')
            self.assertFalse(self.chunk1.is_active)
            self.assertIsNone(self.chunk1.embedding)

    def test_convenience_embed_document_task(self):
        # Create chunk on Version 2 (which is latest)
        DocumentChunk.objects.create(
            document=self.document,
            document_version=self.version_v2,
            chunk_index=0,
            content="Network routing protocols: BGP and OSPF.",
            is_active=False,
            embedding_status="pending"
        )
        res = embed_document(document_id=self.document.id, provider_name='mock')
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['version_id'], self.version_v2.id)

    def test_ingestion_dispatches_embedding_task_downstream(self):
        with patch('pwanimate.tasks.embeddings.embed_document_version.delay') as mock_embed_delay:
            with patch('pwanimate.tasks.ingestion.extract_document') as mock_extract:
                from pwanimate.ingestion.types import ExtractedDocument, ExtractedElement, ElementType, LocationMetadata
                mock_extract.return_value = ExtractedDocument(
                    source_filename="test.txt",
                    format="txt",
                    elements=[
                        ExtractedElement(
                            element_type=ElementType.PARAGRAPH,
                            text="Sample test paragraph content.",
                            location=LocationMetadata(page_number=1)
                        )
                    ]
                )
                # Create a dummy file on v1
                from documents.models import DocumentFile
                import tempfile, os
                with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
                    tf.write(b"Sample test")
                    tpath = tf.name

                df = DocumentFile.objects.create(
                    document_version=self.version_v1,
                    original_filename="test.txt",
                    storage_path=tpath,
                    mime_type="text/plain",
                    extension="txt",
                    size_bytes=11,
                    uploaded_by=self.user
                )
                df.file.name = tpath
                df.save()

                with self.captureOnCommitCallbacks(execute=True):
                    res = ingest_document_version(version_id=self.version_v1.id, force=True)
                self.assertEqual(res['status'], 'success')

                # Confirm downstream dispatch of embed_document_version.delay
                mock_embed_delay.assert_called_once_with(version_id=self.version_v1.id)
                os.unlink(tpath)
