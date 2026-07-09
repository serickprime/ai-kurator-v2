"""Read-only docs source health and staleness reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG, load_external_docs_config
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG, load_service_registry_config
from app.service_registry.types import ServiceDocsStatus

DEFAULT_DOCS_HEALTH_POLICY_CONFIG = Path("config/docs_health_policy.yaml")

DocsHealthStatus = Literal["healthy", "warning", "failed", "stale", "inactive", "unknown"]
DocsStaleStatus = Literal["fresh", "stale", "unknown", "not_applicable"]


@dataclass(frozen=True)
class DocsHealthPolicy:
    """Staleness thresholds for docs source health reports."""

    default_stale_after_days: int = 30
    missing_timestamp_status: str = "unknown"
    service_thresholds: dict[str, int] | None = None
    source_thresholds: dict[str, int] | None = None

    def threshold_for(
        self,
        *,
        service_id: str,
        source_id: str | None,
        external_refresh_days: int | None = None,
    ) -> int | None:
        """Return the stale-after threshold for one source."""
        if source_id and self.source_thresholds and source_id in self.source_thresholds:
            return self.source_thresholds[source_id]
        if service_id and self.service_thresholds and service_id in self.service_thresholds:
            return self.service_thresholds[service_id]
        if external_refresh_days is not None:
            return external_refresh_days
        return self.default_stale_after_days


@dataclass(frozen=True)
class DocsSourceHealth:
    """One read-only docs source health row."""

    service_id: str
    service_display_name: str
    source_id: str | None
    source_title: str
    source_type: str
    registered: bool
    active: bool
    current_status: DocsHealthStatus
    status_reason: str
    docs_status: str
    quality_status: str
    status_notes: tuple[str, ...]
    last_checked_at: datetime | None
    last_success_at: datetime | None
    age_days: int | None
    stale_status: DocsStaleStatus
    stale_reason: str
    document_count: int
    chunk_count: int
    owner_review_required: bool
    suggested_next_action: str
    automatic_refresh_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "service_id": self.service_id,
            "service_display_name": self.service_display_name,
            "source_id": self.source_id,
            "source_title": self.source_title,
            "source_type": self.source_type,
            "registered": self.registered,
            "active": self.active,
            "current_status": self.current_status,
            "status_reason": self.status_reason,
            "docs_status": self.docs_status,
            "quality_status": self.quality_status,
            "status_notes": list(self.status_notes),
            "last_checked_at": _format_datetime(self.last_checked_at),
            "last_success_at": _format_datetime(self.last_success_at),
            "age_days": self.age_days,
            "stale_status": self.stale_status,
            "stale_reason": self.stale_reason,
            "document_count": self.document_count,
            "chunk_count": self.chunk_count,
            "owner_review_required": self.owner_review_required,
            "suggested_next_action": self.suggested_next_action,
            "automatic_refresh_allowed": self.automatic_refresh_allowed,
        }


@dataclass(frozen=True)
class DocsHealthReport:
    """Read-only report for docs source health."""

    sources: tuple[DocsSourceHealth, ...]
    runtime_status: str

    def summary(self) -> dict[str, int]:
        """Return report summary counts."""
        return {
            "total": len(self.sources),
            "healthy": sum(1 for row in self.sources if row.current_status == "healthy"),
            "warning": sum(1 for row in self.sources if row.current_status == "warning"),
            "failed": sum(1 for row in self.sources if row.current_status == "failed"),
            "stale": sum(1 for row in self.sources if row.stale_status == "stale"),
            "inactive": sum(1 for row in self.sources if row.current_status == "inactive"),
            "unknown": sum(1 for row in self.sources if row.current_status == "unknown"),
        }

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "mode": "read-only",
            "runtime_status": self.runtime_status,
            "summary": self.summary(),
            "sources": [source.to_dict() for source in self.sources],
        }


async def load_external_documents_for_health(client: Any, *, limit: int) -> list[dict[str, Any]]:
    """Load existing external docs rows for read-only health reporting."""
    return await client.select(
        "documents",
        params={
            "select": "id,filename,document_key,title,status,metadata,created_at,updated_at",
            "source_type": "eq.external_docs",
            "limit": str(limit),
        },
    )


def load_docs_health_policy(path: Path | str = DEFAULT_DOCS_HEALTH_POLICY_CONFIG) -> DocsHealthPolicy:
    """Load the small docs health policy YAML subset."""
    config_path = Path(path)
    if not config_path.exists():
        return DocsHealthPolicy()
    return _parse_policy(config_path.read_text(encoding="utf-8"))


def external_refresh_days_by_source(path: Path | str = DEFAULT_EXTERNAL_DOCS_CONFIG) -> dict[str, int]:
    """Return configured external docs refresh windows by source."""
    try:
        config = load_external_docs_config(path)
    except Exception:  # noqa: BLE001 - health report must degrade cleanly
        return {}
    return {source.name: source.refresh_days for source in config.sources}


def build_local_config_statuses(
    *,
    registry_config_path: Path | str = DEFAULT_SERVICE_REGISTRY_CONFIG,
    external_config_path: Path | str = DEFAULT_EXTERNAL_DOCS_CONFIG,
) -> tuple[ServiceDocsStatus, ...]:
    """Build config-only status rows when runtime data cannot be read."""
    try:
        registry = load_service_registry_config(registry_config_path)
        configured_sources = set(external_refresh_days_by_source(external_config_path))
    except Exception:  # noqa: BLE001 - CLI should not traceback on broken local config
        return ()

    rows: list[ServiceDocsStatus] = []
    for service in registry.services:
        docs_source = service.docs_source
        docs_source_configured = bool(docs_source and docs_source in configured_sources)
        if service.status == "disabled":
            docs_status = "disabled"
            notes = ("service disabled in registry", "runtime status not verified")
        elif not docs_source:
            docs_status = "not_configured"
            notes = ("docs_source is not configured", "runtime status not verified")
        elif docs_source_configured:
            docs_status = "configured_not_indexed"
            notes = ("runtime status not verified",)
        else:
            docs_status = "needs_review"
            notes = ("docs_source is not present in config/external_docs.yaml", "runtime status not verified")
        rows.append(
            ServiceDocsStatus(
                service_id=service.service_id,
                display_name=service.display_name,
                aliases=service.aliases,
                docs_source=docs_source,
                configured_status=service.status,
                docs_status=docs_status,  # type: ignore[arg-type]
                quality_status="none",
                docs_source_configured=docs_source_configured,
                notes=notes,
            )
        )
    return tuple(rows)


def build_docs_health_report(
    *,
    statuses: Iterable[ServiceDocsStatus],
    documents: Iterable[dict[str, Any]],
    policy: DocsHealthPolicy | None = None,
    external_refresh_days: dict[str, int] | None = None,
    runtime_status: str = "available",
    now: datetime | None = None,
) -> DocsHealthReport:
    """Build a docs source health report from existing read-only status rows."""
    resolved_policy = policy or DocsHealthPolicy()
    refresh_days = external_refresh_days or {}
    rows = list(documents)
    rows_by_source = _documents_by_source(rows)
    reference_now = _normalize_datetime(now or datetime.now(timezone.utc))
    runtime_available = runtime_status == "available"
    sources = tuple(
        _source_health(
            status=status,
            source_documents=rows_by_source.get(status.docs_source or "", ()),
            policy=resolved_policy,
            external_refresh_days=refresh_days.get(status.docs_source or ""),
            runtime_available=runtime_available,
            now=reference_now,
        )
        for status in statuses
    )
    return DocsHealthReport(sources=sources, runtime_status=runtime_status)


def filter_docs_health_report(
    report: DocsHealthReport,
    *,
    service: str | None = None,
    status: str | None = None,
    stale_only: bool = False,
    limit: int | None = None,
) -> DocsHealthReport:
    """Filter report rows without changing source data."""
    rows = list(report.sources)
    if service:
        needle = service.strip().casefold()
        rows = [
            row
            for row in rows
            if row.service_id.casefold() == needle
            or row.service_display_name.casefold() == needle
            or str(row.source_id or "").casefold() == needle
        ]
    if status:
        status_needle = status.strip().casefold()
        rows = [
            row
            for row in rows
            if row.current_status.casefold() == status_needle
            or row.docs_status.casefold() == status_needle
            or row.quality_status.casefold() == status_needle
        ]
    if stale_only:
        rows = [row for row in rows if row.stale_status == "stale"]
    if limit is not None:
        rows = rows[: max(limit, 0)]
    return DocsHealthReport(sources=tuple(rows), runtime_status=report.runtime_status)


def format_docs_health_report(report: DocsHealthReport) -> str:
    """Return a compact owner/admin-friendly text report."""
    summary = report.summary()
    lines = [
        "Docs Source Health Report",
        "",
        "Summary",
        "",
        f"- mode: read-only",
        f"- runtime status: {report.runtime_status}",
        f"- total sources: {summary['total']}",
        f"- healthy: {summary['healthy']}",
        f"- warning: {summary['warning']}",
        f"- failed: {summary['failed']}",
        f"- stale: {summary['stale']}",
        f"- inactive: {summary['inactive']}",
        f"- unknown: {summary['unknown']}",
    ]
    for index, source in enumerate(report.sources, start=1):
        lines.extend(
            [
                "",
                f"Source {index}",
                "",
                f"- service: {source.service_display_name} ({source.service_id})",
                f"- source: {source.source_id or 'none'}",
                f"- source type: {source.source_type}",
                f"- registered: {_yes_no(source.registered)}",
                f"- active: {_yes_no(source.active)}",
                f"- current status: {source.current_status}",
                f"- docs status: {source.docs_status}",
                f"- quality status: {source.quality_status}",
                f"- status reason: {source.status_reason}",
                f"- last checked: {_format_datetime(source.last_checked_at) or 'not available'}",
                f"- last success/update: {_format_datetime(source.last_success_at) or 'not available'}",
                f"- age: {_format_age(source.age_days)}",
                f"- stale: {source.stale_status}",
                f"- stale reason: {source.stale_reason}",
                f"- documents/chunks: {source.document_count}/{source.chunk_count}",
                f"- owner review required: {_yes_no(source.owner_review_required)}",
                f"- suggested next action: {source.suggested_next_action}",
                "- automatic refresh: disabled",
            ]
        )
    return "\n".join(lines)


def _source_health(
    *,
    status: ServiceDocsStatus,
    source_documents: Iterable[dict[str, Any]],
    policy: DocsHealthPolicy,
    external_refresh_days: int | None,
    runtime_available: bool,
    now: datetime,
) -> DocsSourceHealth:
    docs = tuple(source_documents)
    last_checked_at = _latest_datetime_from_docs(docs, ("last_checked_at", "checked_at", "crawled_at"))
    last_success_at = _latest_datetime_from_docs(
        docs,
        ("last_success_at", "last_indexed_at", "crawled_at", "updated_at", "created_at"),
    )
    threshold = policy.threshold_for(
        service_id=status.service_id,
        source_id=status.docs_source,
        external_refresh_days=external_refresh_days,
    )
    age_days = _age_days(last_success_at, now)
    stale_status, stale_reason = _stale_status(
        age_days=age_days,
        threshold=threshold,
        active=status.docs_status == "indexed",
        missing_timestamp_status=policy.missing_timestamp_status,
        runtime_available=runtime_available,
    )
    health_status = _health_status(status, stale_status=stale_status, runtime_available=runtime_available)
    reason = _status_reason(status=status, stale_status=stale_status, stale_reason=stale_reason)
    return DocsSourceHealth(
        service_id=status.service_id,
        service_display_name=status.display_name,
        source_id=status.docs_source,
        source_title=_source_title(status, docs),
        source_type=_source_type(status, docs),
        registered=bool(status.docs_source),
        active=status.docs_status == "indexed" and status.active_docs_count > 0,
        current_status=health_status,
        status_reason=reason,
        docs_status=status.docs_status,
        quality_status=status.quality_status,
        status_notes=status.notes,
        last_checked_at=last_checked_at,
        last_success_at=last_success_at,
        age_days=age_days,
        stale_status=stale_status,
        stale_reason=stale_reason,
        document_count=status.active_docs_count,
        chunk_count=status.active_chunks_count,
        owner_review_required=health_status in {"warning", "failed", "stale", "inactive", "unknown"},
        suggested_next_action=_suggested_next_action(health_status, status),
        automatic_refresh_allowed=False,
    )


def _health_status(
    status: ServiceDocsStatus,
    *,
    stale_status: DocsStaleStatus,
    runtime_available: bool,
) -> DocsHealthStatus:
    if not runtime_available:
        return "unknown"
    if status.docs_status in {"disabled", "not_configured", "configured_not_indexed"}:
        return "inactive"
    if status.quality_status == "FAIL":
        return "failed"
    if status.docs_status == "needs_review" or status.quality_status == "WARN":
        return "warning"
    if stale_status == "stale":
        return "stale"
    if status.docs_status == "indexed" and status.quality_status in {"PASS", "none"}:
        return "healthy"
    return "unknown"


def _status_reason(
    *,
    status: ServiceDocsStatus,
    stale_status: DocsStaleStatus,
    stale_reason: str,
) -> str:
    if status.notes:
        return "; ".join(status.notes[:4])
    if status.quality_status == "FAIL":
        return "quality gate returned FAIL"
    if status.quality_status == "WARN":
        return "quality gate returned WARN"
    if stale_status == "stale":
        return stale_reason
    if status.docs_status == "indexed":
        return "source is indexed and passed current read-only checks"
    return f"docs status is {status.docs_status}"


def _stale_status(
    *,
    age_days: int | None,
    threshold: int | None,
    active: bool,
    missing_timestamp_status: str,
    runtime_available: bool,
) -> tuple[DocsStaleStatus, str]:
    if not runtime_available:
        return "unknown", "runtime status could not be verified"
    if not active:
        return "not_applicable", "source is not active; staleness is not evaluated"
    if age_days is None:
        return "unknown", f"timestamp not available; policy={missing_timestamp_status}"
    if threshold is None:
        return "unknown", "no stale-after policy is configured"
    if age_days > threshold:
        return "stale", f"last successful update is {age_days} days old; threshold is {threshold} days"
    return "fresh", f"last successful update is {age_days} days old; threshold is {threshold} days"


def _suggested_next_action(health_status: DocsHealthStatus, status: ServiceDocsStatus) -> str:
    if health_status == "healthy":
        return "no owner action required"
    if health_status == "stale":
        return "owner/admin may inspect source and approve an explicit refresh later"
    if health_status == "failed":
        return "review last quality errors before any explicit refresh"
    if health_status == "warning":
        if status.docs_source_configured:
            return "inspect source configuration and run read-only docs preview if needed"
        return "inspect source registry entry before any activation"
    if health_status == "inactive":
        if status.docs_status == "configured_not_indexed":
            return "owner/admin may run read-only docs preview before any explicit activation"
        return "inspect service docs registry status"
    return "retry read-only report in an approved runtime environment"


def _documents_by_source(documents: Iterable[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], ...]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in documents:
        source_name = str(_metadata(row).get("source_name") or "").strip()
        if not source_name:
            continue
        if row.get("status") != "active":
            continue
        result.setdefault(source_name, []).append(row)
    return {source: tuple(rows) for source, rows in result.items()}


def _latest_datetime_from_docs(documents: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> datetime | None:
    values: list[datetime] = []
    for row in documents:
        metadata = _metadata(row)
        for key in keys:
            raw_value = row.get(key) if key in row else metadata.get(key)
            parsed = _parse_datetime(raw_value)
            if parsed is not None:
                values.append(parsed)
                break
    if not values:
        return None
    return max(values)


def _age_days(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    delta = now - value
    return max(delta.days, 0)


def _source_title(status: ServiceDocsStatus, documents: tuple[dict[str, Any], ...]) -> str:
    if documents:
        return str(documents[0].get("title") or documents[0].get("filename") or status.display_name)
    return status.display_name


def _source_type(status: ServiceDocsStatus, documents: tuple[dict[str, Any], ...]) -> str:
    if status.docs_source_configured:
        return "external_docs"
    if documents and status.docs_source:
        return "active_candidate_docs"
    if status.docs_source:
        return "registry_config"
    return "none"


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _normalize_datetime(parsed)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_age(age_days: int | None) -> str:
    return "not available" if age_days is None else f"{age_days} days"


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _parse_policy(text: str) -> DocsHealthPolicy:
    default_stale_after_days = 30
    missing_timestamp_status = "unknown"
    services: dict[str, int] = {}
    sources: dict[str, int] = {}
    current_section: str | None = None
    current_row: dict[str, str] | None = None

    for raw_line in text.splitlines():
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not line_without_comment.strip():
            continue
        indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
        line = line_without_comment.strip()
        if indent == 0 and line.endswith(":"):
            if current_row:
                _store_policy_row(current_section, current_row, services, sources)
                current_row = None
            current_section = line[:-1]
            continue
        if indent == 0 and ":" in line:
            if current_row:
                _store_policy_row(current_section, current_row, services, sources)
                current_row = None
            key, value = _split_key_value(line)
            if key == "default_stale_after_days":
                default_stale_after_days = _to_positive_int(value, default=30)
            elif key == "missing_timestamp_status":
                missing_timestamp_status = value or "unknown"
            continue
        if line.startswith("- "):
            if current_row:
                _store_policy_row(current_section, current_row, services, sources)
            current_row = {}
            item = line[2:].strip()
            if item:
                key, value = _split_key_value(item)
                current_row[key] = value
            continue
        if current_row is not None and ":" in line:
            key, value = _split_key_value(line)
            current_row[key] = value
    if current_row:
        _store_policy_row(current_section, current_row, services, sources)

    return DocsHealthPolicy(
        default_stale_after_days=default_stale_after_days,
        missing_timestamp_status=missing_timestamp_status,
        service_thresholds=services,
        source_thresholds=sources,
    )


def _store_policy_row(
    section: str | None,
    row: dict[str, str],
    services: dict[str, int],
    sources: dict[str, int],
) -> None:
    threshold = _to_positive_int(row.get("stale_after_days", ""), default=0)
    if threshold <= 0:
        return
    if section == "services":
        service_id = row.get("service_id", "").strip()
        if service_id:
            services[service_id] = threshold
    if section == "sources":
        source_id = row.get("source_id", "").strip()
        if source_id:
            sources[source_id] = threshold


def _split_key_value(text: str) -> tuple[str, str]:
    key, _, value = text.partition(":")
    return key.strip(), value.strip().strip("'\"")


def _to_positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
