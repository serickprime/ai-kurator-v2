"""Reviewed exact-key reprocessing planning for external docs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

from app.docs_registry.reconciliation_plan import REVIEW_SCHEMA_VERSION, _payload_checksum
from app.docs_registry.reprocessing_plan import (
    DocsReprocessingPlan,
    SourceInventory,
    SourceScope,
    compare_manifest_to_plan,
    verify_manifest,
)
from app.external_docs.policy import is_url_allowed
from app.external_docs.types import CrawledPage, ExternalDocSource, ExternalDocsIndexResult, ExtractedPage


class ReviewedExternalDocsReprocessingError(ValueError):
    """Raised for safe, expected reviewed reprocessing validation errors."""


class ExactKeyFetcher(Protocol):
    """Fetch only explicitly selected external-doc URLs."""

    async def fetch_page(self, source: ExternalDocSource, url: str, *, depth: int = 0) -> CrawledPage | None:
        """Fetch one whitelisted page without source discovery."""


class ExternalDocsExtractorProtocol(Protocol):
    """Extractor used by exact-key reprocessing."""

    def extract(self, page: CrawledPage) -> ExtractedPage:
        """Extract and clean one fetched page."""


class ExternalDocsIndexerProtocol(Protocol):
    """Indexer used by exact-key reprocessing."""

    async def index_page(
        self,
        page: ExtractedPage,
        source: ExternalDocSource,
        *,
        workspace: str = "team",
    ) -> ExternalDocsIndexResult:
        """Create a new version for one extracted page."""


class TermStatisticsRepository(Protocol):
    """Repository operation used after a full successful target batch."""

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        """Refresh workspace term statistics."""


@dataclass(frozen=True)
class ReprocessDocumentRef:
    """Immutable compact reference to one selected active document."""

    document_id: str
    document_key: str
    status: str
    version: int
    source_id: str
    workspace_id: str
    title: str
    content_hash: str
    ingestion_signature: str
    cards_count: int
    sections_count: int
    chunks_count: int
    latest_updated_at: str
    resolved_fetch_url: str
    required_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "document_id": self.document_id,
            "document_key": self.document_key,
            "status": self.status,
            "version": self.version,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "content_hash": self.content_hash,
            "ingestion_signature": self.ingestion_signature,
            "cards_count": self.cards_count,
            "sections_count": self.sections_count,
            "chunks_count": self.chunks_count,
            "latest_updated_at": self.latest_updated_at,
            "resolved_fetch_url": self.resolved_fetch_url,
            "required_terms": list(self.required_terms),
        }


@dataclass(frozen=True)
class ReviewedReprocessDecision:
    """Owner-reviewed keep-active decision for one target key."""

    document_key: str
    classification: str
    owner_decision: str
    review_status: str
    required_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "document_key": self.document_key,
            "classification": self.classification,
            "owner_decision": self.owner_decision,
            "review_status": self.review_status,
            "required_terms": list(self.required_terms),
        }


@dataclass(frozen=True)
class TargetPreview:
    """Preview and validation state for one future reprocessing target."""

    document: ReprocessDocumentRef
    reviewed_decision: ReviewedReprocessDecision
    url_allowed: bool
    expected_cleaner: str
    expected_future_version: int
    expected_replacement_scope: tuple[str, ...]
    backup_present: bool
    drift_status: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "document": self.document.to_dict(),
            "reviewed_decision": self.reviewed_decision.to_dict(),
            "url_allowed": self.url_allowed,
            "expected_cleaner": self.expected_cleaner,
            "expected_future_version": self.expected_future_version,
            "expected_replacement_scope": list(self.expected_replacement_scope),
            "backup_present": self.backup_present,
            "drift_status": self.drift_status,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ReprocessingBackupGate:
    """Fresh rollback-capable backup gate status."""

    provided: bool
    valid: bool
    rollback_capable: bool
    drift_matches: bool
    generated_at: str
    baseline_fingerprint: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "provided": self.provided,
            "valid": self.valid,
            "rollback_capable": self.rollback_capable,
            "drift_matches": self.drift_matches,
            "generated_at": self.generated_at,
            "baseline_fingerprint": self.baseline_fingerprint,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ReviewedExternalDocsReprocessingPlan:
    """Immutable read-only plan for reviewed exact-key reprocessing."""

    mode: str
    service_id: str
    source_id: str
    workspace_id: str
    workspace_name: str
    generated_at: str
    target_count: int
    max_target_count: int
    targets: tuple[TargetPreview, ...]
    reviewed_artifact_checksum_valid: bool
    reviewed_artifact_fingerprint: str
    backup: ReprocessingBackupGate
    live_inventory_fingerprint: str
    full_source_crawl: str
    arbitrary_urls: str
    expected_write_scope: dict[str, tuple[str, ...]]
    term_statistics_strategy: str
    rollback_strategy: str
    readiness: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    automatic_execution_allowed: bool = False

    @property
    def expected_confirmation_phrase(self) -> str:
        """Return exact phrase required for future execution."""
        ids = ",".join(target.document.document_id for target in self.targets) or "missing-targets"
        return f"reprocess-reviewed-external-docs:{ids}"

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "mode": self.mode,
            "service_id": self.service_id,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "generated_at": self.generated_at,
            "target_count": self.target_count,
            "max_target_count": self.max_target_count,
            "targets": [target.to_dict() for target in self.targets],
            "reviewed_artifact_checksum_valid": self.reviewed_artifact_checksum_valid,
            "reviewed_artifact_fingerprint": self.reviewed_artifact_fingerprint,
            "backup": self.backup.to_dict(),
            "live_inventory_fingerprint": self.live_inventory_fingerprint,
            "full_source_crawl": self.full_source_crawl,
            "arbitrary_urls": self.arbitrary_urls,
            "expected_write_scope": {
                key: list(values) for key, values in self.expected_write_scope.items()
            },
            "term_statistics_strategy": self.term_statistics_strategy,
            "rollback_strategy": self.rollback_strategy,
            "ready_for_reprocessing": self.readiness,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "automatic_execution_allowed": self.automatic_execution_allowed,
            "supabase_writes": "disabled" if self.mode == "read-only" else "owner-confirmed only",
            "fetch": "not performed" if self.mode == "read-only" else "exact selected URLs only",
            "full_source_crawl_disabled": True,
            "expected_confirmation_phrase": self.expected_confirmation_phrase,
        }


@dataclass(frozen=True)
class TargetReprocessingResult:
    """Structured per-target result for future execution."""

    document_key: str
    old_document_id: str
    old_version: int
    new_document_id: str = ""
    new_version: int = 0
    fetch_status: str = "not run"
    extraction_status: str = "not run"
    cleaner_status: str = "not run"
    validation_status: str = "not run"
    indexing_status: str = "not run"
    sections_count: int = 0
    chunks_count: int = 0
    useful_terms_preserved: bool = False
    boilerplate_removed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "document_key": self.document_key,
            "old_document_id": self.old_document_id,
            "old_version": self.old_version,
            "new_document_id": self.new_document_id,
            "new_version": self.new_version,
            "fetch_status": self.fetch_status,
            "extraction_status": self.extraction_status,
            "cleaner_status": self.cleaner_status,
            "validation_status": self.validation_status,
            "indexing_status": self.indexing_status,
            "sections_count": self.sections_count,
            "chunks_count": self.chunks_count,
            "useful_terms_preserved": self.useful_terms_preserved,
            "boilerplate_removed": self.boilerplate_removed,
            "error": self.error,
        }


@dataclass(frozen=True)
class ReprocessingExecutionResult:
    """Structured result for future exact-key reprocessing execution."""

    status: str
    target_count: int
    targets: tuple[TargetReprocessingResult, ...]
    changed_keys: tuple[str, ...]
    unchanged_keys: tuple[str, ...]
    failed_keys: tuple[str, ...]
    term_statistics_status: str
    partial_failure: bool
    rollback_required: bool
    automatic_retry: bool
    automatic_rollback: bool
    timestamp: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "status": self.status,
            "target_count": self.target_count,
            "targets": [target.to_dict() for target in self.targets],
            "changed_keys": list(self.changed_keys),
            "unchanged_keys": list(self.unchanged_keys),
            "failed_keys": list(self.failed_keys),
            "term_statistics_status": self.term_statistics_status,
            "partial_failure": self.partial_failure,
            "rollback_required": self.rollback_required,
            "automatic_retry": self.automatic_retry,
            "automatic_rollback": self.automatic_rollback,
            "timestamp": self.timestamp,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def build_reviewed_external_docs_reprocessing_plan(
    *,
    scope: SourceScope,
    inventory: SourceInventory,
    current_plan: DocsReprocessingPlan,
    reviewed_artifact: dict[str, Any],
    backup_manifest: dict[str, Any] | None,
    document_ids: tuple[str, ...],
    max_target_count: int = 2,
    generated_at: datetime | None = None,
) -> ReviewedExternalDocsReprocessingPlan:
    """Build a no-fetch, no-write plan for exact reviewed external docs."""
    blockers: list[str] = []
    warnings: list[str] = []
    selected_ids = tuple(item.strip() for item in document_ids if item and item.strip())
    if not selected_ids:
        blockers.append("target set must not be empty")
    if len(selected_ids) != len(set(selected_ids)):
        blockers.append("duplicate target document IDs are not allowed")
    if len(selected_ids) > max_target_count:
        blockers.append(f"target count exceeds max target count: {max_target_count}")

    reviewed_valid = _payload_checksum(reviewed_artifact) == reviewed_artifact.get("checksum")
    if not reviewed_valid:
        blockers.append("reviewed artifact checksum mismatch")
    if reviewed_artifact.get("schema_version") != REVIEW_SCHEMA_VERSION:
        blockers.append("unsupported reviewed artifact schema version")
    if reviewed_artifact.get("service_id") != scope.service_id:
        blockers.append("reviewed artifact service_id does not match scope")
    if reviewed_artifact.get("source_id") != scope.source_id:
        blockers.append("reviewed artifact source_id does not match scope")
    if reviewed_artifact.get("workspace_id") != inventory.workspace_id:
        blockers.append("reviewed artifact workspace_id does not match runtime")
    if reviewed_artifact.get("automatic_archive_allowed") is not False:
        blockers.append("reviewed artifact automatic archive must be disabled")

    source = _source_from_scope(scope)
    active_docs = tuple(row for row in inventory.documents if row.get("status") == "active")
    targets: list[TargetPreview] = []
    for document_id in selected_ids:
        matches = tuple(row for row in active_docs if str(row.get("id") or "") == document_id)
        if len(matches) != 1:
            blockers.append(f"target must match exactly one active document: {document_id}")
            continue
        row = matches[0]
        decision = _reviewed_keep_active_decision(reviewed_artifact, str(row.get("document_key") or ""))
        if decision is None:
            blockers.append("reviewed artifact does not contain target key")
            continue
        target_blockers: list[str] = []
        target_warnings: list[str] = []
        if decision.owner_decision != "keep_active":
            target_blockers.append(f"reviewed decision blocks reprocessing: {decision.owner_decision}")
        if decision.review_status and decision.review_status != "reviewed":
            target_blockers.append("review_status must be reviewed")
        document = _document_ref(row, inventory, decision.required_terms)
        if document.source_id != scope.source_id:
            target_blockers.append("target source_id does not match scope")
        if document.workspace_id != inventory.workspace_id:
            target_blockers.append("target workspace does not match scope")
        if document.status != "active":
            target_blockers.append("target must be active")
        if document.version <= 0:
            target_blockers.append("target version must be positive")
        key_allowed = is_url_allowed(source, document.document_key)
        fetch_allowed = is_url_allowed(source, document.resolved_fetch_url)
        if not key_allowed or not fetch_allowed:
            target_blockers.append("target URL is outside registered source scope")
        if _looks_like_arbitrary_url(document.document_key, source) is False:
            target_warnings.append("target document key is resolved from live metadata and reviewed artifact")
        target = TargetPreview(
            document=document,
            reviewed_decision=decision,
            url_allowed=key_allowed and fetch_allowed,
            expected_cleaner="ExternalDocsExtractor Phase 7A generic cleaner",
            expected_future_version=document.version + 1,
            expected_replacement_scope=(
                "create new version for exact key",
                "archive previous active version for exact key",
                "activate new version for exact key",
            ),
            backup_present=backup_manifest is not None,
            drift_status="pending backup comparison",
            blockers=tuple(target_blockers),
            warnings=tuple(target_warnings),
        )
        targets.append(target)
        blockers.extend(target_blockers)
        warnings.extend(target_warnings)

    if current_plan.duplicate_active_document_keys:
        blockers.append("duplicate active document keys must be zero")

    backup_gate = _backup_gate(
        backup_manifest=backup_manifest,
        scope=scope,
        inventory=inventory,
        current_plan=current_plan,
        targets=tuple(targets),
    )
    blockers.extend(backup_gate.blockers)
    warnings.extend(backup_gate.warnings)

    targets = [
        _replace_target_drift_status(target, "matches" if backup_gate.drift_matches else "blocked")
        for target in targets
    ]

    return ReviewedExternalDocsReprocessingPlan(
        mode="read-only",
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id=inventory.workspace_id,
        workspace_name=inventory.workspace_name,
        generated_at=_format_datetime(generated_at or datetime.now(timezone.utc)),
        target_count=len(targets),
        max_target_count=max_target_count,
        targets=tuple(targets),
        reviewed_artifact_checksum_valid=reviewed_valid,
        reviewed_artifact_fingerprint=str(reviewed_artifact.get("active_inventory_fingerprint") or ""),
        backup=backup_gate,
        live_inventory_fingerprint=current_plan.baseline_fingerprint,
        full_source_crawl="disabled",
        arbitrary_urls="disabled",
        expected_write_scope=expected_reprocessing_write_scope(),
        term_statistics_strategy=(
            "refresh workspace-wide term_statistics once after all selected targets are successfully replaced"
        ),
        rollback_strategy=(
            "no automatic rollback; failed or partial execution requires a separate owner-approved rollback phase"
        ),
        readiness=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        automatic_execution_allowed=False,
    )


async def execute_reviewed_external_docs_reprocessing(
    *,
    plan: ReviewedExternalDocsReprocessingPlan,
    fetcher: ExactKeyFetcher,
    extractor: ExternalDocsExtractorProtocol,
    indexer: ExternalDocsIndexerProtocol,
    term_repository: TermStatisticsRepository,
    confirmation_phrase: str,
    source: ExternalDocSource | None = None,
) -> ReprocessingExecutionResult:
    """Execute exact-key reprocessing after explicit confirmation."""
    now = _format_datetime(datetime.now(timezone.utc))
    if not plan.readiness:
        return _blocked_result(plan, "plan is not ready for reprocessing", timestamp=now)
    if confirmation_phrase != plan.expected_confirmation_phrase:
        return _blocked_result(plan, "explicit confirmation phrase mismatch", timestamp=now)

    source = source or _source_from_plan(plan)
    prepared: list[tuple[TargetPreview, ExtractedPage, TargetReprocessingResult]] = []
    preflight_results: list[TargetReprocessingResult] = []
    for target in plan.targets:
        base = TargetReprocessingResult(
            document_key=target.document.document_key,
            old_document_id=target.document.document_id,
            old_version=target.document.version,
        )
        crawled: CrawledPage | None = None
        extracted: ExtractedPage | None = None
        try:
            crawled = await fetcher.fetch_page(source, target.document.resolved_fetch_url, depth=0)
            if crawled is None:
                raise ReviewedExternalDocsReprocessingError("fetch returned no page")
            extracted = extractor.extract(crawled)
            validation = validate_reprocessed_target(
                target=target,
                extracted=extracted,
                source=source,
            )
        except Exception as exc:  # noqa: BLE001 - return structured pre-write failure
            preflight_results.append(
                _target_result(
                    base,
                    fetch_status="failed" if crawled is None else "ok",
                    extraction_status="failed" if extracted is None else "ok",
                    validation_status="failed",
                    cleaner_status="failed",
                    error=_safe_error(exc),
                )
            )
            return ReprocessingExecutionResult(
                status="blocked",
                target_count=plan.target_count,
                targets=tuple(preflight_results),
                changed_keys=(),
                unchanged_keys=tuple(target.document.document_key for target in plan.targets),
                failed_keys=(target.document.document_key,),
                term_statistics_status="not run",
                partial_failure=False,
                rollback_required=False,
                automatic_retry=False,
                automatic_rollback=False,
                timestamp=now,
                blockers=("pre-index validation failed; no writes performed",),
            )
        prepared.append(
            (
                target,
                extracted,
                _target_result(
                    base,
                    fetch_status="ok",
                    extraction_status="ok",
                    cleaner_status="ok",
                    validation_status="ok",
                    useful_terms_preserved=validation["useful_terms_preserved"],
                    boilerplate_removed=validation["boilerplate_removed"],
                ),
            )
        )

    changed: list[str] = []
    results: list[TargetReprocessingResult] = []
    for target, extracted, base_result in prepared:
        try:
            indexed = await indexer.index_page(extracted, source, workspace=plan.workspace_name)
        except Exception as exc:  # noqa: BLE001 - surface partial write failure
            failed_result = _target_result(base_result, indexing_status="failed", error=_safe_error(exc))
            results.append(failed_result)
            return _partial_result(plan, results, changed, target.document.document_key, timestamp=now)
        if indexed.error:
            failed_result = _target_result(base_result, indexing_status="failed", error=indexed.error[:300])
            results.append(failed_result)
            return _partial_result(plan, results, changed, target.document.document_key, timestamp=now)
        changed.append(target.document.document_key)
        results.append(
            _target_result(
                base_result,
                indexing_status="ok",
                new_document_id=indexed.document_id,
                new_version=indexed.version,
                sections_count=indexed.sections_count,
                chunks_count=indexed.chunks_count,
            )
        )

    try:
        refreshed = await term_repository.refresh_term_statistics(plan.workspace_id)
    except Exception as exc:  # noqa: BLE001 - structured partial failure after writes
        return ReprocessingExecutionResult(
            status="partial_failure",
            target_count=plan.target_count,
            targets=tuple(results),
            changed_keys=tuple(changed),
            unchanged_keys=(),
            failed_keys=(),
            term_statistics_status=f"failed: {exc.__class__.__name__}",
            partial_failure=True,
            rollback_required=True,
            automatic_retry=False,
            automatic_rollback=False,
            timestamp=now,
            warnings=("all target writes succeeded but term_statistics refresh failed",),
        )

    return ReprocessingExecutionResult(
        status="reprocessed",
        target_count=plan.target_count,
        targets=tuple(results),
        changed_keys=tuple(changed),
        unchanged_keys=(),
        failed_keys=(),
        term_statistics_status=f"updated: {refreshed}",
        partial_failure=False,
        rollback_required=False,
        automatic_retry=False,
        automatic_rollback=False,
        timestamp=now,
    )


def validate_reprocessed_target(
    *,
    target: TargetPreview,
    extracted: ExtractedPage,
    source: ExternalDocSource,
) -> dict[str, bool]:
    """Validate cleaned exact-key output before indexing."""
    canonical = extracted.canonical_url or extracted.source_url
    if canonical != target.document.document_key:
        raise ReviewedExternalDocsReprocessingError("fetched page canonical key changed; new review required")
    if not is_url_allowed(source, canonical):
        raise ReviewedExternalDocsReprocessingError("fetched canonical URL is outside source scope")
    text = extracted.structured_text
    if _contains_generator_boilerplate(text):
        raise ReviewedExternalDocsReprocessingError("generic generator boilerplate remains after cleaning")
    missing_terms = [term for term in target.document.required_terms if term.casefold() not in text.casefold()]
    if missing_terms:
        raise ReviewedExternalDocsReprocessingError("required useful terms missing after cleaning: " + ", ".join(missing_terms[:5]))
    return {"useful_terms_preserved": True, "boilerplate_removed": True}


def expected_reprocessing_write_scope() -> dict[str, tuple[str, ...]]:
    """Return exact intended future write scope for key-scoped reprocessing."""
    return {
        "documents": (
            "insert new version for each exact reviewed key",
            "archive previous active version only for each exact reviewed key",
            "activate new version only for each exact reviewed key",
        ),
        "document_cards": ("insert rows for new selected-key versions",),
        "sections": ("insert rows for new selected-key versions",),
        "chunks": ("insert rows for new selected-key versions",),
        "term_statistics": ("workspace-wide refresh once after full success",),
        "evidence_logs": ("direct writes not expected",),
    }


def format_reprocessing_plan_text(plan: ReviewedExternalDocsReprocessingPlan) -> str:
    """Return compact human-readable reprocessing plan text."""
    target_lines: list[str] = []
    for index, target in enumerate(plan.targets, start=1):
        target_lines.extend(
            [
                f"  {index}. {target.document.document_id}",
                f"     key: {target.document.document_key}",
                f"     status/version: {target.document.status}/{target.document.version}",
                f"     decision: {target.reviewed_decision.owner_decision}",
                f"     fetch URL: {target.document.resolved_fetch_url}",
                f"     URL allowed: {_yes_no(target.url_allowed)}",
                f"     future version: {target.expected_future_version}",
            ]
        )
    return "\n".join(
        [
            "Reviewed External Docs Reprocessing Plan",
            "",
            "- mode: read-only",
            f"- service: {plan.service_id}",
            f"- source: {plan.source_id}",
            f"- workspace: {plan.workspace_name} ({plan.workspace_id})",
            f"- target count: {plan.target_count}",
            f"- max target count: {plan.max_target_count}",
            "- targets:",
            *(target_lines or ["  none"]),
            f"- backup valid: {_yes_no(plan.backup.valid)}",
            f"- rollback capable: {_yes_no(plan.backup.rollback_capable)}",
            f"- live drift matches backup: {_yes_no(plan.backup.drift_matches)}",
            f"- full source crawl: {plan.full_source_crawl}",
            f"- arbitrary URLs: {plan.arbitrary_urls}",
            f"- ready for reprocessing: {_yes_no(plan.readiness)}",
            f"- blockers: {_join_preview(plan.blockers)}",
            f"- warnings: {_join_preview(plan.warnings)}",
            f"- expected confirmation phrase: {plan.expected_confirmation_phrase}",
            "- automatic execution: disabled",
            "- Supabase writes: disabled",
            "- fetch/reprocessing: not performed",
        ]
    )


class NoTermStatisticsRefreshRepository:
    """Delegate repository methods while hiding per-page term refresh from the indexer."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def __getattr__(self, name: str) -> Any:
        if name == "refresh_term_statistics":
            raise AttributeError(name)
        return getattr(self._repository, name)


