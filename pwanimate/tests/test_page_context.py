"""
Tests for page-aware attachment context (Right Context Rail).

The student views a document in the rail; the rail reports the open page and
the orchestrator must hand the model THAT page's content first, precisely.

Covers:
- the open page's own chunks lead the context, tagged and bounded,
- an explicit "page N" in the question overrides the rail's open page,
- summary questions deliberately fall back to an overview,
- the page-scoped block is explicitly closed so the model can tell it from
  the supplementary context that reuses the same "[page N]" labels.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from pwanimate.orchestrator.service import extract_attachment_text


def _chunk(index, page, page_end=None, text=None):
    return SimpleNamespace(
        chunk_index=index,
        page_number=page,
        page_end=page_end if page_end is not None else page,
        content=text if text is not None else f"content of page {page} block {index}",
    )


def _attachment(chunks, name="manual.pdf"):
    return SimpleNamespace(
        id="att-1",
        file_name=name,
        processing_status="ready",
        processing_error="",
        chunks=SimpleNamespace(all=lambda: list(chunks)),
    )


class PageAwareExtractionTests(SimpleTestCase):

    def setUp(self):
        self.chunks = [
            _chunk(0, 1, 1, "page one text"),
            _chunk(1, 2, 2, "page two text"),
            _chunk(2, 3, 3, "page three text"),
            _chunk(3, 4, 4, "page four text"),
            _chunk(4, 5, 5, "page five text"),
        ]
        self.attachment = _attachment(self.chunks)

    def test_open_page_content_leads_the_context(self):
        text = extract_attachment_text(
            self.attachment, max_chars=15000, query="what is on this page?", active_page=3
        )
        self.assertIn("[CURRENTLY OPEN PAGE IN VIEWER — PAGE 3]", text)
        self.assertIn("page three text", text)
        # The focused page comes before the supplementary context.
        self.assertLess(text.index("page three text"), text.index("[ADDITIONAL DOCUMENT CONTEXT]"))

    def test_page_scoped_block_is_closed_explicitly(self):
        text = extract_attachment_text(
            self.attachment, max_chars=15000, query="what is on this page?", active_page=3
        )
        self.assertIn("[END OF CURRENT PAGE CONTEXT]", text)
        self.assertIn("[ADDITIONAL DOCUMENT CONTEXT]", text)
        # The close marker must come after the focused page's own content and
        # before the supplementary items.
        self.assertLess(
            text.index("page three text"), text.index("[END OF CURRENT PAGE CONTEXT]")
        )
        self.assertLess(
            text.index("[END OF CURRENT PAGE CONTEXT]"), text.index("[ADDITIONAL DOCUMENT CONTEXT]")
        )

    def test_explicit_page_in_the_question_beats_the_rail(self):
        text = extract_attachment_text(
            self.attachment,
            max_chars=15000,
            query="explain what is on page 5",
            active_page=2,
        )
        self.assertIn("[CURRENTLY OPEN PAGE IN VIEWER — PAGE 5]", text)
        self.assertIn("page five text", text)
        self.assertNotIn("[CURRENTLY OPEN PAGE IN VIEWER — PAGE 2]", text)

    def test_summary_questions_ignore_the_open_page(self):
        text = extract_attachment_text(
            self.attachment, max_chars=15000, query="summarize this whole book", active_page=3
        )
        self.assertNotIn("[CURRENTLY OPEN PAGE IN VIEWER", text)
        self.assertIn("[ADDITIONAL DOCUMENT CONTEXT]", text)

    def test_a_page_the_student_is_not_on_is_not_focused(self):
        text = extract_attachment_text(
            self.attachment, max_chars=15000, query="what is on this page?", active_page=3
        )
        focus = text.split("[CURRENTLY OPEN PAGE IN VIEWER")[1].split(
            "[END OF CURRENT PAGE CONTEXT]"
        )[0]
        self.assertNotIn("page one text", focus)
        self.assertNotIn("page five text", focus)

    def test_overview_header_reports_the_real_page_count(self):
        text = extract_attachment_text(
            self.attachment, max_chars=15000, query="what is on this page?", active_page=3
        )
        self.assertIn("Total pages: 5", text)
        self.assertIn("Total chunks: 5", text)
