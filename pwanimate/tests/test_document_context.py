"""
Regression tests for the shared Spark document-context pipeline.

Covers the audit scenarios in SPARK_DOCUMENT_CONTEXT_AUDIT.md:

- Canonical identity: UUID vs integer document_id, legacy UUID-in-documentId,
  conflicting identifiers, invalid legacy fields, backward compatibility.
- Authorization: private/unavailable/draft/unindexed documents fail closed.
- Page semantics: active viewer page vs explicit "page N" vs document-wide.
- Retrieval: embedding-independent exact-page text, page priority ordering,
  version pinning, page bounds.
- Diagnostics: structured statuses and privacy-preserving warnings.
- Orchestration: shared pre-routing resolution feeding RAG and tool routes.
"""

import uuid
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase

from documents.models import Document, DocumentVersion, Category
from documents.documents.models import DocumentFile
from pwanimate.models import DocumentChunk
from pwanimate.ai.embeddings.mock import MockEmbeddingProvider
from pwanimate.ai.gateway import AIGateway, LLMRouter
from pwanimate.ai.providers import MockLLMProvider
from pwanimate.orchestrator import OrchestrationRequest, PwanimateOrchestrator
from pwanimate.orchestrator.document_context import (
    DocumentTurnContext,
    collect_document_identifiers,
    extract_page_intent,
    is_document_resource,
)
from pwanimate.orchestrator.service import _truncate_context_text
from pwanimate.retrieval import (
    RetrievalRequest,
    RetrievalResponse,
    UnifiedRetrievalService,
)
from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService

User = get_user_model()


# ---------------------------------------------------------------------------
# Pure unit tests (no database)
# ---------------------------------------------------------------------------

class PageIntentTests(SimpleTestCase):
    """Phase E: predictable active-page vs explicit-page semantics."""

    def test_explain_page_n_is_an_explicit_target(self):
        intent = extract_page_intent("Explain page 35 of the book")
        self.assertEqual(intent["explicit_pages"], [35])
        self.assertFalse(intent["document_wide"])

    def test_this_page_question_uses_viewer_page_only(self):
        intent = extract_page_intent("Explain the second equation on this page.")
        self.assertEqual(intent["explicit_pages"], [])
        self.assertTrue(intent["page_scoped"])
        self.assertFalse(intent["document_wide"])

    def test_comparison_collects_both_sides(self):
        intent = extract_page_intent("Compare this page with page 20")
        self.assertTrue(intent["comparison"])
        self.assertEqual(intent["explicit_pages"], [20])

    def test_incidental_page_mention_does_not_hijack_context(self):
        # "page 4" appears only as content being discussed, not a target.
        intent = extract_page_intent("On page 12 it mentions page 4 in passing")
        self.assertNotIn(4, intent["explicit_pages"])

    def test_document_wide_summary_disables_page_targets(self):
        intent = extract_page_intent("Summarize the entire document for me")
        self.assertTrue(intent["document_wide"])
        self.assertEqual(intent["explicit_pages"], [])

    def test_what_does_page_say_is_explicit(self):
        intent = extract_page_intent("What does page 7 say about mitosis?")
        self.assertEqual(intent["explicit_pages"], [7])


class IdentifierCollectionTests(SimpleTestCase):
    """Phase A: independent normalization of every identifier alias."""

    def test_uuid_inside_documentId_is_a_share_candidate(self):
        uid = str(uuid.uuid4())
        pks, uuids = collect_document_identifiers({"documentId": uid})
        self.assertEqual(pks, [])
        self.assertEqual([str(u) for u in uuids], [uid])

    def test_integer_document_id_is_a_pk_candidate(self):
        pks, uuids = collect_document_identifiers({"document_id": 42})
        self.assertEqual(pks, [42])
        self.assertEqual(uuids, [])

    def test_both_families_collected_independently(self):
        uid = str(uuid.uuid4())
        pks, uuids = collect_document_identifiers({
            "documentId": 7, "documentShareId": uid, "id": uid,
        })
        self.assertEqual(pks, [7])
        self.assertEqual(len(uuids), 1)

    def test_invalid_legacy_value_produces_no_candidates(self):
        pks, uuids = collect_document_identifiers({"documentId": "not-an-id"})
        self.assertEqual(pks, [])
        self.assertEqual(uuids, [])

    def test_legacy_id_field_uuid_supported(self):
        uid = str(uuid.uuid4())
        pks, uuids = collect_document_identifiers({"id": uid})
        self.assertEqual(pks, [])
        self.assertEqual(len(uuids), 1)


