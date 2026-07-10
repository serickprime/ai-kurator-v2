"""Reviewed one-document archive planning for external docs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Protocol

from app.docs_registry.reconciliation_plan import REVIEW_SCHEMA_VERSION, _payload_checksum
from app.docs_registry.reprocessing_plan import (
    DocsReprocessingPlan,
    DocsReprocessingPlanError,
    SourceInventory,
    SourceScope,
    compare_manifest_to_plan,
    verify_manifest,
)


class ReviewedExternalDocArchiveError(ValueError):
    """Raised for safe, expected reviewed archive validation errors."""


class ExternalDocArchiveRepository(Protocol):
    """Write subset needed for future exact one-document archive execution."""

    async def archive_external_document_exact(
        self,
        *,
        document_id: str,
        workspace_id: str,
        document_key: str,
        source_id: str,
        expected_version: int,
    ) -> int:
        """Archive exactly one active external-doc document row."""

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        """Refresh workspace term statistics."""


@dataclass(frozen=True)
class ArchiveDocumentRef:
    """Compact immutable reference to a document in an archive plan."""

    document_id: str
    document_key: str
    status: str
    version: int
    source_id: str
    workspace_id: str
    title: str
    content_hash: str
    ingestion_signature: str
    sections_count: int
    chunks_count: int
    latest_updated_at: str

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
            "sections_count": self.sections_count,
            "chunks_count": self.chunks_count,
            "latest_updated_at": self.latest_updated_at,
        }


@dataclass(frozen=True)
class ReviewedDecision:
    """Reviewed owner decision for one document key."""

    document_key: str
    classification: str
    owner_decision: str
    successor_key: str
    review_status: str

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "document_key": self.document_key,
            "classification": self.classification,
            "owner_decision": self.owner_decision,
            "successor_key": self.successor_key,
            "review_status": self.review_status,
        }


@dataclass(frozen=True)
class BackupArchiveGate:
    """Rollback-capable backup gate status."""

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
class ReviewedExternalDocArchivePlan:
    """Read-only plan for one reviewed external-doc archive operation."""

    mode: str
    service_id: str
    source_id: str
    workspace_id: str
    workspace_name: str
    generated_at: str
    target: ArchiveDocumentRef | None
    successor: ArchiveDocumentRef | None
    reviewed_decision: ReviewedDecision | None
    reviewed_artifact_checksum_valid: bool
    reviewed_artifact_fingerprint: str
    backup: BackupArchiveGate
    live_inventory_fingerprint: str
    expected_write_scope: dict[str, tuple[str, ...]]
    term_statistics_strategy: str
    retrieval_status_semantics: str
    readiness: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    automatic_archive_allowed: bool = False

    @property
    def expected_confirmation_phrase(self) -> str:
        """Return the exact phrase required for future execution."""
        target_id = self.target.document_id if self.target else "missing-target"
        return f"archive-reviewed-external-doc:{target_id}"

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "mode": self.mode,
            "service_id": self.service_id,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "generated_at": self.generated_at,
            "target": self.target.to_dict() if self.target else None,
            "successor": self.successor.to_dict() if self.successor else None,
            "reviewed_decision": self.reviewed_decision.to_dict() if self.reviewed_decision else None,
            "reviewed_artifact_checksum_valid": self.reviewed_artifact_checksum_valid,
            "reviewed_artifact_fingerprint": self.reviewed_artifact_fingerprint,
            "backup": self.backup.to_dict(),
            "live_inventory_fingerprint": self.live_inventory_fingerprint,
            "expected_write_scope": {
                key: list(values) for key, values in self.expected_write_scope.items()
            },
            "term_statistics_strategy": self.term_statistics_strategy,
            "retrieval_status_semantics": self.retrieval_status_semantics,
            "ready_for_archive": self.readiness,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "automatic_archive_allowed": self.automatic_archive_allowed,
            "supabase_writes": "disabled",
            "archive_execution": "not performed",
            "expected_confirmation_phrase": self.expected_confirmation_phrase,
        }


@dataclass(frozen=True)
class ArchiveExecutionResult:
    """Structured result for a future exact archive execution."""

    status: str
    target_document_id: str
    target_document_key: str
    rows_updated: int
    previous_status: str
    new_status: str
    successor_unchanged: bool
    term_statistics_status: str
    partial_failure: bool
    rollback_required: bool
    timestamp: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "status": self.status,
            "target_document_id": self.target_document_id,
            "target_document_key": self.target_document_key,
            "rows_updated": self.rows_updated,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "successor_unchanged": self.successor_unchanged,
            "term_statistics_status": self.term_statistics_status,
            "partial_failure": self.partial_failure,
            "rollback_required": self.rollback_required,
            "timestamp": self.timestamp,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def load_reviewed_artifact(path: Path | str) -> dict[str, Any]:
    """Load a local reviewed reconciliation artifact."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReviewedExternalDocArchiveError(f"invalid reviewed artifact JSON: {exc}") from exc
    except OSError as exc:
        raise ReviewedExternalDocArchiveError(f"could not read reviewed artifact: {exc.__class__.__name__}") from exc
    if not isinstance(value, dict):
        raise ReviewedExternalDocArchiveError("reviewed artifact must be a JSON object")
    return value


