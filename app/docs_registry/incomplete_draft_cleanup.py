"""Preview-first cleanup tooling for incomplete external-doc draft rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Awaitable, Callable, Protocol

from app.docs_registry.reprocessing_plan import (
    DocsReprocessingPlan,
    SourceInventory,
    SourceScope,
    build_reprocessing_plan,
    compare_manifest_to_plan,
    verify_manifest,
)


class IncompleteDraftCleanupError(ValueError):
    """Raised for safe, expected incomplete-draft cleanup validation errors."""


class IncompleteDraftCleanupRepository(Protocol):
    """Write subset needed for future exact incomplete-draft cleanup execution."""

    async def delete_incomplete_external_document_draft_exact(
        self,
        *,
        document_id: str,
        workspace_id: str,
        document_key: str,
        source_id: str,
        expected_version: int,
        expected_content_hash: str,
        expected_ingestion_signature: str,
    ) -> int:
        """Delete exactly one verified draft external-doc document row."""


@dataclass(frozen=True)
class ChildCounts:
    """Child row and embedding counts for one document."""

    cards: int
    sections: int
    chunks: int
    card_embeddings: int
    section_embeddings: int
    chunk_embeddings: int

    def to_dict(self) -> dict[str, int]:
        return {
            "cards": self.cards,
            "sections": self.sections,
            "chunks": self.chunks,
            "card_embeddings": self.card_embeddings,
            "section_embeddings": self.section_embeddings,
            "chunk_embeddings": self.chunk_embeddings,
        }


@dataclass(frozen=True)
class CleanupDocumentRef:
    """Compact immutable reference to a cleanup-related document."""

    document_id: str
    document_key: str
    status: str
    version: int
    source_id: str
    workspace_id: str
    content_hash: str
    ingestion_signature: str
    created_at: str
    updated_at: str
    children: ChildCounts

    def to_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "document_key": self.document_key,
            "status": self.status,
            "version": self.version,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "content_hash": self.content_hash,
            "ingestion_signature": self.ingestion_signature,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "children": self.children.to_dict(),
        }


@dataclass(frozen=True)
class CleanupDelta:
    """Allowed baseline-to-live delta for one incomplete draft subtree."""

    documents: int
    document_cards: int
    sections: int
    chunks: int
    card_embeddings: int
    section_embeddings: int
    chunk_embeddings: int
    broader_drift: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "documents": self.documents,
            "document_cards": self.document_cards,
            "sections": self.sections,
            "chunks": self.chunks,
            "card_embeddings": self.card_embeddings,
            "section_embeddings": self.section_embeddings,
            "chunk_embeddings": self.chunk_embeddings,
            "broader_drift": self.broader_drift,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class CleanupBackupGate:
    """Rollback-capable backup gate for incomplete-draft cleanup."""

    provided: bool
    valid: bool
    rollback_capable: bool
    generated_at: str
    baseline_fingerprint: str
    allowed_delta_matches: bool
    target_absent_from_backup: bool
    protected_active_present: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "provided": self.provided,
            "valid": self.valid,
            "rollback_capable": self.rollback_capable,
            "generated_at": self.generated_at,
            "baseline_fingerprint": self.baseline_fingerprint,
            "allowed_delta_matches": self.allowed_delta_matches,
            "target_absent_from_backup": self.target_absent_from_backup,
            "protected_active_present": self.protected_active_present,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class IncompleteDraftCleanupPlan:
    """Read-only plan for cleaning exactly one incomplete external-doc draft."""

    mode: str
    service_id: str
    source_id: str
    workspace_id: str
    workspace_name: str
    generated_at: str
    target_count: int
    target: CleanupDocumentRef | None
    protected_active: CleanupDocumentRef | None
    backup: CleanupBackupGate
    allowed_delta: CleanupDelta | None
    live_inventory_fingerprint: str
    baseline_counts: dict[str, int]
    live_counts: dict[str, int]
    expected_post_cleanup_counts: dict[str, int]
    target_state_fingerprint: str
    cascade_plan: tuple[str, ...]
    term_statistics_refresh: bool
    fetch_requested: bool
    reprocessing_requested: bool
    execution_requested: bool
    writes_enabled: bool
    rollback_required: bool
    readiness: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    automatic_retry: bool = False
    automatic_rollback: bool = False

    @property
    def expected_confirmation_phrase(self) -> str:
        target_id = self.target.document_id if self.target else "missing-target"
        return f"cleanup-incomplete-external-doc-draft:{self.source_id}:{target_id}:{self.target_state_fingerprint[:12]}"

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "service_id": self.service_id,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "generated_at": self.generated_at,
            "target_count": self.target_count,
            "target": self.target.to_dict() if self.target else None,
            "protected_active": self.protected_active.to_dict() if self.protected_active else None,
            "backup": self.backup.to_dict(),
            "allowed_delta": self.allowed_delta.to_dict() if self.allowed_delta else None,
            "live_inventory_fingerprint": self.live_inventory_fingerprint,
            "baseline_counts": dict(self.baseline_counts),
            "live_counts": dict(self.live_counts),
            "expected_post_cleanup_counts": dict(self.expected_post_cleanup_counts),
            "target_state_fingerprint": self.target_state_fingerprint,
            "cascade_plan": list(self.cascade_plan),
            "term_statistics_refresh": self.term_statistics_refresh,
            "fetch_requested": self.fetch_requested,
            "reprocessing_requested": self.reprocessing_requested,
            "execution_requested": self.execution_requested,
            "writes_enabled": self.writes_enabled,
            "rollback_required": self.rollback_required,
            "ready_for_cleanup": self.readiness,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "automatic_retry": self.automatic_retry,
            "automatic_rollback": self.automatic_rollback,
            "expected_confirmation_phrase": self.expected_confirmation_phrase,
        }


@dataclass(frozen=True)
class IncompleteDraftCleanupResult:
    """Structured result for future exact incomplete-draft cleanup execution."""

    status: str
    target_document_id: str
    target_document_key: str
    rows_deleted: int
    target_absent: bool
    target_children_absent: bool
    protected_active_unchanged: bool
    source_matches_baseline: bool
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
            "target_document_id": self.target_document_id,
            "target_document_key": self.target_document_key,
            "rows_deleted": self.rows_deleted,
            "target_absent": self.target_absent,
            "target_children_absent": self.target_children_absent,
            "protected_active_unchanged": self.protected_active_unchanged,
            "source_matches_baseline": self.source_matches_baseline,
            "term_statistics_status": self.term_statistics_status,
            "partial_failure": self.partial_failure,
            "rollback_required": self.rollback_required,
            "automatic_retry": self.automatic_retry,
            "automatic_rollback": self.automatic_rollback,
            "timestamp": self.timestamp,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def build_incomplete_draft_cleanup_plan(
    *,
    scope: SourceScope,
    inventory: SourceInventory,
    current_plan: DocsReprocessingPlan,
    backup_manifest: dict[str, Any] | None,
    document_ids: tuple[str, ...],
    generated_at: datetime | None = None,
) -> IncompleteDraftCleanupPlan:
    """Build a preview/default plan for one exact incomplete draft cleanup."""
    blockers: list[str] = []
    warnings: list[str] = []
    selected_ids = tuple(item.strip() for item in document_ids if item and item.strip())
    if len(selected_ids) != 1:
        blockers.append("target count must be exactly one")
    target_id = selected_ids[0] if len(selected_ids) == 1 else ""
    if len(selected_ids) != len(set(selected_ids)):
        blockers.append("duplicate target document IDs are not allowed")

    target_row = _single_row(
        [row for row in inventory.documents if str(row.get("id") or "") == target_id]
    )
    if target_id and target_row is None:
        blockers.append("target document ID not found")
    target = _document_ref(target_row, inventory) if target_row else None
    if target is not None:
        blockers.extend(_target_blockers(target, scope, inventory))

    protected_active = _protected_active_ref(target, inventory, blockers)
    baseline_rows = _manifest_rows(backup_manifest)
    baseline_counts = _manifest_counts(backup_manifest)
    live_counts = _inventory_counts(current_plan)
    expected_post_counts = dict(baseline_counts)

    delta: CleanupDelta | None = None
    if target is not None:
        delta = _allowed_delta(
            backup_rows=baseline_rows,
            inventory=inventory,
            target=target,
        )
        blockers.extend(delta.blockers)

    backup_gate = _backup_gate(
        backup_manifest=backup_manifest,
        scope=scope,
        inventory=inventory,
        target=target,
        protected_active=protected_active,
        delta=delta,
    )
    blockers.extend(backup_gate.blockers)
    warnings.extend(backup_gate.warnings)

    if current_plan.duplicate_active_document_keys:
        blockers.append("duplicate_active_key_detected")

    return IncompleteDraftCleanupPlan(
        mode="read-only",
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id=inventory.workspace_id,
        workspace_name=inventory.workspace_name,
        generated_at=_format_datetime(generated_at or datetime.now(timezone.utc)),
        target_count=len(selected_ids),
        target=target,
        protected_active=protected_active,
        backup=backup_gate,
        allowed_delta=delta,
        live_inventory_fingerprint=current_plan.baseline_fingerprint,
        baseline_counts=baseline_counts,
        live_counts=live_counts,
        expected_post_cleanup_counts=expected_post_counts,
        target_state_fingerprint=_target_state_fingerprint(target, delta),
        cascade_plan=(
            "delete exactly one verified draft documents row",
            "database foreign-key cascade removes target document card, sections, and chunks",
            "post-delete read-only verification must prove target subtree absent and protected active unchanged",
        ),
        term_statistics_refresh=False,
        fetch_requested=False,
        reprocessing_requested=False,
        execution_requested=False,
        writes_enabled=False,
        rollback_required=False,
        readiness=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


async def execute_incomplete_draft_cleanup(
    *,
    plan: IncompleteDraftCleanupPlan,
    repository: IncompleteDraftCleanupRepository,
    load_post_cleanup_inventory: Callable[[], Awaitable[SourceInventory]],
    scope: SourceScope,
    backup_manifest: dict[str, Any],
    confirmation_phrase: str,
) -> IncompleteDraftCleanupResult:
    """Execute exact draft cleanup after explicit owner confirmation."""
    now = _format_datetime(datetime.now(timezone.utc))
    if not plan.readiness or plan.target is None:
        return _blocked_result(plan, "plan is not ready for cleanup", timestamp=now)
    if confirmation_phrase != plan.expected_confirmation_phrase:
        return _blocked_result(plan, "explicit confirmation phrase mismatch", timestamp=now)

    try:
        rows_deleted = await repository.delete_incomplete_external_document_draft_exact(
            document_id=plan.target.document_id,
            workspace_id=plan.workspace_id,
            document_key=plan.target.document_key,
            source_id=plan.source_id,
            expected_version=plan.target.version,
            expected_content_hash=plan.target.content_hash,
            expected_ingestion_signature=plan.target.ingestion_signature,
        )
    except Exception as exc:  # noqa: BLE001 - delete outcome may be uncertain
        return IncompleteDraftCleanupResult(
            status="partial_failure",
            target_document_id=plan.target.document_id,
            target_document_key=plan.target.document_key,
            rows_deleted=-1,
            target_absent=False,
            target_children_absent=False,
            protected_active_unchanged=False,
            source_matches_baseline=False,
            term_statistics_status="not run",
            partial_failure=True,
            rollback_required=True,
            automatic_retry=False,
            automatic_rollback=False,
            timestamp=now,
            blockers=(f"delete attempt failed: {exc.__class__.__name__}",),
        )
    if rows_deleted != 1:
        return IncompleteDraftCleanupResult(
            status="failed",
            target_document_id=plan.target.document_id,
            target_document_key=plan.target.document_key,
            rows_deleted=rows_deleted,
            target_absent=False,
            target_children_absent=False,
            protected_active_unchanged=False,
            source_matches_baseline=False,
            term_statistics_status="not run",
            partial_failure=False,
            rollback_required=False,
            automatic_retry=False,
            automatic_rollback=False,
            timestamp=now,
            blockers=(f"expected exactly one draft document row deleted, got {rows_deleted}",),
        )

    post_inventory = await load_post_cleanup_inventory()
    post_plan = build_reprocessing_plan(scope=scope, inventory=post_inventory)
    drift = compare_manifest_to_plan(backup_manifest, post_plan)
    target_absent = not any(row.get("id") == plan.target.document_id for row in post_inventory.documents)
    target_children_absent = _target_child_count(post_inventory, plan.target.document_id) == 0
    protected_unchanged = _protected_active_matches_backup(backup_manifest, post_inventory, plan.protected_active)
    ok = target_absent and target_children_absent and protected_unchanged and drift.matches
    return IncompleteDraftCleanupResult(
        status="cleaned" if ok else "partial_failure",
        target_document_id=plan.target.document_id,
        target_document_key=plan.target.document_key,
        rows_deleted=rows_deleted,
        target_absent=target_absent,
        target_children_absent=target_children_absent,
        protected_active_unchanged=protected_unchanged,
        source_matches_baseline=drift.matches,
        term_statistics_status="not run",
        partial_failure=not ok,
        rollback_required=not ok,
        automatic_retry=False,
        automatic_rollback=False,
        timestamp=now,
        blockers=tuple(drift.blocking_reasons) if not ok else (),
        warnings=(),
    )


def format_incomplete_draft_cleanup_plan_text(plan: IncompleteDraftCleanupPlan) -> str:
    """Return compact human-readable cleanup plan text."""
    target = plan.target
    protected = plan.protected_active
    return "\n".join(
        [
            "Incomplete External Doc Draft Cleanup Plan",
            "",
            "- mode: read-only",
            f"- service: {plan.service_id}",
            f"- source: {plan.source_id}",
            f"- workspace: {plan.workspace_name} ({plan.workspace_id})",
            f"- target count: {plan.target_count}",
            f"- target: {target.document_id if target else 'none'}",
            f"- target key: {target.document_key if target else 'none'}",
            f"- target status/version: {target.status if target else 'none'}/{target.version if target else 'none'}",
            f"- target children: {_counts_text(target.children) if target else 'none'}",
            f"- protected active: {protected.document_id if protected else 'none'}",
            f"- protected status/version: {protected.status if protected else 'none'}/{protected.version if protected else 'none'}",
            f"- backup valid: {_yes_no(plan.backup.valid)}",
            f"- rollback capable: {_yes_no(plan.backup.rollback_capable)}",
            f"- allowed draft-only delta: {_yes_no(plan.backup.allowed_delta_matches)}",
            f"- baseline counts: {plan.baseline_counts}",
            f"- live counts: {plan.live_counts}",
            f"- expected post-cleanup counts: {plan.expected_post_cleanup_counts}",
            "- cascade plan: delete exact draft document row; verify child subtree absent",
            f"- term_statistics refresh: {_yes_no(plan.term_statistics_refresh)}",
            f"- fetch: {_yes_no(plan.fetch_requested)}",
            f"- reprocessing: {_yes_no(plan.reprocessing_requested)}",
            f"- ready for cleanup: {_yes_no(plan.readiness)}",
            f"- blockers: {_join(plan.blockers)}",
            f"- warnings: {_join(plan.warnings)}",
            f"- expected confirmation phrase: {plan.expected_confirmation_phrase}",
            "- execution requested: false",
            "- Supabase writes: disabled",
        ]
    )


def _target_blockers(target: CleanupDocumentRef, scope: SourceScope, inventory: SourceInventory) -> list[str]:
    blockers: list[str] = []
    if target.status != "draft":
        blockers.append("target_status_must_be_draft")
    if target.source_id != scope.source_id:
        blockers.append("target_source_id_mismatch")
    if target.workspace_id != inventory.workspace_id:
        blockers.append("target_workspace_mismatch")
    if not target.document_key:
        blockers.append("target_document_key_missing")
    if target.status == "active":
        blockers.append("target_must_not_be_active")
    same_id_count = sum(1 for row in inventory.documents if str(row.get("id") or "") == target.document_id)
    if same_id_count != 1:
        blockers.append("target_id_not_unique")
    return blockers


def _protected_active_ref(
    target: CleanupDocumentRef | None,
    inventory: SourceInventory,
    blockers: list[str],
) -> CleanupDocumentRef | None:
    if target is None:
        return None
    rows = [
        row
        for row in inventory.documents
        if row.get("document_key") == target.document_key and row.get("status") == "active"
    ]
    if len(rows) != 1:
        blockers.append("protected_active_document_not_unique")
        return None
    protected = _document_ref(rows[0], inventory)
    if protected is None:
        blockers.append("protected_active_document_not_unique")
        return None
    if protected.version >= target.version:
        blockers.append("target_is_not_newer_than_protected_active")
    return protected


def _backup_gate(
    *,
    backup_manifest: dict[str, Any] | None,
    scope: SourceScope,
    inventory: SourceInventory,
    target: CleanupDocumentRef | None,
    protected_active: CleanupDocumentRef | None,
    delta: CleanupDelta | None,
) -> CleanupBackupGate:
    if backup_manifest is None:
        return CleanupBackupGate(
            provided=False,
            valid=False,
            rollback_capable=False,
            generated_at="",
            baseline_fingerprint="",
            allowed_delta_matches=False,
            target_absent_from_backup=False,
            protected_active_present=False,
            blockers=("backup_required",),
            warnings=(),
        )
    verification = verify_manifest(
        backup_manifest,
        expected_service=scope.service_id,
        expected_source=scope.source_id,
        expected_workspace=inventory.workspace_id,
    )
    blockers = list(verification.blocking_reasons)
    warnings = list(verification.warnings)
    rows = _manifest_rows(backup_manifest)
    target_absent = bool(target) and not any(row.get("id") == target.document_id for row in rows.get("documents", ()))
    if target and not target_absent:
        blockers.append("backup_already_contains_target_id")
    protected_present = _protected_active_in_backup(rows, protected_active)
    if protected_active and not protected_present:
        blockers.append("protected_active_document_changed")
    if delta and delta.broader_drift:
        blockers.append("unexpected_broader_drift")
    return CleanupBackupGate(
        provided=True,
        valid=verification.valid,
        rollback_capable=verification.rollback_capable,
        generated_at=str(backup_manifest.get("generated_at") or ""),
        baseline_fingerprint=str(backup_manifest.get("baseline_fingerprint") or ""),
        allowed_delta_matches=bool(delta and not delta.broader_drift),
        target_absent_from_backup=target_absent,
        protected_active_present=protected_present,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(warnings),
    )


def _allowed_delta(
    *,
    backup_rows: dict[str, tuple[dict[str, Any], ...]],
    inventory: SourceInventory,
    target: CleanupDocumentRef,
) -> CleanupDelta:
    live_rows = {
        "documents": tuple(inventory.documents),
        "document_cards": tuple(inventory.document_cards),
        "sections": tuple(inventory.sections),
        "chunks": tuple(inventory.chunks),
    }
    blockers: list[str] = []
    added_by_table: dict[str, list[dict[str, Any]]] = {}
    for table, rows in live_rows.items():
        before = {str(row.get("id") or ""): row for row in backup_rows.get(table, ()) if row.get("id")}
        after = {str(row.get("id") or ""): row for row in rows if row.get("id")}
        added_ids = set(after) - set(before)
        removed_ids = set(before) - set(after)
        modified = [
            row_id
            for row_id in sorted(set(before) & set(after))
            if _normalized_row(before[row_id]) != _normalized_row(after[row_id], reference=before[row_id])
        ]
        if removed_ids:
            blockers.append(f"{table}_removed_rows_detected")
        if modified:
            blockers.append(f"{table}_modified_rows_detected")
        allowed = []
        for row_id in sorted(added_ids):
            row = after[row_id]
            if _row_belongs_to_target(table, row, target.document_id):
                allowed.append(row)
            else:
                blockers.append(f"{table}_unexpected_added_rows_detected")
        added_by_table[table] = allowed
    child_counts = _children_for_doc(
        inventory.document_cards,
        inventory.sections,
        inventory.chunks,
        target.document_id,
    )
    if len(added_by_table.get("documents", ())) != 1:
        blockers.append("allowed_delta_requires_one_draft_document")
    if len(added_by_table.get("document_cards", ())) != child_counts.cards:
        blockers.append("allowed_delta_card_count_mismatch")
    if len(added_by_table.get("sections", ())) != child_counts.sections:
        blockers.append("allowed_delta_section_count_mismatch")
    if len(added_by_table.get("chunks", ())) != child_counts.chunks:
        blockers.append("allowed_delta_chunk_count_mismatch")
    return CleanupDelta(
        documents=len(added_by_table.get("documents", ())),
        document_cards=len(added_by_table.get("document_cards", ())),
        sections=len(added_by_table.get("sections", ())),
        chunks=len(added_by_table.get("chunks", ())),
        card_embeddings=child_counts.card_embeddings,
        section_embeddings=child_counts.section_embeddings,
        chunk_embeddings=child_counts.chunk_embeddings,
        broader_drift=bool(blockers),
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _document_ref(row: dict[str, Any] | None, inventory: SourceInventory) -> CleanupDocumentRef | None:
    if not row:
        return None
    doc_id = str(row.get("id") or "")
    return CleanupDocumentRef(
        document_id=doc_id,
        document_key=str(row.get("document_key") or ""),
        status=str(row.get("status") or ""),
        version=int(row.get("version") or 0),
        source_id=str(_metadata(row).get("source_name") or ""),
        workspace_id=str(row.get("workspace_id") or ""),
        content_hash=str(row.get("content_hash") or ""),
        ingestion_signature=_ingestion_signature(row),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        children=_children_for_doc(
            inventory.document_cards,
            inventory.sections,
            inventory.chunks,
            doc_id,
        ),
    )


def _children_for_doc(
    cards: tuple[dict[str, Any], ...],
    sections: tuple[dict[str, Any], ...],
    chunks: tuple[dict[str, Any], ...],
    document_id: str,
) -> ChildCounts:
    doc_cards = [row for row in cards if row.get("document_id") == document_id]
    doc_sections = [row for row in sections if row.get("document_id") == document_id]
    doc_chunks = [row for row in chunks if row.get("document_id") == document_id]
    return ChildCounts(
        cards=len(doc_cards),
        sections=len(doc_sections),
        chunks=len(doc_chunks),
        card_embeddings=sum(1 for row in doc_cards if row.get("card_embedding") is not None),
        section_embeddings=sum(1 for row in doc_sections if row.get("section_embedding") is not None),
        chunk_embeddings=sum(1 for row in doc_chunks if row.get("embedding") is not None),
    )


def _target_child_count(inventory: SourceInventory, document_id: str) -> int:
    children = _children_for_doc(
        inventory.document_cards,
        inventory.sections,
        inventory.chunks,
        document_id,
    )
    return children.cards + children.sections + children.chunks


def _manifest_rows(manifest: dict[str, Any] | None) -> dict[str, tuple[dict[str, Any], ...]]:
    if not isinstance(manifest, dict):
        return {"documents": (), "document_cards": (), "sections": (), "chunks": ()}
    rows = manifest.get("rows") if isinstance(manifest.get("rows"), dict) else {}
    return {
        "documents": tuple(rows.get("documents") or ()),
        "document_cards": tuple(rows.get("document_cards") or ()),
        "sections": tuple(rows.get("sections") or ()),
        "chunks": tuple(rows.get("chunks") or ()),
    }


def _manifest_counts(manifest: dict[str, Any] | None) -> dict[str, int]:
    counts = manifest.get("counts") if isinstance(manifest, dict) and isinstance(manifest.get("counts"), dict) else {}
    return {
        "active_documents": int(counts.get("active_documents") or 0),
        "total_documents": int(counts.get("total_documents") or 0),
        "document_cards": int(counts.get("document_cards") or 0),
        "sections": int(counts.get("sections") or 0),
        "chunks": int(counts.get("chunks") or 0),
    }


def _inventory_counts(plan: DocsReprocessingPlan) -> dict[str, int]:
    return {
        "active_documents": plan.active_documents_count,
        "total_documents": plan.total_documents_count,
        "document_cards": plan.document_cards_count,
        "sections": plan.sections_count,
        "chunks": plan.chunks_count,
    }


def _row_belongs_to_target(table: str, row: dict[str, Any], document_id: str) -> bool:
    if table == "documents":
        return row.get("id") == document_id and row.get("status") == "draft"
    return row.get("document_id") == document_id


def _protected_active_in_backup(
    rows: dict[str, tuple[dict[str, Any], ...]],
    protected_active: CleanupDocumentRef | None,
) -> bool:
    if protected_active is None:
        return False
    candidates = [row for row in rows.get("documents", ()) if row.get("id") == protected_active.document_id]
    if len(candidates) != 1:
        return False
    backup_ref = _document_ref(
        candidates[0],
        SourceInventory(
            workspace_id=protected_active.workspace_id,
            workspace_name="backup",
            documents=rows.get("documents", ()),
            document_cards=rows.get("document_cards", ()),
            sections=rows.get("sections", ()),
            chunks=rows.get("chunks", ()),
        ),
    )
    return backup_ref == protected_active


def _protected_active_matches_backup(
    manifest: dict[str, Any],
    inventory: SourceInventory,
    protected_active: CleanupDocumentRef | None,
) -> bool:
    if protected_active is None:
        return False
    live_rows = [row for row in inventory.documents if row.get("id") == protected_active.document_id]
    if len(live_rows) != 1:
        return False
    live_ref = _document_ref(live_rows[0], inventory)
    return _protected_active_in_backup(_manifest_rows(manifest), live_ref)


def _normalized_row(row: dict[str, Any], *, reference: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = dict(row)
    normalized.pop("content_tsv", None)
    if reference is not None and "token_count" not in reference:
        normalized.pop("token_count", None)
    return normalized


def _target_state_fingerprint(target: CleanupDocumentRef | None, delta: CleanupDelta | None) -> str:
    return _stable_hash(
        {
            "target": target.to_dict() if target else None,
            "allowed_delta": delta.to_dict() if delta else None,
        }
    )


def _stable_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _single_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return rows[0] if len(rows) == 1 else None


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    return value if isinstance(value, dict) else {}


def _ingestion_signature(row: dict[str, Any]) -> str:
    ingestion = _metadata(row).get("ingestion")
    if isinstance(ingestion, dict):
        return str(ingestion.get("signature") or "")
    return str(_metadata(row).get("ingestion_signature") or "")


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _blocked_result(
    plan: IncompleteDraftCleanupPlan,
    reason: str,
    *,
    timestamp: str,
) -> IncompleteDraftCleanupResult:
    target_id = plan.target.document_id if plan.target else ""
    target_key = plan.target.document_key if plan.target else ""
    return IncompleteDraftCleanupResult(
        status="blocked",
        target_document_id=target_id,
        target_document_key=target_key,
        rows_deleted=0,
        target_absent=False,
        target_children_absent=False,
        protected_active_unchanged=False,
        source_matches_baseline=False,
        term_statistics_status="not run",
        partial_failure=False,
        rollback_required=False,
        automatic_retry=False,
        automatic_rollback=False,
        timestamp=timestamp,
        blockers=(reason,),
    )


def _counts_text(children: ChildCounts) -> str:
    return f"{children.cards} cards / {children.sections} sections / {children.chunks} chunks"


def _join(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
