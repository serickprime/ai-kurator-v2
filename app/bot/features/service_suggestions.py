"""Owner/admin Telegram preview for service-aware docs suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from telegram import Update

from app.service_registry.suggestions import (
    ServiceSuggestion,
    ServiceSuggestionCatalog,
    ServiceSuggestionEngine,
    load_service_suggestion_catalog,
)
from app.service_registry.types import ServiceDocsStatus


class ServiceDocsStatusReader(Protocol):
    """Read-only service/docs status provider."""

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        """Return service/docs status rows."""


CatalogLoader = Callable[[], ServiceSuggestionCatalog]
SafeErrorFormatter = Callable[[Exception], str]


@dataclass(frozen=True)
class ServiceSuggestionPreview:
    """Telegram-ready owner/admin service suggestion preview."""

    suggestion: ServiceSuggestion
    runtime_status: str

    @property
    def docs_availability_checked(self) -> bool:
        """Return true when runtime status rows were available."""
        return self.runtime_status == "available"


async def build_service_suggestion_preview(
    question: str,
    *,
    status_provider: ServiceDocsStatusReader | None = None,
    catalog_loader: CatalogLoader = load_service_suggestion_catalog,
    safe_error: SafeErrorFormatter | None = None,
) -> ServiceSuggestionPreview:
    """Build a read-only service suggestion preview."""
    statuses: tuple[ServiceDocsStatus, ...] = ()
    runtime_status = "unavailable: service status provider not configured"
    if status_provider is not None:
        try:
            statuses = await status_provider.list_statuses(scan_corpus=False)
            runtime_status = "available"
        except Exception as exc:  # noqa: BLE001 - owner preview must not show traceback
            runtime_status = "unavailable: " + _safe_exception_label(exc, safe_error)

    catalog = catalog_loader()
    suggestion = ServiceSuggestionEngine(catalog, statuses=statuses).suggest(question)
    return ServiceSuggestionPreview(suggestion=suggestion, runtime_status=runtime_status)


async def send_service_suggestion_preview(
    update: Update,
    *,
    question: str,
    is_allowed: bool,
    status_provider: ServiceDocsStatusReader | None = None,
    reply_markup: Any | None = None,
    safe_error: SafeErrorFormatter | None = None,
    catalog_loader: CatalogLoader = load_service_suggestion_catalog,
) -> None:
    """Send an owner/admin-only service suggestion preview."""
    if update.message is None:
        return
    if not is_allowed:
        await update.message.reply_text(
            "This command is available to the bot owner/admin.",
            reply_markup=reply_markup,
        )
        return
    if not question.strip():
        await update.message.reply_text(
            "Usage: /service_suggest how to connect Stripe in n8n",
            reply_markup=reply_markup,
        )
        return

    try:
        preview = await build_service_suggestion_preview(
            question,
            status_provider=status_provider,
            catalog_loader=catalog_loader,
            safe_error=safe_error,
        )
    except Exception as exc:  # noqa: BLE001 - command should fail gracefully
        await update.message.reply_text(
            "Could not prepare service suggestion preview: " + _safe_exception_label(exc, safe_error),
            reply_markup=reply_markup,
        )
        return

    await update.message.reply_text(format_service_suggestion_preview(preview), reply_markup=reply_markup)


def format_service_suggestion_preview(preview: ServiceSuggestionPreview) -> str:
    """Format a compact Telegram-friendly owner/admin preview."""
    suggestion = preview.suggestion
    lines = [
        "Service suggestion preview",
        "",
        f"Detected service: {_display_service(suggestion)}",
        f"Service ID: {suggestion.canonical_service_id or 'none'}",
        f"Confidence: {suggestion.confidence:.2f}",
        f"Matched aliases: {_join_or_none(suggestion.matched_aliases)}",
        f"Active context: {_join_or_none(suggestion.active_context_services)}",
        f"Current status: {suggestion.current_status}",
        f"Docs registered: {_yes_no(suggestion.docs_registered)}",
        f"Docs active: {_yes_no(suggestion.docs_active)}",
        f"Docs availability check: {_availability_phrase(preview)}",
        f"Owner review required: {_yes_no(suggestion.owner_review_required)}",
        f"Suggested next action: {suggestion.suggested_action}",
        "Auto activation: disabled",
        f"Runtime status: {preview.runtime_status}",
        "",
        _status_guidance(suggestion, preview),
        "",
        "Preview only: no docs registration, activation, crawl, sync, indexing, reindex, config change, or Supabase write was run.",
    ]
    return "\n".join(lines)


def _status_guidance(suggestion: ServiceSuggestion, preview: ServiceSuggestionPreview) -> str:
    if not preview.docs_availability_checked and suggestion.current_status not in {"unknown-service", "ambiguous"}:
        return "Runtime unavailable: deterministic detection is shown, but docs availability was not verified."
    if suggestion.current_status == "supported-active":
        return "Owner action: not required; use the regular RAG flow."
    if suggestion.current_status == "known-docs-missing":
        return "Owner action: review or add a curated docs candidate before any preview or activation."
    if suggestion.current_status == "known-docs-inactive":
        return "Owner action: run only a read-only docs preview before any activation."
    if suggestion.current_status == "ambiguous":
        return "Owner action: clarify the target service first; no automatic choice was made."
    if suggestion.current_status == "unknown-service":
        return "Owner action: no action; no confident service detection."
    return "Owner action: no automatic action is available."


def _safe_exception_label(exc: Exception, formatter: SafeErrorFormatter | None) -> str:
    if formatter is None:
        return exc.__class__.__name__
    safe = formatter(exc).strip()
    return safe or exc.__class__.__name__


def _display_service(suggestion: ServiceSuggestion) -> str:
    if not suggestion.canonical_service_id:
        return "none"
    return suggestion.display_name or suggestion.canonical_service_id


def _join_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _availability_phrase(preview: ServiceSuggestionPreview) -> str:
    return "available" if preview.docs_availability_checked else "not verified"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
