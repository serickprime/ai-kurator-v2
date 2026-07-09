"""Read-only preparation tools for source-scoped docs reprocessing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

from app.docs_registry.candidates import (
    DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG,
    load_docs_source_candidates_config,
)
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG, load_external_docs_config
from app.external_docs.indexer import EXTERNAL_CHUNK_QUALITY_VERSION
from app.external_docs.types import EXTERNAL_DOCS_VERSION, ExternalDocSource
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG, load_service_registry_config
from app.service_registry.docs_health import DocsHealthReport, DocsSourceHealth

MANIFEST_SCHEMA_VERSION = "docs-reprocessing-baseline-v1"
REPOSITORY_ID = "serickprime/ai-kurator-v2"
DEFAULT_WORKSPACE = "team"
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


class DocsReprocessingPlanError(ValueError):
    """Raised for safe, expected planning or validation errors."""


@dataclass(frozen=True)
class SourceScope:
    """Resolved source scope for one service/source pair."""

    service_id: str
    display_name: str
    source_id: str
    source_title: str
    source_type: str
    registered: bool
    source_config: dict[str, object]


@dataclass(frozen=True)
class SourceInventory:
    """Read-only source inventory loaded from runtime rows."""

    workspace_id: str
    workspace_name: str
    documents: tuple[dict[str, Any], ...]
    document_cards: tuple[dict[str, Any], ...]
    sections: tuple[dict[str, Any], ...]
    chunks: tuple[dict[str, Any], ...]
    term_statistics_count: int | None = None
    runtime_status: str = "available"


@dataclass(frozen=True)
class DocsReprocessingPlan:
    """Immutable read-only plan for one future source-scoped reprocessing run."""

    mode: str
    service_id: str
    source_id: str
    workspace_id: str
    workspace_name: str
    source_title: str
    source_type: str
    registered: bool
    active: bool
    current_health_status: str
    docs_status: str
    quality_status: str
    status_reasons: tuple[str, ...]
    stale_status: str
    stale_reason: str
    active_documents_count: int
    total_documents_count: int
    document_cards_count: int
    sections_count: int
    chunks_count: int
    active_document_ids: tuple[str, ...]
    active_document_keys: tuple[str, ...]
    versions: tuple[int, ...]
    content_hashes: tuple[str, ...]
    ingestion_signatures: tuple[str, ...]
    duplicate_active_document_keys: tuple[str, ...]
    latest_created_at: str | None
    latest_updated_at: str | None
    latest_crawled_at: str | None
    current_source_configuration_fingerprint: str
    baseline_fingerprint: str
    term_statistics_scope_risk: str
    expected_write_entities: dict[str, tuple[str, ...]]
    known_partial_failure_risks: tuple[str, ...]
    readiness: bool
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    automatic_execution_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe plan representation."""
        return {
            "mode": self.mode,
            "service_id": self.service_id,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "source_title": self.source_title,
            "source_type": self.source_type,
            "registered": self.registered,
            "active": self.active,
            "current_health_status": self.current_health_status,
            "docs_status": self.docs_status,
            "quality_status": self.quality_status,
            "status_reasons": list(self.status_reasons),
            "stale_status": self.stale_status,
            "stale_reason": self.stale_reason,
            "counts": {
                "active_documents": self.active_documents_count,
                "total_documents": self.total_documents_count,
                "document_cards": self.document_cards_count,
                "sections": self.sections_count,
                "chunks": self.chunks_count,
            },
            "active_document_ids": list(self.active_document_ids),
            "active_document_keys": list(self.active_document_keys),
            "versions": list(self.versions),
            "content_hashes": list(self.content_hashes),
            "ingestion_signatures": list(self.ingestion_signatures),
            "duplicate_active_document_keys": list(self.duplicate_active_document_keys),
            "latest_timestamps": {
                "created_at": self.latest_created_at,
                "updated_at": self.latest_updated_at,
                "crawled_at": self.latest_crawled_at,
            },
            "current_source_configuration_fingerprint": self.current_source_configuration_fingerprint,
            "baseline_fingerprint": self.baseline_fingerprint,
            "term_statistics_scope_risk": self.term_statistics_scope_risk,
            "expected_write_entities": {
                key: list(values) for key, values in self.expected_write_entities.items()
            },
            "known_partial_failure_risks": list(self.known_partial_failure_risks),
            "ready_for_execution": self.readiness,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
            "automatic_execution_allowed": self.automatic_execution_allowed,
        }


