"""Shared types for the evidence-first RAG pipeline."""

from dataclasses import dataclass, field
from enum import Enum


class AnswerStatus(str, Enum):
    """Final answer status."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass(frozen=True)
class QuestionAnalysis:
    """Structured representation of a user question."""

    raw_question: str
    intent: str = "unknown"
    keywords: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentCandidate:
    """A document selected by the document router."""

    document_id: str
    title: str
    reason: str
    score: float


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
