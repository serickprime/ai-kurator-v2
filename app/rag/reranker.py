"""Evidence reranking."""

from collections.abc import Sequence
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
        return tuple(sorted(spans, key=lambda span: _sort_key(span, analysis)))


def _sort_key(span: EvidenceSpan, analysis: QuestionAnalysis) -> tuple[float, str]:
    score = span.score or 0.0
    text_roots = _roots(_tokens(" ".join([span.document_title, span.locator or "", span.text])))
    object_roots = _roots(analysis.object_terms)
    action_roots = _roots([analysis.requested_action]) if analysis.requested_action else set()
    constraint_roots = _roots([analysis.requested_attribute, *analysis.constraints])

    if object_roots and object_roots & text_roots:
        score += 0.18
    if action_roots and action_roots & text_roots:
        score += 0.08
    if constraint_roots and constraint_roots & text_roots:
        score += 0.10
    if span.metadata.get("fact_ids") or "FACT-ID:" in span.text:
        score += 0.12
    if _near_miss_not_about(span, analysis):
        score -= 0.30
    return (-score, span.evidence_id)


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
