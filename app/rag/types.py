"""Shared types for the evidence-first RAG pipeline."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class AnswerStatus(str, Enum):
    """Final answer status."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_CLARIFICATION = "needs_clarification"


AnswerMode = Literal[
    "answer_from_materials",
    "partial_answer",
    "ask_for_missing_data",
    "general_answer_without_sources",
    "out_of_base",
]


FacetRole = Literal[
    "common",
    "platform",
    "action",
    "object",
    "environment",
    "symptom",
    "constraint",
    "config",
    "exact",
    "rare_anchor",
    "source",
]


ContentType = Literal[
    "lesson_material",
    "homework_task",
    "homework_review_rules",
    "course_catalog",
    "course_structure",
    "course_terms",
    "student_case",
    "official_docs",
    "external_docs",
    "platform_navigation",
    "personal_data",
    "unknown",
]


EvidenceDecisionStatus = Literal["accepted", "partial", "discarded"]


@dataclass(frozen=True)
class QueryFacet:
    """One routing signal extracted from a question."""

    role: FacetRole
    text: str
    importance: float = 1.0


@dataclass(frozen=True)
class QueryPlan:
    """Routing and evidence plan derived from the user question."""

    user_question: str = ""
    normalized_question: str = ""
    question_type: str = "general"
    expected_content_types: tuple[ContentType, ...] = ("unknown",)
    source_priority: tuple[ContentType, ...] = ()
    course_hint: str = ""
    course_hint_confidence: float = 0.0
    domain_hint: str = ""
    domain_hint_confidence: float = 0.0
    action_terms: tuple[str, ...] = ()
    object_terms: tuple[str, ...] = ()
    symptom_terms: tuple[str, ...] = ()
    constraint_terms: tuple[str, ...] = ()
    exact_terms: tuple[str, ...] = ()
    rare_anchor_terms: tuple[str, ...] = ()
    common_terms: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    ambiguity: tuple[str, ...] = ()
    needs_external_docs: bool = False
    source_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_content_types", tuple(self.expected_content_types or ("unknown",)))
        object.__setattr__(self, "source_priority", tuple(self.source_priority))
        object.__setattr__(self, "action_terms", tuple(self.action_terms))
        object.__setattr__(self, "object_terms", tuple(self.object_terms))
        object.__setattr__(self, "symptom_terms", tuple(self.symptom_terms))
        object.__setattr__(self, "constraint_terms", tuple(self.constraint_terms))
        object.__setattr__(self, "exact_terms", tuple(self.exact_terms))
        object.__setattr__(self, "rare_anchor_terms", tuple(self.rare_anchor_terms))
        object.__setattr__(self, "common_terms", tuple(self.common_terms))
        object.__setattr__(self, "evidence_requirements", tuple(self.evidence_requirements))
        object.__setattr__(self, "ambiguity", tuple(self.ambiguity))


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
    primary_object: str = ""
    object_terms: tuple[str, ...] = ()
    requested_action: str = ""
    requested_attribute: str = ""
    generic_terms: tuple[str, ...] = ()
    common_terms: tuple[str, ...] = ()
    platform_terms: tuple[str, ...] = ()
    action_terms: tuple[str, ...] = ()
    symptom_terms: tuple[str, ...] = ()
    environment_terms: tuple[str, ...] = ()
    config_terms: tuple[str, ...] = ()
    exact_terms: tuple[str, ...] = ()
    rare_anchor_terms: tuple[str, ...] = ()
    ignored_weak_terms: tuple[str, ...] = ()
    strongest_evidence_terms: tuple[str, ...] = ()
    user_question: str = ""
    normalized_question: str = ""
    question_type: str = "general"
    expected_content_types: tuple[ContentType, ...] = ("unknown",)
    source_priority: tuple[ContentType, ...] = ()
    course_hint: str = ""
    course_hint_confidence: float = 0.0
    domain_hint: str = ""
    domain_hint_confidence: float = 0.0
    constraint_terms: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    ambiguity: tuple[str, ...] = ()
    needs_external_docs: bool = False
    query_plan: QueryPlan | None = None

    def __post_init__(self) -> None:
        """Keep legacy aliases populated while exposing the v2 contract."""
        original = self.original_question or self.raw_question
        raw = self.raw_question or original
        normalized = self.normalized_question or original
        user_question = self.user_question or original
        question_type = self.question_type if self.question_type != "general" else self.task_type
        intent = self.intent
        if intent == "unknown" and self.primary_intent != "unknown":
            intent = "question"
        constraint_terms = tuple(self.constraint_terms or self.constraints)
        evidence_requirements = tuple(self.evidence_requirements or self.evidence_questions)
        expected_content_types = tuple(self.expected_content_types or ("unknown",))
        source_priority = tuple(self.source_priority or expected_content_types)
        needs_external_docs = self.needs_external_docs or self.needs_official_docs
        query_plan = self.query_plan or QueryPlan(
            user_question=user_question,
            normalized_question=normalized,
            question_type=question_type,
            expected_content_types=expected_content_types,
            source_priority=source_priority,
            course_hint=self.course_hint,
            course_hint_confidence=self.course_hint_confidence,
            domain_hint=self.domain_hint,
            domain_hint_confidence=self.domain_hint_confidence,
            action_terms=self.action_terms,
            object_terms=self.object_terms,
            symptom_terms=self.symptom_terms,
            constraint_terms=constraint_terms,
            exact_terms=self.exact_terms,
            rare_anchor_terms=self.rare_anchor_terms,
            common_terms=self.common_terms,
            evidence_requirements=evidence_requirements,
            ambiguity=self.ambiguity,
            needs_external_docs=needs_external_docs,
            source_required=self.source_required,
        )

        object.__setattr__(self, "original_question", original)
        object.__setattr__(self, "raw_question", raw)
        object.__setattr__(self, "user_question", user_question)
        object.__setattr__(self, "normalized_question", normalized)
        object.__setattr__(self, "question_type", question_type)
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "must_answer_points", tuple(self.must_answer_points))
        object.__setattr__(self, "evidence_questions", tuple(self.evidence_questions))
        object.__setattr__(self, "missing_input_requirements", tuple(self.missing_input_requirements))
        object.__setattr__(self, "query_facets", tuple(self.query_facets))
        object.__setattr__(self, "keywords", tuple(self.keywords))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "object_terms", tuple(self.object_terms))
        object.__setattr__(self, "generic_terms", tuple(self.generic_terms))
        object.__setattr__(self, "common_terms", tuple(self.common_terms))
        object.__setattr__(self, "platform_terms", tuple(self.platform_terms))
        object.__setattr__(self, "action_terms", tuple(self.action_terms))
        object.__setattr__(self, "symptom_terms", tuple(self.symptom_terms))
        object.__setattr__(self, "environment_terms", tuple(self.environment_terms))
        object.__setattr__(self, "config_terms", tuple(self.config_terms))
        object.__setattr__(self, "exact_terms", tuple(self.exact_terms))
        object.__setattr__(self, "rare_anchor_terms", tuple(self.rare_anchor_terms))
        object.__setattr__(self, "ignored_weak_terms", tuple(self.ignored_weak_terms))
        object.__setattr__(self, "strongest_evidence_terms", tuple(self.strongest_evidence_terms))
        object.__setattr__(self, "expected_content_types", expected_content_types)
        object.__setattr__(self, "source_priority", source_priority)
        object.__setattr__(self, "constraint_terms", constraint_terms)
        object.__setattr__(self, "evidence_requirements", evidence_requirements)
        object.__setattr__(self, "ambiguity", tuple(self.ambiguity))
        object.__setattr__(self, "needs_external_docs", needs_external_docs)
        object.__setattr__(self, "query_plan", query_plan)


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
    matched_common_terms: tuple[str, ...] = ()
    matched_anchor_terms: tuple[str, ...] = ()
    missing_action_terms: tuple[str, ...] = ()
    missing_object_terms: tuple[str, ...] = ()
    answerability_score: float = 0.0
    penalties: tuple[str, ...] = ()
    content_type: ContentType = "unknown"
    matched_content_types: tuple[ContentType, ...] = ()
    score_breakdown: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "matched_topics", tuple(self.matched_topics))
        object.__setattr__(self, "matched_questions", tuple(self.matched_questions))
        object.__setattr__(self, "matched_common_terms", tuple(self.matched_common_terms))
        object.__setattr__(self, "matched_anchor_terms", tuple(self.matched_anchor_terms))
        object.__setattr__(self, "missing_action_terms", tuple(self.missing_action_terms))
        object.__setattr__(self, "missing_object_terms", tuple(self.missing_object_terms))
        object.__setattr__(self, "penalties", tuple(self.penalties))
        object.__setattr__(self, "matched_content_types", tuple(self.matched_content_types))
        object.__setattr__(self, "score_breakdown", dict(self.score_breakdown))


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
    is_source: bool = True
    retrieval_reason: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceRef:
    """A source that was actually used in the final evidence pack."""

    document_id: str
    document_title: str
    locator: str | None = None
    source_uri: str | None = None
    evidence_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class EvidenceDecision:
    """Decision explaining why a span was accepted, partial, or discarded."""

    evidence_id: str
    status: EvidenceDecisionStatus
    reasons: tuple[str, ...] = ()
    score: float | None = None
    document_id: str = ""
    preview: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))


