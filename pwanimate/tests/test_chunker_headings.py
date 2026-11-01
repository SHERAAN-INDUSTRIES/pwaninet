"""
Tests for the structure-aware chunker.

The gap these pin: a document whose text is entirely headings (a title page,
slide-style pages, a scan whose only text is a title) used to produce ZERO
chunks — the heading branch consumed the text as section context and
discarded it. Such a document was indexed, searchable in the name only, and
invisible to semantic retrieval.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from pwanimate.ingestion.chunker import HEADING_AS_CONTENT_MIN_TOKENS, chunk_document
from pwanimate.ingestion.types import ElementType, ExtractedDocument


def _element(text, element_type=ElementType.HEADING, page=1):
    return SimpleNamespace(
        text=text,
        element_type=element_type,
        location=SimpleNamespace(page_number=page, slide_number=None, section_heading=""),
        metadata={},
    )


def _document(elements, fmt="pdf"):
    return SimpleNamespace(elements=elements, format=fmt, metadata={})


class HeadingAsContentTests(SimpleTestCase):

    def test_a_document_of_headings_still_produces_chunks(self):
        long_heading = (
            "Chapter 1: Photosynthesis explained. Plants convert light energy into "
            "chemical energy stored as glucose inside the chloroplast."
        )
        document = _document([_element(long_heading, page=1)])
        chunks = chunk_document(document)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Photosynthesis", chunks[0].content)
        self.assertEqual(chunks[0].chunk_type, "heading")
        self.assertEqual(chunks[0].page_number, 1)

    def test_a_short_heading_stays_context_only(self):
        document = _document([_element("Introduction")])
        chunks = chunk_document(document)
        # A one-word heading is navigation, not content.
        self.assertEqual(chunks, [])

    def test_prose_documents_are_unaffected(self):
        document = _document([
            _element("Chapter 1", ElementType.HEADING, page=1),
            _element(
                "Plants convert light energy into chemical energy stored as glucose. "
                "This happens inside the chloroplast of every green plant cell.",
                ElementType.PARAGRAPH,
                page=1,
            ),
        ])
        chunks = chunk_document(document)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].chunk_type, "paragraph")
        self.assertIn("chloroplast", chunks[0].content)
        self.assertEqual(chunks[0].section_heading, "Chapter 1")

    def test_the_threshold_is_sane(self):
        # A heading must carry a real sentence to count as content.
        self.assertGreaterEqual(HEADING_AS_CONTENT_MIN_TOKENS, 15)
        self.assertLessEqual(HEADING_AS_CONTENT_MIN_TOKENS, 40)
