"""Read-only planning for source document-key reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlparse

from app.docs_registry.reprocessing_plan import SourceInventory, SourceScope

SNAPSHOT_SCHEMA_VERSION = "docs-reconciliation-snapshot-v1"
REVIEW_SCHEMA_VERSION = "docs-reconciliation-review-v1"
REPOSITORY_ID = "serickprime/ai-kurator-v2"
SECRET_FIELD_MARKERS = (
    "apikey",
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "service_role",
    "token",
)

DocumentClassification = str


class DocsReconciliationPlanError(ValueError):
    """Raised for safe, expected reconciliation validation errors."""


@dataclass(frozen=True)
class DiscoveredDocument:
    """One discovered canonical document key from a no-write snapshot."""

    document_key: str
    canonical_url: str
    title: str = ""
    content_hash: str | None = None
    discovered_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "document_key": self.document_key,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "content_hash": self.content_hash,
            "discovered_at": self.discovered_at,
        }


@dataclass(frozen=True)
class ReconciliationItem:
    """One document-key classification in a reconciliation plan."""

    document_key: str
    classification: DocumentClassification
    active_version: int | None = None
    active_status: str | None = None
    title: str = ""
    latest_updated_at: str | None = None
    successor_candidates: tuple[str, ...] = ()
    reason: str = ""
    owner_review_required: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "document_key": self.document_key,
            "classification": self.classification,
            "active_version": self.active_version,
            "active_status": self.active_status,
            "title": self.title,
            "latest_updated_at": self.latest_updated_at,
            "successor_candidates": list(self.successor_candidates),
            "reason": self.reason,
            "owner_review_required": self.owner_review_required,
        }


@dataclass(frozen=True)
class DocsReconciliationPlan:
    """Immutable read-only source reconciliation plan."""

    mode: str
    service_id: str
    source_id: str
    workspace_id: str
    workspace_name: str
    generated_at: str
    current_active_keys_count: int
    discovered_keys_count: int
    current_active_fingerprint: str
    discovered_snapshot_fingerprint: str
    source_config_fingerprint: str
    common_keys: tuple[str, ...]
    newly_discovered_keys: tuple[str, ...]
    active_missing_from_snapshot_keys: tuple[str, ...]
    possible_canonical_replacements: tuple[ReconciliationItem, ...]
    ambiguous_cases: tuple[ReconciliationItem, ...]
    canonical_collisions: tuple[str, ...]
    items: tuple[ReconciliationItem, ...]
    readiness: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    automatic_archive_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "mode": self.mode,
            "service_id": self.service_id,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "generated_at": self.generated_at,
            "counts": {
                "current_active_keys": self.current_active_keys_count,
                "discovered_keys": self.discovered_keys_count,
                "common_keys": len(self.common_keys),
                "newly_discovered": len(self.newly_discovered_keys),
                "active_missing_from_snapshot": len(self.active_missing_from_snapshot_keys),
                "possible_canonical_replacements": len(self.possible_canonical_replacements),
                "ambiguous_cases": len(self.ambiguous_cases),
                "canonical_collisions": len(self.canonical_collisions),
            },
            "current_active_fingerprint": self.current_active_fingerprint,
            "discovered_snapshot_fingerprint": self.discovered_snapshot_fingerprint,
            "source_config_fingerprint": self.source_config_fingerprint,
            "common_keys": list(self.common_keys),
            "newly_discovered_keys": list(self.newly_discovered_keys),
            "active_missing_from_snapshot_keys": list(self.active_missing_from_snapshot_keys),
            "possible_canonical_replacements": [item.to_dict() for item in self.possible_canonical_replacements],
            "ambiguous_cases": [item.to_dict() for item in self.ambiguous_cases],
            "canonical_collisions": list(self.canonical_collisions),
            "items": [item.to_dict() for item in self.items],
            "ready_for_owner_review": self.readiness,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "automatic_archive_allowed": self.automatic_archive_allowed,
            "supabase_writes": "disabled",
            "activation_reprocessing": "not performed",
        }


@dataclass(frozen=True)
class SnapshotVerificationResult:
    """Result of validating a discovered-key snapshot."""

    valid: bool
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "valid": self.valid,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
        }


def build_discovered_snapshot(
    *,
    scope: SourceScope,
    workspace_id: str,
    workspace_name: str,
    discovered: Iterable[DiscoveredDocument | dict[str, object]],
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Build a local no-content discovered-key snapshot manifest."""
    rows = [_discovered_from_any(row).to_dict() for row in discovered]
    source_config_fingerprint = source_config_fingerprint_for_scope(scope)
    payload: dict[str, object] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": _format_datetime(generated_at or datetime.now(timezone.utc)),
        "repository": REPOSITORY_ID,
        "service_id": scope.service_id,
        "source_id": scope.source_id,
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
        "source_config_fingerprint": source_config_fingerprint,
        "discovered_documents": rows,
        "discovered_keys": [row["document_key"] for row in rows],
        "snapshot_fingerprint": compute_discovered_snapshot_fingerprint(
            service_id=scope.service_id,
            source_id=scope.source_id,
            workspace_id=workspace_id,
            discovered_documents=rows,
            source_config_fingerprint=source_config_fingerprint,
        ),
    }
    payload["checksum"] = _payload_checksum(payload)
    return payload


