"""
Pwanimate Structure-Aware Document Chunker.

Transforms Phase 2B ExtractedDocument (IR) into deterministic,
version-aware ChunkData objects adhering to Phase 2C DocumentChunk contracts.
Preserves structural provenance (pages, slides, section headings, table rows, notes)
and targets 400-600 tokens with ~60-80 token overlap for prose.
"""

import re
import hashlib
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union

from .types import ExtractedDocument, ExtractedElement, ElementType


# Regular expressions for sentence splitting and exam question detection
SENTENCE_SPLIT_REGEX = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(])')
QUESTION_REGEX = re.compile(
    r'^(?:question\s+\d+|q\d+[\.:\s]|\d+[\.:]\s+|part\s+[a-z0-9]+[\.:\s])',
    re.IGNORECASE
)


# A heading longer than this is treated as content in its own right, so a
# document made only of headings (a title page, slide-style pages, a scan
# whose only text is a title) is still indexed and findable.
HEADING_AS_CONTENT_MIN_TOKENS = 20


def estimate_tokens(text: str) -> int:
    """
    Deterministic token estimation for prose, tables, and code.
    Approximates standard BPE token counts based on ~4 characters per token
    and cross-checks word count (~1.33 tokens per whitespace-delimited word).
    """
    if not text:
        return 0
    words = len(text.split())
    chars = len(text)
    return max(1, max(chars // 4, int(words * 1.33)))


def split_into_sentences(text: str) -> List[str]:
    """Splits prose into sentences cleanly along punctuation boundaries."""
    if not text:
        return []
    sentences = [s.strip() for s in SENTENCE_SPLIT_REGEX.split(text) if s.strip()]
    return sentences if sentences else [text.strip()]


@dataclass
class ChunkData:
    """
    In-memory representation of an atomic document chunk ready for DocumentChunk persistence.
    """
    chunk_index: int
    content: str
    chunk_type: str  # 'heading', 'paragraph', 'table', 'slide', 'notes', 'code', 'text'
    page_number: Optional[int] = None
    page_end: Optional[int] = None
    slide_number: Optional[int] = None
    section_heading: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    token_count: int = 0

    def __post_init__(self):
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode('utf-8')).hexdigest()
        if not self.token_count and self.content:
            self.token_count = estimate_tokens(self.content)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'chunk_index': self.chunk_index,
            'content': self.content,
            'chunk_type': self.chunk_type,
            'page_number': self.page_number,
            'page_end': self.page_end,
            'slide_number': self.slide_number,
            'section_heading': self.section_heading,
            'metadata': self.metadata,
            'content_hash': self.content_hash,
            'token_count': self.token_count,
        }

    def to_document_chunk_kwargs(self, document=None, document_version=None) -> Dict[str, Any]:
        """Convert to kwargs dict for DocumentChunk.objects.create()."""
        kwargs = {
            'chunk_index': self.chunk_index,
            'content': self.content,
            'content_hash': self.content_hash,
            'chunk_type': self.chunk_type,
            'page_number': self.page_number,
            'page_end': self.page_end,
            'slide_number': self.slide_number,
            'section_heading': self.section_heading,
            'metadata': self.metadata,
            'is_active': False,
        }
        if document:
            kwargs['document'] = document
        if document_version:
            kwargs['document_version'] = document_version
        return kwargs


@dataclass
class ChunkingConfig:
    """Configurable sizing and overlap boundaries for structure-aware chunking."""
    target_min_tokens: int = 400
    target_max_tokens: int = 600
    overlap_tokens: int = 70
    max_hard_tokens: int = 700


