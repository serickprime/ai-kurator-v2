import asyncio
import json

import pytest

from scripts import plan_docs_reprocessing as cli

from app.docs_registry.reprocessing_plan import (
    DocsReprocessingPlanError,
    SourceInventory,
    SourceScope,
    build_baseline_manifest,
    build_reprocessing_plan,
    compare_manifest_to_plan,
    compute_baseline_fingerprint,
    format_plan_text,
    resolve_source_scope,
    validate_execution_preconditions,
    verify_manifest,
    write_manifest_atomic,
)
from app.service_registry.docs_health import DocsHealthPolicy, build_docs_health_report
from app.service_registry.types import ServiceDocsStatus


def test_openrouter_plan_is_read_only_and_execution_disabled() -> None:
    plan = _plan("openrouter", "openrouter_docs")

    assert plan.service_id == "openrouter"
    assert plan.source_id == "openrouter_docs"
    assert plan.mode == "read-only"
    assert plan.active_documents_count == 2
    assert plan.document_cards_count == 2
    assert plan.sections_count == 2
    assert plan.chunks_count == 2
    assert plan.duplicate_active_document_keys == ()
    assert plan.automatic_execution_allowed is False


def test_telegram_plan_with_large_counts_formats_compactly() -> None:
    inventory = _inventory(
        "telegram_bot_api_docs",
        documents=[
            _document(f"doc-{index}", "telegram_bot_api_docs", document_key=f"https://core.telegram.org/bots/api#{index}")
            for index in range(12)
        ],
        chunks=[_chunk(f"chunk-{index}", f"doc-{index % 12}") for index in range(50)],
    )
    plan = build_reprocessing_plan(
        scope=_scope("telegram_bot_api", "telegram_bot_api_docs"),
        inventory=inventory,
        health_report=_health_report("telegram_bot_api", "telegram_bot_api_docs"),
    )

    text = format_plan_text(plan)

    assert plan.active_documents_count == 12
    assert "active document IDs: doc-0, doc-1, doc-2, doc-3, doc-4 (+7 more)" in text
    assert "automatic execution: disabled" in text


def test_unknown_service_fail_closed(tmp_path) -> None:
    registry_path, external_path, candidates_path = _scope_config(tmp_path=tmp_path)

    with pytest.raises(DocsReprocessingPlanError):
        resolve_source_scope(
            "missing",
            registry_config_path=registry_path,
            external_config_path=external_path,
            candidates_config_path=candidates_path,
        )


def test_scope_resolution_uses_candidate_catalog(tmp_path) -> None:
    registry_path, external_path, candidates_path = _scope_config(tmp_path=tmp_path)

    scope = resolve_source_scope(
        "openrouter",
        registry_config_path=registry_path,
        external_config_path=external_path,
        candidates_config_path=candidates_path,
    )

    assert scope.service_id == "openrouter"
    assert scope.source_id == "openrouter_docs"


def test_scope_resolution_rejects_source_mismatch(tmp_path) -> None:
    registry_path, external_path, candidates_path = _scope_config(tmp_path=tmp_path)

    with pytest.raises(DocsReprocessingPlanError, match="service/source mismatch"):
        resolve_source_scope(
            "openrouter",
            source_id="telegram_bot_api_docs",
            registry_config_path=registry_path,
            external_config_path=external_path,
            candidates_config_path=candidates_path,
        )


def test_scope_resolution_does_not_choose_ambiguous_source(tmp_path) -> None:
    registry_path, external_path, candidates_path = _scope_config(tmp_path=tmp_path, ambiguous=True)

    with pytest.raises(DocsReprocessingPlanError, match="multiple sources found"):
        resolve_source_scope(
            "openrouter",
            registry_config_path=registry_path,
            external_config_path=external_path,
            candidates_config_path=candidates_path,
        )


def test_duplicate_active_document_keys_block_readiness() -> None:
    inventory = _inventory(
        "openrouter_docs",
        documents=[
            _document("doc-1", "openrouter_docs", document_key="https://openrouter.ai/docs"),
            _document("doc-2", "openrouter_docs", document_key="https://openrouter.ai/docs"),
        ],
    )

    plan = build_reprocessing_plan(
        scope=_scope("openrouter", "openrouter_docs"),
        inventory=inventory,
        health_report=_health_report("openrouter", "openrouter_docs"),
    )

    assert plan.readiness is False
    assert plan.duplicate_active_document_keys == ("https://openrouter.ai/docs",)
    assert "duplicate active document keys exist" in plan.blocking_reasons