def _backup_gate(
    *,
    backup_manifest: dict[str, Any] | None,
    scope: SourceScope,
    inventory: SourceInventory,
    current_plan: DocsReprocessingPlan,
    targets: tuple[TargetPreview, ...],
) -> ReprocessingBackupGate:
    if backup_manifest is None:
        return ReprocessingBackupGate(
            provided=False,
            valid=False,
            rollback_capable=False,
            drift_matches=False,
            generated_at="",
            baseline_fingerprint="",
            blockers=("fresh_post_archive_backup_required",),
            warnings=(),
        )
    verification = verify_manifest(
        backup_manifest,
        expected_service=scope.service_id,
        expected_source=scope.source_id,
        expected_workspace=inventory.workspace_id,
    )
    drift = compare_manifest_to_plan(backup_manifest, current_plan)
    blockers = list(verification.blocking_reasons)
    warnings = list(verification.warnings)
    if not verification.valid:
        blockers.append("fresh backup manifest is invalid")
    if not verification.rollback_capable:
        blockers.append("fresh backup must be rollback-capable")
    if not drift.matches:
        blockers.append("fresh_post_archive_backup_required")
        blockers.extend(drift.blocking_reasons)
    rows = backup_manifest.get("rows") if isinstance(backup_manifest.get("rows"), dict) else {}
    documents = rows.get("documents") if isinstance(rows.get("documents"), list) else []
    for target in targets:
        if not _manifest_has_document(documents, target.document):
            blockers.append("backup manifest does not contain exact target document")
    if _foreign_source_rows(documents, scope.source_id):
        blockers.append("backup manifest contains foreign source rows")
    return ReprocessingBackupGate(
        provided=True,
        valid=verification.valid,
        rollback_capable=verification.rollback_capable,
        drift_matches=drift.matches,
        generated_at=str(backup_manifest.get("generated_at") or ""),
        baseline_fingerprint=str(backup_manifest.get("baseline_fingerprint") or ""),
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _reviewed_keep_active_decision(artifact: dict[str, Any], document_key: str) -> ReviewedReprocessDecision | None:
    rows = [
        item for item in artifact.get("decisions", [])
        if isinstance(item, dict) and str(item.get("document_key") or "") == document_key
    ]
    if len(rows) != 1:
        return None
    row = rows[0]
    return ReviewedReprocessDecision(
        document_key=document_key,
        classification=str(row.get("classification") or ""),
        owner_decision=str(row.get("owner_decision") or ""),
        review_status=str(row.get("review_status") or ""),
        required_terms=_required_terms(row),
    )


def _required_terms(row: dict[str, Any]) -> tuple[str, ...]:
    for key in ("required_content_terms", "content_preservation_terms", "required_terms", "owner_rationale_terms"):
        value = row.get(key)
        if isinstance(value, list):
            return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _document_ref(row: dict[str, Any], inventory: SourceInventory, required_terms: tuple[str, ...]) -> ReprocessDocumentRef:
    document_id = str(row.get("id") or "")
    metadata = _metadata(row)
    return ReprocessDocumentRef(
        document_id=document_id,
        document_key=str(row.get("document_key") or ""),
        status=str(row.get("status") or ""),
        version=int(row.get("version") or 0),
        source_id=str(metadata.get("source_name") or ""),
        workspace_id=str(row.get("workspace_id") or ""),
        title=str(row.get("title") or ""),
        content_hash=str(row.get("content_hash") or ""),
        ingestion_signature=_ingestion_signature(row),
        cards_count=sum(1 for card in inventory.document_cards if str(card.get("document_id") or "") == document_id),
        sections_count=sum(1 for section in inventory.sections if str(section.get("document_id") or "") == document_id),
        chunks_count=sum(1 for chunk in inventory.chunks if str(chunk.get("document_id") or "") == document_id),
        latest_updated_at=str(row.get("updated_at") or ""),
        resolved_fetch_url=str(metadata.get("source_url") or metadata.get("canonical_url") or row.get("document_key") or ""),
        required_terms=required_terms,
    )


def _manifest_has_document(rows: list[Any], expected: ReprocessDocumentRef) -> bool:
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("id") or "") == expected.document_id
            and str(row.get("document_key") or "") == expected.document_key
            and str(row.get("workspace_id") or "") == expected.workspace_id
            and int(row.get("version") or 0) == expected.version
            and str(row.get("status") or "") == expected.status
            and str(row.get("content_hash") or "") == expected.content_hash
            and _ingestion_signature(row) == expected.ingestion_signature
        ):
            return True
    return False