def load_snapshot(path: Path | str) -> dict[str, object]:
    """Load a local snapshot file."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DocsReconciliationPlanError(f"invalid snapshot JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DocsReconciliationPlanError("snapshot must be a JSON object")
    return value


def verify_discovered_snapshot(
    snapshot: dict[str, object],
    *,
    scope: SourceScope,
    workspace_id: str,
    workspace_name: str,
) -> SnapshotVerificationResult:
    """Verify a discovered-key snapshot without writes."""
    blockers: list[str] = []
    warnings: list[str] = []
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        blockers.append("unsupported snapshot schema version")
    if _payload_checksum(snapshot) != snapshot.get("checksum"):
        blockers.append("snapshot checksum mismatch")
    for key in (
        "service_id",
        "source_id",
        "workspace_id",
        "workspace_name",
        "source_config_fingerprint",
        "snapshot_fingerprint",
        "discovered_documents",
        "discovered_keys",
    ):
        if key not in snapshot:
            blockers.append(f"missing required field: {key}")
    if snapshot.get("service_id") != scope.service_id:
        blockers.append("snapshot service_id does not match scope")
    if snapshot.get("source_id") != scope.source_id:
        blockers.append("snapshot source_id does not match scope")
    if snapshot.get("workspace_id") != workspace_id:
        blockers.append("snapshot workspace_id does not match runtime")
    if snapshot.get("workspace_name") != workspace_name:
        blockers.append("snapshot workspace_name does not match runtime")
    current_config_fingerprint = source_config_fingerprint_for_scope(scope)
    if snapshot.get("source_config_fingerprint") != current_config_fingerprint:
        blockers.append("source configuration fingerprint changed")

    rows = snapshot.get("discovered_documents")
    if not isinstance(rows, list):
        blockers.append("discovered_documents must be a list")
        rows = []
    discovered = [_discovered_from_any(row) for row in rows if isinstance(row, dict)]
    keys = [row.document_key for row in discovered]
    if not keys:
        blockers.append("snapshot has no discovered document keys")
    if len(keys) != len(set(keys)):
        blockers.append("snapshot contains duplicate discovered document keys")
    for row in discovered:
        if row.document_key != row.canonical_url:
            warnings.append("snapshot contains key/canonical_url differences; review canonicalization")
        if not _key_allowed_for_scope(row.document_key, scope):
            blockers.append("snapshot contains key outside allowed source scope")
            break
    if _contains_secret_field(snapshot):
        blockers.append("snapshot contains an obvious secret field")
    expected_fingerprint = compute_discovered_snapshot_fingerprint(
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id=workspace_id,
        discovered_documents=[row.to_dict() for row in discovered],
        source_config_fingerprint=current_config_fingerprint,
    )
    if snapshot.get("snapshot_fingerprint") != expected_fingerprint:
        blockers.append("snapshot fingerprint mismatch")
    return SnapshotVerificationResult(
        valid=not blockers,
        blocking_reasons=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def build_reconciliation_plan(
    *,
    scope: SourceScope,
    inventory: SourceInventory,
    snapshot: dict[str, object],
    generated_at: datetime | None = None,
) -> DocsReconciliationPlan:
    """Build a read-only reconciliation plan for one source snapshot."""
    verification = verify_discovered_snapshot(
        snapshot,
        scope=scope,
        workspace_id=inventory.workspace_id,
        workspace_name=inventory.workspace_name,
    )
    if not verification.valid:
        raise DocsReconciliationPlanError("; ".join(verification.blocking_reasons))

    active_docs = tuple(row for row in inventory.documents if row.get("status") == "active")
    active_by_key = {str(row.get("document_key") or ""): row for row in active_docs}
    active_keys = tuple(key for key in active_by_key if key)
    discovered = tuple(_discovered_from_any(row) for row in snapshot.get("discovered_documents", []) if isinstance(row, dict))
    discovered_by_key = {row.document_key: row for row in discovered}
    discovered_keys = tuple(discovered_by_key)
    common_keys = tuple(sorted(set(active_keys) & set(discovered_keys)))
    new_keys = tuple(sorted(set(discovered_keys) - set(active_keys)))
    missing_keys = tuple(sorted(set(active_keys) - set(discovered_keys)))

    items: list[ReconciliationItem] = []
    possible: list[ReconciliationItem] = []
    ambiguous: list[ReconciliationItem] = []
    collisions = _canonical_collisions(discovered)

    for key in common_keys:
        row = active_by_key[key]
        items.append(_item(row, "active_and_discovered", reason="active key is present in discovered snapshot"))
    for key in new_keys:
        found = discovered_by_key[key]
        items.append(
            ReconciliationItem(
                document_key=key,
                classification="newly_discovered",
                title=found.title,
                reason="discovered key is not currently active",
                owner_review_required=False,
            )
        )
    for key in missing_keys:
        row = active_by_key[key]
        candidates = _successor_candidates(row, discovered)
        if len(candidates) == 1:
            item = _item(
                row,
                "possible_superseded",
                successors=(candidates[0].document_key,),
                reason="missing active key has one plausible successor by generic path/title signals",
                owner_review_required=True,
            )
            items.append(item)
            possible.append(item)
        elif len(candidates) > 1:
            item = _item(
                row,
                "ambiguous_needs_review",
                successors=tuple(candidate.document_key for candidate in candidates),
                reason="missing active key has multiple plausible successor candidates",
                owner_review_required=True,
            )
            items.append(item)
            ambiguous.append(item)
        else:
            items.append(
                _item(
                    row,
                    "active_missing_from_snapshot",
                    reason="active key is absent from discovered snapshot; this is not automatic obsolete",
                    owner_review_required=True,
                )
            )

    blockers: list[str] = []
    warnings: list[str] = list(verification.warnings)
    duplicate_active = _duplicate_values(active_keys)
    if duplicate_active:
        blockers.append("duplicate active document keys exist")
    if collisions:
        blockers.append("canonical collisions require owner review")
    if ambiguous:
        blockers.append("ambiguous successor candidates require owner review")
    if missing_keys:
        blockers.append("owner review is required before any archive decision")
    if not discovered_keys:
        blockers.append("snapshot has no discovered keys")
    if set(snapshot.get("discovered_keys", [])) != set(discovered_keys):
        blockers.append("discovered_keys does not match discovered_documents")
    if new_keys:
        warnings.append("newly discovered keys are informational only; no activation is performed")
    if missing_keys:
        warnings.append("missing active keys are not treated as obsolete without owner review")

    return DocsReconciliationPlan(
        mode="read-only",
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id=inventory.workspace_id,
        workspace_name=inventory.workspace_name,
        generated_at=_format_datetime(generated_at or datetime.now(timezone.utc)),
        current_active_keys_count=len(active_keys),
        discovered_keys_count=len(discovered_keys),
        current_active_fingerprint=compute_active_inventory_fingerprint(
            service_id=scope.service_id,
            source_id=scope.source_id,
            workspace_id=inventory.workspace_id,
            active_documents=active_docs,
            source_config_fingerprint=source_config_fingerprint_for_scope(scope),
        ),
        discovered_snapshot_fingerprint=str(snapshot.get("snapshot_fingerprint") or ""),
        source_config_fingerprint=source_config_fingerprint_for_scope(scope),
        common_keys=common_keys,
        newly_discovered_keys=new_keys,
        active_missing_from_snapshot_keys=missing_keys,
        possible_canonical_replacements=tuple(possible),
        ambiguous_cases=tuple(ambiguous),
        canonical_collisions=collisions,
        items=tuple(sorted(items, key=lambda item: (item.classification, item.document_key))),
        readiness=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        automatic_archive_allowed=False,
    )


def build_review_export(plan: DocsReconciliationPlan) -> dict[str, object]:
    """Build a local owner-review file from a read-only reconciliation plan."""
    payload: dict[str, object] = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "generated_at": plan.generated_at,
        "repository": REPOSITORY_ID,
        "service_id": plan.service_id,
        "source_id": plan.source_id,
        "workspace_id": plan.workspace_id,
        "workspace_name": plan.workspace_name,
        "snapshot_fingerprint": plan.discovered_snapshot_fingerprint,
        "active_inventory_fingerprint": plan.current_active_fingerprint,
        "mode": "owner-review-required",
        "automatic_archive_allowed": False,
        "decisions": [
            {
                "document_key": item.document_key,
                "classification": item.classification,
                "successor_candidates": list(item.successor_candidates),
                "owner_decision": "needs_more_review" if item.owner_review_required else "",
                "allowed_decisions": ["keep_active", "archive_candidate", "superseded_by", "needs_more_review"],
                "notes": "",
            }
            for item in plan.items
            if item.classification in {"active_missing_from_snapshot", "possible_superseded", "ambiguous_needs_review"}
        ],
    }
    payload["checksum"] = _payload_checksum(payload)
    return payload


def write_json_manifest_atomic(payload: dict[str, object], output: Path | str, *, force: bool = False) -> None:
    """Write a local JSON manifest atomically and outside the Git repository."""
    output_path = Path(output)
    if _path_is_inside(output_path.resolve(), Path.cwd().resolve()):
        raise DocsReconciliationPlanError("output path must be outside the Git repository")
    if output_path.exists() and not force:
        raise DocsReconciliationPlanError(f"output already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")
    if temp_path.exists():
        temp_path.unlink()
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(output_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def format_reconciliation_plan_text(plan: DocsReconciliationPlan) -> str:
    """Return compact CLI text output."""
    return "\n".join(
        [
            "Docs Reconciliation Plan",
            "",
            "- mode: read-only",
            f"- service: {plan.service_id}",
            f"- source: {plan.source_id}",
            f"- workspace: {plan.workspace_name} ({plan.workspace_id})",
            f"- generated at: {plan.generated_at}",
            f"- current active keys: {plan.current_active_keys_count}",
            f"- discovered keys: {plan.discovered_keys_count}",
            f"- common keys: {len(plan.common_keys)}",
            f"- newly discovered: {_preview(plan.newly_discovered_keys)}",
            f"- active missing from snapshot: {_preview(plan.active_missing_from_snapshot_keys)}",
            f"- possible superseded: {_preview(tuple(item.document_key for item in plan.possible_canonical_replacements))}",
            f"- ambiguous: {_preview(tuple(item.document_key for item in plan.ambiguous_cases))}",
            f"- canonical collisions: {_preview(plan.canonical_collisions)}",
            f"- active inventory fingerprint: {plan.current_active_fingerprint}",
            f"- snapshot fingerprint: {plan.discovered_snapshot_fingerprint}",
            f"- ready for owner review: {_yes_no(plan.readiness)}",
            f"- blockers: {_join_preview(plan.blockers)}",
            f"- warnings: {_join_preview(plan.warnings)}",
            "- automatic archive: disabled",
            "- Supabase writes: disabled",
            "- activation/reprocessing: not performed",
        ]
    )


def compute_active_inventory_fingerprint(
    *,
    service_id: str,
    source_id: str,
    workspace_id: str,
    active_documents: Iterable[dict[str, Any]],
    source_config_fingerprint: str,
) -> str:
    """Return a deterministic fingerprint for active key/version identity."""
    rows = [
        {
            "id": str(row.get("id") or ""),
            "key": str(row.get("document_key") or ""),
            "version": int(row.get("version") or 0),
            "content_hash": str(row.get("content_hash") or ""),
            "ingestion_signature": _ingestion_signature(row),
            "updated_at": str(row.get("updated_at") or ""),
        }
        for row in active_documents
        if row.get("status") == "active"
    ]
    return _stable_hash(
        {
            "service_id": service_id,
            "source_id": source_id,
            "workspace_id": workspace_id,
            "active_documents": sorted(rows, key=lambda row: row["key"]),
            "source_config_fingerprint": source_config_fingerprint,
        }
    )


def compute_discovered_snapshot_fingerprint(
    *,
    service_id: str,
    source_id: str,
    workspace_id: str,
    discovered_documents: Iterable[dict[str, object]],
    source_config_fingerprint: str,
) -> str:
    """Return a deterministic fingerprint for discovered canonical keys."""
    rows = [
        {
            "document_key": str(row.get("document_key") or ""),
            "canonical_url": str(row.get("canonical_url") or row.get("document_key") or ""),
            "title": str(row.get("title") or ""),
            "content_hash": str(row.get("content_hash") or ""),
        }
        for row in discovered_documents
    ]
    return _stable_hash(
        {
            "service_id": service_id,
            "source_id": source_id,
            "workspace_id": workspace_id,
            "discovered_documents": sorted(rows, key=lambda row: row["document_key"]),
            "source_config_fingerprint": source_config_fingerprint,
        }
    )


def source_config_fingerprint_for_scope(scope: SourceScope) -> str:
    """Return a deterministic fingerprint for source config."""
    return _stable_hash(_sanitize_for_manifest(scope.source_config))


def _item(
    row: dict[str, Any],
    classification: str,
    *,
    successors: tuple[str, ...] = (),
    reason: str,
    owner_review_required: bool = False,
) -> ReconciliationItem:
    return ReconciliationItem(
        document_key=str(row.get("document_key") or ""),
        classification=classification,
        active_version=int(row.get("version") or 0),
        active_status=str(row.get("status") or ""),
        title=str(row.get("title") or ""),
        latest_updated_at=str(row.get("updated_at") or "") or None,
        successor_candidates=successors,
        reason=reason,
        owner_review_required=owner_review_required,
    )


def _successor_candidates(row: dict[str, Any], discovered: tuple[DiscoveredDocument, ...]) -> tuple[DiscoveredDocument, ...]:
    title = str(row.get("title") or "")
    key = str(row.get("document_key") or "")
    candidates: list[DiscoveredDocument] = []
    for candidate in discovered:
        signals = _successor_signals(
            old_key=key,
            old_title=title,
            new_key=candidate.document_key,
            new_title=candidate.title,
        )
        if signals >= 2:
            candidates.append(candidate)
    return tuple(candidates)


def _successor_signals(*, old_key: str, old_title: str, new_key: str, new_title: str) -> int:
    old_path = _path_tokens(old_key)
    new_path = _path_tokens(new_key)
    signals = 0
    if old_path and new_path and old_path[-1] == new_path[-1]:
        signals += 1
    if old_path and new_path and _is_suffix(old_path, new_path):
        signals += 1
    old_title_norm = _normalize_text(old_title)
    new_title_norm = _normalize_text(new_title)
    if old_title_norm and new_title_norm and old_title_norm == new_title_norm:
        signals += 2
    overlap = _token_overlap(old_path, new_path)
    if overlap >= 0.67:
        signals += 1
    return signals


def _canonical_collisions(discovered: tuple[DiscoveredDocument, ...]) -> tuple[str, ...]:
    by_slug: dict[str, list[str]] = {}
    for row in discovered:
        slug = _path_tokens(row.document_key)[-1:] or [row.document_key]
        by_slug.setdefault(slug[0], []).append(row.document_key)
    collisions: list[str] = []
    for keys in by_slug.values():
        if len(keys) > 1:
            titles = {_normalize_text(key.rsplit("/", 1)[-1]) for key in keys}
            if len(titles) == 1:
                collisions.extend(sorted(keys))
    return tuple(dict.fromkeys(collisions))


def _path_tokens(url: str) -> list[str]:
    path = urlparse(url).path.strip("/")
    return [part for part in re.split(r"[/_-]+", path.casefold()) if part and part != "docs"]


def _token_overlap(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / max(len(left_set), len(right_set))


def _is_suffix(left: list[str], right: list[str]) -> bool:
    if len(left) > len(right):
        return False
    return right[-len(left) :] == left


def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _discovered_from_any(value: DiscoveredDocument | dict[str, object]) -> DiscoveredDocument:
    if isinstance(value, DiscoveredDocument):
        return value
    key = str(value.get("document_key") or value.get("canonical_url") or "").strip()
    canonical = str(value.get("canonical_url") or key).strip()
    return DiscoveredDocument(
        document_key=key,
        canonical_url=canonical,
        title=str(value.get("title") or "").strip(),
        content_hash=str(value.get("content_hash") or "").strip() or None,
        discovered_at=str(value.get("discovered_at") or "").strip() or None,
    )


def _key_allowed_for_scope(key: str, scope: SourceScope) -> bool:
    parsed = urlparse(key)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    domains = tuple(str(item).casefold() for item in scope.source_config.get("allowed_domains", ()) or ())
    if domains and not any(host == domain or host.endswith("." + domain) for domain in domains):
        return False
    allow_patterns = tuple(str(item) for item in scope.source_config.get("allow_patterns", ()) or ())
    if allow_patterns and not any(re.search(pattern, key) for pattern in allow_patterns):
        return False
    deny_patterns = tuple(str(item) for item in scope.source_config.get("deny_patterns", ()) or ())
    if any(re.search(pattern, key) for pattern in deny_patterns):
        return False
    return True


def _ingestion_signature(row: dict[str, Any]) -> str:
    ingestion = _metadata(row).get("ingestion")
    if isinstance(ingestion, dict):
        return str(ingestion.get("signature") or "")
    return ""


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _duplicate_values(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)


def _sanitize_for_manifest(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                continue
            result[key_text] = _sanitize_for_manifest(item)
        return result
    if isinstance(value, list):
        return [_sanitize_for_manifest(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_manifest(item) for item in value]
    return value


def _contains_secret_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_secret_key(str(key)):
                return True
            if _contains_secret_field(item):
                return True
    if isinstance(value, list):
        return any(_contains_secret_field(item) for item in value)
    return False


def _is_secret_key(key: str) -> bool:
    lowered = key.replace("-", "_").casefold()
    return any(marker in lowered for marker in SECRET_FIELD_MARKERS)


def _payload_checksum(payload: dict[str, object]) -> str:
    clean = {key: value for key, value in payload.items() if key != "checksum"}
    return _stable_hash(clean)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _path_is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _preview(values: tuple[str, ...], *, limit: int = 5) -> str:
    if not values:
        return "none"
    shown = ", ".join(values[:limit])
    suffix = "" if len(values) <= limit else f" (+{len(values) - limit} more)"
    return shown + suffix


def _join_preview(values: tuple[str, ...], *, limit: int = 4) -> str:
    if not values:
        return "none"
    return "; ".join(values[:limit]) + ("" if len(values) <= limit else f"; +{len(values) - limit} more")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
