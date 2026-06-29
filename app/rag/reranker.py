"""Evidence reranking."""

from collections.abc import Sequence
from dataclasses import replace
import re

from app.rag.types import EvidenceSpan, QuestionAnalysis

TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)


class EvidenceReranker:
    """Order evidence spans before packing."""

    def rerank(
        self,
        spans: Sequence[EvidenceSpan],
        analysis: QuestionAnalysis | None = None,
    ) -> tuple[EvidenceSpan, ...]:
        """Return spans ordered by deterministic answerability signals."""
        if analysis is None:
            return tuple(spans)
        scored = [_with_reranker_score(span, analysis) for span in spans]
        return tuple(sorted(scored, key=_sort_key))


def _with_reranker_score(span: EvidenceSpan, analysis: QuestionAnalysis) -> EvidenceSpan:
    base_score = span.score or 0.0
    text = " ".join([span.document_title, span.locator or "", span.text])
    heading = " ".join([span.locator or "", str(span.metadata.get("heading") or "")])
    text_roots = _roots(_tokens(text))
    object_roots = _roots(analysis.object_terms)
    action_roots = _roots([analysis.requested_action]) if analysis.requested_action else set()
    constraint_roots = _roots([analysis.requested_attribute, *analysis.constraints])
    requirement_score = _requirement_coverage_score(span, analysis)
    actionability_score = _actionability_score(span.text)
    exact_anchor_score = _exact_anchor_score(span, analysis)
    heading_match_score = _match_ratio(_query_terms(analysis), heading)
    weak_penalty = _weak_chunk_penalty(span, analysis)

    score = base_score
    if object_roots and object_roots & text_roots:
        score += 0.16
    if action_roots and action_roots & text_roots:
        score += 0.08
    if constraint_roots and constraint_roots & text_roots:
        score += 0.10
    score += requirement_score * 0.20
    score += actionability_score * 0.22
    score += exact_anchor_score * 0.20
    score += heading_match_score * 0.10
    if span.metadata.get("fact_ids") or "FACT-ID:" in span.text:
        score += 0.12
    if _near_miss_not_about(span, analysis):
        score -= 0.30
    score -= weak_penalty

    metadata = dict(span.metadata or {})
    metadata["reranker_score"] = round(score, 4)
    metadata["reranker_score_breakdown"] = {
        "base_score": round(base_score, 4),
        "requirement_coverage": round(requirement_score, 4),
        "actionability": round(actionability_score, 4),
        "exact_anchor": round(exact_anchor_score, 4),
        "heading_match": round(heading_match_score, 4),
        "weak_chunk_penalty": round(weak_penalty, 4),
    }
    return replace(span, score=round(max(score, 0.0), 4), metadata=metadata)


def _sort_key(span: EvidenceSpan) -> tuple[float, str]:
    return (-(span.score or 0.0), span.evidence_id)


def _requirement_coverage_score(span: EvidenceSpan, analysis: QuestionAnalysis) -> float:
    terms = _requirement_terms(analysis)
    if not terms:
        return 0.0
    return _match_ratio(terms, " ".join([span.locator or "", span.text]))


def _actionability_score(text: str) -> float:
    lowered = text.casefold()
    signals = 0
    checks = (
        bool(re.search(r"(^|\n)\s*(?:[0-9]+[.)]|[-*•])\s+\S+", text)),
        bool(re.search(r"`[^`]+`|```", text)),
        bool(re.search(r"\b(?:python|pip|npm|npx|node|docker|git|curl|touch|mkdir|cd|psql|supabase)\b", lowered)),
        bool(re.search(r"\b[A-Za-z0-9_.-]+\.(?:json|ya?ml|toml|env|md|txt|py|js|ts|sql|html|css)\b", text)),
        bool(re.search(r"\b[A-Z][A-Z0-9_]{2,}\b|\b[a-zA-Z_][a-zA-Z0-9_]*=", text)),
        any(word in lowered for word in ("пример", "проверь", "проверить", "результат", "ошибка", "если", "условие")),
        any(word in lowered for word in ("создай", "добавь", "укажи", "настрой", "используйте", "запусти", "открой")),
    )
    for matched in checks:
        signals += int(matched)
    return min(signals / 4, 1.0)