@dataclass(frozen=True)
class ManifestVerificationResult:
    """Result of offline manifest verification."""

    valid: bool
    rollback_capable: bool
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "valid": self.valid,
            "rollback_capable": self.rollback_capable,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DriftComparisonResult:
    """Read-only comparison between a manifest baseline and live runtime rows."""

    matches: bool
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "matches": self.matches,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ExecutionPreconditionResult:
    """Reusable precondition gate for future owner-approved execution."""

    ready: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    required_owner_confirmation: str
    expected_write_scope: dict[str, tuple[str, ...]]
    rollback_reference: str

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe representation."""
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "required_owner_confirmation": self.required_owner_confirmation,
            "expected_write_scope": {
                key: list(values) for key, values in self.expected_write_scope.items()
            },
            "rollback_reference": self.rollback_reference,
        }


class DocsReprocessingRuntimeProvider:
    """Read-only runtime loader for docs reprocessing plans."""

    def __init__(
        self,
        client: Any,
        *,
        workspace: str = DEFAULT_WORKSPACE,
        limit: int = 10000,
    ) -> None:
        self._client = client
        self._workspace = workspace
        self._limit = limit

    async def load_inventory(self, source_id: str) -> SourceInventory:
        """Load source-scoped runtime rows without writes or refreshes."""
        workspace_rows = await self._client.select(
            "workspaces",
            params={"select": "id,name", "name": f"eq.{self._workspace}", "limit": "1"},
        )
        if not workspace_rows:
            raise DocsReprocessingPlanError(f"workspace not found: {self._workspace}")
        workspace_id = str(workspace_rows[0].get("id") or "")
        workspace_name = str(workspace_rows[0].get("name") or self._workspace)
        documents = await self._client.select(
            "documents",
            params={
                "select": (
                    "id,workspace_id,source_type,filename,document_key,title,course,module,lesson,"
                    "version,status,content_hash,metadata,created_at,updated_at"
                ),
                "workspace_id": f"eq.{workspace_id}",
                "source_type": "eq.external_docs",
                "limit": str(self._limit),
            },
        )
        source_documents = tuple(
            row for row in documents if _metadata(row).get("source_name") == source_id
        )
        document_ids = [str(row.get("id") or "") for row in source_documents if row.get("id")]
        cards = await _load_related_rows(
            self._client,
            "document_cards",
            document_ids,
            select=(
                "id,document_id,workspace_id,summary,topics,questions_answered,entities,"
                "task_types,not_about,quality_score,metadata,created_at,updated_at"
            ),
            limit=self._limit,
        )
        sections = await _load_related_rows(
            self._client,
            "sections",
            document_ids,
            select="id,document_id,workspace_id,section_index,heading,summary,page_start,page_end,metadata",
            limit=self._limit,
        )
        chunks = await _load_related_rows(
            self._client,
            "chunks",
            document_ids,
            select="id,document_id,section_id,workspace_id,chunk_index,token_count,page,heading,metadata,created_at",
            limit=self._limit,
        )
        term_rows = await self._client.select(
            "term_statistics",
            params={"select": "term", "workspace_id": f"eq.{workspace_id}", "limit": str(self._limit)},
        )
        return SourceInventory(
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            documents=source_documents,
            document_cards=tuple(cards),
            sections=tuple(sections),
            chunks=tuple(chunks),
            term_statistics_count=len(term_rows),
            runtime_status="available",
        )

    async def load_full_export_rows(self, source_id: str) -> SourceInventory:
        """Load full source-scoped rows for a local baseline export."""
        base = await self.load_inventory(source_id)
        document_ids = [str(row.get("id") or "") for row in base.documents if row.get("id")]
        cards = await _load_related_rows(
            self._client,
            "document_cards",
            document_ids,
            select="*",
            limit=self._limit,
        )
        sections = await _load_related_rows(
            self._client,
            "sections",
            document_ids,
            select="*",
            limit=self._limit,
        )
        chunks = await _load_related_rows(
            self._client,
            "chunks",
            document_ids,
            select="*",
            limit=self._limit,
        )
        return SourceInventory(
            workspace_id=base.workspace_id,
            workspace_name=base.workspace_name,
            documents=base.documents,
            document_cards=tuple(cards),
            sections=tuple(sections),
            chunks=tuple(chunks),
            term_statistics_count=base.term_statistics_count,
            runtime_status=base.runtime_status,
        )


def resolve_source_scope(
    service_id_or_alias: str,
    *,
    source_id: str | None = None,
    registry_config_path: Path | str = DEFAULT_SERVICE_REGISTRY_CONFIG,
    external_config_path: Path | str = DEFAULT_EXTERNAL_DOCS_CONFIG,
    candidates_config_path: Path | str = DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG,
) -> SourceScope:
    """Resolve one service into exactly one canonical docs source."""
    query = service_id_or_alias.strip().casefold()
    if not query:
        raise DocsReprocessingPlanError("service is required")

    scopes: dict[str, SourceScope] = {}
    try:
        registry = load_service_registry_config(registry_config_path)
        external_config = load_external_docs_config(external_config_path)
        external_by_name = {source.name: source for source in external_config.sources}
        for service in registry.services:
            if service.service_id.casefold() != query and not any(alias.casefold() == query for alias in service.aliases):
                continue
            if not service.docs_source:
                raise DocsReprocessingPlanError(f"service has no docs source: {service.service_id}")
            source = external_by_name.get(service.docs_source)
            scopes[service.docs_source] = SourceScope(
                service_id=service.service_id,
                display_name=service.display_name,
                source_id=service.docs_source,
                source_title=service.display_name,
                source_type="external_docs",
                registered=True,
                source_config=_source_config_dict(source) if source else {"source_id": service.docs_source},
            )
    except Exception as exc:
        if not isinstance(exc, DocsReprocessingPlanError):
            scopes = scopes
        else:
            raise

    try:
        candidates = load_docs_source_candidates_config(candidates_config_path)
        for candidate in candidates.candidates:
            if candidate.service_id.casefold() != query and not any(alias.casefold() == query for alias in candidate.aliases):
                continue
            source = ExternalDocSource(
                name=candidate.docs_source,
                source_kind="external_docs",
                allowed_domains=candidate.allowed_domains,
                start_urls=candidate.official_start_urls,
                allow_patterns=candidate.allow_patterns,
                deny_patterns=candidate.deny_patterns,
                crawl_depth=candidate.crawl_depth,
                max_pages=candidate.max_pages,
                refresh_days=14,
            )
            scopes[candidate.docs_source] = SourceScope(
                service_id=candidate.service_id,
                display_name=candidate.display_name,
                source_id=candidate.docs_source,
                source_title=candidate.display_name,
                source_type="active_candidate_docs",
                registered=True,
                source_config={
                    **_source_config_dict(source),
                    "risk_level": candidate.risk_level,
                    "notes": candidate.notes,
                },
            )
    except Exception:
        pass

    if not scopes:
        raise DocsReprocessingPlanError(f"service/source is not registered: {service_id_or_alias}")
    if source_id:
        expected = source_id.strip().casefold()
        matched = scopes.get(expected)
        if matched is None:
            known = ", ".join(sorted(scopes)) or "none"
            raise DocsReprocessingPlanError(
                f"service/source mismatch: {service_id_or_alias} does not use {source_id}; known sources: {known}"
            )
        return matched
    if len(scopes) > 1:
        known = ", ".join(sorted(scopes))
        raise DocsReprocessingPlanError(
            f"multiple sources found for {service_id_or_alias}: {known}; pass --source explicitly"
        )
    return next(iter(scopes.values()))


def build_reprocessing_plan(
    *,
    scope: SourceScope,
    inventory: SourceInventory,
    health_report: DocsHealthReport | None = None,
) -> DocsReprocessingPlan:
    """Build an immutable read-only reprocessing plan from runtime rows."""
    active_docs = tuple(row for row in inventory.documents if row.get("status") == "active")
    active_ids = tuple(str(row.get("id") or "") for row in active_docs if row.get("id"))
    active_keys = tuple(str(row.get("document_key") or "") for row in active_docs)
    versions = tuple(sorted({int(row.get("version") or 0) for row in active_docs}))
    content_hashes = tuple(sorted({str(row.get("content_hash") or "") for row in active_docs if row.get("content_hash")}))
    ingestion_signatures = tuple(
        sorted(
            {
                str(_metadata(row).get("ingestion", {}).get("signature") or "")
                for row in active_docs
                if isinstance(_metadata(row).get("ingestion"), dict)
            }
        )
    )
    duplicate_keys = _duplicate_values(active_keys)
    health = _health_for_scope(health_report, scope)
    source_config_fingerprint = _stable_hash(_sanitize_for_manifest(scope.source_config))
    baseline_fingerprint = compute_baseline_fingerprint(
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id=inventory.workspace_id,
        active_document_ids=active_ids,
        active_document_keys=active_keys,
        versions=versions,
        content_hashes=content_hashes,
        ingestion_signatures=ingestion_signatures,
        counts={
            "active_documents": len(active_docs),
            "total_documents": len(inventory.documents),
            "document_cards": len(inventory.document_cards),
            "sections": len(inventory.sections),
            "chunks": len(inventory.chunks),
        },
        source_config_fingerprint=source_config_fingerprint,
    )
    blockers = list(_plan_blockers(scope=scope, inventory=inventory, active_docs=active_docs, duplicate_keys=duplicate_keys))
    warnings = list(_plan_warnings(health=health, inventory=inventory))
    return DocsReprocessingPlan(
        mode="read-only",
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id=inventory.workspace_id,
        workspace_name=inventory.workspace_name,
        source_title=scope.source_title,
        source_type=scope.source_type,
        registered=scope.registered,
        active=bool(active_docs),
        current_health_status=health.current_status if health else "unknown",
        docs_status=health.docs_status if health else "unknown",
        quality_status=health.quality_status if health else "unknown",
        status_reasons=tuple(health.status_notes if health and health.status_notes else ((health.status_reason,) if health else ())),
        stale_status=health.stale_status if health else "unknown",
        stale_reason=health.stale_reason if health else "not verified",
        active_documents_count=len(active_docs),
        total_documents_count=len(inventory.documents),
        document_cards_count=len(inventory.document_cards),
        sections_count=len(inventory.sections),
        chunks_count=len(inventory.chunks),
        active_document_ids=active_ids,
        active_document_keys=active_keys,
        versions=versions,
        content_hashes=content_hashes,
        ingestion_signatures=ingestion_signatures,
        duplicate_active_document_keys=duplicate_keys,
        latest_created_at=_latest_timestamp(inventory.documents, "created_at"),
        latest_updated_at=_latest_timestamp(inventory.documents, "updated_at"),
        latest_crawled_at=_latest_metadata_timestamp(inventory.documents, "crawled_at"),
        current_source_configuration_fingerprint=source_config_fingerprint,
        baseline_fingerprint=baseline_fingerprint,
        term_statistics_scope_risk=(
            "workspace-wide refresh_term_statistics is expected during future activation; "
            f"current workspace term rows: {inventory.term_statistics_count if inventory.term_statistics_count is not None else 'unknown'}"
        ),
        expected_write_entities=expected_write_scope(),
        known_partial_failure_risks=known_partial_failure_risks(),
        readiness=not blockers,
        blocking_reasons=tuple(blockers),
        warnings=tuple(warnings),
        automatic_execution_allowed=False,
    )


def compute_baseline_fingerprint(
    *,
    service_id: str,
    source_id: str,
    workspace_id: str,
    active_document_ids: Iterable[str],
    active_document_keys: Iterable[str],
    versions: Iterable[int],
    content_hashes: Iterable[str],
    ingestion_signatures: Iterable[str],
    counts: dict[str, int],
    source_config_fingerprint: str,
) -> str:
    """Return a stable fingerprint for live baseline drift checks."""
    return _stable_hash(
        {
            "service_id": service_id,
            "source_id": source_id,
            "workspace_id": workspace_id,
            "active_document_ids": sorted(active_document_ids),
            "active_document_keys": sorted(active_document_keys),
            "versions": sorted(versions),
            "content_hashes": sorted(content_hashes),
            "ingestion_signatures": sorted(ingestion_signatures),
            "counts": counts,
            "source_config_fingerprint": source_config_fingerprint,
        }
    )


def build_baseline_manifest(
    *,
    plan: DocsReprocessingPlan,
    inventory: SourceInventory,
    include_rows: bool = True,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a source-scoped baseline manifest payload."""
    rows = _manifest_rows(inventory) if include_rows else {}
    rollback_capable = include_rows and _export_rows_complete(inventory)
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": _format_datetime(generated_at or datetime.now(timezone.utc)),
        "repository": REPOSITORY_ID,
        "mode": "read-only-baseline-export",
        "service_id": plan.service_id,
        "source_id": plan.source_id,
        "workspace_id": plan.workspace_id,
        "workspace_name": plan.workspace_name,
        "source_configuration_fingerprint": plan.current_source_configuration_fingerprint,
        "baseline_fingerprint": plan.baseline_fingerprint,
        "counts": {
            "active_documents": plan.active_documents_count,
            "total_documents": plan.total_documents_count,
            "document_cards": plan.document_cards_count,
            "sections": plan.sections_count,
            "chunks": plan.chunks_count,
        },
        "active_document_ids": list(plan.active_document_ids),
        "active_document_keys": list(plan.active_document_keys),
        "versions": list(plan.versions),
        "statuses": _document_status_counts(inventory.documents),
        "content_hashes": list(plan.content_hashes),
        "ingestion_signatures": list(plan.ingestion_signatures),
        "relationships": _relationships(inventory),
        "current_health": {
            "status": plan.current_health_status,
            "docs_status": plan.docs_status,
            "quality_status": plan.quality_status,
            "reasons": list(plan.status_reasons),
            "stale_status": plan.stale_status,
            "stale_reason": plan.stale_reason,
        },
        "latest_timestamps": {
            "created_at": plan.latest_created_at,
            "updated_at": plan.latest_updated_at,
            "crawled_at": plan.latest_crawled_at,
        },
        "duplicate_active_document_keys": list(plan.duplicate_active_document_keys),
        "term_statistics": {
            "scope": "workspace-wide",
            "count": inventory.term_statistics_count,
            "restore_strategy": "rebuild through refresh_term_statistics after explicit owner-approved execution",
        },
        "completeness": {
            "level": "full_rows_with_embeddings" if rollback_capable else "metadata_only",
            "rollback_capable": rollback_capable,
            "note": (
                "Full source-scoped rows are included for documents, cards, sections, and chunks."
                if rollback_capable
                else "Manifest is not sufficient for row-level rollback."
            ),
        },
        "warnings": [
            "Do not commit this backup manifest to Git.",
            "Export creation is not approval to run reprocessing.",
            "No Supabase writes or activation were performed by this export.",
        ],
        "rows": rows,
    }
    safe_payload = _sanitize_for_manifest(payload)
    safe_payload["checksum_algorithm"] = "sha256"
    safe_payload["checksum"] = _payload_checksum(safe_payload)
    return safe_payload


