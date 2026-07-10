"""Reviewed canonical relocation planning for external docs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from app.docs_registry.reconciliation_plan import _payload_checksum
from app.docs_registry.reprocessing_plan import (
    DocsReprocessingPlan,
    SourceInventory,
    SourceScope,
    compare_manifest_to_plan,
    verify_manifest,
)
from app.external_docs.policy import is_url_allowed
from app.external_docs.types import CrawledPage, ExternalDocSource, ExternalDocsIndexResult, ExtractedPage


CANONICAL_RELOCATION_REVIEW_SCHEMA_VERSION = "docs-canonical-relocation-review-v1"


class ReviewedCanonicalRelocationError(ValueError):
    """Raised for safe, expected canonical relocation validation errors."""


class CanonicalRelocationFetcher(Protocol):
    """Fetch only the reviewed new canonical URL."""

    async def fetch_page(self, source: ExternalDocSource, url: str, *, depth: int = 0) -> CrawledPage | None:
        """Fetch one whitelisted page without source discovery."""


class ExternalDocsExtractorProtocol(Protocol):
    """Extractor used by canonical relocation."""

    def extract(self, page: CrawledPage) -> ExtractedPage:
        """Extract and clean one fetched page."""


class ExternalDocsIndexerProtocol(Protocol):
    """Indexer used to create the new canonical-key document."""

    async def index_page(
        self,
        page: ExtractedPage,
        source: ExternalDocSource,
        *,
        workspace: str = "team",
    ) -> ExternalDocsIndexResult:
        """Create the new active document for the reviewed canonical key."""


class CanonicalRelocationRepository(Protocol):
    """Repository writes needed after new canonical document creation."""

    async def archive_external_document_exact(
        self,
        *,
        document_id: str,
        workspace_id: str,
        document_key: str,
        source_id: str,
        expected_version: int,
    ) -> int:
        """Archive exactly one old active external-doc document row."""

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        """Refresh workspace term statistics."""


@dataclass(frozen=True)
class RelocationOldDocumentRef:
    """Immutable compact reference to the old canonical document."""

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

    def to_dict(self) -> dict[str, object]:
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
        }


@dataclass(frozen=True)
class RelocationNewCanonicalRef:
    """Reviewed new canonical target for a relocation."""

    document_key: str
    fetch_url: str
    expected_source_id: str
    expected_workspace_id: str
    expected_version: int
    url_allowed: bool
    key_differs_from_old: bool
    inventory_presence: str
    collision_status: str
    lineage_metadata: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "document_key": self.document_key,
            "fetch_url": self.fetch_url,
            "expected_source_id": self.expected_source_id,
            "expected_workspace_id": self.expected_workspace_id,
            "expected_version": self.expected_version,
            "url_allowed": self.url_allowed,
            "key_differs_from_old": self.key_differs_from_old,
            "inventory_presence": self.inventory_presence,
            "collision_status": self.collision_status,
            "lineage_metadata": dict(self.lineage_metadata),
        }


@dataclass(frozen=True)
class CanonicalRelocationReview:
    """Owner-reviewed canonical relocation decision."""

    schema_version: str
    owner_review_status: str
    owner_decision_source: str
    reviewed_at: str
    rationale: str
    decision: str
    materially_equivalent: bool
    relocation_confidence: str
    content_intent: str
    required_terms: tuple[str, ...]
    cleaner_expectations: tuple[str, ...]
    checksum_valid: bool
    fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "owner_review_status": self.owner_review_status,
            "owner_decision_source": self.owner_decision_source,
            "reviewed_at": self.reviewed_at,
            "rationale": self.rationale,
            "decision": self.decision,
            "materially_equivalent": self.materially_equivalent,
            "relocation_confidence": self.relocation_confidence,
            "content_intent": self.content_intent,
            "required_terms": list(self.required_terms),
            "cleaner_expectations": list(self.cleaner_expectations),
            "checksum_valid": self.checksum_valid,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RelocationBackupGate:
    """Fresh rollback-capable backup gate status."""

    provided: bool
    valid: bool
    rollback_capable: bool
    drift_matches: bool
    generated_at: str
    baseline_fingerprint: str
    old_target_present: bool
    new_key_absent: bool
    child_rows_present: bool
    embeddings_present: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "provided": self.provided,
            "valid": self.valid,
            "rollback_capable": self.rollback_capable,
            "drift_matches": self.drift_matches,
            "generated_at": self.generated_at,
            "baseline_fingerprint": self.baseline_fingerprint,
            "old_target_present": self.old_target_present,
            "new_key_absent": self.new_key_absent,
            "child_rows_present": self.child_rows_present,
            "embeddings_present": self.embeddings_present,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class CanonicalRelocationPlan:
    """Immutable read-only plan for one reviewed canonical relocation."""

    mode: str
    service_id: str
    source_id: str
    workspace_id: str
    workspace_name: str
    generated_at: str
    target_count: int
    old_document: RelocationOldDocumentRef | None
    new_canonical: RelocationNewCanonicalRef | None
    review: CanonicalRelocationReview | None
    backup: RelocationBackupGate
    live_inventory_fingerprint: str
    full_source_crawl: str
    arbitrary_urls: str
    fetch_requested: bool
    expected_write_scope: dict[str, tuple[str, ...]]
    term_statistics_strategy: str
    rollback_strategy: str
    transaction_boundary: str
    readiness: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    automatic_execution_allowed: bool = False

    @property
    def expected_confirmation_phrase(self) -> str:
        target_id = self.old_document.document_id if self.old_document else "missing-target"
        return f"relocate-reviewed-external-doc:{target_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "service_id": self.service_id,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "generated_at": self.generated_at,
            "target_count": self.target_count,
            "old_document": self.old_document.to_dict() if self.old_document else None,
            "new_canonical": self.new_canonical.to_dict() if self.new_canonical else None,
            "review": self.review.to_dict() if self.review else None,
            "backup": self.backup.to_dict(),
            "live_inventory_fingerprint": self.live_inventory_fingerprint,
            "full_source_crawl": self.full_source_crawl,
            "arbitrary_urls": self.arbitrary_urls,
            "fetch_requested": self.fetch_requested,
            "expected_write_scope": {
                key: list(values) for key, values in self.expected_write_scope.items()
            },
            "term_statistics_strategy": self.term_statistics_strategy,
            "rollback_strategy": self.rollback_strategy,
            "transaction_boundary": self.transaction_boundary,
            "ready_for_relocation": self.readiness,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "automatic_execution_allowed": self.automatic_execution_allowed,
            "supabase_writes": "disabled" if self.mode == "read-only" else "owner-confirmed only",
            "expected_confirmation_phrase": self.expected_confirmation_phrase,
        }


@dataclass(frozen=True)
class CanonicalRelocationExecutionResult:
    """Structured result for future canonical relocation execution."""

    status: str
    old_document_id: str
    old_document_key: str
    old_version: int
    old_status: str
    new_document_id: str
    new_document_key: str
    new_version: int
    new_status: str
    fetch_status: str
    final_url: str
    canonical_validation_status: str
    extraction_status: str
    cleaner_status: str
    useful_content_status: str
    new_document_creation_status: str
    old_archive_rows_updated: int
    lineage_metadata_status: str
    changed_keys: tuple[str, ...]
    unchanged_keys: tuple[str, ...]
    failed_stage: str
    term_statistics_status: str
    partial_failure: bool
    rollback_required: bool
    automatic_retry: bool
    automatic_rollback: bool
    timestamp: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "old_document_id": self.old_document_id,
            "old_document_key": self.old_document_key,
            "old_version": self.old_version,
            "old_status": self.old_status,
            "new_document_id": self.new_document_id,
            "new_document_key": self.new_document_key,
            "new_version": self.new_version,
            "new_status": self.new_status,
            "fetch_status": self.fetch_status,
            "final_url": self.final_url,
            "canonical_validation_status": self.canonical_validation_status,
            "extraction_status": self.extraction_status,
            "cleaner_status": self.cleaner_status,
            "useful_content_status": self.useful_content_status,
            "new_document_creation_status": self.new_document_creation_status,
            "old_archive_rows_updated": self.old_archive_rows_updated,
            "lineage_metadata_status": self.lineage_metadata_status,
            "changed_keys": list(self.changed_keys),
            "unchanged_keys": list(self.unchanged_keys),
            "failed_stage": self.failed_stage,
            "term_statistics_status": self.term_statistics_status,
            "partial_failure": self.partial_failure,
            "rollback_required": self.rollback_required,
            "automatic_retry": self.automatic_retry,
            "automatic_rollback": self.automatic_rollback,
            "timestamp": self.timestamp,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def load_canonical_relocation_review(path: Path | str) -> dict[str, Any]:
    """Load a local reviewed canonical relocation artifact."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReviewedCanonicalRelocationError(f"invalid relocation review JSON: {exc}") from exc
    except OSError as exc:
        raise ReviewedCanonicalRelocationError(f"could not read relocation review: {exc.__class__.__name__}") from exc
    if not isinstance(value, dict):
        raise ReviewedCanonicalRelocationError("relocation review must be a JSON object")
    return value


