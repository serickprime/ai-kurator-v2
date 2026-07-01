"""Source label normalization for evidence-backed answers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from app.ingestion.text_normalizer import clean_heading, is_boilerplate_label, is_generic_heading
from app.rag.types import SourceRef

BAD_LABELS = {
    "",
    "unknown",
    "none",
    "null",
    "название файла:",
    "название файла",
    "прочее",
    "other",
    "misc",
}


@dataclass(frozen=True)
class _SourceLabelCandidate:
    label: str
    document_key: str
    detailed: bool


class SourceLabelBuilder:
    """Build concise user-facing labels from evidence source refs."""

    def build(self, source: SourceRef) -> str:
        """Return a clean label for one source."""
        metadata = source.metadata or {}
        title = _document_title(source, metadata)
        context = _first_clean(
            [
                metadata.get("section_heading"),
                metadata.get("heading"),
                _section_locator(source.locator),
                _course_lesson(metadata),
            ]
        )
        page = _page_label(metadata, source.locator)

        base_parts = _dedupe([title or source.document_id, context])
        label = " — ".join(base_parts)
        if page and page.casefold() not in label.casefold():
            label = f"{label}, {page}" if not context else f"{label} — {page}"
        source_uri = str(source.source_uri or metadata.get("canonical_url") or metadata.get("source_url") or "").strip()
        if source_uri:
            label = f"{label} ({source_uri})"
        return _truncate(label, 140)

    def build_many(
        self,
        sources: Iterable[SourceRef],
        *,
        max_per_document: int = 3,
        max_labels: int = 3,
    ) -> list[str]:
        """Return deduplicated clean labels for a source list."""
        candidates: list[_SourceLabelCandidate] = []
        seen_labels: set[str] = set()
        for source in sources:
            label = self.build(source)
            label_key = label.casefold()
            document_key = _document_key(source, label)
            if label_key in seen_labels:
                continue
            seen_labels.add(label_key)
            candidates.append(
                _SourceLabelCandidate(
                    label=label,
                    document_key=document_key,
                    detailed=_is_detailed_label(label),
                )
            )

        detailed_documents = {candidate.document_key for candidate in candidates if candidate.detailed}
        labels: list[str] = []
        counts_by_document: dict[str, int] = {}
        for candidate in candidates:
            if candidate.document_key in detailed_documents and not candidate.detailed:
                continue
            if counts_by_document.get(candidate.document_key, 0) >= max_per_document:
                continue
            counts_by_document[candidate.document_key] = counts_by_document.get(candidate.document_key, 0) + 1
            labels.append(candidate.label)
            if len(labels) >= max_labels:
                break
        return labels

    def build_document_label(self, document: dict[str, object]) -> str:
        """Return a clean label for selected document debug output."""
        source = SourceRef(
            document_id=str(document.get("document_id") or document.get("id") or ""),
            document_title=str(document.get("title") or ""),
            metadata={
                "filename": document.get("filename"),
                "course": document.get("course"),
                "module": document.get("module"),
                "lesson": document.get("lesson"),
            },
        )
        return self.build(source)

    def debug(self, sources: Iterable[SourceRef]) -> list[dict[str, object]]:
        """Return compact source-label diagnostics."""
        result: list[dict[str, object]] = []
        for source in sources:
            result.append(
                {
                    "evidence_id": source.evidence_id,
                    "document_id": source.document_id,
                    "raw_title": source.document_title,
                    "raw_locator": source.locator,
                    "label": self.build(source),
                }
            )
        return result


def _course_lesson(metadata: dict[str, object]) -> str:
    course = _clean_part(metadata.get("course"))
    module = _clean_part(metadata.get("module"))
    lesson = _clean_part(metadata.get("lesson"))
    parts = []
    if course:
        parts.append(f"Курс: {course}")
    if module:
        parts.append(f"Модуль: {module}")
    if lesson:
        parts.append(f"Урок: {lesson}")
    return " / ".join(parts)


def _document_title(source: SourceRef, metadata: dict[str, object]) -> str:
    title = _first_clean(
        [
            source.document_title,
            metadata.get("document_title"),
            metadata.get("title"),
        ]
    )
    if title:
        return title
    return _first_clean(
        [
            _course_lesson(metadata),
            metadata.get("filename"),
            metadata.get("source_file"),
            source.document_id,
        ]
    )


def _page_label(metadata: dict[str, object], locator: str | None) -> str:
    page = metadata.get("page")
    if page is None and locator:
        match = re.search(r"\bp\.\s*(\d+)\b|страница\s+(\d+)|стр\.?\s*(\d+)", locator, flags=re.IGNORECASE)
        if match:
            if match.group(1):
                return f"p. {match.group(1)}"
            page = match.group(2) or match.group(3)
    if page is None or str(page).strip() == "":
        return ""
    return f"стр. {page}"


def _section_locator(locator: str | None) -> str:
    clean = _clean_part(locator)
    if not clean:
        return ""
    if re.fullmatch(r"(?:p\.|стр\.?|страница)\s*\d+", clean, flags=re.IGNORECASE):
        return ""
    return clean


def _first_clean(values: Iterable[object]) -> str:
    for value in values:
        clean = _clean_part(value)
        if clean:
            return clean
    return ""


def _clean_part(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" -–—,:;")
    lowered = text.casefold()
    if lowered in BAD_LABELS or is_boilerplate_label(text) or is_generic_heading(text):
        return ""
    if lowered.startswith("source file:"):
        text = text.split(":", 1)[1].strip()
    if re.match(r"^[A-Za-z]:\\|^/", text):
        text = Path(text).name
    if text.endswith((".txt", ".md", ".pdf", ".json")):
        text = text.rsplit(".", 1)[0]
    return clean_heading(text)


def _dedupe(parts: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        clean = _clean_part(part)
        key = clean.casefold()
        if not clean or key in seen:
            continue
        if any(key in existing or existing in key for existing in seen):
            continue
        seen.add(key)
        result.append(clean)
    return result


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _is_detailed_label(label: str) -> bool:
    return " — " in label


def _document_key(source: SourceRef, label: str) -> str:
    if source.document_id:
        return source.document_id
    metadata = source.metadata or {}
    filename = _clean_part(metadata.get("filename") or metadata.get("source_file"))
    return filename.casefold() or label.casefold()