def build_reviewed_external_doc_archive_plan(
    *,
    scope: SourceScope,
    inventory: SourceInventory,
    current_plan: DocsReprocessingPlan,
    reviewed_artifact: dict[str, Any],
    backup_manifest: dict[str, Any] | None,
    document_id: str,
    generated_at: datetime | None = None,
) -> ReviewedExternalDocArchivePlan:
    """Build a read-only one-document archive plan."""
    blockers: list[str] = []
    warnings: list[str] = []
    active_docs = tuple(row for row in inventory.documents if row.get("status") == "active")
    target_rows = tuple(row for row in active_docs if str(row.get("id") or "") == document_id)
    if len(target_rows) != 1:
        blockers.append("target count must be exactly one")
    target_row = target_rows[0] if len(target_rows) == 1 else None

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

    decision = _reviewed_decision_for_target(reviewed_artifact, target_row)
    if decision is None:
        blockers.append("reviewed artifact does not contain exactly one decision for target")
    elif decision.owner_decision not in {"superseded_by", "archive_candidate"}:
        blockers.append(f"reviewed decision blocks archive: {decision.owner_decision}")

    target = _document_ref(target_row, inventory) if target_row else None
    if target is not None:
        if target.source_id != scope.source_id:
            blockers.append("target source_id does not match scope")
        if target.workspace_id != inventory.workspace_id:
            blockers.append("target workspace does not match scope")
        if target.status != "active":
            blockers.append("target must still be active")
        if target.version <= 0:
            blockers.append("target version must be positive")

    successor = None
    successor_key = decision.successor_key if decision else ""
    if decision and decision.owner_decision == "superseded_by":
        if not successor_key:
            blockers.append("superseded_by decision requires one successor")
        successor_rows = tuple(
            row for row in active_docs if str(row.get("document_key") or "") == successor_key
        )
        if len(successor_rows) != 1:
            blockers.append("successor must match exactly one active document")
        else:
            successor = _document_ref(successor_rows[0], inventory)
            if successor.source_id != scope.source_id:
                blockers.append("successor source_id does not match scope")
            if successor.workspace_id != inventory.workspace_id:
                blockers.append("successor workspace does not match target")
            if successor.status != "active":
                blockers.append("successor must be active")
    elif decision and decision.owner_decision == "archive_candidate" and successor_key:
        blockers.append("archive_candidate decision must not require a successor")

    if current_plan.duplicate_active_document_keys:
        blockers.append("duplicate active document keys must be zero")
    if current_plan.current_source_configuration_fingerprint != reviewed_artifact.get("source_config_fingerprint", current_plan.current_source_configuration_fingerprint):
        warnings.append("reviewed artifact does not carry current source_config_fingerprint; using live source scope")

    backup_gate = _backup_gate(
        backup_manifest=backup_manifest,
        scope=scope,
        inventory=inventory,
        current_plan=current_plan,
        target=target,
        successor=successor,
    )
    blockers.extend(backup_gate.blockers)
    warnings.extend(backup_gate.warnings)

    if target and successor and target.document_id == successor.document_id:
        blockers.append("target and successor must be different documents")
    if target and successor and target.document_key == successor.document_key:
        blockers.append("target and successor must have different document keys")

    return ReviewedExternalDocArchivePlan(
        mode="read-only",
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id=inventory.workspace_id,
        workspace_name=inventory.workspace_name,
        generated_at=_format_datetime(generated_at or datetime.now(timezone.utc)),
        target=target,
        successor=successor,
        reviewed_decision=decision,
        reviewed_artifact_checksum_valid=reviewed_valid,
        reviewed_artifact_fingerprint=str(reviewed_artifact.get("active_inventory_fingerprint") or ""),
        backup=backup_gate,
        live_inventory_fingerprint=current_plan.baseline_fingerprint,
        expected_write_scope=expected_archive_write_scope(),
        term_statistics_strategy=(
            "refresh workspace-wide term_statistics after a successful one-row archive; "
            "archive and refresh are not a single transaction"
        ),
        retrieval_status_semantics=(
            "document cards, sections, and chunks remain stored, but active retrieval filters parent documents by status=active"
        ),
        readiness=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        automatic_archive_allowed=False,
    )


