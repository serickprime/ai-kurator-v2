"""Read-only discovery of candidate retrieval glossary anchors."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from app.rag.query_enrichment import (
    DEFAULT_QUERY_GLOSSARY_CONFIG,
    QueryGlossaryConfig,
    QueryGlossaryConfigError,
    load_query_glossary_config,
)
from app.rag.term_scoring import exact_terms as extract_exact_terms
from app.rag.term_scoring import guess_term_type

_CAMEL_CASE_RE = re.compile(r"\b[A-Za-z]+[a-z][A-Z][A-Za-z0-9]*\b")
_SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b")
_FUNCTION_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]+\([^)\s]{0,80}\)")
_CLI_FLAG_RE = re.compile(r"(?<!\w)--[A-Za-z0-9][A-Za-z0-9_-]*")
_ENDPOINT_RE = re.compile(r"(?<!\w)/(?:[A-Za-z0-9_.:-]+/?){1,6}")
_NODE_NAME_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,4}\s+node\b")
_PARAMETER_HINT_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*(?:_id|_url|_key|_token|_name|_type)\b")
_HEXISH_RE = re.compile(r"^[a-f0-9]{16,}$", re.IGNORECASE)
_UUID_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}")
_RANDOM_SAMPLE_RE = re.compile(r"^[A-Za-z0-9-]{10,}$")
_SENSITIVE_RE = re.compile(
    r"(api[_-]?key|secret|service[_-]?role|token|bearer|authorization|password|db[_-]?pass|passphrase)",
    re.IGNORECASE,
)
_NOISE_TERMS = {
    "definition_candidate",
    "primary_definition",
    "external_docs",
    "official_docs",
    "how_to",
    "source_name",
    "source_type",
    "canonical_url",
    "content_hash",
    "document_key",
}
_COMMON_ANCHOR_NOISE = {
    "api",
    "delete",
    "docs",
    "documentation",
    "example",
    "github",
    "head",
    "http",
    "https",
    "html",
    "javascript",
    "json",
    "openai",
    "options",
    "patch",
    "post",
    "python",
    "request",
    "response",
    "rest",
    "typescript",
    "your",
    "yourport",
}
_LOCAL_SOURCE_HINTS = {
    "local",
    "smoke",
    "test",
    "tmp",
    "upload",
}


@dataclass(frozen=True)
class GlossaryCandidate:
    """Suggested retrieval anchors that still need owner/admin review."""

    service_id: str | None = None
    source_id: str | None = None
    topic: str = "general"
    user_phrases: tuple[str, ...] = ()
    technical_terms: tuple[str, ...] = ()
    exact_terms: tuple[str, ...] = ()
    config_terms: tuple[str, ...] = ()
    reason: str = ""
    source_refs: tuple[str, ...] = ()
    confidence: float = 0.0
    status: str = "suggested"
    review_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "service_id": self.service_id,
            "source_id": self.source_id,
            "topic": self.topic,
            "user_phrases": list(self.user_phrases),
            "technical_terms": list(self.technical_terms),
            "exact_terms": list(self.exact_terms),
            "config_terms": list(self.config_terms),
            "reason": self.reason,
            "source_refs": list(self.source_refs),
            "confidence": self.confidence,
            "status": self.status,
            "review_flags": list(self.review_flags),
            "next_action": "review manually; do not auto-apply",
        }


@dataclass(frozen=True)
class GlossaryCandidateReport:
    """Read-only discovery report."""

    workspace: str
    candidates: tuple[GlossaryCandidate, ...]
    existing_glossary_services_checked: tuple[str, ...] = ()
    skipped_duplicates: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    mode: str = "read-only"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "mode": self.mode,
            "workspace": self.workspace,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "existing_glossary_services_checked": list(self.existing_glossary_services_checked),
            "skipped_duplicates": self.skipped_duplicates,
            "source_counts": self.source_counts,
            "warnings": list(self.warnings),
            "not_auto_applied": True,
        }


@dataclass(frozen=True)
class _Observation:
    service_id: str | None
    source_id: str | None
    topic: str
    user_phrases: tuple[str, ...] = ()
    technical_terms: tuple[str, ...] = ()
    exact_terms: tuple[str, ...] = ()
    config_terms: tuple[str, ...] = ()
    reason: str = ""
    source_refs: tuple[str, ...] = ()
    confidence: float = 0.45


@dataclass(frozen=True)
class _ExistingGlossaryIndex:
    service_ids: tuple[str, ...]
    terms: frozenset[str]

    @classmethod
    def from_config(cls, config: QueryGlossaryConfig | None) -> "_ExistingGlossaryIndex":
        if config is None:
            return cls(service_ids=(), terms=frozenset())
        service_ids: list[str] = []
        terms: list[str] = []
        for service in config.services:
            service_ids.append(service.service_id)
            terms.extend([service.service_id, service.display_name, *service.aliases])
            for rule in service.rules:
                terms.extend([*rule.phrases, *rule.exact_terms, *rule.config_terms])
        return cls(service_ids=tuple(service_ids), terms=frozenset(_term_key(term) for term in terms if term))

    def is_known(self, term: str) -> bool:
        """Return true when the seed glossary already contains this term or phrase."""
        return _term_key(term) in self.terms


def load_existing_glossary_index(path: Path | str = DEFAULT_QUERY_GLOSSARY_CONFIG) -> _ExistingGlossaryIndex:
    """Load the existing seed glossary for duplicate filtering."""
    try:
        return _ExistingGlossaryIndex.from_config(load_query_glossary_config(path))
    except QueryGlossaryConfigError:
        return _ExistingGlossaryIndex.from_config(None)


def discover_glossary_candidates(
    *,
    workspace: str = "unknown",
    existing_glossary: QueryGlossaryConfig | None = None,
    term_statistics: Sequence[Mapping[str, Any]] = (),
    evidence_logs: Sequence[Mapping[str, Any]] = (),
    documents: Sequence[Mapping[str, Any]] = (),
    document_cards: Sequence[Mapping[str, Any]] = (),
    sections: Sequence[Mapping[str, Any]] = (),
    chunks: Sequence[Mapping[str, Any]] = (),
    limit: int = 30,
) -> GlossaryCandidateReport:
    """Discover reviewed-only glossary candidates from read-only row snapshots."""
    existing = _ExistingGlossaryIndex.from_config(existing_glossary)
    observations: list[_Observation] = []
    observations.extend(_from_term_statistics(term_statistics))
    observations.extend(_from_evidence_logs(evidence_logs))
    observations.extend(_from_documents(documents))
    observations.extend(_from_document_cards(document_cards, documents))
    observations.extend(_from_sections(sections, documents))
    observations.extend(_from_chunks(chunks, documents))

    grouped = _group_observations(observations)
    candidates: list[GlossaryCandidate] = []
    skipped_duplicates = 0
    for group in grouped.values():
        candidate, skipped = _candidate_from_group(group, existing)
        skipped_duplicates += skipped
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (-item.confidence, item.service_id or "", item.source_id or "", item.topic))
    limited = tuple(candidates[: max(limit, 0)])
    return GlossaryCandidateReport(
        workspace=workspace,
        candidates=limited,
        existing_glossary_services_checked=existing.service_ids,
        skipped_duplicates=skipped_duplicates,
        source_counts={
            "term_statistics": len(term_statistics),
            "evidence_logs": len(evidence_logs),
            "documents": len(documents),
            "document_cards": len(document_cards),
            "sections": len(sections),
            "chunks": len(chunks),
        },
    )


def format_glossary_candidate_report(report: GlossaryCandidateReport) -> str:
    """Format a safe owner-facing report."""
    lines = [
        "Glossary Candidate Discovery Report",
        "",
        f"- mode: {report.mode}",
        f"- workspace: {report.workspace}",
        f"- candidates: {len(report.candidates)}",
        "- existing glossary services checked: "
        + (", ".join(report.existing_glossary_services_checked) if report.existing_glossary_services_checked else "none"),
        f"- skipped duplicates: {report.skipped_duplicates}",
        "- status: suggestions only; not auto-applied; config/query_glossary.yaml unchanged",
    ]
    if report.source_counts:
        counts = ", ".join(f"{key}={value}" for key, value in report.source_counts.items())
        lines.append(f"- source rows: {counts}")
    for warning in report.warnings:
        lines.append(f"- warning: {warning}")

    for index, candidate in enumerate(report.candidates, start=1):
        lines.extend(
            [
                "",
                f"Candidate {index}",
                "",
                f"- service/source: {_service_source_label(candidate)}",
                f"- topic: {candidate.topic}",
                "- suggested user phrases:",
                *_bullet_lines(candidate.user_phrases),
                "- suggested technical anchors:",
                *_bullet_lines(candidate.technical_terms),
                "- suggested exact/config terms:",
                *_bullet_lines(_dedupe([*candidate.exact_terms, *candidate.config_terms], limit=20)),
                f"- reason: {candidate.reason}",
                "- source refs:",
                *_bullet_lines(candidate.source_refs),
                f"- confidence: {candidate.confidence:.2f}",
                f"- status: {candidate.status}",
                "- review flags:",
                *_bullet_lines(candidate.review_flags),
                "- next action: review manually; do not auto-apply",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _from_term_statistics(rows: Sequence[Mapping[str, Any]]) -> list[_Observation]:
    observations: list[_Observation] = []
    for row in rows:
        term = _clean_term(row.get("term") or row.get("normalized_term"))
        if not term or not _looks_useful_term_stat(row, term):
            continue
        metadata = _mapping(row.get("metadata"))
        service_id, source_id = _service_source_from_row(row, metadata)
        exact_terms, config_terms = _classify_terms((term,), str(row.get("term_type_guess") or ""))
        observations.append(
            _Observation(
                service_id=service_id,
                source_id=source_id,
                topic=_topic_from_row(row, metadata, fallback=str(row.get("term_type_guess") or "term_statistics")),
                technical_terms=(term,),
                exact_terms=exact_terms,
                config_terms=config_terms,
                reason="rare or technical term from term_statistics",
                source_refs=tuple(_term_stat_refs(row, term)),
                confidence=0.62 if exact_terms or config_terms else 0.52,
            )
        )
    return observations


def _from_evidence_logs(rows: Sequence[Mapping[str, Any]]) -> list[_Observation]:
    observations: list[_Observation] = []
    for row in rows:
        question = _safe_phrase(row.get("question"))
        if question.startswith("/"):
            continue
        analysis = _mapping(row.get("question_analysis"))
        pack = _mapping(row.get("evidence_pack"))
        items = [_mapping(item) for item in pack.get("items") or []]
        if not items and pack.get("accepted_evidence"):
            items = [_mapping(item) for item in pack.get("accepted_evidence") or []]
        if not items:
            continue

        terms = _extract_terms_from_text(
            " ".join(
                [
                    " ".join(_string_list(analysis.get("exact_terms"))),
                    " ".join(_string_list(analysis.get("config_terms"))),
                    " ".join(_evidence_item_text(item) for item in items[:6]),
                ]
            )
        )
        technical_terms, exact_terms, config_terms = terms
        if not (technical_terms or exact_terms or config_terms):
            continue

        service_id, source_id = _service_source_from_evidence(items, row)
        observations.append(
            _Observation(
                service_id=service_id,
                source_id=source_id,
                topic=_topic_from_evidence(items, row),
                user_phrases=(question,) if question else (),
                technical_terms=technical_terms,
                exact_terms=exact_terms,
                config_terms=config_terms,
                reason="user question and accepted evidence suggest missing retrieval anchors",
                source_refs=tuple(_evidence_refs(row, items)),
                confidence=0.72,
            )
        )
    return observations


def _from_documents(rows: Sequence[Mapping[str, Any]]) -> list[_Observation]:
    observations: list[_Observation] = []
    for row in rows:
        metadata = _mapping(row.get("metadata"))
        service_id, source_id = _service_source_from_row(row, metadata)
        text = " ".join(
            str(value or "")
            for value in (
                row.get("title"),
                row.get("filename"),
                row.get("course"),
                row.get("module"),
                row.get("lesson"),
                _metadata_text(metadata),
            )
        )
        technical_terms, exact_terms, config_terms = _extract_terms_from_text(text)
        if not (technical_terms or exact_terms or config_terms):
            continue
        observations.append(
            _Observation(
                service_id=service_id,
                source_id=source_id,
                topic=_topic_from_row(row, metadata, fallback="document metadata"),
                technical_terms=technical_terms,
                exact_terms=exact_terms,
                config_terms=config_terms,
                reason="active document metadata contains technical anchors",
                source_refs=(_document_ref(row),),
                confidence=0.50,
            )
        )
    return observations


def _from_document_cards(
    rows: Sequence[Mapping[str, Any]],
    documents: Sequence[Mapping[str, Any]],
) -> list[_Observation]:
    docs_by_id = _documents_by_id(documents)
    observations: list[_Observation] = []
    for row in rows:
        document = docs_by_id.get(str(row.get("document_id") or ""), {})
        metadata = _merged_metadata(document, row)
        service_id, source_id = _service_source_from_row(document or row, metadata)
        questions = tuple(_safe_phrase(item) for item in _string_list(row.get("questions_answered")) if _safe_phrase(item))
        text = " ".join(
            [
                str(row.get("summary") or ""),
                " ".join(_string_list(row.get("topics"))),
                " ".join(_string_list(row.get("entities"))),
                " ".join(_string_list(row.get("task_types"))),
                _metadata_text(metadata),
            ]
        )
        technical_terms, exact_terms, config_terms = _extract_terms_from_text(text)
        if not (questions or technical_terms or exact_terms or config_terms):
            continue
        observations.append(
            _Observation(
                service_id=service_id,
                source_id=source_id,
                topic=_first_text(_string_list(row.get("topics"))) or _topic_from_row(document, metadata, fallback="document card"),
                user_phrases=questions,
                technical_terms=technical_terms,
                exact_terms=exact_terms,
                config_terms=config_terms,
                reason="document card topics/questions expose possible retrieval anchors",
                source_refs=(_document_ref(document) if document else f"document_card:{row.get('document_id')}",),
                confidence=0.58,
            )
        )
    return observations


def _from_sections(
    rows: Sequence[Mapping[str, Any]],
    documents: Sequence[Mapping[str, Any]],
) -> list[_Observation]:
    docs_by_id = _documents_by_id(documents)
    observations: list[_Observation] = []
    for row in rows:
        document = docs_by_id.get(str(row.get("document_id") or ""), {})
        metadata = _merged_metadata(document, row)
        service_id, source_id = _service_source_from_row(document or row, metadata)
        heading = str(row.get("heading") or "")
        text = " ".join([heading, str(row.get("summary") or ""), _metadata_text(metadata)])
        technical_terms, exact_terms, config_terms = _extract_terms_from_text(text)
        if not (technical_terms or exact_terms or config_terms):
            continue
        observations.append(
            _Observation(
                service_id=service_id,
                source_id=source_id,
                topic=_short_label(heading) or _topic_from_row(document, metadata, fallback="section"),
                technical_terms=technical_terms,
                exact_terms=exact_terms,
                config_terms=config_terms,
                reason="section heading/summary contains technical anchors",
                source_refs=(f"section:{_short_label(heading) or row.get('id')}",),
                confidence=0.55,
            )
        )
    return observations


def _from_chunks(
    rows: Sequence[Mapping[str, Any]],
    documents: Sequence[Mapping[str, Any]],
) -> list[_Observation]:
    docs_by_id = _documents_by_id(documents)
    observations: list[_Observation] = []
    for row in rows:
        document = docs_by_id.get(str(row.get("document_id") or ""), {})
        metadata = _merged_metadata(document, row)
        service_id, source_id = _service_source_from_row(document or row, metadata)
        heading = str(row.get("heading") or "")
        content = str(row.get("content") or "")[:2400]
        technical_terms, exact_terms, config_terms = _extract_terms_from_text(" ".join([heading, content, _metadata_text(metadata)]))
        if not (technical_terms or exact_terms or config_terms):
            continue
        observations.append(
            _Observation(
                service_id=service_id,
                source_id=source_id,
                topic=_short_label(heading) or _topic_from_row(document, metadata, fallback="chunk"),
                technical_terms=technical_terms,
                exact_terms=exact_terms,
                config_terms=config_terms,
                reason="active chunk text contains technical anchors",
                source_refs=(f"chunk:{_short_label(heading) or row.get('id')}",),
                confidence=0.52,
            )
        )
    return observations


def _group_observations(observations: Sequence[_Observation]) -> dict[tuple[str, str, str], list[_Observation]]:
    grouped: dict[tuple[str, str, str], list[_Observation]] = {}
    for item in observations:
        key = (item.service_id or "", item.source_id or "", _topic_key(item.topic))
        grouped.setdefault(key, []).append(item)
    return grouped


def _candidate_from_group(
    group: Sequence[_Observation],
    existing: _ExistingGlossaryIndex,
) -> tuple[GlossaryCandidate | None, int]:
    user_phrases, skipped_user_phrases = _unknown_terms([term for item in group for term in item.user_phrases], existing, limit=8)
    technical_terms, skipped_technical = _unknown_terms([term for item in group for term in item.technical_terms], existing, limit=14)
    exact_terms, skipped_exact = _unknown_terms([term for item in group for term in item.exact_terms], existing, limit=10)
    config_terms, skipped_config = _unknown_terms([term for item in group for term in item.config_terms], existing, limit=10)
    skipped = skipped_user_phrases + skipped_technical + skipped_exact + skipped_config

    if not (technical_terms or exact_terms or config_terms):
        return None, skipped

    first = group[0]
    reasons = _dedupe([item.reason for item in group if item.reason], limit=4)
    source_refs = _dedupe([ref for item in group for ref in item.source_refs], limit=8)
    review_flags, quality_notes = _candidate_review_flags(
        group,
        user_phrases=user_phrases,
        technical_terms=technical_terms,
        exact_terms=exact_terms,
        config_terms=config_terms,
    )
    confidence = _confidence(
        group,
        exact_terms=exact_terms,
        config_terms=config_terms,
        user_phrases=user_phrases,
        review_flags=review_flags,
    )
    status = _candidate_status(review_flags)
    return (
        GlossaryCandidate(
            service_id=first.service_id,
            source_id=first.source_id,
            topic=first.topic or "general",
            user_phrases=tuple(user_phrases),
            technical_terms=tuple(technical_terms),
            exact_terms=tuple(exact_terms),
            config_terms=tuple(config_terms),
            reason="; ".join(_dedupe([*reasons, *quality_notes], limit=8)),
            source_refs=tuple(source_refs),
            confidence=confidence,
            status=status,
            review_flags=tuple(review_flags),
        ),
        skipped,
    )


def _extract_terms_from_text(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    candidates: list[str] = []
    for regex in (_FUNCTION_RE, _ENDPOINT_RE, _CLI_FLAG_RE, _NODE_NAME_RE, _SNAKE_CASE_RE, _PARAMETER_HINT_RE, _CAMEL_CASE_RE):
        candidates.extend(match.group(0) for match in regex.finditer(text or ""))
    candidates.extend(extract_exact_terms(text or ""))
    terms = _dedupe([_clean_term(term) for term in candidates if _clean_term(term)], limit=30)
    exact_terms, config_terms = _classify_terms(terms, "")
    technical_terms = _dedupe([*terms, *exact_terms, *config_terms], limit=30)
    return tuple(technical_terms), exact_terms, config_terms


def _classify_terms(terms: Sequence[str], fallback_type: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    exact: list[str] = []
    config: list[str] = []
    for term in terms:
        clean = _clean_term(term)
        if not clean:
            continue
        term_type = fallback_type or guess_term_type(clean)
        if _is_config_like(clean, term_type):
            config.append(clean)
        if _is_exact_like(clean, term_type):
            exact.append(clean)
    return tuple(_dedupe(exact, limit=16)), tuple(_dedupe(config, limit=16))


def _looks_useful_term_stat(row: Mapping[str, Any], term: str) -> bool:
    term_type = str(row.get("term_type_guess") or guess_term_type(term))
    return (
        term_type != "term"
        or _is_exact_like(term, term_type)
        or _is_config_like(term, term_type)
    )


def _is_exact_like(term: str, term_type: str) -> bool:
    return (
        term_type in {"function", "endpoint_or_address", "error_or_code", "technical_identifier"}
        or bool(_CAMEL_CASE_RE.search(term))
        or bool(_NODE_NAME_RE.search(term))
        or term.startswith("/")
    )


def _is_config_like(term: str, term_type: str) -> bool:
    return (
        term_type in {"identifier", "path_or_parameter", "config"}
        or bool(_SNAKE_CASE_RE.fullmatch(term))
        or bool(_PARAMETER_HINT_RE.fullmatch(term))
        or term.startswith("--")
    )


def _service_source_from_evidence(
    items: Sequence[Mapping[str, Any]],
    row: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    for item in items:
        metadata = _mapping(item.get("metadata"))
        service_id, source_id = _service_source_from_row(item, metadata)
        if service_id or source_id:
            return service_id, source_id
    sources = _string_list(row.get("final_sources"))
    return None, _first_text(sources)


def _service_source_from_row(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> tuple[str | None, str | None]:
    service_id = _first_text(
        [
            row.get("service_id"),
            metadata.get("service_id"),
            *_string_list(metadata.get("service_ids")),
        ]
    )
    source_id = _first_text(
        [
            row.get("source_id"),
            row.get("source_name"),
            metadata.get("source_id"),
            metadata.get("source_name"),
            metadata.get("docs_source"),
            row.get("document_key"),
        ]
    )
    normalized_source = _safe_slug(source_id)
    normalized_service = _safe_slug(service_id) or _service_from_source(normalized_source)
    return normalized_service, normalized_source


def _topic_from_evidence(items: Sequence[Mapping[str, Any]], row: Mapping[str, Any]) -> str:
    for item in items:
        topic = _short_label(item.get("heading") or item.get("locator") or item.get("document_title"))
        if topic:
            return topic
    question = _safe_phrase(row.get("question"))
    return _short_label(question) or "evidence logs"


def _topic_from_row(row: Mapping[str, Any], metadata: Mapping[str, Any], *, fallback: str) -> str:
    return (
        _short_label(metadata.get("topic"))
        or _short_label(row.get("lesson"))
        or _short_label(row.get("module"))
        or _short_label(row.get("course"))
        or _short_label(row.get("title"))
        or fallback
    )


def _evidence_item_text(item: Mapping[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            item.get("text"),
            item.get("content"),
            item.get("heading"),
            item.get("locator"),
            item.get("document_title"),
            _metadata_text(_mapping(item.get("metadata"))),
        )
    )


def _evidence_refs(row: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> list[str]:
    refs = [f"evidence_log:{row.get('created_at') or 'latest'}"]
    for item in items[:5]:
        label = item.get("evidence_id") or item.get("locator") or item.get("document_title")
        if label:
            refs.append(f"evidence:{_short_label(label)}")
    return _dedupe(refs, limit=8)


def _term_stat_refs(row: Mapping[str, Any], term: str) -> list[str]:
    refs = [f"term_statistics:{term}"]
    for example in row.get("examples") or []:
        if isinstance(example, Mapping):
            label = _short_label(example.get("title") or example.get("filename") or example.get("document_id"))
            if label:
                refs.append(f"example:{label}")
        else:
            label = _short_label(example)
            if label:
                refs.append(f"example:{label}")
    return _dedupe(refs, limit=4)


def _document_ref(row: Mapping[str, Any]) -> str:
    return "document:" + (_short_label(row.get("title") or row.get("filename") or row.get("id")) or "unknown")


def _metadata_text(metadata: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key, value in metadata.items():
        if str(key).casefold() in {"embedding", "raw_candidates", "discarded_candidates"}:
            continue
        if isinstance(value, (str, int, float, bool)):
            values.append(str(value))
        elif isinstance(value, list):
            values.extend(str(item) for item in value[:12] if isinstance(item, (str, int, float, bool)))
    return " ".join(values)


def _merged_metadata(document: Mapping[str, Any], row: Mapping[str, Any]) -> Mapping[str, Any]:
    document_metadata = _mapping(document.get("metadata"))
    row_metadata = _mapping(row.get("metadata"))
    return {**document_metadata, **row_metadata}


def _documents_by_id(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(row.get("id") or ""): row for row in rows if row.get("id")}


def _confidence(
    group: Sequence[_Observation],
    *,
    exact_terms: Sequence[str],
    config_terms: Sequence[str],
    user_phrases: Sequence[str],
    review_flags: Sequence[str],
) -> float:
    base = max((item.confidence for item in group), default=0.45)
    base += min(len(group) * 0.025, 0.18)
    if exact_terms:
        base += 0.08
    if config_terms:
        base += 0.08
    if user_phrases:
        base += 0.05
    ceiling = 0.95
    if "sensitive-review" in review_flags:
        ceiling = min(ceiling, 0.88)
    if "low-confidence" in review_flags:
        ceiling = min(ceiling, 0.58)
    return round(min(base, ceiling), 2)


def _candidate_review_flags(
    group: Sequence[_Observation],
    *,
    user_phrases: Sequence[str],
    technical_terms: Sequence[str],
    exact_terms: Sequence[str],
    config_terms: Sequence[str],
) -> tuple[list[str], list[str]]:
    flags: list[str] = []
    notes: list[str] = []
    all_terms = [*technical_terms, *exact_terms, *config_terms]
    if any(_is_sensitive_like(term) for term in all_terms):
        flags.append("sensitive-review")
        notes.append("sensitive-looking anchors require owner review before any glossary update")
    if any(_is_local_or_unreviewed_source(item) for item in group):
        flags.append("low-confidence")
        notes.append("local, unknown, smoke, upload, test, or tmp source; do not promote without review")
    if any(_looks_broken_text(phrase) for phrase in user_phrases):
        flags.append("low-confidence")
        notes.append("broken or mojibake user phrase was detected")
    return _dedupe(flags, limit=4), _dedupe(notes, limit=4)


def _candidate_status(review_flags: Sequence[str]) -> str:
    if "sensitive-review" in review_flags:
        return "sensitive-review"
    if "low-confidence" in review_flags:
        return "low-confidence"
    return "suggested"


def _unknown_terms(terms: Sequence[str], existing: _ExistingGlossaryIndex, *, limit: int) -> tuple[list[str], int]:
    result: list[str] = []
    skipped = 0
    seen: set[str] = set()
    for term in terms:
        clean = _clean_term(term)
        key = _term_key(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        if existing.is_known(clean):
            skipped += 1
            continue
        result.append(clean)
        if len(result) >= limit:
            break
    return result, skipped


def _service_source_label(candidate: GlossaryCandidate) -> str:
    service = candidate.service_id or "unknown_service"
    source = candidate.source_id or "unknown_source"
    return f"{service} / {source}"


def _bullet_lines(items: Sequence[str]) -> list[str]:
    if not items:
        return ["  - none"]
    return [f"  - {item}" for item in items]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _first_text(values: Sequence[object]) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _safe_slug(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_").casefold()
    return clean or None


def _service_from_source(source_id: str | None) -> str | None:
    if not source_id or not source_id.endswith("_docs"):
        return None
    return source_id[: -len("_docs")] or None


def _safe_phrase(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or len(text) < 4 or _looks_broken_text(text):
        return ""
    return text[:180]


def _short_label(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:120]


def _clean_term(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n.,;:!?")
    text = text[:120]
    if _is_noise_term(text):
        return ""
    return text


def _is_noise_term(text: str) -> bool:
    clean = text.strip()
    key = _term_key(clean)
    if not clean:
        return True
    if _looks_broken_text(clean):
        return True
    if key in _COMMON_ANCHOR_NOISE:
        return True
    if key in _NOISE_TERMS or key.endswith("_docs"):
        return True
    if len(clean) < 3 and not clean.startswith(("/", "--")):
        return True
    if clean.count("(") != clean.count(")"):
        return True
    if clean.isdigit() or _UUID_RE.fullmatch(clean) or _HEXISH_RE.fullmatch(clean):
        return True
    if _looks_random_sample_token(clean):
        return True
    if _TIMESTAMP_RE.search(clean):
        return True
    if clean.startswith("/") and (".." in clean or re.search(r"\.[a-z]{2,}(?:/|$)", clean, flags=re.IGNORECASE)):
        return True
    if clean.startswith("/") and "your" in key:
        return True
    if clean.startswith("/") and re.search(r"/\d{5,}(?:/|$)", clean):
        return True
    if "." in clean and re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", clean):
        return True
    return False


def _looks_broken_text(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    if "\ufffd" in clean or "Ð" in clean or "Ã" in clean:
        return True
    question_marks = clean.count("?")
    return question_marks >= 3 and question_marks / max(len(clean), 1) >= 0.12


def _looks_random_sample_token(text: str) -> bool:
    clean = text.strip()
    if clean.startswith(("/", "--")) or "_" in clean or "." in clean or "(" in clean or ")" in clean:
        return False
    if not _RANDOM_SAMPLE_RE.fullmatch(clean) and not re.fullmatch(r"[a-z]{2,}\d+[a-z0-9]{2,}", clean):
        return False
    if not (re.search(r"[A-Za-z]", clean) and re.search(r"\d", clean)):
        return False
    if re.search(r"^(?:send|set|get|delete|answer|create|update|match)[A-Z_]", clean):
        return False
    return True


def _is_sensitive_like(term: str) -> bool:
    clean = str(term or "").strip()
    return bool(clean and _SENSITIVE_RE.search(clean))


def _is_local_or_unreviewed_source(item: _Observation) -> bool:
    source = item.source_id or ""
    service = item.service_id or ""
    text = " ".join([service, source, item.topic, *item.source_refs]).casefold()
    if not service and (not source or not source.endswith("_docs")):
        return True
    if source.endswith("_docs"):
        return False
    return any(hint in text for hint in _LOCAL_SOURCE_HINTS)


def _term_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().strip())


def _topic_key(value: str) -> str:
    return _term_key(value or "general")[:80]


def _dedupe(items: Sequence[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = _clean_term(item)
        key = _term_key(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