def write_canonical_relocation_review_atomic(
    artifact: dict[str, Any],
    output_path: Path,
    *,
    force: bool = False,
) -> None:
    """Write a local relocation review artifact with checksum and atomic replace."""
    path = output_path.resolve()
    if path.exists() and not force:
        raise ReviewedCanonicalRelocationError(f"review artifact already exists: {path}; use force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(artifact)
    payload["checksum"] = _payload_checksum(payload)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def build_reviewed_canonical_relocation_plan(
    *,
    scope: SourceScope,
    inventory: SourceInventory,
    current_plan: DocsReprocessingPlan,
    relocation_artifact: dict[str, Any],
    backup_manifest: dict[str, Any] | None,
    document_id: str,
    generated_at: datetime | None = None,
) -> CanonicalRelocationPlan:
    """Build a no-fetch, no-write plan for exactly one reviewed canonical relocation."""
    blockers: list[str] = []
    warnings: list[str] = []
    selected_id = document_id.strip()
    if not selected_id:
        blockers.append("target document ID is required")

    review_valid = _payload_checksum(relocation_artifact) == relocation_artifact.get("checksum")
    if not review_valid:
        blockers.append("relocation review checksum mismatch")
    if relocation_artifact.get("schema_version") != CANONICAL_RELOCATION_REVIEW_SCHEMA_VERSION:
        blockers.append("unsupported relocation review schema version")
    if relocation_artifact.get("service_id") != scope.service_id:
        blockers.append("relocation review service_id does not match scope")
    if relocation_artifact.get("source_id") != scope.source_id:
        blockers.append("relocation review source_id does not match scope")
    if relocation_artifact.get("workspace_id") != inventory.workspace_id:
        blockers.append("relocation review workspace_id does not match runtime")
    if relocation_artifact.get("owner_review_status") != "reviewed":
        blockers.append("owner_review_status must be reviewed")

    old_artifact = _as_dict(relocation_artifact.get("old_document"))
    new_artifact = _as_dict(relocation_artifact.get("new_canonical"))
    relationship = _as_dict(relocation_artifact.get("relationship"))
    safety = _as_dict(relocation_artifact.get("safety"))
    if relationship.get("decision") != "canonical_relocation":
        blockers.append("relocation decision must be canonical_relocation")
    if relationship.get("materially_equivalent") is not True:
        blockers.append("materially_equivalent must be confirmed")
    if safety.get("automatic_execution_allowed") is not False:
        blockers.append("automatic execution must be disabled")

    required_terms = _required_terms(relationship)
    if not required_terms:
        blockers.append("required useful-content terms are required")

    target_rows = tuple(row for row in inventory.documents if str(row.get("id") or "") == selected_id)
    if len(target_rows) != 1:
        blockers.append("target count must equal one")
    target_row = target_rows[0] if len(target_rows) == 1 else None
    old_document = _old_document_ref(target_row, inventory) if target_row else None
    if old_document:
        if old_document.document_id != str(old_artifact.get("document_id") or ""):
            blockers.append("old document ID does not match relocation review")
        if old_document.document_key != str(old_artifact.get("document_key") or ""):
            blockers.append("old document_key does not match relocation review")
        if old_document.status != str(old_artifact.get("status") or old_document.status):
            blockers.append("old status drift detected")
        if old_document.version != int(old_artifact.get("version") or 0):
            blockers.append("old version drift detected")
        if old_document.content_hash != str(old_artifact.get("content_hash") or ""):
            blockers.append("old content hash drift detected")
        if old_document.ingestion_signature != str(old_artifact.get("ingestion_signature") or ""):
            blockers.append("old ingestion signature drift detected")
        if old_document.source_id != scope.source_id:
            blockers.append("old source_id does not match scope")
        if old_document.workspace_id != inventory.workspace_id:
            blockers.append("old workspace does not match scope")
        if old_document.status != "active":
            blockers.append("old document must be active")

    new_key = str(new_artifact.get("document_key") or new_artifact.get("canonical_key") or "").strip()
    fetch_url = str(new_artifact.get("fetch_url") or "").strip()
    source = _source_from_scope(scope)
    url_allowed = bool(new_key and fetch_url and is_url_allowed(source, new_key) and is_url_allowed(source, fetch_url))
    if not url_allowed:
        blockers.append("new canonical URL is outside registered source scope")
    if old_document and new_key == old_document.document_key:
        blockers.append("new canonical key must differ from old key")
    expected_source = str(new_artifact.get("expected_source_id") or relocation_artifact.get("source_id") or "")
    expected_workspace = str(new_artifact.get("expected_workspace_id") or relocation_artifact.get("workspace_id") or "")
    if expected_source != scope.source_id:
        blockers.append("new canonical expected source does not match scope")
    if expected_workspace != inventory.workspace_id:
        blockers.append("new canonical expected workspace does not match scope")

    collision_status, collision_blockers = _collision_status(inventory, new_key, scope.source_id, inventory.workspace_id)
    blockers.extend(collision_blockers)
    expected_version = 1 if collision_status == "absent" else 0
    new_canonical = RelocationNewCanonicalRef(
        document_key=new_key,
        fetch_url=fetch_url,
        expected_source_id=expected_source,
        expected_workspace_id=expected_workspace,
        expected_version=expected_version,
        url_allowed=url_allowed,
        key_differs_from_old=bool(old_document and new_key != old_document.document_key),
        inventory_presence=collision_status,
        collision_status="none" if collision_status == "absent" else collision_status,
        lineage_metadata={
            "relocated_from_document_id": old_document.document_id if old_document else "",
            "relocated_from_document_key": old_document.document_key if old_document else "",
            "relocation_review_fingerprint": str(relocation_artifact.get("checksum") or ""),
        },
    )

    if current_plan.duplicate_active_document_keys:
        blockers.append("duplicate_active_key_detected")

    review = CanonicalRelocationReview(
        schema_version=str(relocation_artifact.get("schema_version") or ""),
        owner_review_status=str(relocation_artifact.get("owner_review_status") or ""),
        owner_decision_source=str(relocation_artifact.get("owner_decision_source") or ""),
        reviewed_at=str(relocation_artifact.get("reviewed_at") or ""),
        rationale=str(relocation_artifact.get("rationale") or ""),
        decision=str(relationship.get("decision") or ""),
        materially_equivalent=relationship.get("materially_equivalent") is True,
        relocation_confidence=str(relationship.get("relocation_confidence") or ""),
        content_intent=str(relationship.get("content_intent") or ""),
        required_terms=required_terms,
        cleaner_expectations=tuple(str(item).strip() for item in relationship.get("cleaner_expectations", ()) if str(item).strip())
        if isinstance(relationship.get("cleaner_expectations"), list)
        else (),
        checksum_valid=review_valid,
        fingerprint=str(relocation_artifact.get("checksum") or ""),
    )

    backup_gate = _backup_gate(
        backup_manifest=backup_manifest,
        scope=scope,
        inventory=inventory,
        current_plan=current_plan,
        old_document=old_document,
        new_key=new_key,
    )
    blockers.extend(backup_gate.blockers)
    warnings.extend(backup_gate.warnings)

    return CanonicalRelocationPlan(
        mode="read-only",
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id=inventory.workspace_id,
        workspace_name=inventory.workspace_name,
        generated_at=_format_datetime(generated_at or datetime.now(timezone.utc)),
        target_count=1 if old_document else 0,
        old_document=old_document,
        new_canonical=new_canonical,
        review=review,
        backup=backup_gate,
        live_inventory_fingerprint=current_plan.baseline_fingerprint,
        full_source_crawl="disabled",
        arbitrary_urls="disabled",
        fetch_requested=False,
        expected_write_scope=expected_relocation_write_scope(),
        term_statistics_strategy=(
            "refresh workspace-wide term_statistics once after new canonical document is active and old exact document is archived"
        ),
        rollback_strategy=(
            "no automatic rollback; partial relocation requires a separate owner-approved rollback phase"
        ),
        transaction_boundary=(
            "fetch/extract/clean/validate happen before writes; new document creation, old exact archive, "
            "and term_statistics refresh are not one source-wide transaction"
        ),
        readiness=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        automatic_execution_allowed=False,
    )


async def execute_reviewed_canonical_relocation(
    *,
    plan: CanonicalRelocationPlan,
    fetcher: CanonicalRelocationFetcher,
    extractor: ExternalDocsExtractorProtocol,
    indexer: ExternalDocsIndexerProtocol,
    repository: CanonicalRelocationRepository,
    confirmation_phrase: str,
    source: ExternalDocSource | None = None,
) -> CanonicalRelocationExecutionResult:
    """Execute one reviewed canonical relocation after explicit confirmation."""
    now = _format_datetime(datetime.now(timezone.utc))
    if not plan.readiness or plan.old_document is None or plan.new_canonical is None or plan.review is None:
        return _blocked_result(plan, "plan is not ready for relocation", timestamp=now)
    if confirmation_phrase != plan.expected_confirmation_phrase:
        return _blocked_result(plan, "explicit confirmation phrase mismatch", timestamp=now)

    source = source or _source_from_plan(plan)
    old = plan.old_document
    new = plan.new_canonical
    crawled: CrawledPage | None = None
    extracted: ExtractedPage | None = None
    try:
        crawled = await fetcher.fetch_page(source, new.fetch_url, depth=0)
        if crawled is None:
            raise ReviewedCanonicalRelocationError("fetch returned no page")
        if crawled.status_code >= 400:
            raise ReviewedCanonicalRelocationError(f"fetch failed with status {crawled.status_code}")
        extracted = extractor.extract(crawled)
        validate_relocated_canonical_page(plan=plan, extracted=extracted, final_url=crawled.url, source=source)
    except Exception as exc:  # noqa: BLE001 - structured pre-write failure
        return _execution_result(
            plan,
            status="blocked",
            fetch_status="failed" if crawled is None else "ok",
            final_url=crawled.url if crawled else "",
            canonical_validation_status="failed",
            extraction_status="failed" if extracted is None else "ok",
            cleaner_status="failed",
            useful_content_status="failed",
            failed_stage="pre_write_validation",
            blockers=("validation failed before writes; old document remains active", _safe_error(exc)),
            timestamp=now,
        )

    relocated_page = _with_lineage_metadata(extracted, plan)
    try:
        indexed = await indexer.index_page(relocated_page, source, workspace=plan.workspace_name)
    except Exception as exc:  # noqa: BLE001 - writes may have started inside indexer
        return _execution_result(
            plan,
            status="partial_failure",
            fetch_status="ok",
            final_url=crawled.url if crawled else "",
            canonical_validation_status="ok",
            extraction_status="ok",
            cleaner_status="ok",
            useful_content_status="ok",
            new_document_creation_status="failed",
            failed_stage="new_document_creation",
            partial_failure=True,
            rollback_required=True,
            blockers=(_safe_error(exc),),
            timestamp=now,
        )
    if indexed.error or indexed.document_key != new.document_key or indexed.version != new.expected_version:
        return _execution_result(
            plan,
            status="partial_failure",
            fetch_status="ok",
            final_url=crawled.url if crawled else "",
            canonical_validation_status="ok",
            extraction_status="ok",
            cleaner_status="ok",
            useful_content_status="ok",
            new_document_creation_status="failed",
            new_document_id=indexed.document_id,
            new_version=indexed.version,
            failed_stage="new_document_creation",
            changed_keys=(new.document_key,) if indexed.document_id else (),
            partial_failure=True,
            rollback_required=True,
            blockers=(indexed.error or "new canonical document result did not match reviewed key/version",),
            timestamp=now,
        )

    rows_updated = await repository.archive_external_document_exact(
        document_id=old.document_id,
        workspace_id=old.workspace_id,
        document_key=old.document_key,
        source_id=old.source_id,
        expected_version=old.version,
    )
    if rows_updated != 1:
        return _execution_result(
            plan,
            status="partial_failure",
            fetch_status="ok",
            final_url=crawled.url if crawled else "",
            canonical_validation_status="ok",
            extraction_status="ok",
            cleaner_status="ok",
            useful_content_status="ok",
            new_document_creation_status="ok",
            new_document_id=indexed.document_id,
            new_version=indexed.version,
            old_archive_rows_updated=rows_updated,
            changed_keys=(new.document_key,),
            unchanged_keys=(old.document_key,),
            failed_stage="old_exact_archive",
            partial_failure=True,
            rollback_required=True,
            blockers=(f"expected one old document row archived, got {rows_updated}",),
            timestamp=now,
        )

    try:
        refreshed = await repository.refresh_term_statistics(old.workspace_id)
    except Exception as exc:  # noqa: BLE001 - structured partial failure after writes
        return _execution_result(
            plan,
            status="partial_failure",
            fetch_status="ok",
            final_url=crawled.url if crawled else "",
            canonical_validation_status="ok",
            extraction_status="ok",
            cleaner_status="ok",
            useful_content_status="ok",
            new_document_creation_status="ok",
            new_document_id=indexed.document_id,
            new_version=indexed.version,
            old_archive_rows_updated=rows_updated,
            old_status="archived",
            changed_keys=(new.document_key, old.document_key),
            failed_stage="term_statistics_refresh",
            term_statistics_status=f"failed: {exc.__class__.__name__}",
            partial_failure=True,
            rollback_required=True,
            warnings=("new canonical document is active and old document is archived, but term_statistics refresh failed",),
            timestamp=now,
        )

    return _execution_result(
        plan,
        status="relocated",
        fetch_status="ok",
        final_url=crawled.url if crawled else "",
        canonical_validation_status="ok",
        extraction_status="ok",
        cleaner_status="ok",
        useful_content_status="ok",
        new_document_creation_status="ok",
        new_document_id=indexed.document_id,
        new_version=indexed.version,
        old_archive_rows_updated=rows_updated,
        old_status="archived",
        changed_keys=(new.document_key, old.document_key),
        term_statistics_status=f"updated: {refreshed}",
        timestamp=now,
    )


def validate_relocated_canonical_page(
    *,
    plan: CanonicalRelocationPlan,
    extracted: ExtractedPage,
    final_url: str,
    source: ExternalDocSource,
) -> dict[str, bool]:
    """Validate a fetched relocation target before any write."""
    if plan.new_canonical is None or plan.review is None:
        raise ReviewedCanonicalRelocationError("relocation plan is missing reviewed new canonical data")
    new = plan.new_canonical
    canonical = extracted.canonical_url or extracted.source_url
    if final_url and not is_url_allowed(source, final_url):
        raise ReviewedCanonicalRelocationError("final URL is outside registered source scope")
    if canonical != new.document_key:
        raise ReviewedCanonicalRelocationError("fetched page canonical key does not match reviewed relocation")
    if not is_url_allowed(source, canonical):
        raise ReviewedCanonicalRelocationError("fetched canonical URL is outside source scope")
    text = extracted.structured_text
    if _contains_generator_boilerplate(text):
        raise ReviewedCanonicalRelocationError("generic generator boilerplate remains after cleaning")
    missing_terms = [term for term in plan.review.required_terms if term.casefold() not in text.casefold()]
    if missing_terms:
        raise ReviewedCanonicalRelocationError("required useful terms missing after cleaning: " + ", ".join(missing_terms[:5]))
    return {"materially_equivalent": True, "useful_terms_preserved": True, "boilerplate_removed": True}


def expected_relocation_write_scope() -> dict[str, tuple[str, ...]]:
    """Return exact intended future write scope for one relocation."""
    return {
        "documents": (
            "insert one new active document row under the reviewed new canonical key",
            "archive exactly one old active external_docs row after new document validation",
        ),
        "document_cards": ("insert rows for the new canonical document only",),
        "sections": ("insert rows for the new canonical document only",),
        "chunks": ("insert rows for the new canonical document only",),
        "term_statistics": ("workspace-wide refresh once after full relocation success",),
        "evidence_logs": ("direct writes not expected",),
    }


def format_canonical_relocation_plan_text(plan: CanonicalRelocationPlan) -> str:
    """Return compact human-readable relocation plan text."""
    old = plan.old_document
    new = plan.new_canonical
    review = plan.review
    return "\n".join(
        [
            "Reviewed External Docs Canonical Relocation Plan",
            "",
            "- mode: read-only",
            f"- service: {plan.service_id}",
            f"- source: {plan.source_id}",
            f"- workspace: {plan.workspace_name} ({plan.workspace_id})",
            f"- target count: {plan.target_count}",
            f"- old document id: {old.document_id if old else 'missing'}",
            f"- old key: {old.document_key if old else 'missing'}",
            f"- old status/version: {old.status if old else 'missing'}/{old.version if old else 'missing'}",
            f"- reviewed new key: {new.document_key if new else 'missing'}",
            f"- reviewed fetch URL: {new.fetch_url if new else 'missing'}",
            f"- expected new version: {new.expected_version if new else 'missing'}",
            f"- collision status: {new.collision_status if new else 'missing'}",
            f"- owner review status: {review.owner_review_status if review else 'missing'}",
            f"- decision: {review.decision if review else 'missing'}",
            f"- backup valid: {_yes_no(plan.backup.valid)}",
            f"- rollback capable: {_yes_no(plan.backup.rollback_capable)}",
            f"- live drift matches backup: {_yes_no(plan.backup.drift_matches)}",
            f"- full source crawl: {plan.full_source_crawl}",
            f"- arbitrary URLs: {plan.arbitrary_urls}",
            f"- fetch requested: {_yes_no(plan.fetch_requested)}",
            f"- ready for relocation: {_yes_no(plan.readiness)}",
            f"- blockers: {_join_preview(plan.blockers)}",
            f"- warnings: {_join_preview(plan.warnings)}",
            f"- expected confirmation phrase: {plan.expected_confirmation_phrase}",
            "- automatic execution: disabled",
            "- Supabase writes: disabled",
            "- relocation execution: not performed",
        ]
    )


def _backup_gate(
    *,
    backup_manifest: dict[str, Any] | None,
    scope: SourceScope,
    inventory: SourceInventory,
    current_plan: DocsReprocessingPlan,
    old_document: RelocationOldDocumentRef | None,
    new_key: str,
) -> RelocationBackupGate:
    if backup_manifest is None:
        return RelocationBackupGate(
            provided=False,
            valid=False,
            rollback_capable=False,
            drift_matches=False,
            generated_at="",
            baseline_fingerprint="",
            old_target_present=False,
            new_key_absent=False,
            child_rows_present=False,
            embeddings_present=False,
            blockers=("fresh_backup_required",),
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
        blockers.append("inventory_drift_detected")
        blockers.extend(drift.blocking_reasons)
    rows = backup_manifest.get("rows") if isinstance(backup_manifest.get("rows"), dict) else {}
    documents = rows.get("documents") if isinstance(rows.get("documents"), list) else []
    old_present = bool(old_document and _manifest_has_document(documents, old_document))
    new_absent = not any(isinstance(row, dict) and str(row.get("document_key") or "") == new_key for row in documents)
    child_rows_present = bool(old_document and _manifest_has_children(rows, old_document.document_id))
    embeddings_present = bool(old_document and _manifest_has_embeddings(rows, old_document.document_id))
    if old_document and not old_present:
        blockers.append("backup manifest does not contain exact old target document")
    if not new_absent:
        blockers.append("backup manifest already contains new canonical key")
    if old_document and not child_rows_present:
        blockers.append("backup manifest is missing old target child rows")
    if old_document and not embeddings_present:
        blockers.append("backup manifest is missing old target embeddings")
    if _foreign_source_rows(documents, scope.source_id):
        blockers.append("backup manifest contains foreign source rows")
    return RelocationBackupGate(
        provided=True,
        valid=verification.valid,
        rollback_capable=verification.rollback_capable,
        drift_matches=drift.matches,
        generated_at=str(backup_manifest.get("generated_at") or ""),
        baseline_fingerprint=str(backup_manifest.get("baseline_fingerprint") or ""),
        old_target_present=old_present,
        new_key_absent=new_absent,
        child_rows_present=child_rows_present,
        embeddings_present=embeddings_present,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(warnings),
    )


def _old_document_ref(row: dict[str, Any], inventory: SourceInventory) -> RelocationOldDocumentRef:
    document_id = str(row.get("id") or "")
    return RelocationOldDocumentRef(
        document_id=document_id,
        document_key=str(row.get("document_key") or ""),
        status=str(row.get("status") or ""),
        version=int(row.get("version") or 0),
        source_id=str(_metadata(row).get("source_name") or ""),
        workspace_id=str(row.get("workspace_id") or ""),
        title=str(row.get("title") or ""),
        content_hash=str(row.get("content_hash") or ""),
        ingestion_signature=_ingestion_signature(row),
        cards_count=sum(1 for card in inventory.document_cards if str(card.get("document_id") or "") == document_id),
        sections_count=sum(1 for section in inventory.sections if str(section.get("document_id") or "") == document_id),
        chunks_count=sum(1 for chunk in inventory.chunks if str(chunk.get("document_id") or "") == document_id),
        latest_updated_at=str(row.get("updated_at") or ""),
    )


def _manifest_has_document(rows: list[Any], expected: RelocationOldDocumentRef) -> bool:
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


def _manifest_has_children(rows: dict[str, Any], document_id: str) -> bool:
    return all(
        any(isinstance(row, dict) and str(row.get("document_id") or "") == document_id for row in rows.get(table, []))
        for table in ("document_cards", "sections", "chunks")
    )


def _manifest_has_embeddings(rows: dict[str, Any], document_id: str) -> bool:
    cards = rows.get("document_cards") if isinstance(rows.get("document_cards"), list) else []
    sections = rows.get("sections") if isinstance(rows.get("sections"), list) else []
    chunks = rows.get("chunks") if isinstance(rows.get("chunks"), list) else []
    return (
        any(isinstance(row, dict) and str(row.get("document_id") or "") == document_id and "card_embedding" in row for row in cards)
        and any(isinstance(row, dict) and str(row.get("document_id") or "") == document_id and "section_embedding" in row for row in sections)
        and any(isinstance(row, dict) and str(row.get("document_id") or "") == document_id and "embedding" in row for row in chunks)
    )


def _foreign_source_rows(rows: list[Any], source_id: str) -> bool:
    for row in rows:
        if isinstance(row, dict) and _metadata(row).get("source_name") != source_id:
            return True
    return False


def _collision_status(
    inventory: SourceInventory,
    new_key: str,
    source_id: str,
    workspace_id: str,
) -> tuple[str, tuple[str, ...]]:
    if not new_key:
        return "missing", ("new canonical key is required",)
    blockers: list[str] = []
    rows = [row for row in inventory.documents if str(row.get("document_key") or "") == new_key]
    if not rows:
        return "absent", ()
    statuses = {str(row.get("status") or "") for row in rows}
    if any(str(row.get("workspace_id") or "") != workspace_id or _metadata(row).get("source_name") != source_id for row in rows):
        blockers.append("new_key_foreign_scope_collision")
        return "foreign_scope_collision", tuple(blockers)
    if "active" in statuses:
        blockers.append("new_key_active_collision")
        return "active_collision", tuple(blockers)
    if "archived" in statuses:
        blockers.append("new_key_archived_collision")
        return "archived_collision", tuple(blockers)
    blockers.append("new canonical key already exists in inventory")
    return "other_collision", tuple(blockers)


def _with_lineage_metadata(page: ExtractedPage, plan: CanonicalRelocationPlan) -> ExtractedPage:
    old = plan.old_document
    new = plan.new_canonical
    metadata = {
        **page.metadata,
        "canonical_relocation": {
            "decision": "canonical_relocation",
            "relocated_from_document_id": old.document_id if old else "",
            "relocated_from_document_key": old.document_key if old else "",
            "relocation_review_fingerprint": plan.review.fingerprint if plan.review else "",
        },
    }
    return replace(page, metadata=metadata, canonical_url=new.document_key if new else page.canonical_url)


def _execution_result(
    plan: CanonicalRelocationPlan,
    *,
    status: str,
    timestamp: str,
    fetch_status: str = "not run",
    final_url: str = "",
    canonical_validation_status: str = "not run",
    extraction_status: str = "not run",
    cleaner_status: str = "not run",
    useful_content_status: str = "not run",
    new_document_creation_status: str = "not run",
    new_document_id: str = "",
    new_version: int = 0,
    old_archive_rows_updated: int = 0,
    old_status: str | None = None,
    changed_keys: tuple[str, ...] = (),
    unchanged_keys: tuple[str, ...] | None = None,
    failed_stage: str = "",
    term_statistics_status: str = "not run",
    partial_failure: bool = False,
    rollback_required: bool = False,
    blockers: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> CanonicalRelocationExecutionResult:
    old = plan.old_document
    new = plan.new_canonical
    default_unchanged = tuple(
        key for key in ((old.document_key if old else ""), (new.document_key if new else "")) if key and key not in changed_keys
    )
    return CanonicalRelocationExecutionResult(
        status=status,
        old_document_id=old.document_id if old else "",
        old_document_key=old.document_key if old else "",
        old_version=old.version if old else 0,
        old_status=old_status if old_status is not None else (old.status if old else ""),
        new_document_id=new_document_id,
        new_document_key=new.document_key if new else "",
        new_version=new_version,
        new_status="active" if new_document_id and status in {"relocated", "partial_failure"} else "",
        fetch_status=fetch_status,
        final_url=final_url,
        canonical_validation_status=canonical_validation_status,
        extraction_status=extraction_status,
        cleaner_status=cleaner_status,
        useful_content_status=useful_content_status,
        new_document_creation_status=new_document_creation_status,
        old_archive_rows_updated=old_archive_rows_updated,
        lineage_metadata_status="recorded" if new_document_id else "not recorded",
        changed_keys=changed_keys,
        unchanged_keys=default_unchanged if unchanged_keys is None else unchanged_keys,
        failed_stage=failed_stage,
        term_statistics_status=term_statistics_status,
        partial_failure=partial_failure,
        rollback_required=rollback_required,
        automatic_retry=False,
        automatic_rollback=False,
        timestamp=timestamp,
        blockers=blockers,
        warnings=warnings,
    )


def _blocked_result(plan: CanonicalRelocationPlan, reason: str, *, timestamp: str) -> CanonicalRelocationExecutionResult:
    return _execution_result(
        plan,
        status="blocked",
        timestamp=timestamp,
        failed_stage="preconditions",
        blockers=(reason, *plan.blockers),
    )


def _required_terms(relationship: dict[str, Any]) -> tuple[str, ...]:
    for key in ("required_useful_content_terms", "required_content_terms", "required_terms"):
        value = relationship.get(key)
        if isinstance(value, list):
            return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _source_from_scope(scope: SourceScope) -> ExternalDocSource:
    config = scope.source_config
    return ExternalDocSource(
        name=scope.source_id,
        source_kind=str(config.get("source_kind") or "external_docs"),
        allowed_domains=tuple(str(item) for item in config.get("allowed_domains", ()) or ()),
        start_urls=tuple(str(item) for item in config.get("start_urls", ()) or ()),
        allow_patterns=tuple(str(item) for item in config.get("allow_patterns", ()) or ()),
        deny_patterns=tuple(str(item) for item in config.get("deny_patterns", ()) or ()),
        crawl_depth=0,
        max_pages=1,
        refresh_days=int(config.get("refresh_days") or 14),
    )


def _source_from_plan(plan: CanonicalRelocationPlan) -> ExternalDocSource:
    new = plan.new_canonical
    domain = _domain(new.fetch_url if new else "")
    return ExternalDocSource(
        name=plan.source_id,
        source_kind="external_docs",
        allowed_domains=(domain,) if domain else (),
        start_urls=(new.fetch_url,) if new else (),
        allow_patterns=(),
        deny_patterns=(),
        crawl_depth=0,
        max_pages=1,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _ingestion_signature(row: dict[str, Any]) -> str:
    ingestion = _metadata(row).get("ingestion")
    if isinstance(ingestion, dict):
        return str(ingestion.get("signature") or "")
    return ""


def _contains_generator_boilerplate(text: str) -> bool:
    lowered = text.casefold()
    markers = (
        "for the complete documentation index",
        "this page is also available as markdown",
        "llms.txt",
        "generated from",
    )
    return any(marker in lowered for marker in markers)


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300] or exc.__class__.__name__


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").casefold()


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _join_preview(values: tuple[str, ...], *, limit: int = 4) -> str:
    if not values:
        return "none"
    return "; ".join(values[:limit]) + ("" if len(values) <= limit else f"; +{len(values) - limit} more")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