class ResourceKindTests(SimpleTestCase):

    def test_document_resource_detected(self):
        self.assertTrue(is_document_resource({"type": "document", "documentShareId": "x"}))
        self.assertTrue(is_document_resource({"previewType": "document"}))
        self.assertTrue(is_document_resource({"url": "/documents/document/abc/"}))

    def test_non_document_resources_rejected(self):
        self.assertFalse(is_document_resource({"type": "post", "postId": "p1"}))
        self.assertFalse(is_document_resource({"type": "attachment", "attachmentId": "a1"}))
        self.assertFalse(is_document_resource({"type": "profile", "username": "bob"}))
        self.assertFalse(is_document_resource({"type": "web", "url": "https://x.com"}))
        self.assertFalse(is_document_resource("not-a-dict"))
        self.assertFalse(is_document_resource(None))


class TruncationTests(SimpleTestCase):

    def test_short_text_untouched(self):
        self.assertEqual(_truncate_context_text("hello", 100), "hello")

    def test_truncation_prefers_sentence_boundary(self):
        text = "First sentence here. " + "x" * 200 + ". Tail."
        out = _truncate_context_text(text, 60)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 62)  # max_chars + ellipsis marker


# ---------------------------------------------------------------------------
# Database-backed resolution tests
# ---------------------------------------------------------------------------

