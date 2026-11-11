"""PostgreSQL regressions for actual query, content and permission boundaries."""
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from documents.models import Document, DocumentVersion, Category
from pwanimate.models import DocumentChunk
from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService
from pwanimate.retrieval.services.unified_retrieval import UnifiedRetrievalService
from pwanimate.retrieval.types import RetrievalRequest, RetrievalMode
from pwanimate.ai.embeddings.mock import MockEmbeddingProvider
from pwanimate.ai.embeddings.base import EmbeddingTimeoutError


class RagReliabilityTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='rag-owner')
        self.student = get_user_model().objects.create_user(username='rag-student')
        self.provider = MockEmbeddingProvider(dimensions=768)
        self.service = DocumentSemanticRetrievalService(self.provider)
        self.category = Category.objects.create(code='rag-biology', name='Biology')
        self.document = Document.objects.create(title='Biology notes', slug='rag-biology',
            category=self.category, uploaded_by=self.owner, visibility='public', status='ready', is_available=True)
        self.version = DocumentVersion.objects.create(document=self.document, created_by=self.owner,
            version_number=1, is_latest=True)
        self.chunk = self.add_chunk(self.document, self.version, 'Photosynthesis uses sunlight and chlorophyll.')

    def add_chunk(self, doc, version, content):
        return DocumentChunk.objects.create(document=doc, document_version=version, chunk_index=0,
            content=content, page_number=1, is_active=True, embedding_status='completed',
            embedding_model=self.provider.get_model_name(), embedding=self.provider.embed_query(content))

    def test_embedding_outage_falls_back_to_chunk_content_with_citations(self):
        with patch.object(self.provider, 'embed_query', side_effect=EmbeddingTimeoutError('worker offline')):
            response = UnifiedRetrievalService(document_service=self.service).retrieve(
                RetrievalRequest(query='chlorophyll', user=self.student, mode=RetrievalMode.HYBRID))
        self.assertEqual(response.results[0].metadata['document_id'], self.document.pk)
        self.assertIn('chlorophyll', response.results[0].snippet)
        self.assertTrue(response.results[0].citation)
        self.assertTrue(response.source_metrics['document']['degraded'])

    def test_fallback_does_not_expose_private_or_old_version_chunks(self):
        private = Document.objects.create(title='Secret', slug='rag-secret', uploaded_by=self.owner,
            category=self.category, visibility='private', status='ready', is_available=True)
        v = DocumentVersion.objects.create(document=private, created_by=self.owner, version_number=1)
        self.add_chunk(private, v, 'Chlorophyll private information')
        DocumentVersion.objects.create(document=self.document, created_by=self.owner, version_number=2, is_latest=True)
        request = RetrievalRequest(query='chlorophyll', user=self.student)
        self.assertEqual(self.service.retrieve_lexical_chunks(request), [])
        self.assertEqual(self.service.retrieve(request), [])

    def test_selected_document_filter_applies_to_chunk_fallback(self):
        results = self.service.retrieve_lexical_chunks(RetrievalRequest(query='chlorophyll', user=self.student,
            filters={'document_ids': [self.document.pk + 10000]}))
        self.assertEqual(results, [])

    def test_uploaded_pdf_becomes_searchable_with_page_citation(self):
        import os, tempfile
        import pymupdf
        from documents.models import DocumentFile
        from pwanimate.tasks.ingestion import ingest_document_version
        from pwanimate.tasks.embeddings import embed_document_version
        self.chunk.delete()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'biology.pdf')
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((72, 72), 'Photosynthesis uses chlorophyll to turn sunlight into chemical energy.')
            pdf.save(path)
            pdf.close()
            source = DocumentFile.objects.create(document_version=self.version,
                original_filename='biology.pdf', storage_path=path, extension='pdf',
                mime_type='application/pdf', size_bytes=os.path.getsize(path), uploaded_by=self.owner)
            source.file.name = path
            source.save(update_fields=['file'])
            with patch('pwanimate.tasks.embeddings.embed_document_version.delay'):
                ingestion = ingest_document_version(version_id=self.version.pk)
            self.assertEqual(ingestion['status'], 'success')
            with patch('pwanimate.tasks.embeddings.get_embedding_provider', return_value=self.provider):
                embedding = embed_document_version(version_id=self.version.pk)
            self.assertGreater(embedding['embedded_count'], 0)
            results = self.service.retrieve(RetrievalRequest(query='chlorophyll', user=self.student,
                filters={'document_id': self.document.pk}))
            self.assertTrue(results)
            self.assertEqual(results[0].metadata['page_number'], 1)
            self.assertIn('chlorophyll', results[0].snippet)
            self.assertTrue(results[0].citation)