@dataclass(frozen=True)
class EvidencePack:
    """The only context that answer generation is allowed to see."""

    items: tuple[EvidenceSpan, ...] = field(default_factory=tuple)
    answer_mode: AnswerMode = "answer_from_materials"
    source_matches: tuple[SourceRef, ...] = field(default_factory=tuple)
    missing_requirements: tuple[str, ...] = field(default_factory=tuple)
    decisions: tuple[EvidenceDecision, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        items = tuple(self.items)
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "missing_requirements", tuple(self.missing_requirements))
        decisions = tuple(
            decision
            for decision in self.decisions
            if decision.status in {"accepted", "partial"}
        )
        object.__setattr__(self, "decisions", decisions)
        if self.answer_mode not in {"answer_from_materials", "partial_answer"}:
            source_matches: tuple[SourceRef, ...] = ()
        else:
            source_matches = tuple(self.source_matches) or _sources_from_items(items)
        object.__setattr__(self, "source_matches", source_matches)

    @property
    def is_empty(self) -> bool:
        """Return true when there is no usable evidence."""
        return not self.items

    @property
    def source_document_ids(self) -> tuple[str, ...]:
        """Return unique source document ids in evidence order."""
        seen: set[str] = set()
        ids: list[str] = []
        for source in self.source_matches:
            if source.document_id in seen:
                continue
            seen.add(source.document_id)
            ids.append(source.document_id)
        return tuple(ids)

    def sources(self) -> tuple[SourceRef, ...]:
        """Return source refs derived only from evidence in this pack."""
        if self.answer_mode not in {"answer_from_materials", "partial_answer"}:
            return ()

        seen: set[tuple[str, str | None, str | None]] = set()
        sources: list[SourceRef] = []
        for source in self.source_matches:
            key = (source.document_id, source.locator, source.evidence_id)
            if key in seen:
                continue
            seen.add(key)
            sources.append(source)
        return tuple(sources)


@dataclass(frozen=True)
class AnswerDraft:
    """Draft answer before verification."""

    text: str
    status: AnswerStatus
    used_evidence_ids: tuple[str, ...] = ()
    answer_mode: AnswerMode = "answer_from_materials"
    model_input: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationReport:
    """Claim verification result."""

    is_supported: bool
    unsupported_claims: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    verdict: str = "pass"
    safe_answer: str = ""
    source_leakage: bool = False


@dataclass(frozen=True)
class PipelineResult:
    """Final pipeline result returned to the bot layer."""

    answer: str
    status: AnswerStatus
    sources: tuple[SourceRef, ...]
    verification: VerificationReport
    debug: dict[str, object] = field(default_factory=dict)


def _sources_from_items(items: tuple[EvidenceSpan, ...]) -> tuple[SourceRef, ...]:
    sources: list[SourceRef] = []
    for item in items:
        if not item.is_source:
            continue
        sources.append(
            SourceRef(
                document_id=item.document_id,
                document_title=item.document_title,
                locator=item.locator,
                source_uri=item.source_uri,
                evidence_id=item.evidence_id,
                metadata=item.metadata,
            )
        )
    return tuple(sources)
