"""Document card creation for document-first routing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.ingestion.chunker import SectionDraft
from app.ingestion.loaders import LoadedDocument

KEYWORD_RE = re.compile(r"[\w#+.-]{3,}", re.UNICODE)
ENTITY_RE = re.compile(r"\b(?:[A-ZА-ЯЁ][\wА-Яа-яЁё-]{2,}|[A-Z0-9]{2,})\b")
STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "this",
    "that",
    "как",
    "что",
    "для",
    "это",
    "или",
    "при",
    "если",
    "then",
    "page",
    "source",
}


class DocumentCardLlm(Protocol):
    """Optional LLM adapter for card generation."""

    async def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Return a JSON object generated from chat messages."""


@dataclass(frozen=True)
class DocumentCard:
    """Compact routing representation of a document."""

    title: str
    summary: str
    topics: tuple[str, ...] = ()
    questions_answered: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    task_types: tuple[str, ...] = ()
    not_about: tuple[str, ...] = ()
    quality_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_embedding_text(self) -> str:
        """Return compact card text for embedding."""
        parts = [
            self.title,
            self.summary,
            "Topics: " + ", ".join(self.topics),
            "Questions: " + " | ".join(self.questions_answered),
            "Entities: " + ", ".join(self.entities),
            "Task types: " + ", ".join(self.task_types),
        ]
        return "\n".join(part for part in parts if part.strip())


class DocumentCardBuilder:
    """Create document cards with LLM-first and deterministic fallback behavior."""

    def __init__(self, llm_client: DocumentCardLlm | None = None) -> None:
        self._llm_client = llm_client

    async def build(
        self,
        document: LoadedDocument,
        sections: tuple[SectionDraft, ...],
    ) -> DocumentCard:
        """Build a routing card for a loaded document."""
        if self._llm_client is not None:
            try:
                llm_card = await self._build_with_llm(document, sections)
                if llm_card is not None:
                    return llm_card
            except Exception:
                pass

        return self._build_fallback(document, sections)

    async def _build_with_llm(
        self,
        document: LoadedDocument,
        sections: tuple[SectionDraft, ...],
    ) -> DocumentCard | None:
        if self._llm_client is None:
            return None

        section_preview = "\n".join(
            f"- {section.heading}: {section.summary or section.content[:240]}"
            for section in sections[:16]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Create a compact JSON document card for document-first RAG. "
                    "Return only JSON with keys: title, summary, topics, questions_answered, "
                    "entities, task_types, not_about, quality_score."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Filename: {document.filename}\n"
                    f"Title: {document.title}\n"
                    f"Sections:\n{section_preview}"
                ),
            },
        ]
        raw = await self._llm_client.complete_json(messages)
        return _card_from_mapping(raw, document)

    def _build_fallback(
        self,
        document: LoadedDocument,
        sections: tuple[SectionDraft, ...],
    ) -> DocumentCard:
        headings = [section.heading for section in sections if section.heading and section.heading != "Document"]
        topic_candidates = headings + _top_keywords(document.structured_text, limit=36)
        topics = tuple(_dedupe(topic_candidates, limit=30))
        questions = tuple(_questions_from_sections(sections, document, limit=10))
        entities = tuple(_dedupe(ENTITY_RE.findall(document.structured_text), limit=12))
        task_types = tuple(_task_types(document.structured_text))
        summary = _summary_from_sections(document, sections)
        quality_score = _quality_score(document, sections, topics, questions)

        return DocumentCard(
            title=document.title or document.path.stem,
            summary=summary,
            topics=topics,
            questions_answered=questions,
            entities=entities,
            task_types=task_types,
            not_about=(),
            quality_score=quality_score,
            metadata={
                "generator": "fallback",
                "source_type": document.source_type,
                "section_count": len(sections),
            },
        )


def _card_from_mapping(data: dict[str, Any], document: LoadedDocument) -> DocumentCard | None:
    try:
        return DocumentCard(
            title=str(data.get("title") or document.title),
            summary=str(data["summary"]),
            topics=tuple(_clean_list(data.get("topics", []), 12)),
            questions_answered=tuple(_clean_list(data.get("questions_answered", []), 12)),
            entities=tuple(_clean_list(data.get("entities", []), 12)),
            task_types=tuple(_clean_list(data.get("task_types", []), 8)),
            not_about=tuple(_clean_list(data.get("not_about", []), 8)),
            quality_score=float(data.get("quality_score", 0.75)),
            metadata={"generator": "llm"},
        )
    except (KeyError, TypeError, ValueError):
        return None


def _clean_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, list):
        return []
    return _dedupe([str(item).strip() for item in value if str(item).strip()], limit=limit)


def _top_keywords(text: str, limit: int = 16) -> list[str]:
    counts: dict[str, int] = {}
    for token in KEYWORD_RE.findall(text.lower()):
        if token in STOPWORDS or token.isdigit():
            continue
        counts[token] = counts.get(token, 0) + 1
    return [token for token, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _questions_from_sections(
    sections: tuple[SectionDraft, ...],
    document: LoadedDocument,
    limit: int,
) -> list[str]:
    questions: list[str] = []
    for section in sections:
        heading = section.heading.strip("# ").strip()
        if not heading or heading.lower() == "document":
            continue
        if heading.endswith("?"):
            questions.append(heading)
        else:
            questions.append(f"Что важно знать про {heading}?")

    if not questions:
        first_paragraph = _first_paragraph(document.structured_text)
        if first_paragraph:
            questions.append(f"О чем материал {document.title}?")
            questions.append(f"Какие основные идеи есть в разделе: {first_paragraph[:80]}?")
    return _dedupe(questions, limit=limit)


def _task_types(text: str) -> list[str]:
    lowered = text.lower()
    mapping = {
        "setup": ("install", "setup", "configure", "настро", "установ"),
        "troubleshooting": ("error", "ошиб", "debug", "fix", "исправ"),
        "how_to": ("how to", "как ", "шаг", "step"),
        "reference": ("api", "reference", "schema", "parameter"),
        "concept": ("overview", "concept", "обзор", "понят"),
    }
    return [label for label, needles in mapping.items() if any(needle in lowered for needle in needles)]


def _summary_from_sections(document: LoadedDocument, sections: tuple[SectionDraft, ...]) -> str:
    section_summaries = [section.summary for section in sections[:4] if section.summary]
    text = " ".join(section_summaries) or _first_paragraph(document.structured_text) or document.title
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= 700:
        return clean
    return clean[:699].rstrip() + "."


def _quality_score(
    document: LoadedDocument,
    sections: tuple[SectionDraft, ...],
    topics: tuple[str, ...],
    questions: tuple[str, ...],
) -> float:
    score = 0.35
    if len(document.structured_text) > 500:
        score += 0.2
    if sections:
        score += min(len(sections), 6) * 0.04
    if topics:
        score += 0.1
    if questions:
        score += 0.1
    return round(min(score, 1.0), 2)


def _first_paragraph(text: str) -> str:
    for paragraph in re.split(r"\n\s*\n", text):
        clean = re.sub(r"\s+", " ", paragraph).strip()
        if clean and not clean.startswith("[[page:"):
            return clean
    return ""


def _dedupe(items: list[str] | tuple[str, ...], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = re.sub(r"\s+", " ", str(item)).strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result
