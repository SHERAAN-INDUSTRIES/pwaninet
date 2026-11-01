"""
Tests for the explicit-mention boost in semantic retrieval.

A student asking "what does the <their own file> say" must get that file.
Before this, the academic boost (which favours programme-aligned material)
could bury a personal upload completely — a resume has no academic unit, so
it scored below the Java tutorial for a question naming it.
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from pwanimate.models import DocumentChunk
from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService
from pwanimate.retrieval.types import RetrievalRequest


class _FixedEmbeddingProvider:
    """Deterministic, content-derived embeddings.

    Content-derived (rather than one fixed vector) so the raw similarities
    differ and the boost is what decides the ranking — otherwise every
    candidate ties at the score cap and the order is arbitrary.
    """

    model_name = "multilingual-e5-base"

    def get_dimensions(self):
        return 768

    def get_model_name(self):
        return self.model_name

    def _vector(self, text):
        import hashlib

        vector = [0.0] * 768
        digest = hashlib.sha256((text or "").encode("utf-8")).digest()
        for index in range(768):
            vector[index] = digest[index % len(digest)] / 255.0
        return vector

    def embed_query(self, text):
        return self._vector(text)

    def embed_texts(self, texts, task_type="RETRIEVAL_DOCUMENT"):
        return [self._vector(text) for text in texts]


class ExplicitMentionBoostTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from documents.models import Category, Document, DocumentVersion

        cls.user = get_user_model().objects.create_user(
            username="mention_student", email="mention@vasto.local", password="testpassword123"
        )
        category, _ = Category.objects.get_or_create(code="notes", defaults={"name": "Notes"})

        for title, filename in (
            ("Ramsey Athman Shaban - Resume", "Ramsey Athman Shaban - Resume.pdf"),
            ("java_tutorial", "java_tutorial.pdf"),
            ("OOP-NOTES-final", "OOP-NOTES-final.pdf"),
        ):
            document = Document.objects.create(
                title=title,
                description="test",
                category=category,
                visibility="public",
                status="ready",
                is_available=True,
                uploaded_by=cls.user,
            )
            version = DocumentVersion.objects.create(
                document=document, version_number=1, is_latest=True, created_by=cls.user
            )
            provider = _FixedEmbeddingProvider()
            DocumentChunk.objects.create(
                document=document,
                document_version=version,
                chunk_index=0,
                content=f"{title}: python, django, sql, testing.",
                chunk_type="paragraph",
                page_number=1,
                embedding=provider._vector(f"{title}: python, django, sql, testing."),
                embedding_model="multilingual-e5-base",
                embedding_status="completed",
                is_active=True,
            )

    def _service(self):
        return DocumentSemanticRetrievalService(embedding_provider=_FixedEmbeddingProvider())

    def test_a_named_document_is_ranked_first(self):
        results = self._service().retrieve(
            RetrievalRequest(
                query="What does the Ramsey Athman Shaban resume say about his skills?",
                user=self.user,
                limit=5,
            )
        )
        self.assertTrue(results)
        self.assertIn("Ramsey", results[0].title)
        self.assertTrue(results[0].metadata.get("explicit_mention"))

    def test_a_named_tutorial_is_ranked_first(self):
        results = self._service().retrieve(
            RetrievalRequest(
                query="summarise the java tutorial",
                user=self.user,
                limit=5,
            )
        )
        self.assertTrue(results)
        self.assertIn("java_tutorial", results[0].title)

    def test_a_generic_query_boosts_nothing(self):
        """Without a document name, ranking must be left alone."""
        results = self._service().retrieve(
            RetrievalRequest(
                query="what are my skills?",
                user=self.user,
                limit=5,
            )
        )
        self.assertTrue(results)
        self.assertFalse(results[0].metadata.get("explicit_mention"))

    def test_generic_title_words_are_not_treated_as_names(self):
        results = self._service().retrieve(
            RetrievalRequest(
                query="show me the uploaded document",
                user=self.user,
                limit=5,
            )
        )
        self.assertFalse(any(r.metadata.get("explicit_mention") for r in results))
