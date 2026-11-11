"""
Comprehensive unit and integration test suite for Pwanimate Phase 3:
Unified Retrieval Architecture.
"""

import uuid
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.contrib.auth import get_user_model

from documents.models import Document, DocumentVersion, Category
from documents.academic.models import (
    Faculty, School, Department, Programme, AcademicUnit,
    AcademicLevel, AcademicYear, Semester, ProgrammeUnit
)
from documents.documents.models import DocumentAcademicUnit
from posts.models import Post, HiddenPost, AuthorPreference
from groups.models import Group, Membership, MembershipStatus, PostVisibility
from pwanimate.models import DocumentChunk
from pwanimate.ai.embeddings.mock import MockEmbeddingProvider
from pwanimate.retrieval.types import (
    RetrievalRequest,
    RetrievalResult,
    RetrievalResponse,
    SourceType,
    RetrievalMode,
)
from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService
from pwanimate.retrieval.services.unified_retrieval import UnifiedRetrievalService
from pwanimate.retrieval.adapters.pwaninet_search import pwaninetSearchAdapter

User = get_user_model()


class RetrievalTestCase(TestCase):
    def setUp(self):
        self.mock_provider = MockEmbeddingProvider(dimensions=768)
        self.doc_service = DocumentSemanticRetrievalService(embedding_provider=self.mock_provider)
        self.search_adapter = pwaninetSearchAdapter()
        self.unified_service = UnifiedRetrievalService(
            document_service=self.doc_service,
            search_adapter=self.search_adapter,
        )

        # Users
        self.uploader = User.objects.create_user(
            username='uploader_user',
            email='uploader@vasto.local',
            first_name='Alice',
            last_name='Uploader'
        )
        self.student_cs = User.objects.create_user(
            username='cs_student',
            email='cs@vasto.local',
            first_name='Bob',
            last_name='Coder'
        )
        self.student_bio = User.objects.create_user(
            username='bio_student',
            email='bio@vasto.local',
            first_name='Charlie',
            last_name='Darwin'
        )
        self.president_user = User.objects.create_user(
            username='pres_user',
            email='pres@vasto.local',
            global_role='PRESIDENT'
        )
        self.staff_user = User.objects.create_user(
            username='staff_user',
            email='staff@vasto.local',
            is_staff=True
        )

        # Academic structure
        self.faculty = Faculty.objects.create(name="Faculty of Science", code="FOS")
        self.school = School.objects.create(name="School of Pure and Applied Sciences", code="SPAS", faculty=self.faculty)
        self.dept = Department.objects.create(name="Computing and Information Technology", code="CIT", school=self.school)
        self.prog_cs = Programme.objects.create(name="BSc Computer Science", code="BSCS", department=self.dept, degree_type="Bachelor", duration_years=4)
        self.prog_bio = Programme.objects.create(name="BSc Biochemistry", code="BSBC", department=self.dept, degree_type="Bachelor", duration_years=4)

        self.student_cs.programme = self.prog_cs
        self.student_cs.save()

        self.student_bio.programme = self.prog_bio
        self.student_bio.save()

        self.unit_os = AcademicUnit.objects.create(name="Operating Systems", code="CSC211")
        self.acad_year = AcademicYear.objects.create(name="2025/2026", code="2025/2026", start_date="2025-09-01", end_date="2026-06-30")
        self.semester = Semester.objects.create(academic_year=self.acad_year, number=1, start_date="2025-09-01", end_date="2026-01-31")
        self.level_2 = AcademicLevel.objects.create(level=2, name="Year 2")

        self.prog_unit = ProgrammeUnit.objects.create(
            programme=self.prog_cs,
            academic_unit=self.unit_os,
            academic_level=self.level_2,
            semester=self.semester
        )

        # Document category
        self.category = Category.objects.create(name="Lecture Notes", code="lecture_notes")

        # 1. Public Ready Document
        self.doc_public = Document.objects.create(
            title="Operating Systems Concepts",
            slug="operating-systems-concepts",
            category=self.category,
            uploaded_by=self.uploader,
            status='ready',
            is_available=True,
            visibility='public'
        )
        self.v_public = DocumentVersion.objects.create(
            document=self.doc_public,
            version_number=1,
            created_by=self.uploader,
            is_latest=True
        )
        self.chunk_public_1 = DocumentChunk.objects.create(
            document=self.doc_public,
            document_version=self.v_public,
            chunk_index=0,
            content="Processes and threads execution in modern operating systems kernel.",
            chunk_type="paragraph",
            page_number=5,
            section_heading="Process Management",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Processes and threads execution in modern operating systems kernel.")
        )
        self.chunk_public_2 = DocumentChunk.objects.create(
            document=self.doc_public,
            document_version=self.v_public,
            chunk_index=1,
            content="Virtual memory paging and segmentation mechanisms for RAM allocation.",
            chunk_type="paragraph",
            page_number=12,
            section_heading="Memory Management",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Virtual memory paging and segmentation mechanisms for RAM allocation.")
        )

        # 2. Private Document (Uploaded by Alice)
        self.doc_private = Document.objects.create(
            title="Alice Private Research Draft",
            slug="alice-private-research-draft",
            category=self.category,
            uploaded_by=self.uploader,
            status='ready',
            is_available=True,
            visibility='private'
        )
        self.v_private = DocumentVersion.objects.create(
            document=self.doc_private,
            version_number=1,
            created_by=self.uploader,
            is_latest=True
        )
        self.chunk_private = DocumentChunk.objects.create(
            document=self.doc_private,
            document_version=self.v_private,
            chunk_index=0,
            content="Confidential quantum computing research findings and proprietary algorithms.",
            chunk_type="paragraph",
            page_number=1,
            section_heading="Confidential Quantum Draft",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Confidential quantum computing research findings and proprietary algorithms.")
        )

        # 3. Restricted Document (Restricted to Computer Science via ProgrammeUnit)
        self.doc_restricted = Document.objects.create(
            title="Advanced Algorithms Exam Preparation",
            slug="advanced-algorithms-exam-preparation",
            category=self.category,
            uploaded_by=self.uploader,
            status='ready',
            is_available=True,
            visibility='restricted'
        )
        DocumentAcademicUnit.objects.create(
            document=self.doc_restricted,
            academic_unit=self.unit_os,
            academic_level=self.level_2,
            semester=self.semester,
            academic_year=self.acad_year
        )
        self.v_restricted = DocumentVersion.objects.create(
            document=self.doc_restricted,
            version_number=1,
            created_by=self.uploader,
            is_latest=True
        )
        self.chunk_restricted = DocumentChunk.objects.create(
            document=self.doc_restricted,
            document_version=self.v_restricted,
            chunk_index=0,
            content="Dynamic programming and Bellman-Ford algorithm questions for CSC211.",
            chunk_type="slide",
            slide_number=4,
            section_heading="Exam Revision",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Dynamic programming and Bellman-Ford algorithm questions for CSC211.")
        )

        # 4. Unavailable Document (is_available=False)
        self.doc_unavailable = Document.objects.create(
            title="Unavailable Historical Archive",
            slug="unavailable-historical-archive",
            category=self.category,
            uploaded_by=self.uploader,
            status='ready',
            is_available=False,
            visibility='public'
        )
        self.v_unavailable = DocumentVersion.objects.create(
            document=self.doc_unavailable,
            version_number=1,
            created_by=self.uploader,
            is_latest=True
        )
        self.chunk_unavailable = DocumentChunk.objects.create(
            document=self.doc_unavailable,
            document_version=self.v_unavailable,
            chunk_index=0,
            content="Archived historical records that should not be retrieved.",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Archived historical records that should not be retrieved.")
        )

        # 5. Draft Document (status='draft')
        self.doc_draft = Document.objects.create(
            title="Draft Operating Systems Syllabus",
            slug="draft-operating-systems-syllabus",
            category=self.category,
            uploaded_by=self.uploader,
            status='draft',
            is_available=True,
            visibility='public'
        )
        self.v_draft = DocumentVersion.objects.create(
            document=self.doc_draft,
            version_number=1,
            created_by=self.uploader,
            is_latest=True
        )
        self.chunk_draft = DocumentChunk.objects.create(
            document=self.doc_draft,
            document_version=self.v_draft,
            chunk_index=0,
            content="Draft notes for operating systems course outline.",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Draft notes for operating systems course outline.")
        )

        # 6. Inactive Chunk (Older version chunk with is_active=False)
        self.chunk_inactive = DocumentChunk.objects.create(
            document=self.doc_public,
            document_version=self.v_public,
            chunk_index=99,
            content="Old obsolete operating systems notes that was deactivated.",
            is_active=False,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Old obsolete operating systems notes that was deactivated.")
        )

        # 7. Chunks with incompatible embedding model
        self.chunk_wrong_model = DocumentChunk.objects.create(
            document=self.doc_public,
            document_version=self.v_public,
            chunk_index=98,
            content="Incompatible model vector chunk.",
            is_active=True,
            embedding_status="completed",
            embedding_model="legacy-model-v0",
            embedding=self.mock_provider.embed_query("Incompatible model vector chunk.")
        )

    # -------------------------------------------------------------------------
    # Authorization & Candidate Filtering Tests
    # -------------------------------------------------------------------------
    def test_anonymous_user_can_only_retrieve_public_ready_available_documents(self):
        req = RetrievalRequest(query="Processes and threads in operating systems", user=None)
        results = self.doc_service.retrieve(req)

        self.assertGreater(len(results), 0)
        returned_doc_ids = {r.metadata["document_id"] for r in results}
        self.assertIn(self.doc_public.id, returned_doc_ids)
        self.assertNotIn(self.doc_private.id, returned_doc_ids)
        self.assertNotIn(self.doc_restricted.id, returned_doc_ids)
        self.assertNotIn(self.doc_unavailable.id, returned_doc_ids)
        self.assertNotIn(self.doc_draft.id, returned_doc_ids)

    def test_uploader_can_retrieve_their_own_private_document(self):
        req = RetrievalRequest(
            query="quantum computing research algorithms",
            user=self.uploader
        )
        results = self.doc_service.retrieve(req)
        returned_doc_ids = {r.metadata["document_id"] for r in results}
        self.assertIn(self.doc_private.id, returned_doc_ids)

    def test_other_student_cannot_retrieve_private_document(self):
        req = RetrievalRequest(
            query="quantum computing research algorithms",
            user=self.student_cs
        )
        results = self.doc_service.retrieve(req)
        returned_doc_ids = {r.metadata["document_id"] for r in results}
        self.assertNotIn(self.doc_private.id, returned_doc_ids)

    def test_staff_and_president_can_retrieve_all_ready_available_documents(self):
        # Staff
        req_staff = RetrievalRequest(query="quantum computing algorithms", user=self.staff_user)
        results_staff = self.doc_service.retrieve(req_staff)
        self.assertIn(self.doc_private.id, {r.metadata["document_id"] for r in results_staff})

        # President
        req_pres = RetrievalRequest(query="quantum computing algorithms", user=self.president_user)
        results_pres = self.doc_service.retrieve(req_pres)
        self.assertIn(self.doc_private.id, {r.metadata["document_id"] for r in results_pres})

    def test_restricted_document_accessible_only_to_enrolled_programme_student(self):
        # CS student enrolled in programme with unit CSC211
        req_cs = RetrievalRequest(query="Bellman-Ford dynamic programming CSC211", user=self.student_cs)
        results_cs = self.doc_service.retrieve(req_cs)
        self.assertIn(self.doc_restricted.id, {r.metadata["document_id"] for r in results_cs})

        # Biochemistry student not enrolled in CS programme
        req_bio = RetrievalRequest(query="Bellman-Ford dynamic programming CSC211", user=self.student_bio)
        results_bio = self.doc_service.retrieve(req_bio)
        self.assertNotIn(self.doc_restricted.id, {r.metadata["document_id"] for r in results_bio})

    def test_unavailable_and_draft_documents_are_never_retrieved(self):
        req = RetrievalRequest(query="Historical archive syllabus", user=self.uploader)
        results = self.doc_service.retrieve(req)
        returned_doc_ids = {r.metadata["document_id"] for r in results}
        self.assertNotIn(self.doc_unavailable.id, returned_doc_ids)
        self.assertNotIn(self.doc_draft.id, returned_doc_ids)

    # -------------------------------------------------------------------------
    # Semantic Retrieval & Ranking Tests
    # -------------------------------------------------------------------------
    def test_semantic_ranking_returns_most_relevant_chunk_first(self):
        query = "Processes and threads execution in modern operating systems kernel."
        req = RetrievalRequest(query=query, user=self.student_cs, limit=5)
        results = self.doc_service.retrieve(req)

        self.assertGreater(len(results), 0)
        top_result = results[0]
        # Chunk 1 matches the query exactly in content
        self.assertEqual(top_result.object_id, self.chunk_public_1.id)
        self.assertAlmostEqual(top_result.score, 1.0, delta=0.01)

    def test_top_k_limit_is_respected(self):
        req = RetrievalRequest(query="operating systems memory processes", user=None, limit=1)
        results = self.doc_service.retrieve(req)
        self.assertEqual(len(results), 1)

    def test_inactive_and_incompatible_model_chunks_excluded(self):
        req = RetrievalRequest(query="obsolete notes incompatible model", user=None)
        results = self.doc_service.retrieve(req)
        returned_chunk_ids = {r.object_id for r in results}
        self.assertNotIn(self.chunk_inactive.id, returned_chunk_ids)
        self.assertNotIn(self.chunk_wrong_model.id, returned_chunk_ids)

    # -------------------------------------------------------------------------
    # Result Contract, Citations & URLs
    # -------------------------------------------------------------------------
    def test_result_contract_fields_and_citation_generation(self):
        req = RetrievalRequest(query="Processes and threads execution in modern operating systems kernel.", user=None, limit=1)
        results = self.doc_service.retrieve(req)
        self.assertEqual(len(results), 1)
        res = results[0]

        self.assertEqual(res.source, SourceType.DOCUMENT)
        self.assertEqual(res.title, "Operating Systems Concepts")
        self.assertEqual(res.url, f"/documents/document/{self.doc_public.share_id}/")
        self.assertIn("v1", res.citation)
        self.assertIn("p. 5", res.citation)
        self.assertIn("Process Management", res.citation)
        self.assertEqual(res.metadata["page_number"], 5)
        self.assertEqual(res.metadata["version_number"], 1)

        # Check serialization
        d = res.to_dict()
        self.assertEqual(d["source"], "document")
        self.assertEqual(d["title"], "Operating Systems Concepts")
        self.assertIn("citation", d)

    def test_slide_citation_generation(self):
        citation = self.doc_service.generate_citation(self.chunk_restricted)
        self.assertIn("Slide 4", citation)
        self.assertIn("Advanced Algorithms Exam Preparation", citation)

    # -------------------------------------------------------------------------
    # Edge Cases & Failure Behavior
    # -------------------------------------------------------------------------
    def test_empty_or_whitespace_query_returns_empty_list(self):
        for empty_q in ["", "   ", None]:
            req = RetrievalRequest(query=empty_q)
            results = self.doc_service.retrieve(req)
            self.assertEqual(results, [])

    # -------------------------------------------------------------------------
    # Unified Retrieval Service & Adapters Tests
    # -------------------------------------------------------------------------
    def test_unified_retrieval_service_multi_source_orchestration(self):
        # Create a post
        post = Post.objects.create(
            author=self.uploader,
            content="Studying operating systems processes for the exam."
        )

        # Create an open group
        group = Group.objects.create(
            name="CS Revision Group",
            join_policy="open",
            description="Discussion on algorithms and systems."
        )

        req = RetrievalRequest(
            query="operating systems",
            user=self.student_cs,
            sources=[SourceType.DOCUMENT, SourceType.POST, SourceType.GROUP],
            mode=RetrievalMode.HYBRID,
            limit=10
        )
        response = self.unified_service.retrieve(req)

        self.assertIsInstance(response, RetrievalResponse)
        self.assertEqual(response.query, "operating systems")
        self.assertGreater(len(response.results), 0)
        self.assertGreater(response.execution_time_ms, 0.0)
        self.assertIn("document", response.source_metrics)
        self.assertIn("post", response.source_metrics)
        self.assertIn("group", response.source_metrics)

    def test_adapter_people_search(self):
        req = RetrievalRequest(query="Alice", user=None, sources=[SourceType.USER])
        results = self.search_adapter.search_people(req)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].source, SourceType.USER)
        self.assertEqual(results[0].url, "/users/user/uploader_user/")

    def test_unified_retrieval_semantic_failure_falls_back_to_lexical(self):
        from unittest.mock import MagicMock
        from documents.services.search_service import SearchService
        SearchService().index_document(self.doc_public)

        failing_doc_service = MagicMock()
        failing_doc_service.retrieve.side_effect = RuntimeError("Embedding service unavailable")

        service = UnifiedRetrievalService(
            document_service=failing_doc_service,
            search_adapter=self.search_adapter,
        )

        req = RetrievalRequest(
            query="Operating Systems",
            user=self.student_cs,
            sources=[SourceType.DOCUMENT],
            mode=RetrievalMode.HYBRID,
            limit=5
        )
        response = service.retrieve(req)
        self.assertIsInstance(response, RetrievalResponse)
        # Should have fallen back to lexical search and found Operating Systems Concepts
        self.assertGreater(len(response.results), 0)
        self.assertEqual(response.results[0].title, "Operating Systems Concepts")

    def test_document_semantic_retrieval_metadata_enrichment(self):
        """Document semantic retrieval results must include thumbnail_url, media_url, file_type, and author."""
        from documents.models import DocumentFile
        DocumentFile.objects.create(
            document_version=self.v_public,
            original_filename="os_notes.pdf",
            extension="pdf",
            size_bytes=1024,
            uploaded_by=self.uploader,
            thumbnail_path="documents/thumbnails/os_thumb.png",
            preview_path="documents/previews/os_preview.png",
        )
        req = RetrievalRequest(
            query="Processes and threads",
            filters={'document_id': self.doc_public.pk},
            user=self.student_cs,
            sources=[SourceType.DOCUMENT],
            mode=RetrievalMode.SEMANTIC,
            limit=1
        )
        results = self.doc_service.retrieve(req)
        self.assertEqual(len(results), 1)
        meta = results[0].metadata
        self.assertEqual(meta["resource_type"], "document")
        self.assertEqual(meta["file_type"], "pdf")
        self.assertIn("os_thumb.png", meta["thumbnail_url"])
        self.assertEqual(meta["author"], "Alice Uploader")

    def test_post_search_adapter_media_and_hls_metadata(self):
        """Post search adapter must enrich metadata with HLS stream, media, and resource_type."""
        post = Post.objects.create(
            author=self.uploader,
            content="Check out this physics experiment video on campus.",
            hls_playlist="posts/videos/hls/exp1/master.m3u8",
        )
        req = RetrievalRequest(
            query="physics experiment video",
            user=self.student_cs,
            sources=[SourceType.POST],
            limit=5
        )
        results = self.search_adapter.search_posts(req)
        self.assertGreater(len(results), 0)
        p_res = next(r for r in results if r.object_id == post.id)
        self.assertEqual(p_res.metadata["resource_type"], "video")
        self.assertIn("master.m3u8", p_res.metadata["hls_url"])