def _exact_anchor_score(span: EvidenceSpan, analysis: QuestionAnalysis) -> float:
    anchors = _exact_anchors(analysis)
    if not anchors:
        return 0.0
    text = " ".join([span.locator or "", span.text]).casefold()
    matched = 0
    for anchor in anchors:
        clean = anchor.casefold().strip()
        if clean and clean in text:
            matched += 1
    return min(matched / max(len(anchors), 1), 1.0)


def _weak_chunk_penalty(span: EvidenceSpan, analysis: QuestionAnalysis) -> float:
    text = span.text.strip()
    lowered = text.casefold()
    penalty = 0.0
    if len(text) < 120:
        penalty += 0.10
    navigation_markers = (
        "следующий урок",
        "предыдущий урок",
        "понравился ли урок",
        "отправить",
        "нравится",
        "подписаться",
        "действия",
        "текст страницы",
        "визуальные элементы",
    )
    if any(marker in lowered for marker in navigation_markers):
        penalty += 0.18
    query_terms = _query_terms(analysis)
    if query_terms and _match_ratio(query_terms, text) < 0.2:
        penalty += 0.12
    if _actionability_score(text) == 0 and analysis.task_type in {"setup", "debug", "admin", "general"}:
        penalty += 0.08
    return min(penalty, 0.45)


def _query_terms(analysis: QuestionAnalysis) -> tuple[str, ...]:
    return tuple(
        term
        for term in (
            *analysis.object_terms,
            *analysis.action_terms,
            analysis.requested_action,
            analysis.requested_attribute,
            *analysis.exact_terms,
            *analysis.rare_anchor_terms,
            *analysis.config_terms,
            *analysis.strongest_evidence_terms,
        )
        if str(term).strip()
    )


def _requirement_terms(analysis: QuestionAnalysis) -> tuple[str, ...]:
    parts = [*analysis.evidence_questions, *analysis.must_answer_points]
    roots = _roots(_tokens(" ".join(parts)))
    generic = {
        "источ",
        "действ",
        "пункт",
        "ответ",
        "матер",
        "нужн",
        "подтв",
        "source",
        "answer",
    }
    return tuple(root for root in roots if root not in generic)


def _exact_anchors(analysis: QuestionAnalysis) -> tuple[str, ...]:
    text = " ".join(
        [
            analysis.original_question,
            " ".join(analysis.exact_terms),
            " ".join(analysis.rare_anchor_terms),
            " ".join(analysis.config_terms),
            " ".join(analysis.strongest_evidence_terms),
        ]
    )
    anchors = set()
    for match in re.findall(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", text):
        anchors.update(part.strip() for part in match if part.strip())
    anchors.update(re.findall(r"\b[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+\b", text))
    anchors.update(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text))
    anchors.update(term for term in (*analysis.exact_terms, *analysis.rare_anchor_terms, *analysis.config_terms) if term)
    return tuple(sorted(anchor for anchor in anchors if len(anchor) >= 2))


def _match_ratio(terms: Sequence[str], text: str) -> float:
    term_roots = _roots(_tokens(" ".join(terms)))
    if not term_roots:
        return 0.0
    text_roots = _roots(_tokens(text))
    return len(term_roots & text_roots) / max(len(term_roots), 1)


def _near_miss_not_about(span: EvidenceSpan, analysis: QuestionAnalysis) -> bool:
    lowered = " ".join([span.locator or "", span.text]).casefold()
    if "не объясняет" not in lowered and "не относится" not in lowered:
        return False
    object_roots = _roots(analysis.object_terms)
    return bool(object_roots) and not (object_roots & _roots(_tokens(span.document_title)))


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
