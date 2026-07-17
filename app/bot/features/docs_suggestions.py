"""Owner/admin Telegram review surface for persisted docs suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from app.db.repositories import DocsCandidateSuggestion
from app.db.supabase_client import SupabaseRequestError
from app.docs_registry.activation import (
    DocsActivationResult,
    DynamicDocsActivationPolicy,
)
from app.docs_registry.models import DocsCandidatePreviewResult, DocsSourceCandidate, DocsSourceCandidatesConfig
from app.docs_registry.preview import DocsCandidatePreviewService

CALLBACK_PREFIX = "docs_suggest"
DEFAULT_REJECTION_REASON = "rejected_by_owner"
MISSING_TABLE_MESSAGE = (
    "Docs suggestions are not available yet: the database migration has not been applied. "
    "Apply app/db/migrations/20260716_create_docs_candidate_suggestions.sql after explicit owner approval."
)


class DocsSuggestionsRepository(Protocol):
    """Repository methods required by the Telegram suggestions review surface."""

    async def list_pending(self, workspace_id: str, *, limit: int = 10) -> tuple[DocsCandidateSuggestion, ...]:
        """Return reviewable suggestions."""

    async def get(self, suggestion_id: str) -> DocsCandidateSuggestion | None:
        """Return one suggestion by id."""

    async def save_preview_result(
        self,
        suggestion_id: str,
        *,
        preview_status: str,
        preview_result: dict[str, Any],
        status: str | None = None,
    ) -> DocsCandidateSuggestion:
        """Persist preview output."""

    async def reject(
        self,
        suggestion_id: str,
        *,
        reviewed_by_user_id: int,
        rejection_reason: str = "",
    ) -> DocsCandidateSuggestion:
        """Reject without deleting."""

    async def save_activation_result(
        self,
        suggestion_id: str,
        *,
        activation_result: dict[str, Any],
        status: str,
        reviewed_by_user_id: int | None = None,
    ) -> DocsCandidateSuggestion:
        """Persist compact activation output."""


class DocsSuggestionPreviewReader(Protocol):
    """Existing docs preview service interface."""

    async def preview(self, service_id_or_alias: str, *, limit: int = 5) -> DocsCandidatePreviewResult:
        """Return a safe dry-run preview."""


class DocsSuggestionActivationReader(Protocol):
    """Existing docs activation service dynamic candidate interface."""

    async def activate_candidate(
        self,
        candidate_id: str,
        candidate: DocsSourceCandidate,
        *,
        policy: DynamicDocsActivationPolicy,
    ) -> DocsActivationResult:
        """Run controlled activation for one persisted suggestion."""


SafeErrorFormatter = Callable[[Exception], str]


@dataclass(frozen=True)
class DocsSuggestionsList:
    """Telegram-ready suggestions list."""

    suggestions: tuple[DocsCandidateSuggestion, ...]


async def send_docs_suggestions(
    update: Update,
    *,
    repository: DocsSuggestionsRepository | None,
    workspace_id: str,
    is_allowed: bool,
    suggestion_id_or_prefix: str = "",
    reply_markup: Any | None = None,
    safe_error: SafeErrorFormatter | None = None,
) -> None:
    """Send pending suggestions list, or one suggestion card by id/prefix."""
    if update.message is None:
        return
    if not is_allowed:
        await update.message.reply_text("This command is available to the bot owner/admin.", reply_markup=reply_markup)
        return
    if repository is None or not workspace_id:
        await update.message.reply_text(MISSING_TABLE_MESSAGE, reply_markup=reply_markup)
        return
    try:
        if suggestion_id_or_prefix.strip():
            suggestion = await _resolve_suggestion(repository, workspace_id, suggestion_id_or_prefix)
            if suggestion is None:
                await update.message.reply_text("Suggestion not found.", reply_markup=reply_markup)
                return
            await update.message.reply_text(
                format_docs_suggestion_card(suggestion),
                reply_markup=docs_suggestion_card_keyboard(suggestion),
            )
            return
        suggestions = await repository.list_pending(workspace_id, limit=10)
    except Exception as exc:  # noqa: BLE001 - command must not break runtime if migration is absent
        await update.message.reply_text(_safe_repository_error(exc, safe_error), reply_markup=reply_markup)
        return
    await update.message.reply_text(
        format_docs_suggestions_list(DocsSuggestionsList(suggestions=suggestions)),
        reply_markup=docs_suggestions_list_keyboard(suggestions),
    )


async def send_docs_suggestion_callback(
    update: Update,
    *,
    repository: DocsSuggestionsRepository | None,
    workspace_id: str,
    action: str,
    suggestion_id: str,
    reviewer_user_id: int | None,
    is_allowed: bool,
    preview_service: DocsSuggestionPreviewReader | None = None,
    activation_service: DocsSuggestionActivationReader | None = None,
    safe_error: SafeErrorFormatter | None = None,
) -> None:
    """Handle owner/admin suggestion review callbacks."""
    query = update.callback_query
    if query is None:
        return
    if not is_allowed:
        await query.answer()
        await _edit_or_reply_callback(query, "This action is available to the bot owner/admin.")
        return
    if repository is None or not workspace_id:
        await query.answer()
        await _edit_or_reply_callback(query, MISSING_TABLE_MESSAGE)
        return
    try:
        if action == "list":
            await query.answer()
            suggestions = await repository.list_pending(workspace_id, limit=10)
            await _edit_or_reply_callback(
                query,
                format_docs_suggestions_list(DocsSuggestionsList(suggestions=suggestions)),
                reply_markup=docs_suggestions_list_keyboard(suggestions),
            )
            return
        suggestion = await repository.get(suggestion_id)
        if suggestion is None:
            await query.answer()
            await _edit_or_reply_callback(query, "Suggestion not found.", reply_markup=docs_suggestions_back_keyboard())
            return
        if action == "open":
            await query.answer()
            await _edit_or_reply_callback(
                query,
                format_docs_suggestion_card(suggestion),
                reply_markup=docs_suggestion_card_keyboard(suggestion),
            )
            return
        if action == "preview":
            await query.answer("Checking preview...")
            updated = await preview_docs_suggestion(
                repository,
                suggestion,
                preview_service=preview_service,
            )
            await _edit_or_reply_callback(
                query,
                format_docs_suggestion_card(updated),
                reply_markup=docs_suggestion_card_keyboard(updated),
            )
            return
        if action == "approve":
            await query.answer()
            if not _can_confirm_add(suggestion):
                await _edit_or_reply_callback(
                    query,
                    format_docs_suggestion_add_blocked(suggestion),
                    reply_markup=docs_suggestion_card_keyboard(suggestion),
                )
                return
            await _edit_or_reply_callback(
                query,
                format_docs_suggestion_add_confirmation(suggestion),
                reply_markup=docs_suggestion_confirm_add_keyboard(suggestion.id),
            )
            return
        if action == "confirm_add":
            await query.answer("Adding documentation...")
            updated = await activate_docs_suggestion(
                repository,
                suggestion,
                activation_service=activation_service,
                reviewer_user_id=reviewer_user_id or 0,
            )
            await _edit_or_reply_callback(
                query,
                format_docs_suggestion_card(updated),
                reply_markup=docs_suggestion_card_keyboard(updated),
            )
            return
        if action == "reject":
            await query.answer("Rejected")
            updated = await repository.reject(
                suggestion.id,
                reviewed_by_user_id=reviewer_user_id or 0,
                rejection_reason=DEFAULT_REJECTION_REASON,
            )
            await _edit_or_reply_callback(
                query,
                format_docs_suggestion_rejected(updated),
                reply_markup=docs_suggestions_back_keyboard(),
            )
            return
        await query.answer("Unknown action")
    except Exception as exc:  # noqa: BLE001 - callback must fail gracefully
        await query.answer()
        await _edit_or_reply_callback(query, _safe_repository_error(exc, safe_error), reply_markup=docs_suggestions_back_keyboard())


async def preview_docs_suggestion(
    repository: DocsSuggestionsRepository,
    suggestion: DocsCandidateSuggestion,
    *,
    preview_service: DocsSuggestionPreviewReader | None = None,
) -> DocsCandidateSuggestion:
    """Run existing preview service for a persisted suggestion and save compact result."""
    service = preview_service or DocsCandidatePreviewService(
        candidates_config=DocsSourceCandidatesConfig(candidates=(_candidate_from_suggestion(suggestion),))
    )
    try:
        result = await service.preview(suggestion.service_id, limit=5)
        return await repository.save_preview_result(
            suggestion.id,
            preview_status=result.status,
            preview_result=_compact_preview_result(result),
        )
    except Exception as exc:  # noqa: BLE001 - preview failures are persisted as failed
        return await repository.save_preview_result(
            suggestion.id,
            preview_status="failed",
            preview_result={
                "status": "failed",
                "error": exc.__class__.__name__,
            },
            status="failed",
        )


async def activate_docs_suggestion(
    repository: DocsSuggestionsRepository,
    suggestion: DocsCandidateSuggestion,
    *,
    activation_service: DocsSuggestionActivationReader | None,
    reviewer_user_id: int,
) -> DocsCandidateSuggestion:
    """Activate one previewed suggestion through the existing activation service."""
    if not _can_confirm_add(suggestion):
        return suggestion
    if activation_service is None:
        return await repository.save_activation_result(
            suggestion.id,
            activation_result={"status": "failed", "error": "DocsActivationRuntimeUnavailableError"},
            status="failed",
            reviewed_by_user_id=reviewer_user_id,
        )
    candidate = _candidate_from_suggestion(suggestion)
    policy = DynamicDocsActivationPolicy(
        candidate_id=suggestion.id,
        official_url=suggestion.official_url,
        allowed_domain=suggestion.allowed_domain,
        preview_status=suggestion.preview_status,
        confirmed_by_user_id=reviewer_user_id,
    )
    try:
        result = await activation_service.activate_candidate(suggestion.id, candidate, policy=policy)
    except Exception as exc:  # noqa: BLE001 - activation failures are persisted without leaking details
        return await repository.save_activation_result(
            suggestion.id,
            activation_result={"status": "failed", "error": exc.__class__.__name__},
            status="failed",
            reviewed_by_user_id=reviewer_user_id,
        )
    compact = _compact_activation_result(result)
    status = "activated" if _activation_succeeded(result) else "failed"
    compact["status"] = status
    return await repository.save_activation_result(
        suggestion.id,
        activation_result=compact,
        status=status,
        reviewed_by_user_id=reviewer_user_id,
    )


def format_docs_suggestions_list(review: DocsSuggestionsList) -> str:
    """Format reviewable suggestions without raw metadata."""
    lines = ["Docs suggestions", ""]
    if not review.suggestions:
        lines.append("No pending suggestions.")
        return "\n".join(lines)
    for suggestion in review.suggestions[:10]:
        lines.append(
            " - ".join(
                (
                    _short_id(suggestion.id),
                    suggestion.display_name or suggestion.service_id,
                    suggestion.allowed_domain or "unknown domain",
                    f"status={suggestion.status}",
                    f"preview={suggestion.preview_status}",
                    f"risk={suggestion.risk_level}",
                )
            )
        )
    lines.extend(["", "Open a card with the buttons below or /docs_suggestions <id>."])
    return "\n".join(lines)


def format_docs_suggestion_card(suggestion: DocsCandidateSuggestion) -> str:
    """Format one suggestion card without raw metadata/debug fields."""
    return "\n".join(
        [
            f"Docs suggestion: {suggestion.display_name or suggestion.service_id}",
            "",
            f"ID: {_short_id(suggestion.id)}",
            f"Service: {suggestion.service_id}",
            f"URL: {suggestion.official_url}",
            f"Domain: {suggestion.allowed_domain}",
            f"Candidate source: {_candidate_source(suggestion)}",
            f"Confidence: {suggestion.confidence:.2f}",
            f"Risk: {suggestion.risk_level}",
            f"Status: {suggestion.status}",
            f"Preview status: {suggestion.preview_status}",
            *_preview_failure_lines(suggestion),
            f"Reason: {_compact(suggestion.discovery_reason or 'not provided')}",
            *_activation_summary_lines(suggestion),
            "",
            _card_safety_line(suggestion),
        ]
    )


def format_docs_suggestion_add_blocked(suggestion: DocsCandidateSuggestion) -> str:
    """Format a blocked activation attempt."""
    return "\n".join(
        [
            f"Docs suggestion: {suggestion.display_name or suggestion.service_id}",
            "",
            "Adding is blocked until preview status is ok or needs_review.",
            f"Preview status: {suggestion.preview_status}",
        ]
    )


def format_docs_suggestion_add_confirmation(suggestion: DocsCandidateSuggestion) -> str:
    """Format explicit confirmation text before activation/crawl/indexing."""
    return "\n".join(
        [
            f"Confirm adding documentation: {suggestion.display_name or suggestion.service_id}",
            "",
            f"URL: {suggestion.official_url}",
            f"Domain: {suggestion.allowed_domain}",
            f"Preview status: {suggestion.preview_status}",
            "",
            "This will use the existing activation, crawler, extractor, and indexer services.",
        ]
    )


def format_docs_suggestion_rejected(suggestion: DocsCandidateSuggestion) -> str:
    """Format a rejected result."""
    return "\n".join(
        [
            f"Docs suggestion rejected: {suggestion.display_name or suggestion.service_id}",
            "",
            f"ID: {_short_id(suggestion.id)}",
            f"Status: {suggestion.status}",
            f"Reason: {suggestion.rejection_reason or DEFAULT_REJECTION_REASON}",
            "",
            "The record was kept for deduplication and review history.",
        ]
    )


def docs_suggestions_list_keyboard(suggestions: tuple[DocsCandidateSuggestion, ...]) -> InlineKeyboardMarkup | None:
    """Return list buttons for suggestion cards."""
    if not suggestions:
        return None
    rows = [
        [
            InlineKeyboardButton(
                f"{_short_id(suggestion.id)} {suggestion.display_name or suggestion.service_id}",
                callback_data=f"{CALLBACK_PREFIX}:open:{suggestion.id}",
            )
        ]
        for suggestion in suggestions[:10]
    ]
    return InlineKeyboardMarkup(rows)


def docs_suggestion_card_keyboard(suggestion: DocsCandidateSuggestion) -> InlineKeyboardMarkup:
    """Return card review actions."""
    preview_label = "Проверить снова" if suggestion.preview_status == "failed" or suggestion.status == "failed" else "Проверить"
    rows = [
        [
            InlineKeyboardButton(preview_label, callback_data=f"{CALLBACK_PREFIX}:preview:{suggestion.id}"),
            InlineKeyboardButton("Отклонить", callback_data=f"{CALLBACK_PREFIX}:reject:{suggestion.id}"),
        ]
    ]
    if _can_confirm_add(suggestion):
        rows.append([InlineKeyboardButton("Добавить", callback_data=f"{CALLBACK_PREFIX}:approve:{suggestion.id}")])
    rows.append([InlineKeyboardButton("Назад", callback_data=f"{CALLBACK_PREFIX}:list")])
    return InlineKeyboardMarkup(rows)
    rows = [
        [
            InlineKeyboardButton("Проверить", callback_data=f"{CALLBACK_PREFIX}:preview:{suggestion.id}"),
            InlineKeyboardButton("Отклонить", callback_data=f"{CALLBACK_PREFIX}:reject:{suggestion.id}"),
        ]
    ]
    if _can_confirm_add(suggestion):
        rows.append([InlineKeyboardButton("Добавить", callback_data=f"{CALLBACK_PREFIX}:approve:{suggestion.id}")])
    rows.append([InlineKeyboardButton("Назад", callback_data=f"{CALLBACK_PREFIX}:list")])
    return InlineKeyboardMarkup(rows)


def docs_suggestion_confirm_add_keyboard(suggestion_id: str) -> InlineKeyboardMarkup:
    """Return explicit confirmation actions for adding docs."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Подтвердить добавление", callback_data=f"{CALLBACK_PREFIX}:confirm_add:{suggestion_id}")],
            [InlineKeyboardButton("Назад", callback_data=f"{CALLBACK_PREFIX}:open:{suggestion_id}")],
        ]
    )