async def execute_reviewed_external_doc_archive(
    *,
    plan: ReviewedExternalDocArchivePlan,
    repository: ExternalDocArchiveRepository,
    confirmation_phrase: str,
) -> ArchiveExecutionResult:
    """Execute a previously built exact archive plan after explicit confirmation."""
    now = _format_datetime(datetime.now(timezone.utc))
    if not plan.readiness or plan.target is None:
        return _blocked_execution(plan, "plan is not ready for archive", timestamp=now)
    if confirmation_phrase != plan.expected_confirmation_phrase:
        return _blocked_execution(plan, "explicit confirmation phrase mismatch", timestamp=now)

    target = plan.target
    rows_updated = await repository.archive_external_document_exact(
        document_id=target.document_id,
        workspace_id=target.workspace_id,
        document_key=target.document_key,
        source_id=target.source_id,
        expected_version=target.version,
    )
    if rows_updated != 1:
        return ArchiveExecutionResult(
            status="failed",
            target_document_id=target.document_id,
            target_document_key=target.document_key,
            rows_updated=rows_updated,
            previous_status=target.status,
            new_status=target.status,
            successor_unchanged=True,
            term_statistics_status="not run",
            partial_failure=False,
            rollback_required=False,
            timestamp=now,
            blockers=(f"expected one row updated, got {rows_updated}",),
        )
    try:
        refreshed = await repository.refresh_term_statistics(target.workspace_id)
    except Exception as exc:  # noqa: BLE001 - execution result must surface partial failure
        return ArchiveExecutionResult(
            status="partial_failure",
            target_document_id=target.document_id,
            target_document_key=target.document_key,
            rows_updated=rows_updated,
            previous_status=target.status,
            new_status="archived",
            successor_unchanged=True,
            term_statistics_status=f"failed: {exc.__class__.__name__}",
            partial_failure=True,
            rollback_required=True,
            timestamp=now,
            warnings=("archive succeeded but term_statistics refresh failed",),
        )
    return ArchiveExecutionResult(
        status="archived",
        target_document_id=target.document_id,
        target_document_key=target.document_key,
        rows_updated=rows_updated,
        previous_status=target.status,
        new_status="archived",
        successor_unchanged=True,
        term_statistics_status=f"updated: {refreshed}",
        partial_failure=False,
        rollback_required=False,
        timestamp=now,
    )