def _foreign_source_rows(rows: list[Any], source_id: str) -> bool:
    for row in rows:
        if isinstance(row, dict) and _metadata(row).get("source_name") != source_id:
            return True
    return False


def _replace_target_drift_status(target: TargetPreview, status: str) -> TargetPreview:
    return TargetPreview(
        document=target.document,
        reviewed_decision=target.reviewed_decision,
        url_allowed=target.url_allowed,
        expected_cleaner=target.expected_cleaner,
        expected_future_version=target.expected_future_version,
        expected_replacement_scope=target.expected_replacement_scope,
        backup_present=target.backup_present,
        drift_status=status,
        blockers=target.blockers,
        warnings=target.warnings,
    )


def _source_from_plan(plan: ReviewedExternalDocsReprocessingPlan) -> ExternalDocSource:
    domains = tuple(_domain(target.document.document_key) for target in plan.targets if _domain(target.document.document_key))
    return ExternalDocSource(
        name=plan.source_id,
        source_kind="external_docs",
        allowed_domains=tuple(dict.fromkeys(domains)),
        start_urls=tuple(target.document.resolved_fetch_url for target in plan.targets),
        allow_patterns=(),
        deny_patterns=(),
        crawl_depth=0,
        max_pages=plan.target_count,
        refresh_days=14,
    )


