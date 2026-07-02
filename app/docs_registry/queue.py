"""Batch preview and controlled activation queue for docs candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.docs_registry.activation import DocsActivationResult, DocsActivationRuntimeUnavailableError
from app.docs_registry.candidates import load_docs_source_candidates_config
from app.docs_registry.models import DocsCandidatePreviewResult, DocsSourceCandidate, DocsSourceCandidatesConfig
from app.docs_registry.preview import DocsCandidatePreviewService
from app.service_registry.types import ServiceDocsStatus

QueueItemStatus = Literal["ready", "needs_review", "failed", "already_connected"]

DEFAULT_BATCH_ACTIVATION_ALLOWLIST: frozenset[str] = frozenset({"openrouter", "telegram_bot_api"})


class DocsQueuePreviewReader(Protocol):
    """Preview interface used by the activation queue."""

    async def preview(self, service_id_or_alias: str, *, limit: int = 5) -> DocsCandidatePreviewResult:
        """Return a safe dry-run preview for one curated candidate."""


class DocsQueueActivationReader(Protocol):
    """Activation interface used only after explicit owner/admin confirmation."""

    async def activate(self, service_id_or_alias: str) -> DocsActivationResult:
        """Run controlled activation for one curated candidate."""


class DocsQueueStatusReader(Protocol):
    """Read-only docs status provider."""

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        """Return service/docs status rows."""


@dataclass(frozen=True)
class DocsQueueItem:
    """One candidate classified for docs activation."""

    candidate: DocsSourceCandidate
    status: QueueItemStatus
    reason: str
    pages_found: int = 0
    pages_checked: int = 0
    warnings: tuple[str, ...] = ()
    preview: DocsCandidatePreviewResult | None = None

    @property
    def service_id(self) -> str:
        """Return candidate service id."""
        return self.candidate.service_id

    @property
    def display_name(self) -> str:
        """Return candidate display name."""
        return self.candidate.display_name


@dataclass(frozen=True)
class DocsQueueReport:
    """Batch preview report for all curated docs candidates."""

    items: tuple[DocsQueueItem, ...]

    @property
    def ready(self) -> tuple[DocsQueueItem, ...]:
        """Return candidates ready for activation."""
        return tuple(item for item in self.items if item.status == "ready")

    @property
    def needs_review(self) -> tuple[DocsQueueItem, ...]:
        """Return candidates that require manual review."""
        return tuple(item for item in self.items if item.status == "needs_review")

    @property
    def failed(self) -> tuple[DocsQueueItem, ...]:
        """Return candidates whose preview failed."""
        return tuple(item for item in self.items if item.status == "failed")

    @property
    def already_connected(self) -> tuple[DocsQueueItem, ...]:
        """Return candidates whose docs source is already active."""
        return tuple(item for item in self.items if item.status == "already_connected")

    def ready_for_activation(
        self,
        *,
        allowlist: frozenset[str] = DEFAULT_BATCH_ACTIVATION_ALLOWLIST,
    ) -> tuple[DocsQueueItem, ...]:
        """Return ready candidates allowed by the MVP batch activation policy."""
        return tuple(item for item in self.ready if item.service_id in allowlist)


@dataclass(frozen=True)
class DocsQueueActivationResult:
    """Result of activating all ready allowlisted candidates."""

    report: DocsQueueReport
    activated: tuple[DocsActivationResult, ...] = ()
    skipped: tuple[DocsQueueItem, ...] = ()
    errors: tuple[str, ...] = ()


class DocsActivationQueueService:
    """Preview and activate curated docs candidates in batches."""

    def __init__(
        self,
        *,
        candidates_config: DocsSourceCandidatesConfig | None = None,
        preview_service: DocsQueuePreviewReader | None = None,
        activation_service: DocsQueueActivationReader | None = None,
        status_provider: DocsQueueStatusReader | None = None,
        activation_allowlist: frozenset[str] = DEFAULT_BATCH_ACTIVATION_ALLOWLIST,
    ) -> None:
        self._candidates_config = candidates_config
        self._preview_service = preview_service
        self._activation_service = activation_service
        self._status_provider = status_provider
        self._activation_allowlist = activation_allowlist

    async def preview_all(self) -> DocsQueueReport:
        """Run safe previews for all candidates that are not already connected."""
        connected = await self._connected_sources()
        preview_service = self._preview_service or DocsCandidatePreviewService(candidates_config=self._config())
        items: list[DocsQueueItem] = []
        for candidate in self._config().candidates:
            if _candidate_connected(candidate, connected):
                items.append(
                    DocsQueueItem(
                        candidate=candidate,
                        status="already_connected",
                        reason="already connected",
                    )
                )
                continue
            try:
                preview = await preview_service.preview(candidate.service_id, limit=5)
            except Exception as exc:  # noqa: BLE001 - queue report should classify per candidate
                items.append(
                    DocsQueueItem(
                        candidate=candidate,
                        status="failed",
                        reason=_safe_reason(exc),
                    )
                )
                continue
            items.append(_item_from_preview(candidate, preview))
        return DocsQueueReport(items=tuple(items))

    async def ready(self) -> DocsQueueReport:
        """Return a fresh report; callers can use `.ready` for ready candidates."""
        return await self.preview_all()

    async def activation_plan(self) -> DocsQueueReport:
        """Return a fresh report without activating anything."""
        return await self.preview_all()

    async def activate_ready(self) -> DocsQueueActivationResult:
        """Activate ready allowlisted candidates after explicit confirmation."""
        report = await self.preview_all()
        ready = report.ready_for_activation(allowlist=self._activation_allowlist)
        skipped = tuple(item for item in report.items if item not in ready)
        if not ready:
            return DocsQueueActivationResult(report=report, skipped=skipped)
        if self._activation_service is None:
            raise DocsActivationRuntimeUnavailableError("Activation runtime is not configured.")

        activated: list[DocsActivationResult] = []
        errors: list[str] = []
        for item in ready:
            try:
                activated.append(await self._activation_service.activate(item.service_id))
            except Exception as exc:  # noqa: BLE001 - one activation should not hide the rest
                errors.append(f"{item.display_name}: {_safe_reason(exc)}")
        return DocsQueueActivationResult(
            report=report,
            activated=tuple(activated),
            skipped=skipped,
            errors=tuple(errors),
        )

    async def _connected_sources(self) -> tuple[ServiceDocsStatus, ...]:
        if self._status_provider is None:
            return ()
        try:
            return await self._status_provider.list_statuses(scan_corpus=False)
        except Exception:  # noqa: BLE001 - status is useful but preview can continue without it
            return ()

    def _config(self) -> DocsSourceCandidatesConfig:
        if self._candidates_config is not None:
            return self._candidates_config
        return load_docs_source_candidates_config()


def _item_from_preview(candidate: DocsSourceCandidate, preview: DocsCandidatePreviewResult) -> DocsQueueItem:
    status, reason = _classify_preview(preview)
    return DocsQueueItem(
        candidate=candidate,
        status=status,
        reason=reason,
        pages_found=preview.pages_found,
        pages_checked=preview.pages_checked,
        warnings=preview.warnings,
        preview=preview,
    )


def _classify_preview(preview: DocsCandidatePreviewResult) -> tuple[QueueItemStatus, str]:
    if preview.status == "failed" or preview.pages_found <= 0:
        return "failed", _first_reason(preview.warnings, default="no pages found")
    if preview.risk_level != "low" or preview.status == "needs_review":
        return "needs_review", "risk review"
    if preview.pages_checked > 1 and preview.pages_found < 2:
        return "needs_review", f"only {preview.pages_found} page"
    critical_warning = _critical_warning(preview.warnings)
    if critical_warning:
        return "needs_review", critical_warning
    return "ready", f"{preview.pages_found} pages"


def _candidate_connected(candidate: DocsSourceCandidate, statuses: tuple[ServiceDocsStatus, ...]) -> bool:
    service_id = candidate.service_id.casefold()
    docs_source = candidate.docs_source.casefold()
    for status in statuses:
        if status.docs_status != "indexed" or int(status.active_docs_count or 0) <= 0:
            continue
        if status.service_id.casefold() == service_id:
            return True
        if str(status.docs_source or "").casefold() == docs_source:
            return True
    return False


def _critical_warning(warnings: tuple[str, ...]) -> str:
    for warning in warnings:
        clean = " ".join(str(warning).split())
        if clean:
            return clean[:160]
    return ""


def _first_reason(warnings: tuple[str, ...], *, default: str) -> str:
    return _critical_warning(warnings) or default


def _safe_reason(exc: Exception) -> str:
    return " ".join(str(exc).split())[:200] or exc.__class__.__name__
