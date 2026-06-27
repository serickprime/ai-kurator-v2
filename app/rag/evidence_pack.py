"""Evidence pack construction."""

from collections.abc import Sequence

from app.rag.types import EvidencePack, EvidenceSpan, QuestionAnalysis, SourceRef


class EvidencePackBuilder:
    """Build the narrow context passed to answer generation."""

    def build(
        self,
        spans: Sequence[EvidenceSpan],
        max_items: int = 12,
        analysis: QuestionAnalysis | None = None,
    ) -> EvidencePack:
        """Build a compact evidence pack from reranked spans."""
        selected = tuple(span for span in spans if span.text.strip())[:max_items]
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
        return "ask_for_missing_data"
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