def test_baseline_fingerprint_changes_when_active_key_changes() -> None:
    base = compute_baseline_fingerprint(
        service_id="openrouter",
        source_id="openrouter_docs",
        workspace_id="workspace-1",
        active_document_ids=("doc-1",),
        active_document_keys=("https://openrouter.ai/docs",),
        versions=(1,),
        content_hashes=("hash-1",),
        ingestion_signatures=("sig-1",),
        counts={"active_documents": 1, "total_documents": 1, "document_cards": 1, "sections": 1, "chunks": 1},
        source_config_fingerprint="cfg",
    )
    changed = compute_baseline_fingerprint(
        service_id="openrouter",
        source_id="openrouter_docs",
        workspace_id="workspace-1",
        active_document_ids=("doc-1",),
        active_document_keys=("https://openrouter.ai/docs/quickstart",),
        versions=(1,),
        content_hashes=("hash-1",),
        ingestion_signatures=("sig-1",),
        counts={"active_documents": 1, "total_documents": 1, "document_cards": 1, "sections": 1, "chunks": 1},
        source_config_fingerprint="cfg",
    )

    assert base != changed


def test_export_manifest_contains_only_target_source_and_no_secret_fields(tmp_path) -> None:
    plan = _plan("openrouter", "openrouter_docs", include_embeddings=True)
    inventory = _inventory("openrouter_docs", include_embeddings=True)
    inventory.documents[0]["metadata"]["api_key"] = "should-not-export"
    manifest = build_baseline_manifest(plan=plan, inventory=inventory)
    output = tmp_path / "openrouter-baseline.json"

    write_manifest_atomic(manifest, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert data["service_id"] == "openrouter"
    assert data["source_id"] == "openrouter_docs"
    assert data["checksum"]
    assert data["completeness"]["rollback_capable"] is True
    assert all(row["metadata"]["source_name"] == "openrouter_docs" for row in data["rows"]["documents"])
    assert "api_key" not in json.dumps(data)


def test_export_does_not_overwrite_without_force(tmp_path) -> None:
    manifest = build_baseline_manifest(
        plan=_plan("openrouter", "openrouter_docs", include_embeddings=True),
        inventory=_inventory("openrouter_docs", include_embeddings=True),
    )
    output = tmp_path / "baseline.json"
    write_manifest_atomic(manifest, output)

    with pytest.raises(DocsReprocessingPlanError):
        write_manifest_atomic(manifest, output)

    write_manifest_atomic(manifest, output, force=True)
    assert output.exists()


def test_export_refuses_paths_inside_current_repository(tmp_path, monkeypatch) -> None:
    manifest = build_baseline_manifest(
        plan=_plan("openrouter", "openrouter_docs", include_embeddings=True),
        inventory=_inventory("openrouter_docs", include_embeddings=True),
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(DocsReprocessingPlanError, match="outside the Git repository"):
        write_manifest_atomic(manifest, tmp_path / "baseline.json")


def test_manifest_verification_accepts_valid_manifest() -> None:
    manifest = build_baseline_manifest(
        plan=_plan("openrouter", "openrouter_docs", include_embeddings=True),
        inventory=_inventory("openrouter_docs", include_embeddings=True),
    )

    result = verify_manifest(manifest, expected_service="openrouter", expected_source="openrouter_docs")

    assert result.valid is True
    assert result.rollback_capable is True
    assert result.blocking_reasons == ()


def test_manifest_verification_rejects_corrupted_checksum() -> None:
    manifest = build_baseline_manifest(
        plan=_plan("openrouter", "openrouter_docs", include_embeddings=True),
        inventory=_inventory("openrouter_docs", include_embeddings=True),
    )
    manifest["counts"]["chunks"] = 99

    result = verify_manifest(manifest)

    assert result.valid is False
    assert "manifest checksum mismatch" in result.blocking_reasons


def test_manifest_verification_rejects_incomplete_backup_as_not_rollback_capable() -> None:
    manifest = build_baseline_manifest(
        plan=_plan("openrouter", "openrouter_docs"),
        inventory=_inventory("openrouter_docs"),
        include_rows=False,
    )

    result = verify_manifest(manifest)

    assert result.valid is False
    assert result.rollback_capable is False
    assert "manifest is not rollback-capable" in result.blocking_reasons


def test_drift_detection_passes_for_matching_live_plan() -> None:
    plan = _plan("openrouter", "openrouter_docs", include_embeddings=True)
    manifest = build_baseline_manifest(plan=plan, inventory=_inventory("openrouter_docs", include_embeddings=True))

    drift = compare_manifest_to_plan(manifest, plan)

    assert drift.matches is True
    assert drift.blocking_reasons == ()


def test_drift_detection_blocks_count_changes() -> None:
    plan = _plan("openrouter", "openrouter_docs", include_embeddings=True)
    manifest = build_baseline_manifest(plan=plan, inventory=_inventory("openrouter_docs", include_embeddings=True))
    changed_plan = _plan("openrouter", "openrouter_docs", documents=3, include_embeddings=True)

    drift = compare_manifest_to_plan(manifest, changed_plan)

    assert drift.matches is False
    assert "baseline fingerprint changed" in drift.blocking_reasons
    assert "source counts changed" in drift.blocking_reasons


def test_precondition_validator_blocks_without_manifest() -> None:
    result = validate_execution_preconditions(
        manifest_result=None,
        drift_result=None,
        current_plan=_plan("openrouter", "openrouter_docs"),
        runtime_available=True,
    )

    assert result.ready is False
    assert "baseline manifest is required" in result.blockers
    assert "explicit owner confirmation is required before any execution" in result.blockers


def test_precondition_validator_blocks_runtime_unavailable_and_duplicates() -> None:
    plan = build_reprocessing_plan(
        scope=_scope("openrouter", "openrouter_docs"),
        inventory=_inventory(
            "openrouter_docs",
            documents=[
                _document("doc-1", "openrouter_docs", document_key="same"),
                _document("doc-2", "openrouter_docs", document_key="same"),
            ],
        ),
        health_report=_health_report("openrouter", "openrouter_docs"),
    )
    manifest = build_baseline_manifest(plan=plan, inventory=_inventory("openrouter_docs", include_embeddings=True))
    verification = verify_manifest(manifest)
    drift = compare_manifest_to_plan(manifest, plan)

    result = validate_execution_preconditions(
        manifest_result=verification,
        drift_result=drift,
        current_plan=plan,
        runtime_available=False,
    )

    assert result.ready is False
    assert "duplicate active document keys must be zero" in result.blockers
    assert "runtime connectivity must be available" in result.blockers


def test_text_output_contains_safety_boundaries() -> None:
    text = format_plan_text(_plan("openrouter", "openrouter_docs"))

    assert "mode: read-only" in text
    assert "Supabase writes: disabled" in text
    assert "activation/reprocessing: not performed" in text


def test_cli_runtime_unavailable_does_not_show_traceback(monkeypatch, capsys) -> None:
    class Args:
        verify = None
        service = "openrouter"
        export = None
        format = "text"

    async def fail_build(*_args, **_kwargs):
        raise RuntimeError("full runtime detail should not be printed")

    monkeypatch.setattr(cli, "parse_args", lambda: Args())
    monkeypatch.setattr(cli, "_build_live_plan", fail_build)

    result = asyncio.run(cli.main_async())
    captured = capsys.readouterr()

    assert result == 2
    assert "runtime unavailable: RuntimeError" in captured.err
    assert "Traceback" not in captured.err
    assert "full runtime detail" not in captured.err


def _scope(service_id: str, source_id: str) -> SourceScope:
    return SourceScope(
        service_id=service_id,
        display_name=service_id.replace("_", " ").title(),
        source_id=source_id,
        source_title=source_id.replace("_", " ").title(),
        source_type="active_candidate_docs",
        registered=True,
        source_config={
            "source_id": source_id,
            "allowed_domains": ["example.com"],
            "start_urls": [f"https://example.com/{source_id}"],
            "max_pages": 25,
        },
    )


def _plan(
    service_id: str,
    source_id: str,
    *,
    documents: int = 2,
    include_embeddings: bool = False,
):
    inventory = _inventory(source_id, documents=documents, include_embeddings=include_embeddings)
    return build_reprocessing_plan(
        scope=_scope(service_id, source_id),
        inventory=inventory,
        health_report=_health_report(service_id, source_id),
    )


def _inventory(
    source_id: str,
    *,
    include_embeddings: bool = False,
    chunks: list[dict[str, object]] | None = None,
    documents: list[dict[str, object]] | int = 2,
) -> SourceInventory:
    if isinstance(documents, int):
        docs = [_document(f"doc-{index}", source_id) for index in range(1, documents + 1)]
    else:
        docs = documents
    cards = [_card(f"card-{index}", doc["id"], include_embeddings=include_embeddings) for index, doc in enumerate(docs)]
    section_rows = [_section(f"section-{index}", doc["id"], include_embeddings=include_embeddings) for index, doc in enumerate(docs)]
    chunk_rows = chunks or [_chunk(f"chunk-{index}", doc["id"], include_embeddings=include_embeddings) for index, doc in enumerate(docs)]
    return SourceInventory(
        workspace_id="workspace-1",
        workspace_name="team",
        documents=tuple(docs),
        document_cards=tuple(cards),
        sections=tuple(section_rows),
        chunks=tuple(chunk_rows),
        term_statistics_count=100,
    )


def _document(
    document_id: str,
    source_id: str,
    *,
    document_key: str | None = None,
) -> dict[str, object]:
    return {
        "id": document_id,
        "workspace_id": "workspace-1",
        "source_type": "external_docs",
        "filename": f"{document_id}.html",
        "document_key": document_key or f"https://example.com/{source_id}/{document_id}",
        "title": f"Doc {document_id}",
        "version": 1,
        "status": "active",
        "content_hash": f"hash-{document_id}",
        "metadata": {
            "source_name": source_id,
            "crawled_at": "2026-07-02T00:00:00Z",
            "ingestion": {"signature": f"sig-{document_id}", "pipeline_version": "external-docs-v1"},
        },
        "created_at": "2026-07-02T00:00:00Z",
        "updated_at": "2026-07-02T00:10:00Z",
    }


def _card(card_id: str, document_id: str, *, include_embeddings: bool) -> dict[str, object]:
    row: dict[str, object] = {"id": card_id, "document_id": document_id, "workspace_id": "workspace-1"}
    if include_embeddings:
        row["card_embedding"] = [0.1, 0.2]
    return row


def _section(section_id: str, document_id: str, *, include_embeddings: bool) -> dict[str, object]:
    row: dict[str, object] = {"id": section_id, "document_id": document_id, "workspace_id": "workspace-1"}
    if include_embeddings:
        row["section_embedding"] = [0.1, 0.2]
    return row


def _chunk(chunk_id: str, document_id: str, *, include_embeddings: bool = False) -> dict[str, object]:
    row: dict[str, object] = {
        "id": chunk_id,
        "document_id": document_id,
        "workspace_id": "workspace-1",
        "content": "Useful docs content",
    }
    if include_embeddings:
        row["embedding"] = [0.1, 0.2]
    return row


def _health_report(service_id: str, source_id: str):
    return build_docs_health_report(
        statuses=(
            ServiceDocsStatus(
                service_id=service_id,
                display_name=service_id.replace("_", " ").title(),
                aliases=(service_id,),
                docs_source=source_id,
                configured_status="enabled",
                docs_status="indexed",
                active_docs_count=2,
                active_chunks_count=2,
                quality_status="WARN" if service_id == "openrouter" else "FAIL",
                docs_source_configured=False,
                notes=("quality gate returned WARN",) if service_id == "openrouter" else ("quality gate returned FAIL",),
            ),
        ),
        documents=[_document("doc-1", source_id), _document("doc-2", source_id)],
        policy=DocsHealthPolicy(default_stale_after_days=30),
        runtime_status="available",
    )


def _scope_config(tmp_path, *, ambiguous: bool = False):
    if tmp_path is None:
        pytest.skip("tmp_path fixture required")
    registry_path = tmp_path / "service_docs_registry.yaml"
    external_path = tmp_path / "external_docs.yaml"
    candidates_path = tmp_path / "docs_source_candidates.yaml"
    registry_source = "openrouter_alt_docs" if ambiguous else "openrouter_docs"
    registry_path.write_text(
        "\n".join(
            [
                "services:",
                "  - service_id: openrouter",
                "    display_name: OpenRouter",
                "    aliases:",
                "      - openrouter",
                f"    docs_source: {registry_source}",
                "    status: enabled",
            ]
        ),
        encoding="utf-8",
    )
    external_path.write_text(
        "\n".join(
            [
                "sources:",
                f"  - name: {registry_source}",
                "    source_kind: external_docs",
                "    allowed_domains:",
                "      - openrouter.ai",
                "    start_urls:",
                "      - https://openrouter.ai/docs",
                "    max_pages: 25",
                "    crawl_depth: 2",
                "    refresh_days: 14",
            ]
        ),
        encoding="utf-8",
    )
    candidates_path.write_text(
        "\n".join(
            [
                "candidates:",
                "  - service_id: openrouter",
                "    display_name: OpenRouter",
                "    aliases:",
                "      - openrouter",
                "    docs_source: openrouter_docs",
                "    official_start_urls:",
                "      - https://openrouter.ai/docs",
                "    allowed_domains:",
                "      - openrouter.ai",
                "    allow_patterns:",
                "      - '^https://openrouter\\.ai/docs'",
                "    deny_patterns:",
                "      - /login",
                "    max_pages: 25",
                "    crawl_depth: 2",
                "    risk_level: low",
            ]
        ),
        encoding="utf-8",
    )
    return registry_path, external_path, candidates_path