class DocumentContextResolutionTests(TestCase):
    """Phase A/C/G: identity, authorization and diagnostics end-to-end."""

    def setUp(self):
        self.emb = MockEmbeddingProvider(dimensions=768)
        self.doc_service = DocumentSemanticRetrievalService(embedding_provider=self.emb)

        self.owner = User.objects.create_user(username="doc_owner", email="o@vasto.local")
        self.student = User.objects.create_user(username="student", email="s@vasto.local")
        self.other = User.objects.create_user(username="other", email="x@vasto.local")
        self.category = Category.objects.create(name="Notes", code="notes")

        self.doc_public = self._make_doc(
            "Public Doc", self.owner, visibility="public", status="ready", is_available=True,
        )
        self.v_public = self._make_version(self.doc_public, 1)
        self._make_file(self.v_public, page_count=40)
        self.chunk_p5 = self._make_chunk(self.doc_public, self.v_public, 0, "page five kernels", page=5)
        self.chunk_p12 = self._make_chunk(self.doc_public, self.v_public, 1, "page twelve virtual memory", page=12)
        self.chunk_p6 = self._make_chunk(self.doc_public, self.v_public, 2, "page six scheduling", page=6)

        self.doc_private = self._make_doc(
            "Private Doc", self.owner, visibility="private", status="ready", is_available=True,
        )
        self.v_private = self._make_version(self.doc_private, 1)
        self._make_chunk(self.doc_private, self.v_private, 0, "secret research", page=1)

        self.doc_unavailable = self._make_doc(
            "Unavailable Doc", self.owner, visibility="public", status="ready", is_available=False,
        )
        self.doc_draft = self._make_doc(
            "Draft Doc", self.owner, visibility="public", status="draft", is_available=True,
        )
        self.doc_unindexed = self._make_doc(
            "Scanned Doc", self.owner, visibility="public", status="ready", is_available=True,
        )
        self.doc_pending = self._make_doc(
            "Pending Embed Doc", self.owner, visibility="public", status="ready", is_available=True,
        )
        self.v_pending = self._make_version(self.doc_pending, 1)
        DocumentChunk.objects.create(
            document=self.doc_pending, document_version=self.v_pending, chunk_index=0,
            content="extracted but not yet embedded page text", chunk_type="paragraph",
            page_number=3, is_active=False, embedding_status="pending",
        )

    # -- fixture helpers ----------------------------------------------------

    def _make_doc(self, title, uploader, visibility="public", status="ready", is_available=True):
        return Document.objects.create(
            title=title, slug=title.lower().replace(" ", "-"), category=self.category,
            uploaded_by=uploader, status=status, is_available=is_available,
            visibility=visibility,
        )

    def _make_version(self, doc, number):
        return DocumentVersion.objects.create(
            document=doc, version_number=number, created_by=doc.uploaded_by, is_latest=True,
        )

    def _make_file(self, version, page_count=None):
        return DocumentFile.objects.create(
            document_version=version, uploaded_by=version.created_by,
            file=f"docs/{version.document_id}/f.pdf",
            original_filename="f.pdf", storage_path=f"docs/{version.document_id}/f.pdf",
            mime_type="application/pdf", extension="pdf", size_bytes=1000,
            page_count=page_count, processing_status="ready",
        )

    def _make_chunk(self, doc, version, index, content, page, active=True):
        return DocumentChunk.objects.create(
            document=doc, document_version=version, chunk_index=index, content=content,
            chunk_type="paragraph", page_number=page, page_end=page,
            is_active=active, embedding_status="completed" if active else "pending",
            embedding_model="mock-embedding-768" if active else "",
            embedding=self.emb.embed_query(content) if active else None,
        )

    def _res(self, **overrides):
        base = {"type": "document", "title": "Doc", "pageNumber": 5}
        base.update(overrides)
        return base

    # -- Phase A: identity ----------------------------------------------------

    def test_picker_style_uuid_in_documentId_resolves(self):
        """The dominant bug: picker sends share UUID inside documentId."""
        uid = str(self.doc_public.share_id)
        res = self._res(id=uid, documentId=uid, documentShareId=uid, pageNumber=5)
        ctx = DocumentTurnContext.resolve(self.student, [res], "what is on this page?")
        self.assertEqual(len(ctx.resolutions), 1)
        r = ctx.resolutions[0]
        self.assertEqual(r.status, "resolved")
        self.assertEqual(r.document_id, self.doc_public.id)
        self.assertEqual(r.viewer_page, 5)
        self.assertIn(self.doc_public.id, ctx.filter_document_ids)

    def test_integer_document_id_resolves(self):
        ctx = DocumentTurnContext.resolve(
            self.student, [self._res(document_id=self.doc_public.id)], "hi"
        )
        self.assertEqual(ctx.resolutions[0].status, "resolved")

    def test_both_ids_matching_resolve(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            document_id=self.doc_public.id,
            documentShareId=str(self.doc_public.share_id),
        )], "hi")
        self.assertEqual(ctx.resolutions[0].status, "resolved")

    def test_conflicting_ids_fail_closed(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            document_id=self.doc_public.id,
            documentShareId=str(self.doc_private.share_id),
        )], "hi")
        self.assertEqual(ctx.resolutions[0].status, "identifier_conflict")
        self.assertNotIn(self.doc_public.id, ctx.filter_document_ids)
        self.assertNotIn(self.doc_private.id, ctx.filter_document_ids)

    def test_invalid_legacy_id_does_not_suppress_valid_share(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentId="garbage!!", documentShareId=str(self.doc_public.share_id),
        )], "hi")
        self.assertEqual(ctx.resolutions[0].status, "resolved")

    def test_missing_document_is_inaccessible_not_dropped_silently(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(uuid.uuid4()),
        )], "hi")
        self.assertEqual(ctx.resolutions[0].status, "inaccessible")
        warnings = ctx.to_warnings()
        self.assertTrue(any(w["code"] == "document_inaccessible" for w in warnings))
        # Privacy: the message must not confirm the document exists.
        self.assertNotIn("Public Doc", warnings[0]["message"])

    # -- Phase G: lifecycle diagnostics ---------------------------------------

    def test_private_doc_of_other_user_is_inaccessible(self):
        ctx = DocumentTurnContext.resolve(self.other, [self._res(
            documentShareId=str(self.doc_private.share_id),
        )], "hi")
        self.assertEqual(ctx.resolutions[0].status, "inaccessible")
        # Owner CAN resolve it.
        ctx_owner = DocumentTurnContext.resolve(self.owner, [self._res(
            documentShareId=str(self.doc_private.share_id),
        )], "hi")
        self.assertEqual(ctx_owner.resolutions[0].status, "resolved")

    def test_unavailable_document_diagnosed(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_unavailable.share_id),
        )], "hi")
        self.assertEqual(ctx.resolutions[0].status, "unavailable")

    def test_draft_document_awaiting_processing(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_draft.share_id),
        )], "hi")
        self.assertEqual(ctx.resolutions[0].status, "awaiting_processing")

    def test_unindexed_document_diagnosed(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_unindexed.share_id),
        )], "hi")
        self.assertEqual(ctx.resolutions[0].status, "unindexed")

    def test_pending_embeddings_still_resolve_with_flag(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_pending.share_id), pageNumber=3,
        )], "what is on this page?")
        r = ctx.resolutions[0]
        self.assertEqual(r.status, "resolved")
        self.assertTrue(r.embeddings_pending)
        warnings = ctx.to_warnings()
        self.assertTrue(any(w["code"] == "embeddings_pending" for w in warnings))

    # -- Phase D/E: page evidence ---------------------------------------------

    def test_page_evidence_prioritizes_active_page(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_public.share_id), pageNumber=5,
        )], "what is on this page?")
        chunks = ctx.collect_page_evidence(self.student, "what is on this page?")
        pages = [c.metadata.get("page_number") for c in chunks]
        self.assertIn(5, pages)
        # Active page evidence precedes neighbor/other-page evidence.
        first_page = chunks[0].metadata.get("page_number")
        self.assertEqual(first_page, 5)

    def test_explicit_page_target_retrieved_alongside_viewer(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_public.share_id), pageNumber=5,
        )], "Explain page 12 please")
        r = ctx.resolutions[0]
        self.assertEqual(r.viewer_page, 5)
        self.assertIn(12, r.target_pages)
        chunks = ctx.collect_page_evidence(self.student, "Explain page 12 please")
        pages = {c.metadata.get("page_number") for c in chunks}
        self.assertIn(5, pages)
        self.assertIn(12, pages)

    def test_unembedded_page_text_is_retrievable(self):
        """Extracted text works while embeddings are pending (Phase D)."""
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_pending.share_id), pageNumber=3,
        )], "what is on this page?")
        chunks = ctx.collect_page_evidence(self.student, "what is on this page?")
        self.assertTrue(chunks)
        self.assertTrue(all(c.metadata.get("text_only") for c in chunks))
        # Semantic search still excludes the inactive chunk.
        results = self.doc_service.retrieve(RetrievalRequest(
            query="extracted but not yet embedded", user=self.student,
        ))
        self.assertNotIn(self.doc_pending.id, {r.metadata.get("document_id") for r in results})

    def test_page_out_of_bounds_warns(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_public.share_id), pageNumber=999,
        )], "what is on this page?")
        r = ctx.resolutions[0]
        # The document stays usable; only the bogus page is flagged.
        self.assertEqual(r.status, "resolved")
        self.assertEqual(r.detail, "page_out_of_bounds")
        warnings = ctx.to_warnings()
        self.assertTrue(any(w["code"] == "page_out_of_bounds" for w in warnings))

    def test_document_wide_query_uses_overview(self):
        ctx = DocumentTurnContext.resolve(self.student, [self._res(
            documentShareId=str(self.doc_public.share_id), pageNumber=5,
        )], "Summarize the entire document")
        self.assertTrue(ctx.resolutions[0].document_wide)
        chunks = ctx.collect_page_evidence(self.student, "Summarize the entire document")
        origins = {c.metadata.get("retrieval_origin") for c in chunks}
        self.assertIn("document_overview", origins)

    def test_multi_document_context_both_resolve(self):
        ctx = DocumentTurnContext.resolve(self.student, [
            self._res(documentShareId=str(self.doc_public.share_id), pageNumber=5),
            self._res(document_id=self.doc_pending.id, pageNumber=3),
        ], "compare these")
        resolved = [r for r in ctx.resolutions if r.resolved]
        self.assertEqual(len(resolved), 2)
        self.assertEqual(len(ctx.filter_document_ids), 2)

    # -- Phase C: orchestrator integration ------------------------------------

    def test_orchestrator_run_resolves_document_context_before_routing(self):
        mock_retrieval = MagicMock(spec=UnifiedRetrievalService)
        mock_retrieval.retrieve.return_value = RetrievalResponse(
            query="what is on this page?", results=[], total_count=0, execution_time_ms=1.0,
        )
        mock_provider = MockLLMProvider()
        gateway = AIGateway(router=LLMRouter(providers={"mock": mock_provider}, default_provider="mock"))
        orchestrator = PwanimateOrchestrator(
            retrieval_service=mock_retrieval, gateway=gateway,
        )
        req = OrchestrationRequest(
            query="what is on this page?",
            user=self.student,
            context_resources=[self._res(
                documentShareId=str(self.doc_public.share_id), pageNumber=5,
            )],
        )
        resp = orchestrator.run(req)
        self.assertIsNotNone(req.document_context)
        self.assertTrue(req.document_context.has_documents)
        # Semantic retrieval was scoped to the authorized document.
        call_req = mock_retrieval.retrieve.call_args[0][0]
        self.assertEqual(call_req.filters.get("document_ids"), [self.doc_public.id])
        self.assertIn("document_context", resp.metadata)

    def test_tool_route_receives_document_evidence(self):
        """A deterministic tool turn must not discard the selected document."""
        from pwanimate.tools import ToolRoute, ToolResult

        mock_provider = MockLLMProvider()
        gateway = AIGateway(router=LLMRouter(providers={"mock": mock_provider}, default_provider="mock"))
        orchestrator = PwanimateOrchestrator(gateway=gateway)

        # Force a deterministic tool route and stub its execution.
        tool_route = ToolRoute(
            tool_name="content_search",
            parameters={"query": "kernels", "content_type": "document"},
            confidence=1.0, matched_intent="test",
        )
        orchestrator.tool_router = MagicMock()
        orchestrator.tool_router.route.return_value = tool_route
        orchestrator.tool_registry = MagicMock()
        orchestrator.tool_registry.execute.return_value = ToolResult.ok([], count=0)

        req = OrchestrationRequest(
            query="search my documents for kernels",
            user=self.student,
            context_resources=[self._res(
                documentShareId=str(self.doc_public.share_id), pageNumber=5,
            )],
        )
        # The tool route is only taken when the agent path is bypassed;
        # context_resources present already guarantees that.
        resp = orchestrator.run(req)
        self.assertEqual(resp.metadata.get("intent"), "tool")
        # Document page evidence was merged into the grounded context package:
        # at minimum the resolution diagnostics travelled with the response.
        self.assertIn("document_context", resp.metadata)
        self.assertTrue(req.document_context.has_documents)

    def test_conversation_without_documents_unaffected(self):
        mock_provider = MockLLMProvider()
        gateway = AIGateway(router=LLMRouter(providers={"mock": mock_provider}, default_provider="mock"))
        orchestrator = PwanimateOrchestrator(gateway=gateway)
        req = OrchestrationRequest(query="Hello there!", user=self.student)
        resp = orchestrator.run(req)
        self.assertIsNone(req.document_context)
        self.assertEqual(resp.provider, "mock")
