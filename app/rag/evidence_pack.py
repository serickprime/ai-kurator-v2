"""Evidence pack construction."""

from collections.abc import Sequence
import re

from app.rag import term_scoring
from app.rag.types import EvidenceDecision, EvidencePack, EvidenceSpan, QuestionAnalysis, SourceRef

TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)


class EvidencePackBuilder:
    """Build the narrow context passed to answer generation."""

    def __init__(self, term_scorer: term_scoring.CorpusTermScorer | None = None) -> None:
        self._term_scorer = term_scorer or term_scoring.CorpusTermScorer.neutral()
        self.last_decisions: tuple[EvidenceDecision, ...] = ()

    def build(
        self,
        spans: Sequence[EvidenceSpan],
        max_items: int = 5,
        analysis: QuestionAnalysis | None = None,
    ) -> EvidencePack:
        """Build a compact evidence pack from reranked spans."""
        selected, decisions = _select_spans(
            spans,
            analysis,
            max_items=max_items,
            term_scorer=self._term_scorer,
        )
        self.last_decisions = tuple(decisions)
        answer_mode = _answer_mode(selected, analysis)
        missing = tuple(analysis.missing_input_requirements) if analysis else ()
        return EvidencePack(
            items=selected,
            answer_mode=answer_mode,
            source_matches=_source_matches(selected, answer_mode),
            missing_requirements=missing,
            decisions=tuple(decision for decision in decisions if decision.status in {"accepted", "partial"}),
        )


def build_sources(evidence_pack: EvidencePack) -> list[str]:
    """Build display sources only from evidence_pack.source_matches."""
    if evidence_pack.answer_mode not in {"answer_from_materials", "partial_answer"}:
        return []

    sources: list[str] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    for source in evidence_pack.source_matches:
        key = (source.document_id, source.locator, source.source_uri, source.evidence_id)
        if key in seen:
            continue
        seen.add(key)
        label = source.document_title or source.document_id
        if source.locator:
            label = f"{label}, {source.locator}"
        if source.source_uri:
            label = f"{label} ({source.source_uri})"
        sources.append(label)
    return sources


def _answer_mode(
    selected: tuple[EvidenceSpan, ...],
    analysis: QuestionAnalysis | None,
) -> str:
    if analysis is not None and not analysis.source_required:
        return "general_answer_without_sources"
    if analysis is not None and analysis.missing_input_requirements:
        return "ask_for_missing_data"
    if not selected:
        return "out_of_base"
    if analysis is not None and _has_uncovered_points(selected, analysis):
        return "partial_answer"
    return "answer_from_materials"


def _source_matches(selected: tuple[EvidenceSpan, ...], answer_mode: str) -> tuple[SourceRef, ...]:
    if answer_mode not in {"answer_from_materials", "partial_answer"}:
        return ()
    return tuple(
        SourceRef(
            document_id=span.document_id,
            document_title=span.document_title,
            locator=span.locator,
            source_uri=span.source_uri,
            evidence_id=span.evidence_id,
        )
        for span in selected
        if span.is_source
    )


