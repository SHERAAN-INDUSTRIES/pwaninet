"""
Tests for the popularity-first re-embed driver (manage.py reembed_corpus).

Pins the two behaviours that matter:
- only chunks that are NOT (completed AND on the target model) are scheduled,
- documents are ordered by popularity (DocumentAnalytics.popularity_score)
  so the retrievable corpus recovers with the most useful documents first.
"""

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from documents.models import Category, Document, DocumentVersion
from documents.engagement.models import DocumentAnalytics
from pwanimate.models import DocumentChunk

TARGET = "multilingual-e5-base"


class ReembedCorpusTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="reembed_student", email="reembed@vasto.local", password="testpassword123"
        )
        self.category = Category.objects.create(name="Notes", code="notes")
        self.documents = []
        for index, popularity in enumerate((5.0, 50.0, 20.0)):
            document = Document.objects.create(
                title=f"Doc {index}",
                description="test doc",
                category=self.category,
                language="en",
                uploaded_by=self.user,
            )
            DocumentAnalytics.objects.create(
                document=document, popularity_score=popularity
            )
            version = DocumentVersion.objects.create(
                document=document, version_number=1, is_latest=True, created_by=self.user
            )
            for chunk_index in range(2):
                DocumentChunk.objects.create(
                    document=document,
                    document_version=version,
                    chunk_index=chunk_index,
                    content=f"chunk {chunk_index} of doc {index}",
                    embedding_status="completed",
                    embedding_model="gemini-embedding-2",
                    is_active=True,
                )
            self.documents.append(document)

    def _plan(self, **kwargs):
        out = StringIO()
        call_command("reembed_corpus", "--model", TARGET, "--dry-run", stdout=out, **kwargs)
        return out.getvalue()

    def test_dry_run_lists_only_documents_needing_migration(self):
        output = self._plan()
        self.assertIn("documents to migrate: 3", output)
        self.assertIn("chunks to re-embed  : 6", output)
        self.assertIn("Dry run", output)

    def test_documents_are_ordered_by_popularity(self):
        output = self._plan()
        # Popularity 50 (Doc 1) first, then 20 (Doc 2), then 5 (Doc 0).
        self.assertLess(output.index("Doc 1"), output.index("Doc 2"))
        self.assertLess(output.index("Doc 2"), output.index("Doc 0"))

    def test_already_migrated_chunks_are_skipped(self):
        chunk = DocumentChunk.objects.filter(
            document=self.documents[0], embedding_model="gemini-embedding-2"
        ).first()
        chunk.embedding_model = TARGET
        chunk.save(update_fields=["embedding_model"])
        output = self._plan()
        # Only the still-unmigrated chunk is counted: 2 + 2 + 1 = 5.
        self.assertIn("chunks to re-embed  : 5", output)
        # Doc 0 is still listed, but with a single chunk left to do.
        self.assertIn("Doc 0", output)

    def test_enqueues_the_celery_task_for_each_document(self):
        with mock.patch(
            "pwanimate.tasks.embeddings.embed_document_version.delay"
        ) as delay:
            call_command("reembed_corpus", "--model", TARGET, stdout=StringIO())
        self.assertEqual(delay.call_count, 3)
        enqueued_versions = {call.kwargs["version_id"] for call in delay.call_args_list}
        expected = {
            version.id
            for version in DocumentVersion.objects.filter(
                document__in=self.documents
            )
        }
        self.assertEqual(enqueued_versions, expected)

    def test_limit_caps_the_documents_processed(self):
        with mock.patch(
            "pwanimate.tasks.embeddings.embed_document_version.delay"
        ) as delay:
            call_command(
                "reembed_corpus", "--model", TARGET, "--limit", "1", stdout=StringIO()
            )
        self.assertEqual(delay.call_count, 1)
