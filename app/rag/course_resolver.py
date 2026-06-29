"""Soft course hint resolution for routing.

Course hints are routing signals only. They are never evidence and must not be
used as sources. The resolver is intentionally data-driven: aliases come from
course catalogs, document metadata, or tests, not from a fixed service list.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.rag import term_scoring

_COURSE_PHRASE_RE = re.compile(
    r"(?:\bcourse\b|курс(?:е|а|у|ом)?|курсе|по курсу|в курсе)\s+['\"`«»]?([^?.,;:!()\n]{2,80})",
    re.IGNORECASE | re.UNICODE,
)


@dataclass(frozen=True)
class CourseAlias:
    """One canonical course name with aliases from catalog or metadata."""

    course: str
    aliases: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def all_names(self) -> tuple[str, ...]:
        """Return canonical name and aliases."""
        return tuple(term_scoring.dedupe([self.course, *self.aliases], limit=None))


@dataclass(frozen=True)
class CourseHint:
    """Resolved soft course scope."""

    course: str = ""
    confidence: float = 0.0
    matched_text: str = ""
    source: str = "none"
    soft_scope: bool = True

    @property
    def found(self) -> bool:
        """Return true when a usable course hint was found."""
        return bool(self.course and self.confidence > 0)


class CourseHintResolver:
    """Resolve short course hints without using them as evidence."""

    def __init__(self, aliases: Sequence[CourseAlias] = ()) -> None:
        self._aliases = tuple(aliases)

    def resolve(self, question: str) -> CourseHint:
        """Return the best soft course hint for a question."""
        explicit = _explicit_course_phrase(question)
        if explicit:
            alias_hint = self._alias_hint(explicit)
            if alias_hint.found:
                return alias_hint
            return CourseHint(
                course=explicit,
                confidence=0.64,
                matched_text=explicit,
                source="explicit_phrase",
                soft_scope=True,
            )
        return self._alias_hint(question)

    def _alias_hint(self, text: str) -> CourseHint:
        if not self._aliases:
            return CourseHint()

        text_roots = term_scoring.roots(term_scoring.significant_terms(text))
        if not text_roots:
            return CourseHint()

        best = CourseHint()
        for alias in self._aliases:
            for name in alias.all_names():
                name_terms = term_scoring.significant_terms(name)
                name_roots = term_scoring.roots(name_terms)
                if not name_roots:
                    continue
                overlap = text_roots & name_roots
                if not overlap:
                    continue
                coverage = len(overlap) / max(len(name_roots), 1)
                confidence = min(0.95, 0.42 + coverage * 0.48)
                if confidence > best.confidence:
                    best = CourseHint(
                        course=alias.course,
                        confidence=round(confidence, 4),
                        matched_text=name,
                        source="alias",
                        soft_scope=True,
                    )
        return best


def aliases_from_metadata(rows: Iterable[dict[str, object]]) -> tuple[CourseAlias, ...]:
    """Build course aliases from document/course metadata rows."""
    grouped: dict[str, list[str]] = {}
    for row in rows:
        course = str(row.get("course") or row.get("title") or row.get("name") or "").strip()
        if not course:
            continue
        aliases = grouped.setdefault(course, [])
        for key in ("alias", "aliases", "short_name", "slug", "document_key", "filename"):
            value = row.get(key)
            if isinstance(value, list):
                aliases.extend(str(item) for item in value if str(item).strip())
            elif value:
                aliases.append(str(value))
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            for key in ("alias", "aliases", "short_name", "slug"):
                value = metadata.get(key)
                if isinstance(value, list):
                    aliases.extend(str(item) for item in value if str(item).strip())
                elif value:
                    aliases.append(str(value))
    return tuple(
        CourseAlias(course=course, aliases=tuple(term_scoring.dedupe(aliases, limit=24)))
        for course, aliases in grouped.items()
    )


def _explicit_course_phrase(question: str) -> str:
    match = _COURSE_PHRASE_RE.search(question or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip(" \"'`«»")
