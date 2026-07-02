"""Read-only Telegram dashboard for documentation sources."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from telegram import Update

from app.docs_registry.activation import (
    ArbitraryDocsActivationUrlError,
    DocsActivationCandidateNotFoundError,
    DocsActivationError,
    DocsActivationPlan,
    DocsActivationResult,
    DocsActivationRuntimeUnavailableError,
    DocsActivationService,
)
from app.docs_registry.candidates import load_docs_source_candidates_config
from app.docs_registry.models import DocsCandidatePreviewResult, DocsSourceCandidate
from app.docs_registry.preview import (
    ArbitraryDocsUrlError,
    DocsCandidateNotFoundError,
    DocsCandidatePreviewService,
)
from app.service_registry.types import ServiceDocsStatus


class ServiceDocsStatusReader(Protocol):
    """Read-only service docs status provider."""

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        """Return service/docs status rows."""


class DocsPreviewReader(Protocol):
    """Read-only candidate preview service."""

    async def preview(self, service_id_or_alias: str, *, limit: int = 5) -> DocsCandidatePreviewResult:
        """Return a safe dry-run preview for a curated candidate."""


class DocsActivationReader(Protocol):
    """Controlled docs activation service."""

    def plan(self, service_id_or_alias: str) -> DocsActivationPlan:
        """Return a safe activation plan without indexing or writing."""

    async def activate(self, service_id_or_alias: str) -> DocsActivationResult:
        """Run controlled activation after explicit confirmation."""


async def send_docs_dashboard(
    update: Update,
    *,
    status_provider: ServiceDocsStatusReader | None,
    is_allowed: bool,
    reply_markup: Any | None = None,
    safe_error: Callable[[Exception], str] | None = None,
    candidate_loader: Callable[[], tuple[DocsSourceCandidate, ...]] | None = None,
) -> None:
    """Send the read-only `/docs` dashboard."""
    if update.message is None:
        return
    if not is_allowed:
        await update.message.reply_text(
            "Панель документации доступна владельцу бота.",
            reply_markup=reply_markup,
        )
        return
    if status_provider is None:
        await update.message.reply_text(
            "Панель документации пока недоступна: не подключено чтение Supabase или registry.",
            reply_markup=reply_markup,
        )
        return
    try:
        statuses = await status_provider.list_statuses(scan_corpus=False)
    except Exception as exc:  # noqa: BLE001 - Telegram command should fail gracefully
        error = safe_error(exc) if safe_error is not None else str(exc)
        await update.message.reply_text(
            "Не получилось получить панель документации: " + error,
            reply_markup=reply_markup,
        )
        return
    candidates = _load_candidates(candidate_loader)
    await update.message.reply_text(format_docs_dashboard(statuses, candidates=candidates), reply_markup=reply_markup)


def format_docs_dashboard(
    statuses: tuple[ServiceDocsStatus, ...],
    *,
    candidates: tuple[DocsSourceCandidate, ...] = (),
) -> str:
    """Format a compact read-only documentation dashboard."""
    connected = tuple(status for status in statuses if _is_connected(status))
    not_connected = tuple(status for status in statuses if not _is_connected(status))
    available_candidates = _available_candidates(candidates, statuses)

    lines = [
        "Документация сервисов:",
        "",
        "Подключено:",
    ]
    if connected:
        lines.extend(_connected_line(status) for status in connected[:10])
    else:
        lines.append("нет данных")

    lines.extend(["", "Не подключено:"])
    if not_connected:
        lines.extend(_not_connected_line(status) for status in not_connected[:10])
    else:
        lines.append("нет данных")

    lines.extend(["", "Можно подключить позже:"])
    if available_candidates:
        shown = available_candidates[:8]
        lines.extend(f"➕ {candidate.display_name}" for candidate in shown)
        hidden_count = len(available_candidates) - len(shown)
        if hidden_count > 0:
            lines.append(f"Ещё: {hidden_count}")
    else:
        lines.append("нет данных")

    lines.extend(
        [
            "",
            "Что можно делать:",
            "- /services — технический статус сервисов",
            "- /base_status — статус базы знаний",
            "- /docs_preview <id> — безопасный предпросмотр кандидата",
            "- /docs_activate openrouter — план controlled activation для OpenRouter",
            "",
            "Для предпросмотра:",
            "`/docs_preview <id>`",
            "",
            "Для controlled activation:",
            "`/docs_activate openrouter`",
            "",
            "Следующий этап:",
            "подключение новых official docs будет через безопасный preview/dry-run и подтверждение владельца.",
        ]
    )
    return "\n".join(lines)


async def send_docs_activation(
    update: Update,
    *,
    service_id_or_alias: str,
    confirm: bool,
    is_allowed: bool,
    activation_service: DocsActivationReader | None = None,
    reply_markup: Any | None = None,
    safe_error: Callable[[Exception], str] | None = None,
) -> None:
    """Send an activation plan or run controlled activation after confirm."""
    if update.message is None:
        return
    if not is_allowed:
        await update.message.reply_text(
            "Подключение документации доступно владельцу бота.",
            reply_markup=reply_markup,
        )
        return
    if not service_id_or_alias.strip():
        await update.message.reply_text(
            "Укажите сервис: /docs_activate openrouter",
            reply_markup=reply_markup,
        )
        return

    service = activation_service or DocsActivationService()
    try:
        if not confirm:
            await update.message.reply_text(
                format_docs_activation_plan(service.plan(service_id_or_alias.strip())),
                reply_markup=reply_markup,
            )
            return
        if activation_service is None:
            raise DocsActivationRuntimeUnavailableError("activation runtime is not configured")
        result = await activation_service.activate(service_id_or_alias.strip())
    except ArbitraryDocsActivationUrlError:
        await update.message.reply_text(
            "Произвольные URL нельзя подключать. Используйте сервис из /docs.",
            reply_markup=reply_markup,
        )
        return
    except DocsActivationCandidateNotFoundError:
        await update.message.reply_text(
            "Кандидат не найден. Посмотрите список в /docs.",
            reply_markup=reply_markup,
        )
        return
    except DocsActivationRuntimeUnavailableError:
        await update.message.reply_text(
            "Подключение документации пока недоступно: не хватает runtime-настроек.",
            reply_markup=reply_markup,
        )
        return
    except DocsActivationError as exc:
        await update.message.reply_text(str(exc), reply_markup=reply_markup)
        return
    except Exception as exc:  # noqa: BLE001 - Telegram command should fail gracefully
        error = safe_error(exc) if safe_error is not None else str(exc)
        await update.message.reply_text(
            "Не получилось выполнить controlled activation: " + error,
            reply_markup=reply_markup,
        )
        return
    await update.message.reply_text(format_docs_activation_result(result), reply_markup=reply_markup)


def format_docs_activation_plan(plan: DocsActivationPlan) -> str:
    """Format a pre-confirm activation plan."""
    lines = [
        f"Controlled activation: {plan.display_name}",
        "",
        "Статус: готов к проверочному подключению",
        f"Домен: {', '.join(plan.allowed_domains) if plan.allowed_domains else 'нет данных'}",
        "Стартовый URL:",
        *plan.start_urls,
        "",
        f"Лимит страниц: {plan.max_pages}",
        f"Глубина crawl: {plan.crawl_depth}",
        f"Риск: {plan.risk_level}",
        "",
        "Это действие подключит official docs source после crawl/index/quality gate.",
        "",
        "Для запуска напишите:",
        f"`{plan.confirm_command}`",
    ]
    if plan.warnings:
        lines.extend(["", "Предупреждения:", *[f"- {warning}" for warning in plan.warnings[:5]]])
    return "\n".join(lines)


def format_docs_activation_result(result: DocsActivationResult) -> str:
    """Format controlled activation result without raw JSON."""
    lines = [
        f"Controlled activation: {result.plan.display_name}",
        "",
        f"Quality gate: {result.quality_gate.quality}",
        f"Fetched pages: {result.fetched_pages}",
        f"Indexed new: {result.indexed_new}",
        f"Skipped unchanged: {result.skipped_unchanged}",
        f"Archived old: {result.archived_old}",
        f"Failed: {result.failed}",
        f"Chunks: {result.chunks_total}",
    ]
    if result.quality_gate.failures:
        lines.extend(["", "Failures:", *[f"- {failure}" for failure in result.quality_gate.failures[:5]]])
    if result.quality_gate.warnings:
        lines.extend(["", "Warnings:", *[f"- {warning}" for warning in result.quality_gate.warnings[:5]]])
    if result.errors:
        lines.extend(["", "Errors:", *[f"- {error}" for error in result.errors[:3]]])
    lines.extend(
        [
            "",
            (
                "OpenRouter docs indexed through controlled activation."
                if result.quality_gate.passed
                else "Activation needs review before relying on this source."
            ),
        ]
    )
    return "\n".join(lines)


async def send_docs_preview(
    update: Update,
    *,
    service_id_or_alias: str,
    is_allowed: bool,
    preview_service: DocsPreviewReader | None = None,
    reply_markup: Any | None = None,
    safe_error: Callable[[Exception], str] | None = None,
) -> None:
    """Send a safe read-only preview for one curated docs candidate."""
    if update.message is None:
        return
    if not is_allowed:
        await update.message.reply_text(
            "Предпросмотр документации доступен владельцу бота.",
            reply_markup=reply_markup,
        )
        return
    if not service_id_or_alias.strip():
        await update.message.reply_text(
            "Укажите сервис: /docs_preview claude_code",
            reply_markup=reply_markup,
        )
        return
    service = preview_service or DocsCandidatePreviewService()
    try:
        result = await service.preview(service_id_or_alias.strip(), limit=5)
    except ArbitraryDocsUrlError:
        await update.message.reply_text(
            "Произвольные URL нельзя проверять. Используйте сервис из /docs.",
            reply_markup=reply_markup,
        )
        return
    except DocsCandidateNotFoundError:
        await update.message.reply_text(
            "Кандидат документации не найден. Посмотрите список в /docs.",
            reply_markup=reply_markup,
        )
        return
    except Exception as exc:  # noqa: BLE001 - Telegram command should fail gracefully
        error = safe_error(exc) if safe_error is not None else str(exc)
        await update.message.reply_text(
            "Не получилось подготовить предпросмотр документации: " + error,
            reply_markup=reply_markup,
        )
        return
    await update.message.reply_text(format_docs_preview(result), reply_markup=reply_markup)


def format_docs_preview(result: DocsCandidatePreviewResult) -> str:
    """Format a safe preview result for Telegram."""
    status_text = {
        "ok": "можно проверить",
        "needs_review": "нужна ручная проверка",
        "failed": "не удалось проверить",
    }.get(result.status, result.status)
    lines = [
        f"Предпросмотр документации: {result.display_name}",
        "",
        f"Статус: {status_text}",
        f"Домен: {', '.join(result.allowed_domains) if result.allowed_domains else 'нет данных'}",
        "Стартовый URL:",
    ]
    lines.extend(result.start_urls or ("нет данных",))
    lines.extend(
        [
            "",
            f"Проверено страниц: {result.pages_checked}",
            f"Найдено страниц: {result.pages_found}",
        ]
    )
    samples = _preview_samples(result)
    if samples:
        lines.extend(["", "Примеры:"])
        lines.extend(f"- {sample}" for sample in samples[:5])
    if result.warnings:
        lines.extend(["", "Предупреждения:"])
        lines.extend(f"- {warning}" for warning in result.warnings[:5])
    lines.extend(
        [
            "",
            f"Риск: {result.risk_level}",
            "",
            "Это только preview. Документация не подключена и не используется в ответах.",
            "",
            "Следующий этап:",
            "после проверки можно будет добавить отдельное подтверждение подключения.",
        ]
    )
    return "\n".join(lines)


def _preview_samples(result: DocsCandidatePreviewResult) -> tuple[str, ...]:
    samples = tuple(item for item in result.sample_titles if item.strip())
    if samples:
        return samples
    return tuple(item for item in result.sample_urls if item.strip())


def _load_candidates(loader: Callable[[], tuple[DocsSourceCandidate, ...]] | None) -> tuple[DocsSourceCandidate, ...]:
    try:
        if loader is not None:
            return loader()
        return load_docs_source_candidates_config().candidates
    except Exception:  # noqa: BLE001 - `/docs` must keep working when optional catalog is unavailable
        return ()


def _available_candidates(
    candidates: tuple[DocsSourceCandidate, ...],
    statuses: tuple[ServiceDocsStatus, ...],
) -> tuple[DocsSourceCandidate, ...]:
    connected_service_ids = {
        status.service_id
        for status in statuses
        if _is_connected(status) or status.docs_source_configured or status.docs_status == "configured_not_indexed"
    }
    connected_docs_sources = {
        str(status.docs_source)
        for status in statuses
        if status.docs_source and (_is_connected(status) or status.docs_source_configured)
    }
    return tuple(
        candidate
        for candidate in candidates
        if candidate.service_id not in connected_service_ids and candidate.docs_source not in connected_docs_sources
    )


def _is_connected(status: ServiceDocsStatus) -> bool:
    return (
        status.docs_status == "indexed"
        and bool(status.docs_source_configured or status.docs_source)
        and status.active_docs_count > 0
    )


def _connected_line(status: ServiceDocsStatus) -> str:
    quality = _quality_label(status)
    suffix = f" — {quality}" if quality else ""
    return f"✅ {status.display_name}{suffix}"


def _not_connected_line(status: ServiceDocsStatus) -> str:
    reason = _not_connected_reason(status)
    suffix = f" — {reason}" if reason else ""
    return f"⚪ {status.display_name}{suffix}"


def _quality_label(status: ServiceDocsStatus) -> str:
    quality = (status.quality_status or "").strip()
    if quality and quality.casefold() != "none":
        return quality.upper()
    return "indexed" if status.docs_status == "indexed" else ""


def _not_connected_reason(status: ServiceDocsStatus) -> str:
    if status.docs_status == "disabled" or status.configured_status == "disabled":
        return "отключено"
    if status.docs_status == "configured_not_indexed":
        return "настроено, не проиндексировано"
    if status.docs_status == "needs_review" or status.configured_status == "needs_review":
        return "нужна проверка"
    return ""
