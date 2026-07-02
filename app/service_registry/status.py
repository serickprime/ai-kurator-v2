"""Build service/docs registry status reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from app.external_docs.validation import ExternalDocsValidationResult, validate_external_docs
from app.service_registry.detector import ServiceDetector
from app.service_registry.types import ServiceDefinition, ServiceDocsStatus


def build_service_docs_statuses(
    *,
    services: Iterable[ServiceDefinition],
    configured_docs_sources: Iterable[str],
    documents: Iterable[dict[str, Any]],
    chunks: Iterable[dict[str, Any]],
    mention_counts: dict[str, int] | None = None,
    detected_document_counts: dict[str, int] | None = None,
    detected_chunk_counts: dict[str, int] | None = None,
) -> tuple[ServiceDocsStatus, ...]:
    """Build one status row for each service definition."""
    service_rows = tuple(services)
    source_names = {str(name) for name in configured_docs_sources}
    document_rows = list(documents)
    chunk_rows = list(chunks)
    active_docs_by_source = _active_docs_by_source(document_rows)
    active_chunks_by_source = _active_chunks_by_source(chunk_rows, active_docs_by_source)
    quality_reports_by_source = _quality_reports_by_source(source_names, document_rows, chunk_rows)

    statuses = [
        _service_status(
            service=service,
            configured_docs_sources=source_names,
            active_docs_by_source=active_docs_by_source,
            active_chunks_by_source=active_chunks_by_source,
            quality_reports_by_source=quality_reports_by_source,
            mention_counts=mention_counts or {},
            detected_document_counts=detected_document_counts or {},
            detected_chunk_counts=detected_chunk_counts or {},
        )
        for service in service_rows
    ]
    return tuple(statuses)


def count_service_mentions(
    *,
    services: Iterable[ServiceDefinition],
    corpus_rows: Iterable[dict[str, Any]],
) -> dict[str, int]:
    """Count rows that mention each service at least once."""
    detector = ServiceDetector(tuple(services))
    counts: Counter[str] = Counter()
    for row in corpus_rows:
        text = _row_text(row)
        seen = {mention.service_id for mention in detector.detect(text)}
        counts.update(seen)
    return dict(counts)


def count_service_metadata(
    *,
    documents: Iterable[dict[str, Any]],
    chunks: Iterable[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    """Count documents and chunks tagged with service_ids metadata."""
    document_counts: Counter[str] = Counter()
    chunk_counts: Counter[str] = Counter()
    for row in documents:
        document_counts.update(_service_ids_from_metadata(row))
    for row in chunks:
        chunk_counts.update(_service_ids_from_metadata(row))
    return dict(document_counts), dict(chunk_counts)


def status_payload(statuses: Iterable[ServiceDocsStatus]) -> dict[str, object]:
    """Return a JSON-safe status payload."""
    rows = [status.to_dict() for status in statuses]
    needs_review = [row["service_id"] for row in rows if row["docs_status"] == "needs_review"]
    return {
        "status": "ok",
        "services": rows,
        "summary": {
            "total": len(rows),
            "indexed": sum(1 for row in rows if row["docs_status"] == "indexed"),
            "not_configured": sum(1 for row in rows if row["docs_status"] == "not_configured"),
            "configured_not_indexed": sum(1 for row in rows if row["docs_status"] == "configured_not_indexed"),
            "disabled": sum(1 for row in rows if row["docs_status"] == "disabled"),
            "needs_review": len(needs_review),
            "needs_review_services": needs_review,
        },
    }


def _service_status(
    *,
    service: ServiceDefinition,
    configured_docs_sources: set[str],
    active_docs_by_source: dict[str, list[dict[str, Any]]],
    active_chunks_by_source: dict[str, list[dict[str, Any]]],
    quality_reports_by_source: dict[str, ExternalDocsValidationResult],
    mention_counts: dict[str, int],
    detected_document_counts: dict[str, int],
    detected_chunk_counts: dict[str, int],
) -> ServiceDocsStatus:
    docs_source = service.docs_source
    notes: list[str] = []
    if service.status == "disabled":
        return _status(
            service,
            "disabled",
            mention_counts=mention_counts,
            detected_document_counts=detected_document_counts,
            detected_chunk_counts=detected_chunk_counts,
            notes=("service disabled in registry",),
        )
    if not docs_source:
        return _status(
            service,
            "not_configured",
            mention_counts=mention_counts,
            detected_document_counts=detected_document_counts,
            detected_chunk_counts=detected_chunk_counts,
            notes=("docs_source is not configured",),
        )

    source_configured = docs_source in configured_docs_sources
    active_docs = active_docs_by_source.get(docs_source, [])
    active_chunks = active_chunks_by_source.get(docs_source, [])
    quality_report = quality_reports_by_source.get(docs_source)
    quality = quality_report.quality if quality_report is not None else "none"
    if not source_configured:
        notes.append("docs_source is not present in config/external_docs.yaml")
        final_status = "needs_review"
    elif service.status == "needs_review":
        notes.append("service marked needs_review in registry")
        final_status = "needs_review"
    elif not active_docs:
        final_status = "configured_not_indexed"
    elif quality in {"FAIL", "WARN"}:
        notes.extend(quality_report_notes(quality_report))
        final_status = "needs_review"
    else:
        final_status = "indexed"

    return ServiceDocsStatus(
        service_id=service.service_id,
        display_name=service.display_name,
        aliases=service.aliases,
        docs_source=docs_source,
        configured_status=service.status,
        docs_status=final_status,  # type: ignore[arg-type]
        active_docs_count=len(active_docs),
        active_chunks_count=len(active_chunks),
        detected_documents_count=detected_document_counts.get(service.service_id, 0),
        detected_chunks_count=detected_chunk_counts.get(service.service_id, 0),
        quality_status=quality,
        mention_count=mention_counts.get(service.service_id),
        docs_source_configured=source_configured,
        notes=tuple(notes),
    )


def quality_report_notes(report: ExternalDocsValidationResult | None) -> tuple[str, ...]:
    """Return concise human-readable quality notes for status surfaces."""
    if report is None or report.quality not in {"FAIL", "WARN"}:
        return ()
    notes = [f"quality gate returned {report.quality}"]
    notes.extend(report.failures[:3])
    notes.extend(report.warnings[:3])
    return tuple(_dedupe([note for note in notes if note], limit=6))


def _status(
    service: ServiceDefinition,
    docs_status: str,
    *,
    mention_counts: dict[str, int],
    detected_document_counts: dict[str, int],
    detected_chunk_counts: dict[str, int],
    notes: tuple[str, ...],
) -> ServiceDocsStatus:
    return ServiceDocsStatus(
        service_id=service.service_id,
        display_name=service.display_name,
        aliases=service.aliases,
        docs_source=service.docs_source,
        configured_status=service.status,
        docs_status=docs_status,  # type: ignore[arg-type]
        detected_documents_count=detected_document_counts.get(service.service_id, 0),
        detected_chunks_count=detected_chunk_counts.get(service.service_id, 0),
        quality_status="none",
        mention_count=mention_counts.get(service.service_id),
        docs_source_configured=False,
        notes=notes,
    )


def _quality_reports_by_source(
    source_names: set[str],
    documents: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> dict[str, ExternalDocsValidationResult]:
    result: dict[str, ExternalDocsValidationResult] = {}
    for source_name in source_names:
        active_count = sum(
            1
            for row in documents
            if row.get("status") == "active" and _metadata(row).get("source_name") == source_name
        )
        if active_count <= 0:
            continue
        result[source_name] = validate_external_docs(
            source_name=source_name,
            documents=documents,
            chunks=chunks,
        )
    return result


def _active_docs_by_source(documents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in documents:
        if row.get("status") != "active":
            continue
        source_name = str(_metadata(row).get("source_name") or "")
        if source_name:
            result[source_name].append(row)
    return dict(result)


def _active_chunks_by_source(
    chunks: list[dict[str, Any]],
    active_docs_by_source: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    source_by_document_id = {
        str(row.get("id") or ""): source_name
        for source_name, rows in active_docs_by_source.items()
        for row in rows
    }
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in chunks:
        source_name = source_by_document_id.get(str(row.get("document_id") or ""))
        if source_name:
            result[source_name].append(row)
    return dict(result)


def _row_text(row: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("title", "filename", "course", "module", "lesson", "heading", "content", "summary"):
        value = row.get(key)
        if value:
            values.append(str(value))
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        for key in ("source_name", "source_kind", "canonical_url", "source_url"):
            value = metadata.get(key)
            if value:
                values.append(str(value))
    for key in ("topics", "questions_answered", "entities", "task_types"):
        value = row.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
    return "\n".join(values)


def _service_ids_from_metadata(row: dict[str, Any]) -> list[str]:
    metadata = _metadata(row)
    values = metadata.get("service_ids")
    if isinstance(values, list):
        return [str(value).strip() for value in values if str(value).strip()]
    if isinstance(values, tuple):
        return [str(value).strip() for value in values if str(value).strip()]
    mentions = metadata.get("service_mentions")
    if isinstance(mentions, list):
        result: list[str] = []
        for item in mentions:
            if isinstance(item, dict) and str(item.get("service_id") or "").strip():
                result.append(str(item["service_id"]).strip())
        return result
    return []


def _dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = " ".join(str(item).split()).strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}
