"""Shared types for the evidence-first RAG pipeline."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class AnswerStatus(str, Enum):
    """Final answer status."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_CLARIFICATION = "needs_clarification"


FacetRole = Literal[
    "platform",
    "action",
    "object",
    "environment",
    "symptom",
    "constraint",
    "source",
]


@dataclass(frozen=True)
class QueryFacet:
    """One routing signal extracted from a question."""

    role: FacetRole
    text: str
    importance: float = 1.0


@dataclass(frozen=True)
class QuestionAnalysis:
    """Structured representation of a user question."""

    original_question: str = ""
    primary_intent: str = "unknown"
    task_type: str = "general"
    source_required: bool = True
    diagnostic: bool = False
    conceptual: bool = False
    needs_official_docs: bool = False
    answer_scope: str = "knowledge_base"
    must_answer_points: tuple[str, ...] = ()
    evidence_questions: tuple[str, ...] = ()
    missing_input_requirements: tuple[str, ...] = ()
    query_facets: tuple[QueryFacet, ...] = ()
    raw_question: str = ""
    intent: str = "unknown"
    keywords: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Keep legacy aliases populated while exposing the v2 contract."""
        original = self.original_question or self.raw_question
        raw = self.raw_question or original
        intent = self.intent
        if intent == "unknown" and self.primary_intent != "unknown":
            intent = "question"

        object.__setattr__(self, "original_question", original)
        object.__setattr__(self, "raw_question", raw)
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "must_answer_points", tuple(self.must_answer_points))
        object.__setattr__(self, "evidence_questions", tuple(self.evidence_questions))
        object.__setattr__(self, "missing_input_requirements", tuple(self.missing_input_requirements))
        object.__setattr__(self, "query_facets", tuple(self.query_facets))
        object.__setattr__(self, "keywords", tuple(self.keywords))
        object.__setattr__(self, "constraints", tuple(self.constraints))


@dataclass(frozen=True)
class DocumentCandidate:
    """A document selected by the document router."""

    document_id: str
    filename: str = ""
    title: str = ""
    course: str | None = None
    lesson: str | None = None
    score: float = 0.0
    reason: str = ""
    matched_topics: tuple[str, ...] = ()
    matched_questions: tuple[str, ...] = ()
    route: str = "document_card"

    def __post_init__(self) -> None:
        object.__setattr__(self, "matched_topics", tuple(self.matched_topics))
        object.__setattr__(self, "matched_questions", tuple(self.matched_questions))


@dataclass(frozen=True)
class EvidenceSpan:
    """A compact span of text that can support answer claims."""

    evidence_id: str
    document_id: str
    document_title: str
    text: str
    locator: str | None = None
    source_uri: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class SourceRef:
    """A source that was actually used in the final evidence pack."""

    document_id: str
    document_title: str
    locator: str | None = None
    source_uri: str | None = None


@dataclass(frozen=True)
class EvidencePack:
    """The only context that answer generation is allowed to see."""

    items: tuple[EvidenceSpan, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))

    @property
    def is_empty(self) -> bool:
        """Return true when there is no usable evidence."""
        return not self.items

    @property
    def source_document_ids(self) -> tuple[str, ...]:
        """Return unique source document ids in evidence order."""
        seen: set[str] = set()
        ids: list[str] = []
        for item in self.items:
            if item.document_id in seen:
                continue
            seen.add(item.document_id)
            ids.append(item.document_id)
        return tuple(ids)

    def sources(self) -> tuple[SourceRef, ...]:
        """Return source refs derived only from evidence in this pack."""
        seen: set[tuple[str, str | None]] = set()
        sources: list[SourceRef] = []
        for item in self.items:
            key = (item.document_id, item.locator)
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                SourceRef(
                    document_id=item.document_id,
                    document_title=item.document_title,
                    locator=item.locator,
                    source_uri=item.source_uri,
                )
            )
        return tuple(sources)


@dataclass(frozen=True)
class AnswerDraft:
    """Draft answer before verification."""

    text: str
    status: AnswerStatus
    used_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationReport:
    """Claim verification result."""

    is_supported: bool
    unsupported_claims: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineResult:
    """Final pipeline result returned to the bot layer."""

    answer: str
    status: AnswerStatus
    sources: tuple[SourceRef, ...]
    verification: VerificationReport