class StructureAwareChunker:
    """
    Deterministic, format-sensitive chunker that transforms ExtractedDocument
    into structured ChunkData instances.
    """

    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or ChunkingConfig()

    def chunk(self, extracted_doc: ExtractedDocument) -> List[ChunkData]:
        """Main entry point: chunks an ExtractedDocument."""
        if not extracted_doc or not extracted_doc.elements:
            return []

        # Check for presentation/slides format
        if extracted_doc.format == 'pptx' or any(e.location.slide_number is not None for e in extracted_doc.elements):
            return self._chunk_presentation(extracted_doc)

        # Standard structured document (PDF, DOCX, TXT, MD)
        return self._chunk_document_elements(extracted_doc)

    def _chunk_presentation(self, doc: ExtractedDocument) -> List[ChunkData]:
        """
        Slide-aware chunking for presentations:
        Normally 1 slide = 1 chunk.
        Preserves slide number, title, body, and distinguishes speaker notes.
        """
        chunks: List[ChunkData] = []
        slide_groups: Dict[int, List[ExtractedElement]] = {}

        for elem in doc.elements:
            slide_num = elem.location.slide_number or 1
            slide_groups.setdefault(slide_num, []).append(elem)

        chunk_idx = 0
        for slide_num in sorted(slide_groups.keys()):
            elements = slide_groups[slide_num]

            slide_title = ""
            body_lines = []
            notes_lines = []
            shape_metadata = []

            for elem in elements:
                text = elem.text.strip()
                if not text:
                    continue

                if elem.element_type == ElementType.HEADING or elem.metadata.get('is_slide_title'):
                    if not slide_title:
                        slide_title = text
                    else:
                        body_lines.append(text)
                elif elem.element_type == ElementType.NOTES or elem.metadata.get('is_speaker_notes'):
                    notes_lines.append(text)
                else:
                    body_lines.append(text)

                if elem.metadata:
                    shape_metadata.append(elem.metadata)

            content_parts = []
            if slide_title:
                content_parts.append(slide_title)
            if body_lines:
                content_parts.append("\n\n".join(body_lines))

            has_notes = bool(notes_lines)
            notes_text = "\n\n".join(notes_lines) if has_notes else ""
            if has_notes:
                content_parts.append(f"[Speaker Notes]:\n{notes_text}")

            full_content = "\n\n".join(content_parts)
            token_count = estimate_tokens(full_content)

            chunk_meta = {
                "source_format": "pptx",
                "has_speaker_notes": has_notes,
            }
            if has_notes:
                chunk_meta["speaker_notes"] = notes_text
            if shape_metadata:
                chunk_meta["shape_count"] = len(shape_metadata)

            # If slide is extraordinarily large (> max_hard_tokens), split at body boundaries
            if token_count > self.config.max_hard_tokens and len(body_lines) > 1:
                sub_chunks = self._split_large_slide(
                    slide_num=slide_num,
                    slide_title=slide_title,
                    body_lines=body_lines,
                    notes_text=notes_text,
                    start_idx=chunk_idx
                )
                chunks.extend(sub_chunks)
                chunk_idx += len(sub_chunks)
            else:
                chunks.append(
                    ChunkData(
                        chunk_index=chunk_idx,
                        content=full_content,
                        chunk_type="slide",
                        slide_number=slide_num,
                        section_heading=slide_title,
                        metadata=chunk_meta,
                        token_count=token_count
                    )
                )
                chunk_idx += 1

        return chunks

    def _split_large_slide(
        self,
        slide_num: int,
        slide_title: str,
        body_lines: List[str],
        notes_text: str,
        start_idx: int
    ) -> List[ChunkData]:
        """Split an exceptionally large slide into multiple slide sub-chunks."""
        sub_chunks: List[ChunkData] = []
        cur_lines = []
        cur_tokens = estimate_tokens(slide_title)
        part = 1

        for line in body_lines:
            line_tokens = estimate_tokens(line)
            if cur_lines and (cur_tokens + line_tokens > self.config.target_max_tokens):
                content = f"{slide_title}\n\n" + "\n\n".join(cur_lines) if slide_title else "\n\n".join(cur_lines)
                sub_chunks.append(
                    ChunkData(
                        chunk_index=start_idx + len(sub_chunks),
                        content=content,
                        chunk_type="slide",
                        slide_number=slide_num,
                        section_heading=slide_title,
                        metadata={"source_format": "pptx", "part": part}
                    )
                )
                part += 1
                cur_lines = [line]
                cur_tokens = estimate_tokens(slide_title) + line_tokens
            else:
                cur_lines.append(line)
                cur_tokens += line_tokens

        if cur_lines:
            content_parts = []
            if slide_title:
                content_parts.append(slide_title)
            content_parts.append("\n\n".join(cur_lines))
            if notes_text:
                content_parts.append(f"[Speaker Notes]:\n{notes_text}")

            sub_chunks.append(
                ChunkData(
                    chunk_index=start_idx + len(sub_chunks),
                    content="\n\n".join(content_parts),
                    chunk_type="slide",
                    slide_number=slide_num,
                    section_heading=slide_title,
                    metadata={"source_format": "pptx", "part": part, "has_speaker_notes": bool(notes_text)}
                )
            )

        return sub_chunks

    def _chunk_document_elements(self, doc: ExtractedDocument) -> List[ChunkData]:
        """
        Structure-aware chunking for prose, tables, exams, and code.
        Preserves section headings, table structure, code blocks, and page boundaries.
        """
        chunks: List[ChunkData] = []
        active_heading = ""
        current_buffer: List[Dict[str, Any]] = []
        current_buffer_tokens = 0
        chunk_idx = 0

        def flush_buffer(force_new: bool = False):
            nonlocal current_buffer, current_buffer_tokens, chunk_idx
            if not current_buffer:
                return

            text_pieces = [b['text'] for b in current_buffer]
            combined_text = "\n\n".join(text_pieces)
            pages = [b['page'] for b in current_buffer if b['page'] is not None]
            start_page = min(pages) if pages else None
            end_page = max(pages) if pages else None

            # Determine predominant chunk type
            types = [b.get('type') for b in current_buffer]
            chunk_type = "paragraph"
            if "code" in types:
                chunk_type = "code"
            elif "table" in types:
                chunk_type = "table"

            chunk = ChunkData(
                chunk_index=chunk_idx,
                content=combined_text,
                chunk_type=chunk_type,
                page_number=start_page,
                page_end=end_page,
                section_heading=active_heading,
                metadata={
                    "element_count": len(current_buffer),
                    "source_format": doc.format,
                },
                token_count=current_buffer_tokens
            )
            chunks.append(chunk)
            chunk_idx += 1

            # Prepare overlap for next buffer
            if force_new or not self.config.overlap_tokens:
                current_buffer = []
                current_buffer_tokens = 0
            else:
                current_buffer, current_buffer_tokens = self._extract_overlap(current_buffer)

        for elem in doc.elements:
            raw_text = elem.text.strip()
            if not raw_text:
                continue

            elem_type = elem.element_type
            page_num = elem.location.page_number

            # 1. Heading Handling
            if elem_type == ElementType.HEADING:
                # Flush existing buffer since a new section has started
                flush_buffer(force_new=True)
                active_heading = raw_text.lstrip('#').strip()
                # A heading can BE the content: a title page, a slide-style
                # page, or a scan whose only text is a title. Without this the
                # whole document would vanish (0 chunks) and be invisible to
                # retrieval. Short headings stay as section context only.
                heading_tokens = estimate_tokens(active_heading)
                if heading_tokens >= HEADING_AS_CONTENT_MIN_TOKENS:
                    chunks.append(
                        ChunkData(
                            chunk_index=chunk_idx,
                            content=active_heading,
                            chunk_type="heading",
                            page_number=page_num,
                            section_heading=active_heading,
                            metadata={"source_format": doc.format, "from_heading": True},
                            token_count=heading_tokens,
                        )
                    )
                    chunk_idx += 1
                continue

            # 2. Table Handling
            if elem_type == ElementType.TABLE:
                table_tokens = estimate_tokens(raw_text)
                if table_tokens <= self.config.max_hard_tokens:
                    # Flush prose buffer before inserting table
                    flush_buffer(force_new=True)
                    row_count = elem.metadata.get('row_count')
                    chunks.append(
                        ChunkData(
                            chunk_index=chunk_idx,
                            content=raw_text,
                            chunk_type="table",
                            page_number=page_num,
                            page_end=page_num,
                            section_heading=active_heading or elem.location.section_heading or "",
                            metadata={
                                "row_count": row_count,
                                "source_format": doc.format,
                            },
                            token_count=table_tokens
                        )
                    )
                    chunk_idx += 1
                else:
                    # Large table: split along row boundaries
                    flush_buffer(force_new=True)
                    table_chunks = self._split_large_table(
                        raw_text=raw_text,
                        page_num=page_num,
                        section_heading=active_heading or elem.location.section_heading or "",
                        source_format=doc.format,
                        start_idx=chunk_idx
                    )
                    chunks.extend(table_chunks)
                    chunk_idx += len(table_chunks)
                continue

            # 3. Code Handling
            if elem_type == ElementType.CODE:
                code_tokens = estimate_tokens(raw_text)
                if code_tokens <= self.config.max_hard_tokens:
                    flush_buffer(force_new=True)
                    chunks.append(
                        ChunkData(
                            chunk_index=chunk_idx,
                            content=raw_text,
                            chunk_type="code",
                            page_number=page_num,
                            page_end=page_num,
                            section_heading=active_heading or elem.location.section_heading or "",
                            metadata={
                                "language": elem.metadata.get("source_extension", "code"),
                                "source_format": doc.format,
                            },
                            token_count=code_tokens
                        )
                    )
                    chunk_idx += 1
                else:
                    flush_buffer(force_new=True)
                    code_chunks = self._split_large_code(
                        raw_text=raw_text,
                        page_num=page_num,
                        section_heading=active_heading or elem.location.section_heading or "",
                        source_format=doc.format,
                        start_idx=chunk_idx
                    )
                    chunks.extend(code_chunks)
                    chunk_idx += len(code_chunks)
                continue

            # 4. Exam Question Handling
            if QUESTION_REGEX.match(raw_text):
                q_tokens = estimate_tokens(raw_text)
                # If question is reasonably sized, keep it atomic
                if q_tokens <= self.config.target_max_tokens:
                    flush_buffer(force_new=True)
                    chunks.append(
                        ChunkData(
                            chunk_index=chunk_idx,
                            content=raw_text,
                            chunk_type="paragraph",
                            page_number=page_num,
                            page_end=page_num,
                            section_heading=active_heading or elem.location.section_heading or "",
                            metadata={"is_question": True, "source_format": doc.format},
                            token_count=q_tokens
                        )
                    )
                    chunk_idx += 1
                    continue

            # 5. Normal Prose / Large Paragraph Handling
            elem_tokens = estimate_tokens(raw_text)

            # If this single paragraph exceeds the maximum target size, split along sentence boundaries
            if elem_tokens > self.config.target_max_tokens:
                flush_buffer(force_new=True)
                sentence_chunks = self._split_large_paragraph(
                    raw_text=raw_text,
                    page_num=page_num,
                    section_heading=active_heading or elem.location.section_heading or "",
                    source_format=doc.format,
                    start_idx=chunk_idx
                )
                chunks.extend(sentence_chunks)
                chunk_idx += len(sentence_chunks)
                continue

            # Check if adding this paragraph fits into the current buffer
            if current_buffer and (current_buffer_tokens + elem_tokens > self.config.target_max_tokens):
                flush_buffer(force_new=False)

            current_buffer.append({
                'text': raw_text,
                'page': page_num,
                'type': elem_type.value if hasattr(elem_type, 'value') else str(elem_type)
            })
            current_buffer_tokens += elem_tokens

        # Flush any remaining buffer at the end of the document
        if current_buffer:
            text_pieces = [b['text'] for b in current_buffer]
            combined_text = "\n\n".join(text_pieces)
            pages = [b['page'] for b in current_buffer if b['page'] is not None]

            # Merge with previous chunk if this final residue is very small and fits
            if (
                chunks and
                current_buffer_tokens < 60 and
                chunks[-1].chunk_type == "paragraph" and
                chunks[-1].section_heading == active_heading and
                chunks[-1].token_count + current_buffer_tokens <= self.config.max_hard_tokens
            ):
                prev = chunks[-1]
                prev.content = prev.content + "\n\n" + combined_text
                prev.content_hash = hashlib.sha256(prev.content.encode('utf-8')).hexdigest()
                prev.token_count += current_buffer_tokens
                if pages:
                    prev.page_end = max(prev.page_end or prev.page_number or 1, max(pages))
            else:
                chunks.append(
                    ChunkData(
                        chunk_index=chunk_idx,
                        content=combined_text,
                        chunk_type="paragraph",
                        page_number=min(pages) if pages else None,
                        page_end=max(pages) if pages else None,
                        section_heading=active_heading,
                        metadata={
                            "element_count": len(current_buffer),
                            "source_format": doc.format,
                        },
                        token_count=current_buffer_tokens
                    )
                )

        return chunks

    def _extract_overlap(self, buffer: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """
        Extracts approximately 60-80 tokens of trailing content from buffer
        to seed the subsequent chunk for semantic continuity.
        """
        if not buffer:
            return [], 0

        overlap_items = []
        acc_tokens = 0

        # Traverse backwards to collect trailing items
        for item in reversed(buffer):
            t_count = estimate_tokens(item['text'])
            if acc_tokens + t_count <= self.config.overlap_tokens or not overlap_items:
                overlap_items.insert(0, item)
                acc_tokens += t_count
            else:
                # If a single trailing paragraph is large, take its trailing sentences
                sentences = split_into_sentences(item['text'])
                sub_sentences = []
                for s in reversed(sentences):
                    s_tokens = estimate_tokens(s)
                    if acc_tokens + s_tokens <= self.config.overlap_tokens or not sub_sentences:
                        sub_sentences.insert(0, s)
                        acc_tokens += s_tokens
                    else:
                        break
                if sub_sentences:
                    overlap_items.insert(0, {
                        'text': " ".join(sub_sentences),
                        'page': item['page'],
                        'type': item.get('type', 'paragraph')
                    })
                break

        return overlap_items, acc_tokens

    def _split_large_table(
        self,
        raw_text: str,
        page_num: Optional[int],
        section_heading: str,
        source_format: str,
        start_idx: int
    ) -> List[ChunkData]:
        """Splits an oversized table along row boundaries, preserving table header."""
        rows = [r.strip() for r in raw_text.split('\n') if r.strip()]
        if not rows:
            return []

        header = rows[0]
        data_rows = rows[1:] if len(rows) > 1 else []

        table_chunks: List[ChunkData] = []
        cur_rows = [header]
        cur_tokens = estimate_tokens(header)

        for row in data_rows:
            r_tokens = estimate_tokens(row)
            if cur_rows and (cur_tokens + r_tokens > self.config.target_max_tokens):
                content = "\n".join(cur_rows)
                table_chunks.append(
                    ChunkData(
                        chunk_index=start_idx + len(table_chunks),
                        content=content,
                        chunk_type="table",
                        page_number=page_num,
                        page_end=page_num,
                        section_heading=section_heading,
                        metadata={
                            "row_count": len(cur_rows),
                            "source_format": source_format,
                            "is_split_table": True,
                        },
                        token_count=cur_tokens
                    )
                )
                cur_rows = [header, row]
                cur_tokens = estimate_tokens(header) + r_tokens
            else:
                cur_rows.append(row)
                cur_tokens += r_tokens

        if cur_rows:
            content = "\n".join(cur_rows)
            table_chunks.append(
                ChunkData(
                    chunk_index=start_idx + len(table_chunks),
                    content=content,
                    chunk_type="table",
                    page_number=page_num,
                    page_end=page_num,
                    section_heading=section_heading,
                    metadata={
                        "row_count": len(cur_rows),
                        "source_format": source_format,
                        "is_split_table": True,
                    },
                    token_count=cur_tokens
                )
            )

        return table_chunks

    def _split_large_code(
        self,
        raw_text: str,
        page_num: Optional[int],
        section_heading: str,
        source_format: str,
        start_idx: int
    ) -> List[ChunkData]:
        """Splits an oversized code block along line/function boundaries."""
        lines = raw_text.split('\n')
        code_chunks: List[ChunkData] = []
        cur_lines = []
        cur_tokens = 0

        for line in lines:
            l_tokens = estimate_tokens(line)
            if cur_lines and (cur_tokens + l_tokens > self.config.target_max_tokens):
                code_chunks.append(
                    ChunkData(
                        chunk_index=start_idx + len(code_chunks),
                        content="\n".join(cur_lines),
                        chunk_type="code",
                        page_number=page_num,
                        page_end=page_num,
                        section_heading=section_heading,
                        metadata={"source_format": source_format, "is_split_code": True},
                        token_count=cur_tokens
                    )
                )
                cur_lines = [line]
                cur_tokens = l_tokens
            else:
                cur_lines.append(line)
                cur_tokens += l_tokens

        if cur_lines:
            code_chunks.append(
                ChunkData(
                    chunk_index=start_idx + len(code_chunks),
                    content="\n".join(cur_lines),
                    chunk_type="code",
                    page_number=page_num,
                    page_end=page_num,
                    section_heading=section_heading,
                    metadata={"source_format": source_format, "is_split_code": True},
                    token_count=cur_tokens
                )
            )

        return code_chunks

    def _split_large_paragraph(
        self,
        raw_text: str,
        page_num: Optional[int],
        section_heading: str,
        source_format: str,
        start_idx: int
    ) -> List[ChunkData]:
        """Splits an oversized paragraph along sentence boundaries."""
        sentences = split_into_sentences(raw_text)
        para_chunks: List[ChunkData] = []
        cur_sentences = []
        cur_tokens = 0

        for s in sentences:
            s_tokens = estimate_tokens(s)
            if cur_sentences and (cur_tokens + s_tokens > self.config.target_max_tokens):
                para_chunks.append(
                    ChunkData(
                        chunk_index=start_idx + len(para_chunks),
                        content=" ".join(cur_sentences),
                        chunk_type="paragraph",
                        page_number=page_num,
                        page_end=page_num,
                        section_heading=section_heading,
                        metadata={"source_format": source_format, "is_split_paragraph": True},
                        token_count=cur_tokens
                    )
                )
                # Overlap: keep trailing sentence if it fits overlap budget
                overlap_sentences = []
                overlap_toks = 0
                for prev_s in reversed(cur_sentences):
                    pt = estimate_tokens(prev_s)
                    if overlap_toks + pt <= self.config.overlap_tokens or not overlap_sentences:
                        overlap_sentences.insert(0, prev_s)
                        overlap_toks += pt
                    else:
                        break
                cur_sentences = overlap_sentences + [s]
                cur_tokens = overlap_toks + s_tokens
            else:
                cur_sentences.append(s)
                cur_tokens += s_tokens

        if cur_sentences:
            para_chunks.append(
                ChunkData(
                    chunk_index=start_idx + len(para_chunks),
                    content=" ".join(cur_sentences),
                    chunk_type="paragraph",
                    page_number=page_num,
                    page_end=page_num,
                    section_heading=section_heading,
                    metadata={"source_format": source_format, "is_split_paragraph": True},
                    token_count=cur_tokens
                )
            )

        return para_chunks


# Functional public interface
def chunk_document(
    extracted_doc: ExtractedDocument,
    config: Optional[ChunkingConfig] = None
) -> List[ChunkData]:
    """
    Convenience function to chunk an ExtractedDocument using StructureAwareChunker.
    """
    chunker = StructureAwareChunker(config=config)
    return chunker.chunk(extracted_doc)


def create_document_chunks_from_data(
    document_version,
    chunks: List[ChunkData],
    document=None
):
    """
    Thin persistence helper creating DocumentChunk records for a DocumentVersion.
    Leaves all chunks in inactive (is_active=False) state pending Phase 2E validation.
    """
    from ..models import DocumentChunk

    doc = document or document_version.document
    records = [
        DocumentChunk(
            document=doc,
            document_version=document_version,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            content_hash=chunk.content_hash,
            chunk_type=chunk.chunk_type,
            page_number=chunk.page_number,
            page_end=chunk.page_end,
            slide_number=chunk.slide_number,
            section_heading=chunk.section_heading,
            metadata=chunk.metadata,
            is_active=False,
        )
        for chunk in chunks
    ]
    return DocumentChunk.objects.bulk_create(records)