class PageAwareDocumentRetrievalTestCase(TestCase):
    """
    Test suite for Phase 2B: Authorized Page-Aware Document Retrieval.
    Verifies deterministic relational chunk retrieval covering specific pages,
    handling multi-page spans, boundary conditions, strict authorization invariants,
    version isolation, and input validation.
    """

    def setUp(self):
        self.mock_provider = MockEmbeddingProvider(dimensions=768)
        self.service = DocumentSemanticRetrievalService(embedding_provider=self.mock_provider)

        # Users
        self.uploader = User.objects.create_user(
            username='page_uploader',
            email='page_uploader@vasto.local',
            first_name='Grace',
            last_name='Hopper'
        )
        self.student_cs = User.objects.create_user(
            username='page_student_cs',
            email='page_cs@vasto.local',
            first_name='Alan',
            last_name='Turing'
        )
        self.student_bio = User.objects.create_user(
            username='page_student_bio',
            email='page_bio@vasto.local',
            first_name='Gregor',
            last_name='Mendel'
        )

        # Academic Structure for Restricted Document Access
        self.faculty = Faculty.objects.create(name="Science Faculty", code="SCI")
        self.school = School.objects.create(name="Computing School", code="SCIT", faculty=self.faculty)
        self.dept = Department.objects.create(name="Computer Science Dept", code="CSD", school=self.school)
        self.prog_cs = Programme.objects.create(
            name="BSc Computer Science", code="BSCS_PAGE", department=self.dept, degree_type="Bachelor", duration_years=4
        )
        self.prog_bio = Programme.objects.create(
            name="BSc Biology", code="BSB_PAGE", department=self.dept, degree_type="Bachelor", duration_years=4
        )
        self.student_cs.programme = self.prog_cs
        self.student_cs.save()
        self.student_bio.programme = self.prog_bio
        self.student_bio.save()

        self.unit_os = AcademicUnit.objects.create(name="OS Architecture", code="CSC212")
        self.acad_year = AcademicYear.objects.create(name="2025/2026", code="2025/2026", start_date="2025-09-01", end_date="2026-06-30")
        self.semester = Semester.objects.create(academic_year=self.acad_year, number=1, start_date="2025-09-01", end_date="2026-01-31")
        self.level_2 = AcademicLevel.objects.create(level=2, name="Year 2")

        self.prog_unit = ProgrammeUnit.objects.create(
            programme=self.prog_cs,
            academic_unit=self.unit_os,
            academic_level=self.level_2,
            semester=self.semester
        )

        self.category = Category.objects.create(name="Lecture Slides", code="lecture_slides")

        # 1. Multi-Page Public Document (Section 14 dataset)
        self.doc_multipage = Document.objects.create(
            title="Operating Systems Principles",
            slug="operating-systems-principles",
            category=self.category,
            uploaded_by=self.uploader,
            status='ready',
            is_available=True,
            visibility='public'
        )
        self.v_multipage_1 = DocumentVersion.objects.create(
            document=self.doc_multipage,
            version_number=1,
            created_by=self.uploader,
            is_latest=True
        )

        # Section 14 chunks:
        # chunk_index=0 -> pages 1-1
        # chunk_index=1 -> pages 2-4
        # chunk_index=2 -> pages 4-6
        # chunk_index=3 -> pages 7-7
        # chunk_index=4 -> pages 6-8
        # chunk_index=5 -> pages 9-10
        self.chunk_0 = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_1,
            chunk_index=0,
            content="Chapter 1: Operating system overview and architecture.",
            chunk_type="paragraph",
            page_number=1,
            page_end=1,
            section_heading="Introduction",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Chapter 1: Operating system overview and architecture.")
        )
        self.chunk_1 = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_1,
            chunk_index=1,
            content="Chapter 2: Process synchronization and thread states.",
            chunk_type="paragraph",
            page_number=2,
            page_end=4,
            section_heading="Process Management",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Chapter 2: Process synchronization and thread states.")
        )
        self.chunk_2 = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_1,
            chunk_index=2,
            content="Chapter 3: Memory hierarchies, virtual memory and segmentation.",
            chunk_type="paragraph",
            page_number=4,
            page_end=6,
            section_heading="Memory Subsystems",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Chapter 3: Memory hierarchies, virtual memory and segmentation.")
        )
        self.chunk_3 = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_1,
            chunk_index=3,
            content="Chapter 4: Concurrency primitives, semaphores and condition variables.",
            chunk_type="paragraph",
            page_number=7,
            page_end=7,
            section_heading="Concurrency",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Chapter 4: Concurrency primitives, semaphores and condition variables.")
        )
        self.chunk_4 = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_1,
            chunk_index=4,
            content="Chapter 4B: Deadlock prevention, detection and avoidance algorithms.",
            chunk_type="paragraph",
            page_number=6,
            page_end=8,
            section_heading="Deadlocks",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Chapter 4B: Deadlock prevention, detection and avoidance algorithms.")
        )
        self.chunk_5 = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_1,
            chunk_index=5,
            content="Chapter 5: File system implementation, inodes and directory structures.",
            chunk_type="paragraph",
            page_number=9,
            page_end=10,
            section_heading="Storage",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Chapter 5: File system implementation, inodes and directory structures.")
        )

        # Chunk with page_end=None (single-page legacy chunk)
        self.chunk_single_no_end = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_1,
            chunk_index=6,
            content="Appendix A: System call quick reference.",
            chunk_type="paragraph",
            page_number=11,
            page_end=None,
            section_heading="Appendix",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Appendix A: System call quick reference.")
        )

        # Inactive chunk on page 7 (must be excluded)
        self.chunk_inactive = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_1,
            chunk_index=7,
            content="Unpublished notes on page 7 concurrency.",
            chunk_type="paragraph",
            page_number=7,
            page_end=7,
            is_active=False,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Unpublished notes on page 7 concurrency.")
        )

        # Older Version 2 (is_latest=False) for version isolation testing
        self.v_multipage_2 = DocumentVersion.objects.create(
            document=self.doc_multipage,
            version_number=2,
            created_by=self.uploader,
            is_latest=False
        )
        self.chunk_v2 = DocumentChunk.objects.create(
            document=self.doc_multipage,
            document_version=self.v_multipage_2,
            chunk_index=0,
            content="Version 2 draft text for concurrency on page 7.",
            chunk_type="paragraph",
            page_number=7,
            page_end=7,
            section_heading="Concurrency v2",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Version 2 draft text for concurrency on page 7.")
        )

        # 2. Restricted Document (restricted to CS student via ProgrammeUnit)
        self.doc_restricted = Document.objects.create(
            title="CSC212 Exam Prep Questions",
            slug="csc212-exam-prep-questions",
            category=self.category,
            uploaded_by=self.uploader,
            status='ready',
            is_available=True,
            visibility='restricted'
        )
        DocumentAcademicUnit.objects.create(
            document=self.doc_restricted,
            academic_unit=self.unit_os,
            academic_level=self.level_2,
            semester=self.semester,
            academic_year=self.acad_year
        )
        self.v_restricted = DocumentVersion.objects.create(
            document=self.doc_restricted,
            version_number=1,
            created_by=self.uploader,
            is_latest=True
        )
        self.chunk_restricted = DocumentChunk.objects.create(
            document=self.doc_restricted,
            document_version=self.v_restricted,
            chunk_index=0,
            content="Restricted solutions for question 3 on page 7.",
            chunk_type="paragraph",
            page_number=7,
            page_end=7,
            section_heading="Solutions",
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Restricted solutions for question 3 on page 7.")
        )

        # 3. Independent Document for cross-document version defense
        self.doc_other = Document.objects.create(
            title="Other Syllabus",
            slug="other-syllabus",
            category=self.category,
            uploaded_by=self.uploader,
            status='ready',
            is_available=True,
            visibility='public'
        )
        self.v_other = DocumentVersion.objects.create(
            document=self.doc_other,
            version_number=1,
            created_by=self.uploader,
            is_latest=True
        )
        self.chunk_other = DocumentChunk.objects.create(
            document=self.doc_other,
            document_version=self.v_other,
            chunk_index=0,
            content="Other course content on page 7.",
            chunk_type="paragraph",
            page_number=7,
            page_end=7,
            is_active=True,
            embedding_status="completed",
            embedding_model="mock-embedding-768",
            embedding=self.mock_provider.embed_query("Other course content on page 7.")
        )

    def test_exact_page_chunk(self):
        """Test 1: Exact page chunk covering page 1 is returned for page 1, and page_end=None is handled."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=1
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].metadata["chunk_index"], 0)
        self.assertEqual(results[0].snippet, self.chunk_0.content)

        # Test single-page chunk where page_end is NULL
        results_none_end = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=11
        )
        self.assertEqual(len(results_none_end), 1)
        self.assertEqual(results_none_end[0].metadata["chunk_index"], 6)
        self.assertEqual(results_none_end[0].snippet, self.chunk_single_no_end.content)

    def test_multipage_chunk(self):
        """Test 2: A multi-page chunk covering pages 2-4 is returned for interior page 3."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=3
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].metadata["chunk_index"], 1)
        self.assertEqual(results[0].metadata["page_number"], 2)
        self.assertEqual(results[0].metadata["page_end"], 4)

    def test_boundary_start(self):
        """Test 3: Boundary start condition - page 4 matches chunk 1 (2-4 end) and chunk 2 (4-6 start)."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=4
        )
        indices = [r.metadata["chunk_index"] for r in results]
        self.assertEqual(indices, [1, 2])

    def test_boundary_end(self):
        """Test 4: Boundary end condition - page 8 matches chunk 4 (pages 6-8 end)."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=8
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].metadata["chunk_index"], 4)

    def test_non_matching_page(self):
        """Test 5: Non-matching page - chunk 5 (pages 9-10) is not returned for page 7."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=7
        )
        indices = [r.metadata["chunk_index"] for r in results]
        self.assertNotIn(5, indices)

    def test_ordering_by_chunk_index(self):
        """Test 6: Multiple matching chunks are returned strictly ordered by chunk_index."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=7
        )
        indices = [r.metadata["chunk_index"] for r in results]
        self.assertEqual(indices, [3, 4])
        self.assertEqual(indices, sorted(indices))

    def test_section_14_multipage_dataset(self):
        """Section 14: Verifies the multi-page behavior against explicit test specification."""
        # For page 7, expected matching chunks: chunk_index 3 and 4
        res_7 = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=7
        )
        self.assertEqual([r.metadata["chunk_index"] for r in res_7], [3, 4])

        # For page 6, expected matching chunks: chunk_index 2 and 4
        res_6 = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=6
        )
        self.assertEqual([r.metadata["chunk_index"] for r in res_6], [2, 4])

    def test_unauthorized_document_rejection(self):
        """Test 7 & 15: Unauthorized user cannot retrieve chunks from restricted document (zero leakage)."""
        results = self.service.get_page_chunks(
            user=self.student_bio,
            document_share_id=self.doc_restricted.share_id,
            page_number=7
        )
        self.assertEqual(results, [])

    def test_authorized_document_access(self):
        """Test 8 & 15: Authorized user receives expected chunks from restricted document."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_restricted.share_id,
            page_number=7
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].snippet, self.chunk_restricted.content)

    def test_unauthenticated_user_rejected(self):
        """Unauthenticated user (None or AnonymousUser) receives empty results."""
        from django.contrib.auth.models import AnonymousUser

        res_none = self.service.get_page_chunks(
            user=None,
            document_share_id=self.doc_multipage.share_id,
            page_number=1
        )
        self.assertEqual(res_none, [])

        res_anon = self.service.get_page_chunks(
            user=AnonymousUser(),
            document_share_id=self.doc_multipage.share_id,
            page_number=1
        )
        self.assertEqual(res_anon, [])

    def test_inactive_chunk_excluded(self):
        """Test 9: Inactive chunks are strictly excluded even if page matches."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=7
        )
        indices = [r.metadata["chunk_index"] for r in results]
        self.assertNotIn(7, indices)
        self.assertEqual(indices, [3, 4])

    def test_version_isolation_default_latest(self):
        """Test 10A: When no version is supplied, chunks from latest version are returned."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=7
        )
        versions = [r.metadata["document_version_id"] for r in results]
        self.assertTrue(all(v == self.v_multipage_1.id for v in versions))
        self.assertNotIn(self.v_multipage_2.id, versions)

    def test_version_isolation_explicit_version(self):
        """Test 10B: When explicit version is supplied, only chunks belonging to that version are returned."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=7,
            document_version_id=self.v_multipage_2.id
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].snippet, self.chunk_v2.content)
        self.assertEqual(results[0].metadata["document_version_id"], self.v_multipage_2.id)

    def test_version_mismatch_cross_document_defense(self):
        """Test 10C: Version belonging to another document cannot bypass document identity constraint."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=7,
            document_version_id=self.v_other.id
        )
        self.assertEqual(results, [])

    def test_empty_page(self):
        """Test 11: Querying a page with no matching content returns empty list."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=99
        )
        self.assertEqual(results, [])

    def test_invalid_page_numbers(self):
        """Test 12: Invalid page numbers (zero, negative, string, float, bool, None) return empty list."""
        for invalid_page in [0, -1, -10, "abc", None, True, False, 7.5]:
            res = self.service.get_page_chunks(
                user=self.student_cs,
                document_share_id=self.doc_multipage.share_id,
                page_number=invalid_page
            )
            self.assertEqual(res, [], f"Failed to reject invalid page: {invalid_page}")

    def test_invalid_document_share_id(self):
        """Invalid or non-existent document share_id returns empty list cleanly."""
        for invalid_id in [None, "", "not-a-uuid", 12345, uuid.uuid4()]:
            res = self.service.get_page_chunks(
                user=self.student_cs,
                document_share_id=invalid_id,
                page_number=1
            )
            self.assertEqual(res, [], f"Failed to reject invalid share_id: {invalid_id}")

    def test_invalid_document_version_id(self):
        """Invalid version IDs (zero, negative, string, float, bool) return empty list cleanly."""
        for invalid_ver in [0, -1, "bad-version", True, False, 1.5]:
            res = self.service.get_page_chunks(
                user=self.student_cs,
                document_share_id=self.doc_multipage.share_id,
                page_number=1,
                document_version_id=invalid_ver
            )
            self.assertEqual(res, [], f"Failed to reject invalid version_id: {invalid_ver}")

    def test_retrieval_result_formatting_and_citations(self):
        """RetrievalResult formatting, artificial score, explicit_page metadata, and citation format."""
        results = self.service.get_page_chunks(
            user=self.student_cs,
            document_share_id=self.doc_multipage.share_id,
            page_number=7
        )
        self.assertEqual(len(results), 2)
        for res in results:
            self.assertIsInstance(res, RetrievalResult)
            self.assertEqual(res.score, 1.0)
            self.assertEqual(res.metadata["retrieval_mode"], "explicit_page")
            self.assertEqual(res.metadata["match_type"], "exact_page")
            self.assertTrue(res.citation.startswith("[Operating Systems Principles, v1:"))

        # Chunk 3 is single page: 'p. 7'
        self.assertIn("p. 7", results[0].citation)
        self.assertIn('"Concurrency"', results[0].citation)

        # Chunk 4 spans pages 6-8: 'pp. 6–8'
        self.assertIn("pp. 6–8", results[1].citation)
        self.assertIn('"Deadlocks"', results[1].citation)



