"""Parent-child text splitting for document-first RAG ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.loaders import LoadedDocument
from app.ingestion.text_normalizer import clean_heading, is_boilerplate_label

PAGE_MARKER_RE = re.compile(r"^\[\[page:(?P<page>\d+)]]\s*$")
HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")
TOKEN_RE = re.compile(r"[\w#+.-]+", re.UNICODE)


@dataclass(frozen=True)
class SectionDraft:
    """Parent block that groups related child chunks."""

    section_index: int
    heading: str
    content: str
    page_start: int | None = None
    page_end: int | None = None
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkDraft:
    """Small searchable fragment linked to a parent section."""

    chunk_index: int
    section_index: int
    content: str
    heading: str
    page: int | None
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextUnit:
    """Backward-compatible searchable unit alias."""

    ordinal: int
    text: str
    locator: str | None = None


class ParentChildChunker:
    """Split structured text into sections and child chunks."""

    def __init__(self, chunk_size: int = 1200, chunk_overlap: int = 180) -> None:
        if chunk_size < 300:
            raise ValueError("chunk_size must be at least 300 characters")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split_sections(self, document: LoadedDocument) -> tuple[SectionDraft, ...]:
        """Create parent sections from structured document text."""
        default_heading = clean_heading(document.title, fallback=document.filename)
        sections = self._split_by_headings(document.structured_text, default_heading=default_heading)
        if not sections:
            sections = (
                SectionDraft(
                    section_index=0,
                    heading=default_heading,
                    content=document.structured_text.strip(),
                    page_start=_first_page(document),
                    page_end=_last_page(document),
                    summary=_summarize(document.structured_text),
                    metadata={"fallback": "single_section"},
                ),
            )
        return sections

    def split_chunks(self, sections: tuple[SectionDraft, ...]) -> tuple[ChunkDraft, ...]:
        """Create child chunks linked to section indexes."""
        chunks: list[ChunkDraft] = []
        for section in sections:
            for part_index, content in enumerate(self._split_text(section.content), start=1):
                chunks.append(
                    ChunkDraft(
                        chunk_index=len(chunks),
                        section_index=section.section_index,
                        content=content,
                        heading=section.heading,
                        page=section.page_start,
                        token_count=count_tokens(content),
                        metadata={
                            "section_index": section.section_index,
                            "part_index": part_index,
                            "page_start": section.page_start,
                            "page_end": section.page_end,
                        },
                    )
                )
        return tuple(chunks)

    def _split_by_headings(self, text: str, *, default_heading: str) -> tuple[SectionDraft, ...]:
        current_heading = default_heading
        current_lines: list[str] = []
        current_pages: list[int] = []
        current_page: int | None = None
        sections: list[SectionDraft] = []

        def flush() -> None:
            content = "\n".join(current_lines).strip()
            if not content:
                return
            index = len(sections)
            page_start = min(current_pages) if current_pages else current_page
            page_end = max(current_pages) if current_pages else current_page
            sections.append(
                SectionDraft(
                    section_index=index,
                    heading=clean_heading(current_heading, fallback=default_heading or f"Section {index + 1}"),
                    content=content,
                    page_start=page_start,
                    page_end=page_end,
                    summary=_summarize(content),
                    metadata={"heading": clean_heading(current_heading, fallback=default_heading)},
                )
            )

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            page_match = PAGE_MARKER_RE.match(line.strip())
            if page_match:
                current_page = int(page_match.group("page"))
                continue

            heading_match = HEADING_RE.match(line.strip())
            if heading_match:
                flush()
                heading = clean_heading(heading_match.group("title"), fallback=default_heading)
                current_heading = heading
                current_lines = [] if is_boilerplate_label(heading_match.group("title")) else [line]
                current_pages = [current_page] if current_page is not None else []
                continue

            if line.strip() and current_page is not None:
                current_pages.append(current_page)
            current_lines.append(line)

        flush()
        return tuple(sections)

    def _split_text(self, text: str) -> tuple[str, ...]:
        text = _normalize_blank_lines(text)
        if len(text) <= self._chunk_size:
            return (text,)

        paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
        chunks: list[str] = []
        current = ""

        for paragraph in paragraphs:
            if len(paragraph) > self._chunk_size:
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.extend(self._split_long_paragraph(paragraph))
                continue

            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= self._chunk_size:
                current = candidate
                continue

            if current:
                chunks.append(current.strip())
            overlap = _tail_overlap(current, self._chunk_overlap)
            current = f"{overlap}\n\n{paragraph}".strip() if overlap else paragraph

        if current.strip():
            chunks.append(current.strip())
        return tuple(chunk for chunk in chunks if chunk.strip())

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        chunks: list[str] = []
        start = 0
        while start < len(paragraph):
            end = min(start + self._chunk_size, len(paragraph))
            if end < len(paragraph):
                boundary = max(paragraph.rfind(". ", start, end), paragraph.rfind(" ", start, end))
                if boundary > start + self._chunk_size // 2:
                    end = boundary + 1
            chunks.append(paragraph[start:end].strip())
            if end >= len(paragraph):
                break
            start = max(end - self._chunk_overlap, start + 1)
        return chunks


def count_tokens(text: str) -> int:
    """Return a deterministic approximate token count."""
    return len(TOKEN_RE.findall(text))


def _normalize_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def _summarize(text: str, max_chars: int = 360) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "."


def _tail_overlap(text: str, max_chars: int) -> str:
    if not text or max_chars <= 0:
        return ""
    tail = text[-max_chars:]
    boundary = tail.find(" ")
    return tail[boundary + 1 :].strip() if boundary > 0 else tail.strip()


def _first_page(document: LoadedDocument) -> int | None:
    pages = [page.page_number for page in document.pages if page.page_number is not None]
    return min(pages) if pages else None


def _last_page(document: LoadedDocument) -> int | None:
    pages = [page.page_number for page in document.pages if page.page_number is not None]
    return max(pages) if pages else None