def expected_archive_write_scope() -> dict[str, tuple[str, ...]]:
    """Return the exact intended future archive write scope."""
    return {
        "documents": ("update exactly one external_docs row from active to archived",),
        "document_cards": ("no direct writes; rows remain for archived document history",),
        "sections": ("no direct writes; rows remain for archived document history",),
        "chunks": ("no direct writes; rows remain for archived document history",),
        "term_statistics": ("workspace-wide refresh after successful archive",),
        "evidence_logs": ("direct writes not expected",),
    }


def format_archive_plan_text(plan: ReviewedExternalDocArchivePlan) -> str:
    """Return compact human-readable archive plan text."""
    target = plan.target
    successor = plan.successor
    return "\n".join(
        [
            "Reviewed External Docs Archive Plan",
            "",
            "- mode: read-only",
            f"- service: {plan.service_id}",
            f"- source: {plan.source_id}",
            f"- workspace: {plan.workspace_name} ({plan.workspace_id})",
            f"- target count: {1 if target else 0}",
            f"- target id: {target.document_id if target else 'missing'}",
            f"- target key: {target.document_key if target else 'missing'}",
            f"- target status/version: {target.status if target else 'missing'}/{target.version if target else 'missing'}",
            f"- reviewed decision: {plan.reviewed_decision.owner_decision if plan.reviewed_decision else 'missing'}",
            f"- successor id: {successor.document_id if successor else 'none'}",
            f"- successor key: {successor.document_key if successor else 'none'}",
            f"- backup valid: {_yes_no(plan.backup.valid)}",
            f"- rollback capable: {_yes_no(plan.backup.rollback_capable)}",
            f"- live drift matches backup: {_yes_no(plan.backup.drift_matches)}",
            f"- ready for archive: {_yes_no(plan.readiness)}",
            f"- blockers: {_join_preview(plan.blockers)}",
            f"- warnings: {_join_preview(plan.warnings)}",
            f"- expected confirmation phrase: {plan.expected_confirmation_phrase}",
            "- automatic archive: disabled",
            "- Supabase writes: disabled",
            "- archive execution: not performed",
            "- crawl/activation/indexing/reindex: not performed",
        ]
    )


def _reviewed_decision_for_target(
    artifact: dict[str, Any],
    target_row: dict[str, Any] | None,
) -> ReviewedDecision | None:
    if target_row is None:
        return None
    target_key = str(target_row.get("document_key") or "")
    rows = [
        item for item in artifact.get("decisions", [])
        if isinstance(item, dict) and str(item.get("document_key") or "") == target_key
    ]
    if len(rows) != 1:
        return None
    row = rows[0]
    successor = str(row.get("owner_successor") or row.get("successor") or "").strip()
    candidates = row.get("successor_candidates")
    if not successor and isinstance(candidates, list) and len(candidates) == 1:
        successor = str(candidates[0] or "").strip()
    return ReviewedDecision(
        document_key=target_key,
        classification=str(row.get("classification") or ""),
        owner_decision=str(row.get("owner_decision") or ""),
        successor_key=successor,
        review_status=str(row.get("review_status") or ""),
    )


