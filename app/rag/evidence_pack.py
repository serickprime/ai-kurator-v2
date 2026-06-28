"""Evidence pack construction."""

from collections.abc import Sequence
import re

from app.rag import term_scoring
from app.rag.types import EvidencePack, EvidenceSpan, QuestionAnalysis, SourceRef

TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)


class EvidencePackBuilder:
    """Build the narrow context passed to answer generation."""

    def __init__(self, term_scorer: term_scoring.CorpusTermScorer | None = None) -> None:
        self._term_scorer = term_scorer or term_scoring.CorpusTermScorer.neutral()

    def build(
        self,
        spans: Sequence[EvidenceSpan],
        max_items: int = 5,
        analysis: QuestionAnalysis | None = None,
    ) -> EvidencePack:
        """Build a compact evidence pack from reranked spans."""
        selected = tuple(_strong_spans(spans, analysis, max_items=max_items, term_scorer=self._term_scorer))
        answer_mode = _answer_mode(selected, analysis)
        missing = tuple(analysis.missing_input_requirements) if analysis else ()
        return EvidencePack(
            items=selected,
            answer_mode=answer_mode,
            source_matches=_source_matches(selected, answer_mode),
            missing_requirements=missing,
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


def _strong_spans(
    spans: Sequence[EvidenceSpan],
    analysis: QuestionAnalysis | None,
    *,
    max_items: int,
    term_scorer: term_scoring.CorpusTermScorer,
) -> list[EvidenceSpan]:
    selected: list[EvidenceSpan] = []
    for span in spans:
        if not span.text.strip():
            continue
        if span.score is not None and span.score < 0.16:
            continue
        if analysis is not None and _misses_object(span, analysis):
            continue
        if analysis is not None and term_scorer.has_statistics and not _has_strong_term(span, analysis, term_scorer):
            continue
        selected.append(span)
        if len(selected) >= max_items:
            break
    return selected


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