def _source_from_scope(scope: SourceScope) -> ExternalDocSource:
    config = scope.source_config
    return ExternalDocSource(
        name=scope.source_id,
        source_kind=str(config.get("source_kind") or "external_docs"),
        allowed_domains=tuple(str(item) for item in config.get("allowed_domains", ()) or ()),
        start_urls=tuple(str(item) for item in config.get("start_urls", ()) or ()),
        allow_patterns=tuple(str(item) for item in config.get("allow_patterns", ()) or ()),
        deny_patterns=tuple(str(item) for item in config.get("deny_patterns", ()) or ()),
        crawl_depth=int(config.get("crawl_depth") or 0),
        max_pages=int(config.get("max_pages") or 1),
        refresh_days=int(config.get("refresh_days") or 14),
    )


def _contains_generator_boilerplate(text: str) -> bool:
    lowered = text.casefold()
    markers = (
        "for the complete documentation index",
        "this page is also available as markdown",
        "llms.txt",
        "generated from",
    )
    return any(marker in lowered for marker in markers)


def _target_result(base: TargetReprocessingResult, **updates: Any) -> TargetReprocessingResult:
    values = base.__dict__ | updates
    return TargetReprocessingResult(**values)


def _blocked_result(
    plan: ReviewedExternalDocsReprocessingPlan,
    reason: str,
    *,
    timestamp: str,
) -> ReprocessingExecutionResult:
    return ReprocessingExecutionResult(
        status="blocked",
        target_count=plan.target_count,
        targets=(),
        changed_keys=(),
        unchanged_keys=tuple(target.document.document_key for target in plan.targets),
        failed_keys=(),
        term_statistics_status="not run",
        partial_failure=False,
        rollback_required=False,
        automatic_retry=False,
        automatic_rollback=False,
        timestamp=timestamp,
        blockers=(reason, *plan.blockers),
    )