def _backup_gate(
    *,
    backup_manifest: dict[str, Any] | None,
    scope: SourceScope,
    inventory: SourceInventory,
    current_plan: DocsReprocessingPlan,
    target: ArchiveDocumentRef | None,
    successor: ArchiveDocumentRef | None,
) -> BackupArchiveGate:
    if backup_manifest is None:
        return BackupArchiveGate(
            provided=False,
            valid=False,
            rollback_capable=False,
            drift_matches=False,
            generated_at="",
            baseline_fingerprint="",
            blockers=("fresh_post_activation_backup_required",),
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
        blockers.extend(drift.blocking_reasons)

    rows = backup_manifest.get("rows") if isinstance(backup_manifest.get("rows"), dict) else {}
    documents = rows.get("documents") if isinstance(rows.get("documents"), list) else []
    if target and not _manifest_has_document(documents, target):
        blockers.append("backup manifest does not contain exact target document")
    if successor and not _manifest_has_document(documents, successor):
        blockers.append("backup manifest does not contain exact successor document")
    if not _backup_is_fresh_for_current_docs(backup_manifest, target=target, successor=successor):
        blockers.append("fresh_post_activation_backup_required")
    if _foreign_source_rows(documents, scope.source_id):
        blockers.append("backup manifest contains foreign source rows")
    return BackupArchiveGate(
        provided=True,
        valid=verification.valid,
        rollback_capable=verification.rollback_capable,
        drift_matches=drift.matches,
        generated_at=str(backup_manifest.get("generated_at") or ""),
        baseline_fingerprint=str(backup_manifest.get("baseline_fingerprint") or ""),
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _document_ref(row: dict[str, Any], inventory: SourceInventory) -> ArchiveDocumentRef:
    document_id = str(row.get("id") or "")
    return ArchiveDocumentRef(
        document_id=document_id,
        document_key=str(row.get("document_key") or ""),
        status=str(row.get("status") or ""),
        version=int(row.get("version") or 0),
        source_id=str(_metadata(row).get("source_name") or ""),
        workspace_id=str(row.get("workspace_id") or ""),
        title=str(row.get("title") or ""),
        content_hash=str(row.get("content_hash") or ""),
        ingestion_signature=_ingestion_signature(row),
        sections_count=sum(1 for section in inventory.sections if str(section.get("document_id") or "") == document_id),
        chunks_count=sum(1 for chunk in inventory.chunks if str(chunk.get("document_id") or "") == document_id),
        latest_updated_at=str(row.get("updated_at") or ""),
    )


def _manifest_has_document(rows: list[Any], expected: ArchiveDocumentRef) -> bool:
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


def _backup_is_fresh_for_current_docs(
    backup_manifest: dict[str, Any],
    *,
    target: ArchiveDocumentRef | None,
    successor: ArchiveDocumentRef | None,
) -> bool:
    generated = _parse_datetime(str(backup_manifest.get("generated_at") or ""))
    if generated is None:
        return False
    for doc in (target, successor):
        if doc is None or not doc.latest_updated_at:
            continue
        updated = _parse_datetime(doc.latest_updated_at)
        if updated is not None and generated < updated:
            return False
    return True


def _foreign_source_rows(rows: list[Any], source_id: str) -> bool:
    for row in rows:
        if isinstance(row, dict) and _metadata(row).get("source_name") != source_id:
            return True
    return False


def _blocked_execution(
    plan: ReviewedExternalDocArchivePlan,
    reason: str,
    *,
    timestamp: str,
) -> ArchiveExecutionResult:
    target_id = plan.target.document_id if plan.target else ""
    target_key = plan.target.document_key if plan.target else ""
    status = plan.target.status if plan.target else ""
    return ArchiveExecutionResult(
        status="blocked",
        target_document_id=target_id,
        target_document_key=target_key,
        rows_updated=0,
        previous_status=status,
        new_status=status,
        successor_unchanged=True,
        term_statistics_status="not run",
        partial_failure=False,
        rollback_required=False,
        timestamp=timestamp,
        blockers=(reason, *plan.blockers),
    )


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _ingestion_signature(row: dict[str, Any]) -> str:
    ingestion = _metadata(row).get("ingestion")
    if isinstance(ingestion, dict):
        return str(ingestion.get("signature") or "")
    return ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    clean = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _join_preview(values: tuple[str, ...], *, limit: int = 4) -> str:
    if not values:
        return "none"
    return "; ".join(values[:limit]) + ("" if len(values) <= limit else f"; +{len(values) - limit} more")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