def docs_suggestions_back_keyboard() -> InlineKeyboardMarkup:
    """Return a back button to the suggestions list."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data=f"{CALLBACK_PREFIX}:list")]])


async def _resolve_suggestion(
    repository: DocsSuggestionsRepository,
    workspace_id: str,
    suggestion_id_or_prefix: str,
) -> DocsCandidateSuggestion | None:
    clean = suggestion_id_or_prefix.strip()
    if not clean:
        return None
    if len(clean) >= 32:
        return await repository.get(clean)
    suggestions = await repository.list_pending(workspace_id, limit=50)
    matches = tuple(suggestion for suggestion in suggestions if suggestion.id.startswith(clean))
    return matches[0] if len(matches) == 1 else None


def _candidate_from_suggestion(suggestion: DocsCandidateSuggestion) -> DocsSourceCandidate:
    metadata = suggestion.metadata if isinstance(suggestion.metadata, dict) else {}
    return DocsSourceCandidate(
        service_id=suggestion.service_id,
        display_name=suggestion.display_name or suggestion.service_id,
        aliases=suggestion.aliases or (suggestion.service_id,),
        docs_source=str(metadata.get("docs_source") or f"{suggestion.service_id}_docs"),
        official_start_urls=(suggestion.official_url,),
        allowed_domains=(suggestion.allowed_domain,),
        allow_patterns=tuple(str(item) for item in metadata.get("allow_patterns") or ()),
        deny_patterns=tuple(str(item) for item in metadata.get("deny_patterns") or ()),
        max_pages=_int(metadata.get("max_pages"), default=5),
        crawl_depth=_int(metadata.get("crawl_depth"), default=1),
        risk_level=suggestion.risk_level if suggestion.risk_level in {"low", "medium", "review"} else "review",
        notes=str(metadata.get("notes") or ""),
    )


def _compact_preview_result(result: DocsCandidatePreviewResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "pages_checked": result.pages_checked,
        "pages_found": result.pages_found,
        "sample_titles": [_compact(item, limit=120) for item in result.sample_titles[:3]],
        "sample_urls": [_compact(item, limit=200) for item in result.sample_urls[:3]],
        "warnings": [_compact(item, limit=160) for item in result.warnings[:5]],
        "docs_source": result.docs_source,
        "allowed_domains": list(result.allowed_domains[:3]),
        "start_urls": list(result.start_urls[:3]),
    }


def _compact_activation_result(result: DocsActivationResult) -> dict[str, Any]:
    return {
        "fetched_pages": result.fetched_pages,
        "indexed_new": result.indexed_new,
        "skipped_unchanged": result.skipped_unchanged,
        "archived_old": result.archived_old,
        "failed": result.failed,
        "chunks_total": result.chunks_total,
        "quality": result.quality_gate.quality,
        "quality_failures": [_compact(item, limit=160) for item in result.quality_gate.failures[:5]],
        "quality_warnings": [_compact(item, limit=160) for item in result.quality_gate.warnings[:5]],
        "errors": [_compact(item, limit=160) for item in result.errors[:5]],
        "docs_source": result.plan.docs_source,
        "allowed_domains": list(result.plan.allowed_domains[:3]),
        "start_urls": list(result.plan.start_urls[:3]),
    }


def _activation_succeeded(result: DocsActivationResult) -> bool:
    return bool(result.quality_gate.passed and result.fetched_pages > 0 and result.failed < result.fetched_pages)


def _preview_failure_lines(suggestion: DocsCandidateSuggestion) -> list[str]:
    if suggestion.preview_status != "failed":
        return []
    result = suggestion.preview_result if isinstance(suggestion.preview_result, dict) else {}
    reason = str(result.get("error") or result.get("status") or "failed")
    return [f"Preview error: {_compact(reason, limit=120)}"]


def _activation_summary_lines(suggestion: DocsCandidateSuggestion) -> list[str]:
    metadata = suggestion.metadata if isinstance(suggestion.metadata, dict) else {}
    result = metadata.get("activation_result")
    if not isinstance(result, dict):
        return []
    status = _compact(str(result.get("status") or suggestion.status), limit=80)
    quality = _compact(str(result.get("quality") or ""), limit=80)
    fetched = _compact(str(result.get("fetched_pages") or 0), limit=20)
    indexed = _compact(str(result.get("indexed_new") or 0), limit=20)
    lines = [f"Activation status: {status}"]
    if quality:
        lines.append(f"Activation quality: {quality}")
    lines.append(f"Activation pages/indexed: {fetched}/{indexed}")
    return lines


def _card_safety_line(suggestion: DocsCandidateSuggestion) -> str:
    if suggestion.status == "activated":
        return "Activated through the existing controlled docs activation service."
    return "Preview only until owner/admin confirms adding."


def _can_confirm_add(suggestion: DocsCandidateSuggestion) -> bool:
    return suggestion.status == "preview_ready" and suggestion.preview_status in {"ok", "needs_review"}


def _candidate_source(suggestion: DocsCandidateSuggestion) -> str:
    raw = str((suggestion.metadata or {}).get("source") or "").casefold()
    if "docs_source_candidates" in raw or raw == "curated":
        return "curated"
    if raw:
        return "discovered"
    return "unknown"


def _safe_repository_error(exc: Exception, formatter: SafeErrorFormatter | None) -> str:
    if _is_missing_table(exc):
        return MISSING_TABLE_MESSAGE
    if formatter is not None:
        safe = formatter(exc).strip()
        if safe:
            return "Could not prepare docs suggestions: " + safe
    return "Could not prepare docs suggestions: " + exc.__class__.__name__


def _is_missing_table(exc: Exception) -> bool:
    return isinstance(exc, SupabaseRequestError) and exc.is_missing_relation


async def _edit_or_reply_callback(query: Any, text: str, *, reply_markup: Any | None = None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
        return
    except Exception:  # noqa: BLE001 - keep callback visible if edit fails
        message = getattr(query, "message", None)
        reply_text = getattr(message, "reply_text", None)
        if reply_text is None:
            raise
        await reply_text(text, reply_markup=reply_markup)


def _short_id(value: str) -> str:
    return str(value or "")[:8] or "unknown"


def _compact(value: str, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _int(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
