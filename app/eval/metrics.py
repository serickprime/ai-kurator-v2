"""Evaluation metrics for evidence-first RAG."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvalScore:
    """Per-case metric bundle."""

    document_precision: float
    source_precision: float
    evidence_precision: float
    answer_term_score: float
    forbidden_leakage: float
    claim_grounding_score: float
    final_score: float
    checks: dict[str, Any] = field(default_factory=dict)


def source_precision(used_source_ids: set[str], shown_source_ids: set[str]) -> float:
    """Measure how many shown sources were actually used."""
    if not shown_source_ids:
        return 1.0
    return len(used_source_ids & shown_source_ids) / len(shown_source_ids)


def score_eval_case(case: Any, prediction: Any) -> EvalScore:
    """Score one eval case against one normalized prediction."""
    documents = tuple(getattr(prediction, "documents", ()))
    evidence_items = tuple(getattr(prediction, "evidence_items", ()))
    chunks = tuple(getattr(prediction, "chunks", ()))
    sources = tuple(getattr(prediction, "sources", ()))
    discarded_candidates = tuple(getattr(prediction, "discarded_candidates", ()))
    answer = str(getattr(prediction, "answer", ""))
    answer_mode = str(getattr(prediction, "answer_mode", ""))

    expected_documents = tuple(getattr(case, "expected_documents", ()))
    forbidden_documents = tuple(getattr(case, "forbidden_documents", ()))
    expected_answer_terms = tuple(getattr(case, "expected_answer_terms", ()))
    forbidden_answer_terms = tuple(getattr(case, "forbidden_answer_terms", ()))
    expected_supported_points = tuple(getattr(case, "expected_supported_points", ()))

    document_haystack = documents + sources
    evidence_haystack = evidence_items + chunks
    all_output = (answer,) + documents + sources + evidence_haystack

    expected_document_hits = matched_terms(expected_documents, document_haystack)
    forbidden_document_hits = matched_terms(forbidden_documents, document_haystack)
    forbidden_answer_hits = matched_terms(forbidden_answer_terms, (answer,))
    forbidden_output_hits = matched_terms(forbidden_documents + forbidden_answer_terms, all_output)

    document_precision_score = _precision_with_expected(expected_documents, documents, expected_document_hits)
    source_precision_score = _source_precision_for_case(case, sources, expected_documents)
    evidence_precision_score = _evidence_precision(
        expected_documents + expected_answer_terms + expected_supported_points,
        evidence_haystack,
        expected_answer_mode=str(getattr(case, "expected_answer_mode", "")),
    )
    answer_term_score_value = term_coverage(expected_answer_terms, answer)
    if forbidden_answer_hits:
        answer_term_score_value = 0.0

    forbidden_leakage_value = 1.0 if forbidden_output_hits else 0.0
    claim_grounding_score_value = _claim_grounding_score(
        expected_supported_points,
        answer=answer,
        evidence_text="\n".join(evidence_haystack),
        answer_mode=answer_mode,
    )

    expected_answer_mode = str(getattr(case, "expected_answer_mode", ""))
    mode_matches = answer_mode == expected_answer_mode
    source_count = len([source for source in sources if source])
    source_count_max = int(getattr(case, "expected_source_count_max", 999999))
    source_count_ok = source_count <= source_count_max
    requires_sources = bool(getattr(case, "requires_sources", False))
    sources_required_ok = bool(source_count) if requires_sources else True
    sources_with_missing_data = answer_mode == "ask_for_missing_data" and source_count > 0

    used_discarded = bool(getattr(prediction, "used_discarded_candidates", False))
    if discarded_candidates:
        used_discarded = used_discarded or bool(matched_terms(discarded_candidates, (answer,) + sources + evidence_haystack))
    discarded_ok = not bool(getattr(case, "must_not_use_discarded_candidates", False)) or not used_discarded

    mode_score = 1.0 if mode_matches else 0.0
    weighted = (
        document_precision_score * 0.18
        + source_precision_score * 0.16
        + evidence_precision_score * 0.18
        + answer_term_score_value * 0.16
        + claim_grounding_score_value * 0.20
        + mode_score * 0.12
    )
    penalty = 0.0
    if forbidden_leakage_value:
        penalty += 0.30
    if not source_count_ok:
        penalty += 0.15
    if not sources_required_ok:
        penalty += 0.15
    if sources_with_missing_data:
        penalty += 0.20
    if not discarded_ok:
        penalty += 0.25

    final = clamp01(weighted - penalty)
    checks = {
        "expected_document_hits": expected_document_hits,
        "forbidden_document_hits": forbidden_document_hits,
        "forbidden_answer_hits": forbidden_answer_hits,
        "forbidden_output_hits": forbidden_output_hits,
        "answer_mode_matches": mode_matches,
        "source_count": source_count,
        "source_count_ok": source_count_ok,
        "sources_required_ok": sources_required_ok,
        "sources_with_missing_data": sources_with_missing_data,
        "used_discarded_candidates": used_discarded,
        "discarded_ok": discarded_ok,
    }
    return EvalScore(
        document_precision=round(document_precision_score, 4),
        source_precision=round(source_precision_score, 4),
        evidence_precision=round(evidence_precision_score, 4),
        answer_term_score=round(answer_term_score_value, 4),
        forbidden_leakage=round(forbidden_leakage_value, 4),
        claim_grounding_score=round(claim_grounding_score_value, 4),
        final_score=round(final, 4),
        checks=checks,
    )


def matched_terms(terms: tuple[str, ...] | list[str], haystacks: tuple[str, ...] | list[str]) -> list[str]:
    """Return terms that appear in any haystack text."""
    matched: list[str] = []
    for term in terms:
        if any(term_matches_text(term, text) for text in haystacks):
            matched.append(term)
    return matched


def term_coverage(terms: tuple[str, ...] | list[str], text: str) -> float:
    """Return the fraction of expected terms covered by text."""
    if not terms:
        return 1.0
    return len(matched_terms(list(terms), [text])) / len(terms)


def term_matches_text(term: str, text: str) -> bool:
    """Return true when a term is reasonably represented in text."""
    normalized_term = normalize_text(term)
    normalized_text = normalize_text(text)
    if not normalized_term:
        return False
    if normalized_term in normalized_text:
        return True

    term_tokens = set(tokenize(normalized_term))
    text_tokens = set(tokenize(normalized_text))
    term_roots = {_root(token) for token in term_tokens}
    text_roots = {_root(token) for token in text_tokens}
    if not term_tokens:
        return False
    if len(term_tokens) == 1:
        token = next(iter(term_tokens))
        root = _root(token)
        return token in text_tokens or root in text_roots
    overlap = term_tokens & text_tokens
    root_overlap = term_roots & text_roots
    return max(len(overlap) / len(term_tokens), len(root_overlap) / len(term_roots)) >= 0.6


def normalize_text(text: str) -> str:
    """Normalize text for language-agnostic fuzzy matching."""
    lowered = str(text).lower().replace("ё", "е")
    lowered = lowered.replace("н8н", "n8n").replace("n8н", "n8n")
    return re.sub(r"\s+", " ", lowered).strip()


def tokenize(text: str) -> tuple[str, ...]:
    """Tokenize normalized text into comparable terms."""
    return tuple(token for token in re.findall(r"[\w#+.-]{2,}", text, re.UNICODE) if token)


def _root(token: str) -> str:
    """Return a coarse language-agnostic token root for fuzzy eval matching."""
    if len(token) <= 4:
        return token
    for suffix in (
        "иями",
        "ями",
        "ами",
        "ого",
        "ему",
        "ыми",
        "ыми",
        "ing",
        "tion",
        "ки",
        "ка",
        "ия",
        "ие",
        "ый",
        "ой",
        "ая",
        "ую",
        "ов",
        "ев",
        "ам",
        "ом",
        "ах",
        "ях",
        "ed",
        "es",
        "s",
        "а",
        "у",
        "ы",
        "и",
        "е",
    ):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    if len(token) >= 8:
        return token[:7]
    if len(token) >= 6:
        return token[:5]
    return token


def clamp01(value: float) -> float:
    """Clamp a metric to the 0..1 range."""
    return max(0.0, min(1.0, value))


def _precision_with_expected(
    expected_terms: tuple[str, ...],
    actual_items: tuple[str, ...],
    hits: list[str],
) -> float:
    if not expected_terms:
        return 1.0 if not actual_items else 0.0
    denominator = max(len([item for item in actual_items if item]), len(expected_terms), 1)
    return clamp01(len(hits) / denominator)


def _source_precision_for_case(case: Any, sources: tuple[str, ...], expected_documents: tuple[str, ...]) -> float:
    source_count = len([source for source in sources if source])
    requires_sources = bool(getattr(case, "requires_sources", False))
    if source_count == 0:
        return 0.0 if requires_sources else 1.0
    if not expected_documents:
        return 1.0
    hits = matched_terms(expected_documents, sources)
    return clamp01(len(hits) / source_count)


def _evidence_precision(
    expected_signals: tuple[str, ...],
    evidence_items: tuple[str, ...],
    *,
    expected_answer_mode: str,
) -> float:
    evidence_count = len([item for item in evidence_items if item])
    if expected_answer_mode in {"ask_for_missing_data", "general_answer_without_sources"}:
        return 1.0 if evidence_count == 0 else 0.0
    if not expected_signals:
        return 1.0 if evidence_count == 0 else 0.5
    if evidence_count == 0:
        return 0.0
    hits = matched_terms(expected_signals, evidence_items)
    return clamp01(len(hits) / max(evidence_count, len(expected_signals), 1))


def _claim_grounding_score(
    expected_supported_points: tuple[str, ...],
    *,
    answer: str,
    evidence_text: str,
    answer_mode: str,
) -> float:
    if not expected_supported_points:
        return 1.0
    grounded = 0
    material_answer = answer_mode in {"answer_from_materials", "partial_answer"}
    for point in expected_supported_points:
        answer_has_point = term_matches_text(point, answer)
        evidence_has_point = term_matches_text(point, evidence_text)
        if answer_has_point and (evidence_has_point or not material_answer):
            grounded += 1
    return grounded / len(expected_supported_points)