def _partial_result(
    plan: ReviewedExternalDocsReprocessingPlan,
    results: list[TargetReprocessingResult],
    changed: list[str],
    failed_key: str,
    *,
    timestamp: str,
) -> ReprocessingExecutionResult:
    return ReprocessingExecutionResult(
        status="partial_failure",
        target_count=plan.target_count,
        targets=tuple(results),
        changed_keys=tuple(changed),
        unchanged_keys=tuple(
            target.document.document_key
            for target in plan.targets
            if target.document.document_key not in changed and target.document.document_key != failed_key
        ),
        failed_keys=(failed_key,),
        term_statistics_status="not run",
        partial_failure=True,
        rollback_required=True,
        automatic_retry=False,
        automatic_rollback=False,
        timestamp=timestamp,
        blockers=("target indexing failed after writes may have started",),
    )


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _ingestion_signature(row: dict[str, Any]) -> str:
    ingestion = _metadata(row).get("ingestion")
    if isinstance(ingestion, dict):
        return str(ingestion.get("signature") or "")
    return ""


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").casefold()


def _looks_like_arbitrary_url(value: str, source: ExternalDocSource) -> bool:
    return is_url_allowed(source, value)


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300] or exc.__class__.__name__


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _join_preview(values: tuple[str, ...], *, limit: int = 4) -> str:
    if not values:
        return "none"
    return "; ".join(values[:limit]) + ("" if len(values) <= limit else f"; +{len(values) - limit} more")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
