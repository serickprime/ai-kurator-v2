"""Owner/admin Telegram preview for docs source health reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from telegram import Update

from app.service_registry.docs_health import (
    DocsHealthReport,
    DocsSourceHealth,
    build_docs_health_report,
    build_local_config_statuses,
    filter_docs_health_report,
)
from app.service_registry.types import ServiceDocsStatus


class DocsHealthReportReader(Protocol):
    """Read-only docs health report provider."""

    async def build_report(self) -> DocsHealthReport:
        """Return docs health report without writes or refreshes."""


class ServiceDocsStatusReader(Protocol):
    """Read-only service/docs status provider fallback."""

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        """Return service/docs status rows."""


SafeErrorFormatter = Callable[[Exception], str]
HEALTH_STATUSES = {"healthy", "warning", "failed", "stale", "inactive", "unknown"}


@dataclass(frozen=True)
class DocsHealthPreview:
    """Telegram-ready read-only docs health preview."""

    report: DocsHealthReport
    filter_text: str
    filter_kind: str
    not_found: bool
    available_service_ids: tuple[str, ...]


async def build_docs_health_preview(
    filter_text: str = "",
    *,
    report_provider: DocsHealthReportReader | None = None,
    status_provider: ServiceDocsStatusReader | None = None,
    safe_error: SafeErrorFormatter | None = None,
) -> DocsHealthPreview:
    """Build a read-only Telegram preview using Phase 6A report logic."""
    report = await _load_report(
        report_provider=report_provider,
        status_provider=status_provider,
        safe_error=safe_error,
    )
    available_service_ids = tuple(sorted({row.service_id for row in report.sources if row.service_id}))
    clean_filter = filter_text.strip()
    filter_kind = "none"
    filtered = report
    if clean_filter:
        lowered = clean_filter.casefold()
        if lowered in HEALTH_STATUSES:
            filter_kind = "status"
            filtered = filter_docs_health_report(report, status=lowered)
        else:
            filter_kind = "service"
            filtered = filter_docs_health_report(report, service=clean_filter)
    return DocsHealthPreview(
        report=filtered,
        filter_text=clean_filter,
        filter_kind=filter_kind,
        not_found=bool(clean_filter and not filtered.sources),
        available_service_ids=available_service_ids,
    )


async def send_docs_health_preview(
    update: Update,
    *,
    filter_text: str,
    is_allowed: bool,
    report_provider: DocsHealthReportReader | None = None,
    status_provider: ServiceDocsStatusReader | None = None,
    reply_markup: Any | None = None,
    safe_error: SafeErrorFormatter | None = None,
) -> None:
    """Send owner/admin-only docs health preview."""
    if update.message is None:
        return
    if not is_allowed:
        await update.message.reply_text(
            "This command is available to the bot owner/admin.",
            reply_markup=reply_markup,
        )
        return
    if len(filter_text.split()) > 1:
        await update.message.reply_text(
            "Usage: /docs_health\n/docs_health <service_id>",
            reply_markup=reply_markup,
        )
        return

    try:
        preview = await build_docs_health_preview(
            filter_text,
            report_provider=report_provider,
            status_provider=status_provider,
            safe_error=safe_error,
        )
    except Exception as exc:  # noqa: BLE001 - owner preview must fail gracefully
        await update.message.reply_text(
            "Could not prepare docs health preview: " + _safe_exception_label(exc, safe_error),
            reply_markup=reply_markup,
        )
        return

    await update.message.reply_text(format_docs_health_preview(preview), reply_markup=reply_markup)


def format_docs_health_preview(preview: DocsHealthPreview) -> str:
    """Format a compact Telegram-friendly docs health preview."""
    report = preview.report
    summary = report.summary()
    lines = [
        "Docs Health",
        "",
        f"Total: {summary['total']}",
        f"Healthy: {summary['healthy']}",
        f"Warning: {summary['warning']}",
        f"Failed: {summary['failed']}",
        f"Stale: {summary['stale']}",
        f"Inactive: {summary['inactive']}",
        f"Unknown: {summary['unknown']}",
        f"Runtime: {report.runtime_status}",
    ]
    if preview.filter_text:
        lines.append(f"Filter: {preview.filter_kind}={preview.filter_text}")
    if preview.not_found:
        lines.extend(
            [
                "",
                f"No matching docs source for: {preview.filter_text}",
                f"Available service IDs: {_join_or_none(preview.available_service_ids)}",
                "",
                "Automatic refresh: disabled",
                "Preview only: no status change, activation, crawl, sync, indexing, reindex, migration, or Supabase write was run.",
            ]
        )
        return "\n".join(lines)

    for index, source in enumerate(report.sources, start=1):
        lines.extend(_format_source(source, index))

    if not report.sources:
        lines.extend(["", "No docs sources were available for this preview."])
    lines.extend(
        [
            "",
            "Preview only: no status change, activation, crawl, sync, indexing, reindex, migration, or Supabase write was run.",
        ]
    )
    return "\n".join(lines)


async def _load_report(
    *,
    report_provider: DocsHealthReportReader | None,
    status_provider: ServiceDocsStatusReader | None,
    safe_error: SafeErrorFormatter | None,
) -> DocsHealthReport:
    if report_provider is not None:
        try:
            return await report_provider.build_report()
        except Exception as exc:  # noqa: BLE001 - preview must degrade cleanly
            return _runtime_unavailable_report(exc, safe_error)
    if status_provider is not None:
        try:
            statuses = await status_provider.list_statuses(scan_corpus=False)
            return build_docs_health_report(statuses=statuses, documents=(), runtime_status="available")
        except Exception as exc:  # noqa: BLE001 - preview must degrade cleanly
            return _runtime_unavailable_report(exc, safe_error)
    return build_docs_health_report(
        statuses=build_local_config_statuses(),
        documents=(),
        runtime_status="unavailable: service status provider not configured",
    )


def _runtime_unavailable_report(exc: Exception, safe_error: SafeErrorFormatter | None) -> DocsHealthReport:
    return build_docs_health_report(
        statuses=build_local_config_statuses(),
        documents=(),
        runtime_status="unavailable: " + _safe_exception_label(exc, safe_error),
    )


def _format_source(source: DocsSourceHealth, index: int) -> list[str]:
    return [
        "",
        f"Source {index}",
        f"Service: {source.service_display_name} ({source.service_id})",
        f"Source: {source.source_id or 'none'}",
        f"Status: {source.current_status}",
        f"Active: {_yes_no(source.active)}",
        f"Stale: {source.stale_status}",
        f"Last checked/updated: {_date_or_unknown(source.last_checked_at)} / {_date_or_unknown(source.last_success_at)}",
        f"Documents/chunks: {source.document_count}/{source.chunk_count}",
        f"Reason: {_compact(source.status_reason)}",
        f"Owner action: {source.suggested_next_action}",
        "Automatic refresh: disabled",
    ]


def _safe_exception_label(exc: Exception, formatter: SafeErrorFormatter | None) -> str:
    if formatter is None:
        return exc.__class__.__name__
    safe = formatter(exc).strip()
    return safe or exc.__class__.__name__


def _date_or_unknown(value: object) -> str:
    if value is None:
        return "not available"
    text = str(value)
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10] if len(text) > 10 else text


def _compact(value: str, *, limit: int = 320) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _join_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