def _has_uncovered_points(selected: tuple[EvidenceSpan, ...], analysis: QuestionAnalysis) -> bool:
    if not analysis.must_answer_points:
        return False
    evidence_text = " ".join(span.text.lower() for span in selected)
    covered = 0
    for point in analysis.must_answer_points:
        point_terms = [term for term in point.lower().split() if len(term) >= 4]
        if any(term[:5] in evidence_text for term in point_terms):
            covered += 1
    return covered < max(1, len(analysis.must_answer_points) // 2)


def _select_spans(
    spans: Sequence[EvidenceSpan],
    analysis: QuestionAnalysis | None,
    *,
    max_items: int,
    term_scorer: term_scoring.CorpusTermScorer,
) -> tuple[tuple[EvidenceSpan, ...], list[EvidenceDecision]]:
    accepted: list[EvidenceSpan] = []
    partial: list[EvidenceSpan] = []
    decisions: list[EvidenceDecision] = []
    for span in spans:
        decision = _decision_for_span(span, analysis, term_scorer)
        decisions.append(decision)
        if decision.status == "accepted":
            accepted.append(span)
        elif decision.status == "partial":
            partial.append(span)

    selected = accepted[:max_items]
    if len(selected) < min(3, max_items):
        selected_ids = {span.evidence_id for span in selected}
        partial_decisions = {decision.evidence_id: decision for decision in decisions if decision.status == "partial"}
        for span in partial:
            if span.evidence_id in selected_ids:
                continue
            decision = partial_decisions.get(span.evidence_id)
            if selected and decision is not None and _partial_is_not_needed(decision):
                continue
            selected.append(span)
            if len(selected) >= max_items:
                break
    return tuple(selected), decisions


def _partial_is_not_needed(decision: EvidenceDecision) -> bool:
    weak_reasons = {"common_term_only", "content_type_mismatch", "weak_support"}
    return bool(set(decision.reasons) & weak_reasons)


def _decision_for_span(
    span: EvidenceSpan,
    analysis: QuestionAnalysis | None,
    term_scorer: term_scoring.CorpusTermScorer,
) -> EvidenceDecision:
    reasons: list[str] = []
    if not span.text.strip():
        return _decision(span, "discarded", ["empty_text"])
    if span.score is not None and span.score < 0.16:
        return _decision(span, "discarded", ["below_evidence_threshold"])
    if analysis is None:
        return _decision(span, "accepted", ["no_question_analysis_available"])

    text = " ".join([span.document_title, span.locator or "", span.text])
    metadata = span.metadata or {}
    breakdown = metadata.get("score_breakdown") if isinstance(metadata.get("score_breakdown"), dict) else {}
    object_match = _match_ratio(analysis.object_terms, text)
    action_match = _match_ratio(tuple([analysis.requested_action]) if analysis.requested_action else (), text)
    symptom_match = _match_ratio(analysis.symptom_terms, text)
    constraint_match = _match_ratio(analysis.constraint_terms or analysis.constraints, text)
    anchor_match = _match_ratio(
        tuple(
            term_scoring.dedupe(
                [
                    *analysis.rare_anchor_terms,
                    *analysis.exact_terms,
                    *analysis.config_terms,
                    *analysis.strongest_evidence_terms,
                ],
                limit=16,
            )
        ),
        text,
    )

    if object_match:
        reasons.append("object_match")
    if action_match:
        reasons.append("action_match")
    if symptom_match:
        reasons.append("symptom_match")
    if constraint_match:
        reasons.append("constraint_match")
    if anchor_match:
        reasons.append("rare_anchor_match")
    if _covers_evidence_requirement(span, analysis):
        reasons.append("covers_evidence_requirement")
    if span.is_source:
        reasons.append("marked_as_source")

    if _misses_object(span, analysis):
        return _decision(span, "discarded", [*reasons, "missing_object_terms"])
    if _content_type_mismatch(span, analysis):
        reasons.append("content_type_mismatch")
    if term_scorer.has_statistics and not _has_strong_term(span, analysis, term_scorer):
        if object_match or action_match or symptom_match:
            return _decision(span, "partial", [*reasons, "common_term_only"])
        return _decision(span, "discarded", [*reasons, "common_term_only"])

    if not _has_required_query_terms(analysis):
        return _decision(span, "accepted", reasons or ["no_required_query_terms"])

    support = max(
        object_match,
        anchor_match,
        symptom_match,
        constraint_match,
        float(breakdown.get("rare_anchor_match", 0.0) or 0.0),
        float(breakdown.get("object_match", 0.0) or 0.0),
    )
    if span.score is not None and span.score >= 0.42:
        support = max(support, 0.72)
    elif span.score is not None and span.score >= 0.24:
        support = max(support, 0.45)

    if "content_type_mismatch" in reasons and support < 0.75:
        return _decision(span, "discarded", reasons)
    if support >= 0.58 or "covers_evidence_requirement" in reasons:
        return _decision(span, "accepted", reasons or ["strong_support"])
    if support >= 0.35 or object_match or action_match or symptom_match:
        return _decision(span, "partial", reasons or ["partial_support"])
    return _decision(span, "discarded", reasons or ["weak_support"])


def _decision(span: EvidenceSpan, status: str, reasons: list[str]) -> EvidenceDecision:
    return EvidenceDecision(
        evidence_id=span.evidence_id,
        status=status,  # type: ignore[arg-type]
        reasons=tuple(term_scoring.dedupe(reasons, limit=12)),
        score=span.score,
        document_id=span.document_id,
        preview=re.sub(r"\s+", " ", span.text).strip()[:180],
    )


def _has_strong_term(
    span: EvidenceSpan,
    analysis: QuestionAnalysis,
    term_scorer: term_scoring.CorpusTermScorer,
) -> bool:
    text = " ".join([span.document_title, span.locator or "", span.text])
    return term_scorer.has_strong_evidence_match(analysis, text)


def _misses_object(span: EvidenceSpan, analysis: QuestionAnalysis) -> bool:
    object_roots = _roots(analysis.object_terms)
    if not object_roots:
        return False
    text_roots = _roots(_tokens(" ".join([span.document_title, span.locator or "", span.text])))
    return not bool(object_roots & text_roots)


def _has_required_query_terms(analysis: QuestionAnalysis) -> bool:
    return bool(
        analysis.object_terms
        or analysis.symptom_terms
        or analysis.constraint_terms
        or analysis.constraints
        or analysis.rare_anchor_terms
        or analysis.exact_terms
        or analysis.config_terms
        or analysis.strongest_evidence_terms
    )


def _match_ratio(terms: Sequence[str], text: str) -> float:
    roots = _roots(_tokens(text))
    term_roots = _roots(_tokens(" ".join(terms)))
    if not term_roots:
        return 0.0
    return len(term_roots & roots) / max(len(term_roots), 1)


def _covers_evidence_requirement(span: EvidenceSpan, analysis: QuestionAnalysis) -> bool:
    if not analysis.evidence_questions:
        return False
    text_roots = _roots(_tokens(" ".join([span.document_title, span.locator or "", span.text])))
    for requirement in analysis.evidence_questions:
        requirement_roots = _roots(_tokens(requirement))
        if requirement_roots and len(requirement_roots & text_roots) >= min(2, len(requirement_roots)):
            return True
    return False


def _content_type_mismatch(span: EvidenceSpan, analysis: QuestionAnalysis) -> bool:
    expected = {content_type for content_type in analysis.expected_content_types if content_type != "unknown"}
    if not expected:
        return False
    metadata = span.metadata or {}
    values: list[object] = []
    for key in ("content_type", "content_types", "material_type"):
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    actual = {re.sub(r"[\s-]+", "_", str(value).strip().casefold()) for value in values if str(value).strip()}
    return bool(actual and not (actual & expected))


def _tokens(text: str) -> list[str]:
    return [token.casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»") for token in TOKEN_RE.findall(text)]


def _roots(tokens: Sequence[str]) -> set[str]:
    return {_root(token) for token in tokens if token}


def _root(token: str) -> str:
    clean = token.casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»")
    clean = _stem_ru(clean)
    if len(clean) >= 8:
        return clean[:7]
    if len(clean) >= 6:
        return clean[:5]
    return clean


def _stem_ru(token: str) -> str:
    if not re.search(r"[а-я]", token):
        return token
    endings = (
        "иями",
        "ями",
        "ами",
        "ого",
        "ему",
        "ыми",
        "ими",
        "его",
        "ая",
        "яя",
        "ое",
        "ее",
        "ые",
        "ие",
        "ый",
        "ий",
        "ой",
        "ом",
        "ем",
        "ах",
        "ях",
        "ов",
        "ев",
        "ам",
        "ям",
        "ою",
        "ею",
        "ей",
        "у",
        "ю",
        "а",
        "я",
        "ы",
        "и",
        "е",
        "ь",
    )
    for ending in endings:
        if len(token) > len(ending) + 3 and token.endswith(ending):
            return token[: -len(ending)]
    return token