def write_manifest_atomic(manifest: dict[str, Any], output_path: Path, *, force: bool = False) -> None:
    """Write a manifest with an atomic temp-file rename."""
    path = output_path.resolve()
    if _path_is_inside(path, Path.cwd().resolve()):
        raise DocsReprocessingPlanError(
            "export path must be outside the Git repository; store production backups in a separate safe location"
        )
    if path.exists() and not force:
        raise DocsReprocessingPlanError(f"export path already exists: {path}; use --force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a JSON manifest without touching runtime."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - caller formats safe CLI error
        raise DocsReprocessingPlanError(f"could not read manifest: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict):
        raise DocsReprocessingPlanError("manifest root must be an object")
    return data


def verify_manifest(
    manifest: dict[str, Any],
    *,
    expected_service: str | None = None,
    expected_source: str | None = None,
    expected_workspace: str | None = None,
) -> ManifestVerificationResult:
    """Verify a baseline manifest without Supabase access."""
    blockers: list[str] = []
    warnings: list[str] = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        blockers.append("unsupported manifest schema version")
    if _payload_checksum(manifest) != manifest.get("checksum"):
        blockers.append("manifest checksum mismatch")
    for key in (
        "service_id",
        "source_id",
        "workspace_id",
        "baseline_fingerprint",
        "counts",
        "active_document_ids",
        "active_document_keys",
        "relationships",
        "completeness",
    ):
        if key not in manifest:
            blockers.append(f"missing required field: {key}")
    if expected_service and manifest.get("service_id") != expected_service:
        blockers.append("manifest service_id does not match expected service")
    if expected_source and manifest.get("source_id") != expected_source:
        blockers.append("manifest source_id does not match expected source")
    if expected_workspace and manifest.get("workspace_id") != expected_workspace:
        blockers.append("manifest workspace_id does not match expected workspace")

    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    relationships = manifest.get("relationships") if isinstance(manifest.get("relationships"), dict) else {}
    if len(manifest.get("active_document_ids") or []) != int(counts.get("active_documents") or 0):
        blockers.append("active document id count does not match active document count")
    if len(set(manifest.get("active_document_keys") or [])) != len(manifest.get("active_document_keys") or []):
        blockers.append("duplicate active document keys in manifest")
    for entity in ("document_cards", "sections", "chunks"):
        actual = _relationship_total(relationships, entity)
        expected = int(counts.get(entity) or 0)
        if actual != expected:
            blockers.append(f"relationship count mismatch for {entity}: {actual} != {expected}")

    rows = manifest.get("rows") if isinstance(manifest.get("rows"), dict) else {}
    source_id = str(manifest.get("source_id") or "")
    for row in rows.get("documents", []) if isinstance(rows.get("documents"), list) else []:
        row_source = _metadata(row).get("source_name")
        if row_source != source_id:
            blockers.append("manifest contains document rows for another source")
            break
    if _contains_secret_field(manifest):
        blockers.append("manifest contains an obvious secret field")
    rollback_capable = bool(
        isinstance(manifest.get("completeness"), dict)
        and manifest["completeness"].get("rollback_capable") is True
    )
    if not rollback_capable:
        blockers.append("manifest is not rollback-capable")
    if not rows:
        warnings.append("manifest has no full row payload")
    return ManifestVerificationResult(
        valid=not blockers,
        rollback_capable=rollback_capable,
        blocking_reasons=tuple(dict.fromkeys(blockers)),
        warnings=tuple(warnings),
    )


def compare_manifest_to_plan(manifest: dict[str, Any], plan: DocsReprocessingPlan) -> DriftComparisonResult:
    """Compare a verified manifest with a current live read-only plan."""
    blockers: list[str] = []
    if manifest.get("service_id") != plan.service_id:
        blockers.append("service_id changed")
    if manifest.get("source_id") != plan.source_id:
        blockers.append("source_id changed")
    if manifest.get("workspace_id") != plan.workspace_id:
        blockers.append("workspace_id changed")
    if manifest.get("baseline_fingerprint") != plan.baseline_fingerprint:
        blockers.append("baseline fingerprint changed")
    if manifest.get("source_configuration_fingerprint") != plan.current_source_configuration_fingerprint:
        blockers.append("source configuration fingerprint changed")
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    current_counts = plan.to_dict()["counts"]
    if counts != current_counts:
        blockers.append("source counts changed")
    if sorted(manifest.get("active_document_ids") or []) != sorted(plan.active_document_ids):
        blockers.append("active document IDs changed")
    if sorted(manifest.get("active_document_keys") or []) != sorted(plan.active_document_keys):
        blockers.append("active document keys changed")
    if sorted(manifest.get("versions") or []) != sorted(plan.versions):
        blockers.append("active versions changed")
    if plan.duplicate_active_document_keys:
        blockers.append("duplicate active document keys appeared")
    return DriftComparisonResult(matches=not blockers, blocking_reasons=tuple(dict.fromkeys(blockers)))


def validate_execution_preconditions(
    *,
    manifest_result: ManifestVerificationResult | None,
    drift_result: DriftComparisonResult | None,
    current_plan: DocsReprocessingPlan | None,
    runtime_available: bool,
) -> ExecutionPreconditionResult:
    """Return reusable gates for future execution without calling activation."""
    blockers: list[str] = []
    warnings: list[str] = []
    if manifest_result is None:
        blockers.append("baseline manifest is required")
    elif not manifest_result.valid:
        blockers.extend(manifest_result.blocking_reasons)
    elif not manifest_result.rollback_capable:
        blockers.append("rollback-capable manifest is required")
    if drift_result is None:
        blockers.append("live baseline drift comparison is required")
    elif not drift_result.matches:
        blockers.extend(drift_result.blocking_reasons)
    if current_plan is None:
        blockers.append("current read-only plan is required")
    else:
        if current_plan.duplicate_active_document_keys:
            blockers.append("duplicate active document keys must be zero")
        if current_plan.active_documents_count <= 0 or current_plan.chunks_count <= 0:
            blockers.append("current source counts must be non-zero")
        if not current_plan.active or not current_plan.registered:
            blockers.append("target source must be registered and active")
        warnings.extend(current_plan.warnings)
    if not runtime_available:
        blockers.append("runtime connectivity must be available")
    blockers.append("explicit owner confirmation is required before any execution")
    return ExecutionPreconditionResult(
        ready=False,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        required_owner_confirmation="owner must explicitly approve Phase 7B.1 for one source",
        expected_write_scope=expected_write_scope(),
        rollback_reference="verified source-scoped baseline manifest",
    )


def expected_write_scope() -> dict[str, tuple[str, ...]]:
    """Return expected write entities for the future activation path."""
    return {
        "documents": ("insert draft", "archive previous active by document_key", "activate new version"),
        "document_cards": ("insert rows for new versions",),
        "sections": ("insert rows for new versions",),
        "chunks": ("insert rows for new versions",),
        "term_statistics": ("workspace-wide refresh",),
        "evidence_logs": ("direct writes not expected",),
    }


def known_partial_failure_risks() -> tuple[str, ...]:
    """Return known risks from the existing per-page activation path."""
    return (
        "no transaction boundary for the entire source",
        "partial page failure may leave draft or partially related rows",
        "pages absent from a new crawl can remain active",
        "canonical URL drift can create new document_key values",
        "term_statistics refresh is workspace-wide",
        "source-wide exact replace is not guaranteed",
    )


def format_plan_text(plan: DocsReprocessingPlan) -> str:
    """Format a compact CLI text plan."""
    return "\n".join(
        [
            "Docs Reprocessing Preflight Plan",
            "",
            "- mode: read-only",
            f"- service: {plan.service_id}",
            f"- source: {plan.source_id}",
            f"- workspace: {plan.workspace_name} ({plan.workspace_id})",
            f"- source type: {plan.source_type}",
            f"- registered: {_yes_no(plan.registered)}",
            f"- active: {_yes_no(plan.active)}",
            f"- current health: {plan.current_health_status}",
            f"- docs status: {plan.docs_status}",
            f"- quality status: {plan.quality_status}",
            f"- stale: {plan.stale_status}",
            f"- status reasons: {_join_preview(plan.status_reasons)}",
            f"- active/total documents: {plan.active_documents_count}/{plan.total_documents_count}",
            f"- document cards: {plan.document_cards_count}",
            f"- sections: {plan.sections_count}",
            f"- chunks: {plan.chunks_count}",
            f"- active document IDs: {_preview(plan.active_document_ids)}",
            f"- active document keys: {_preview(plan.active_document_keys)}",
            f"- versions: {_preview(tuple(str(item) for item in plan.versions))}",
            f"- duplicate active document keys: {len(plan.duplicate_active_document_keys)}",
            f"- latest crawled: {plan.latest_crawled_at or 'not available'}",
            f"- latest updated: {plan.latest_updated_at or 'not available'}",
            f"- source config fingerprint: {plan.current_source_configuration_fingerprint}",
            f"- baseline fingerprint: {plan.baseline_fingerprint}",
            f"- term statistics scope/risk: {plan.term_statistics_scope_risk}",
            f"- ready for execution: {_yes_no(plan.readiness)}",
            f"- blocking reasons: {_join_preview(plan.blocking_reasons)}",
            f"- warnings: {_join_preview(plan.warnings)}",
            "- automatic execution: disabled",
            "- Supabase writes: disabled",
            "- activation/reprocessing: not performed",
        ]
    )


def format_verification_text(
    verification: ManifestVerificationResult,
    *,
    drift: DriftComparisonResult | None = None,
    preconditions: ExecutionPreconditionResult | None = None,
) -> str:
    """Format compact verification output."""
    lines = [
        "Docs Reprocessing Manifest Verification",
        "",
        f"- valid: {_yes_no(verification.valid)}",
        f"- rollback capable: {_yes_no(verification.rollback_capable)}",
        f"- blocking reasons: {_join_preview(verification.blocking_reasons)}",
        f"- warnings: {_join_preview(verification.warnings)}",
        "- Supabase writes: disabled",
        "- activation/reprocessing: not performed",
    ]
    if drift is not None:
        lines.extend(
            [
                "",
                "Live Drift",
                "",
                f"- matches baseline: {_yes_no(drift.matches)}",
                f"- blocking reasons: {_join_preview(drift.blocking_reasons)}",
                f"- warnings: {_join_preview(drift.warnings)}",
            ]
        )
    if preconditions is not None:
        lines.extend(
            [
                "",
                "Execution Preconditions",
                "",
                f"- ready: {_yes_no(preconditions.ready)}",
                f"- blockers: {_join_preview(preconditions.blockers)}",
                f"- required owner confirmation: {preconditions.required_owner_confirmation}",
            ]
        )
    return "\n".join(lines)


async def _load_related_rows(
    client: Any,
    table: str,
    document_ids: list[str],
    *,
    select: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for document_id in [item for item in document_ids if item]:
        rows.extend(
            await client.select(
                table,
                params={"select": select, "document_id": f"eq.{document_id}", "limit": str(limit)},
            )
        )
    return rows


def _plan_blockers(
    *,
    scope: SourceScope,
    inventory: SourceInventory,
    active_docs: tuple[dict[str, Any], ...],
    duplicate_keys: tuple[str, ...],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not scope.registered:
        blockers.append("source is not registered")
    if not inventory.workspace_id:
        blockers.append("workspace is not resolved")
    if not active_docs:
        blockers.append("source has no active documents")
    if duplicate_keys:
        blockers.append("duplicate active document keys exist")
    if len(inventory.chunks) <= 0:
        blockers.append("source has no chunks")
    return tuple(blockers)


def _plan_warnings(health: DocsSourceHealth | None, inventory: SourceInventory) -> tuple[str, ...]:
    warnings: list[str] = []
    if health is None:
        warnings.append("health status was not available")
    elif health.current_status in {"warning", "failed"}:
        warnings.append(f"current source health is {health.current_status}: {health.status_reason}")
    if inventory.term_statistics_count is None:
        warnings.append("term_statistics count is not available")
    warnings.append(f"expected ingestion version: {EXTERNAL_DOCS_VERSION}")
    warnings.append(f"expected chunk quality version: {EXTERNAL_CHUNK_QUALITY_VERSION}")
    return tuple(warnings)


def _health_for_scope(report: DocsHealthReport | None, scope: SourceScope) -> DocsSourceHealth | None:
    if report is None:
        return None
    for row in report.sources:
        if row.service_id == scope.service_id or row.source_id == scope.source_id:
            return row
    return None


def _source_config_dict(source: ExternalDocSource | None) -> dict[str, object]:
    if source is None:
        return {}
    return {
        "source_id": source.name,
        "source_kind": source.source_kind,
        "allowed_domains": list(source.allowed_domains),
        "start_urls": list(source.start_urls),
        "allow_patterns": list(source.allow_patterns),
        "deny_patterns": list(source.deny_patterns),
        "crawl_depth": source.crawl_depth,
        "max_pages": source.max_pages,
        "refresh_days": source.refresh_days,
    }


def _manifest_rows(inventory: SourceInventory) -> dict[str, object]:
    return {
        "documents": [_sanitize_for_manifest(row) for row in inventory.documents],
        "document_cards": [_sanitize_for_manifest(row) for row in inventory.document_cards],
        "sections": [_sanitize_for_manifest(row) for row in inventory.sections],
        "chunks": [_sanitize_for_manifest(row) for row in inventory.chunks],
    }


def _export_rows_complete(inventory: SourceInventory) -> bool:
    if not inventory.documents:
        return False
    cards_complete = all("card_embedding" in row for row in inventory.document_cards) if inventory.document_cards else False
    sections_complete = all("section_embedding" in row for row in inventory.sections) if inventory.sections else False
    chunks_complete = all("embedding" in row and "content" in row for row in inventory.chunks) if inventory.chunks else False
    return cards_complete and sections_complete and chunks_complete


def _relationships(inventory: SourceInventory) -> dict[str, object]:
    result: dict[str, dict[str, int]] = {}
    for document in inventory.documents:
        document_id = str(document.get("id") or "")
        if not document_id:
            continue
        result[document_id] = {
            "document_cards": sum(1 for row in inventory.document_cards if row.get("document_id") == document_id),
            "sections": sum(1 for row in inventory.sections if row.get("document_id") == document_id),
            "chunks": sum(1 for row in inventory.chunks if row.get("document_id") == document_id),
        }
    return result


def _relationship_total(relationships: dict[str, object], entity: str) -> int:
    total = 0
    for value in relationships.values():
        if isinstance(value, dict):
            total += int(value.get(entity) or 0)
    return total


def _document_status_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        result[status] = result.get(status, 0) + 1
    return result


def _duplicate_values(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)


def _latest_timestamp(rows: Iterable[dict[str, Any]], key: str) -> str | None:
    values = [str(row.get(key) or "") for row in rows if row.get(key)]
    return max(values) if values else None


def _latest_metadata_timestamp(rows: Iterable[dict[str, Any]], key: str) -> str | None:
    values = [str(_metadata(row).get(key) or "") for row in rows if _metadata(row).get(key)]
    return max(values) if values else None


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    return value if isinstance(value, dict) else {}


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


def _payload_checksum(payload: dict[str, Any]) -> str:
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
