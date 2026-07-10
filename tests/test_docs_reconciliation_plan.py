import asyncio
import json

import pytest

from scripts import plan_docs_reconciliation as cli

from app.docs_registry.reconciliation_plan import (
    DocsReconciliationPlanError,
    DiscoveredDocument,
    build_discovered_snapshot,
    build_reconciliation_plan,
    build_review_export,
    compute_active_inventory_fingerprint,
    compute_discovered_snapshot_fingerprint,
    format_reconciliation_plan_text,
    verify_discovered_snapshot,
    write_json_manifest_atomic,
)
from app.docs_registry.reprocessing_plan import SourceInventory, SourceScope


def test_common_keys_are_active_and_discovered() -> None:
    plan = _plan(
        active=[_doc("doc-1", "https://example.com/docs/quickstart")],
        discovered=[_discovered("https://example.com/docs/quickstart")],
    )

    assert plan.readiness is True
    assert plan.common_keys == ("https://example.com/docs/quickstart",)
    assert _classification(plan, "https://example.com/docs/quickstart") == "active_and_discovered"
    assert plan.automatic_archive_allowed is False


def test_newly_discovered_keys_do_not_trigger_writes() -> None:
    plan = _plan(
        active=[_doc("doc-1", "https://example.com/docs/quickstart")],
        discovered=[
            _discovered("https://example.com/docs/quickstart"),
            _discovered("https://example.com/docs/new-page"),
        ],
    )

    assert plan.newly_discovered_keys == ("https://example.com/docs/new-page",)
    assert _classification(plan, "https://example.com/docs/new-page") == "newly_discovered"
    assert plan.automatic_archive_allowed is False
    assert plan.to_dict()["supabase_writes"] == "disabled"


def test_missing_active_keys_are_not_archived_automatically() -> None:
    plan = _plan(
        active=[
            _doc("doc-1", "https://example.com/docs/quickstart"),
            _doc("doc-2", "https://example.com/docs/old-page"),
        ],
        discovered=[_discovered("https://example.com/docs/quickstart")],
    )

    assert plan.readiness is False
    assert plan.active_missing_from_snapshot_keys == ("https://example.com/docs/old-page",)
    assert _classification(plan, "https://example.com/docs/old-page") == "active_missing_from_snapshot"
    assert "owner review is required before any archive decision" in plan.blockers


def test_possible_superseded_requires_owner_review() -> None:
    plan = _plan(
        active=[_doc("doc-1", "https://example.com/docs/mcp-server", title="MCP Server")],
        discovered=[
            _discovered("https://example.com/docs/guides/overview/mcp-server", title="MCP Server"),
        ],
    )

    item = _item(plan, "https://example.com/docs/mcp-server")
    assert item.classification == "possible_superseded"
    assert item.successor_candidates == ("https://example.com/docs/guides/overview/mcp-server",)
    assert item.owner_review_required is True
    assert plan.automatic_archive_allowed is False


def test_ambiguous_successor_blocks_readiness() -> None:
    plan = _plan(
        active=[_doc("doc-1", "https://example.com/docs/old-page", title="Old Page")],
        discovered=[
            _discovered("https://example.com/docs/guides/old-page", title="Old Page"),
            _discovered("https://example.com/docs/reference/old-page", title="Old Page"),
        ],
    )

    item = _item(plan, "https://example.com/docs/old-page")
    assert item.classification == "ambiguous_needs_review"
    assert len(item.successor_candidates) == 2
    assert plan.readiness is False
    assert "ambiguous successor candidates require owner review" in plan.blockers


def test_snapshot_scope_mismatch_fails_closed() -> None:
    scope = _scope("example", "example_docs")
    snapshot = build_discovered_snapshot(
        scope=_scope("other", "other_docs"),
        workspace_id="workspace-1",
        workspace_name="team",
        discovered=[_discovered("https://example.com/docs/page")],
    )

    result = verify_discovered_snapshot(
        snapshot,
        scope=scope,
        workspace_id="workspace-1",
        workspace_name="team",
    )

    assert result.valid is False
    assert "snapshot service_id does not match scope" in result.blocking_reasons
    assert "snapshot source_id does not match scope" in result.blocking_reasons


def test_snapshot_checksum_validation_rejects_corruption() -> None:
    snapshot = _snapshot([_discovered("https://example.com/docs/page")])

    assert _verify(snapshot).valid is True
    snapshot["discovered_keys"] = ["https://example.com/docs/changed"]
    result = _verify(snapshot)

    assert result.valid is False
    assert "snapshot checksum mismatch" in result.blocking_reasons


def test_empty_snapshot_blocks_reconciliation() -> None:
    snapshot = _snapshot([])
    result = _verify(snapshot)

    assert result.valid is False
    assert "snapshot has no discovered document keys" in result.blocking_reasons
    with pytest.raises(DocsReconciliationPlanError):
        build_reconciliation_plan(
            scope=_scope("example", "example_docs"),
            inventory=_inventory([_doc("doc-1", "https://example.com/docs/page")]),
            snapshot=snapshot,
        )


def test_config_drift_blocks_snapshot() -> None:
    snapshot = _snapshot([_discovered("https://example.com/docs/page")])
    changed_scope = _scope("example", "example_docs", allow_patterns=[r"^https://example\.com/reference"])

    result = verify_discovered_snapshot(
        snapshot,
        scope=changed_scope,
        workspace_id="workspace-1",
        workspace_name="team",
    )

    assert result.valid is False
    assert "source configuration fingerprint changed" in result.blocking_reasons


def test_fingerprints_are_deterministic_and_change_with_keys() -> None:
    scope = _scope("example", "example_docs")
    active = [_doc("doc-1", "https://example.com/docs/a")]
    first = compute_active_inventory_fingerprint(
        service_id="example",
        source_id="example_docs",
        workspace_id="workspace-1",
        active_documents=active,
        source_config_fingerprint="cfg",
    )
    second = compute_active_inventory_fingerprint(
        service_id="example",
        source_id="example_docs",
        workspace_id="workspace-1",
        active_documents=active,
        source_config_fingerprint="cfg",
    )
    changed = compute_discovered_snapshot_fingerprint(
        service_id=scope.service_id,
        source_id=scope.source_id,
        workspace_id="workspace-1",
        discovered_documents=[_discovered("https://example.com/docs/b").to_dict()],
        source_config_fingerprint="cfg",
    )

    assert first == second
    assert first != changed


def test_review_export_is_atomic_secret_free_and_protected(tmp_path, monkeypatch) -> None:
    plan = _plan(
        active=[_doc("doc-1", "https://example.com/docs/old-page")],
        discovered=[_discovered("https://example.com/docs/new-page")],
    )
    export = build_review_export(plan)
    output = tmp_path / "review.json"

    write_json_manifest_atomic(export, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert output.exists()
    assert not output.with_name(output.name + ".tmp").exists()
    assert data["automatic_archive_allowed"] is False
    assert data["decisions"][0]["owner_decision"] == "needs_more_review"
    assert "content" not in json.dumps(data).casefold()
    assert "embedding" not in json.dumps(data).casefold()
    with pytest.raises(DocsReconciliationPlanError, match="already exists"):
        write_json_manifest_atomic(export, output)

    monkeypatch.chdir(tmp_path)
    with pytest.raises(DocsReconciliationPlanError, match="outside the Git repository"):
        write_json_manifest_atomic(export, tmp_path / "inside.json")


def test_openrouter_pilot_fixture_classifies_current_incident_generically() -> None:
    plan = _openrouter_pilot_plan()

    assert _classification(plan, "https://openrouter.ai/docs/mcp-server") == "possible_superseded"
    assert _item(plan, "https://openrouter.ai/docs/mcp-server").successor_candidates == (
        "https://openrouter.ai/docs/guides/overview/mcp-server",
    )
    assert _classification(plan, "https://openrouter.ai/docs/guides/overview/mcp-server") == "newly_discovered"
    assert _classification(plan, "https://openrouter.ai/docs/app-attribution") == "active_missing_from_snapshot"
    assert _classification(plan, "https://openrouter.ai/docs/features/service-tiers") == "active_missing_from_snapshot"
    assert _classification(plan, "https://openrouter.ai/docs/quickstart") == "active_and_discovered"
    assert plan.automatic_archive_allowed is False


def test_neutral_exampledocs_source_uses_same_superseded_logic() -> None:
    plan = _plan(
        service_id="exampledocs",
        source_id="exampledocs_docs",
        active=[_doc("doc-1", "https://example.com/docs/install", title="Install")],
        discovered=[_discovered("https://example.com/docs/guides/install", title="Install")],
    )

    item = _item(plan, "https://example.com/docs/install")
    assert item.classification == "possible_superseded"
    assert item.successor_candidates == ("https://example.com/docs/guides/install",)


def test_canonical_collision_blocks_readiness() -> None:
    plan = _plan(
        active=[],
        discovered=[
            _discovered("https://example.com/docs/guides/install"),
            _discovered("https://example.com/docs/reference/install"),
        ],
    )

    assert plan.readiness is False
    assert "canonical collisions require owner review" in plan.blockers
    assert len(plan.canonical_collisions) == 2


def test_text_and_json_output_are_compact_and_structured() -> None:
    plan = _openrouter_pilot_plan()
    text = format_reconciliation_plan_text(plan)
    data = plan.to_dict()

    assert "Docs Reconciliation Plan" in text
    assert "Mode" not in text
    assert "mode: read-only" in text
    assert "Automatic archive" not in text
    assert "automatic archive: disabled" in text
    assert data["automatic_archive_allowed"] is False
    assert data["supabase_writes"] == "disabled"
    assert len(text.splitlines()) < 40


def test_cli_expected_validation_error_has_no_traceback(monkeypatch, capsys) -> None:
    class Args:
        service = "example"
        source = None
        snapshot = "snapshot.json"
        format = "text"
        review_export = None
        force = False

    async def fail_build(_args):
        raise DocsReconciliationPlanError("snapshot checksum mismatch")

    monkeypatch.setattr(cli, "parse_args", lambda: Args())
    monkeypatch.setattr(cli, "_build_plan", fail_build)

    result = asyncio.run(cli.main_async())
    captured = capsys.readouterr()

    assert result == 2
    assert "snapshot checksum mismatch" in captured.err
    assert "Traceback" not in captured.err


def test_cli_review_export_does_not_apply_writes(monkeypatch, tmp_path, capsys) -> None:
    plan = _plan(
        active=[_doc("doc-1", "https://example.com/docs/old-page")],
        discovered=[_discovered("https://example.com/docs/new-page")],
    )
    output = tmp_path / "review.json"

    class Args:
        service = "example"
        source = None
        snapshot = "snapshot.json"
        format = "json"
        review_export = output
        force = False

    async def fake_build(_args):
        return plan

    monkeypatch.setattr(cli, "parse_args", lambda: Args())
    monkeypatch.setattr(cli, "_build_plan", fake_build)

    result = asyncio.run(cli.main_async())
    captured = capsys.readouterr()

    assert result == 2
    assert output.exists()
    assert "automatic_archive_allowed" in captured.out
    assert "Traceback" not in captured.err


def _openrouter_pilot_plan():
    active = [
        _doc("doc-1", "https://openrouter.ai/docs/mcp-server", title="MCP Server"),
        _doc("doc-2", "https://openrouter.ai/docs/app-attribution", title="App Attribution"),
        _doc("doc-3", "https://openrouter.ai/docs/features/service-tiers", title="Service Tiers"),
        _doc("doc-4", "https://openrouter.ai/docs/quickstart", title="Quickstart"),
        _doc("doc-5", "https://openrouter.ai/docs/api-reference/overview", title="API Reference"),
    ]
    discovered = [
        _discovered("https://openrouter.ai/docs/guides/overview/mcp-server", title="MCP Server"),
        _discovered("https://openrouter.ai/docs/guides/features/tool-calling", title="Tool Calling"),
        _discovered("https://openrouter.ai/docs/guides/features/workspaces", title="Workspaces"),
        _discovered(
            "https://openrouter.ai/docs/guides/features/workspaces/workspace-budgets",
            title="Workspace Budgets",
        ),
        _discovered("https://openrouter.ai/docs/quickstart", title="Quickstart"),
        _discovered("https://openrouter.ai/docs/api-reference/overview", title="API Reference"),
    ]
    return _plan(
        service_id="openrouter",
        source_id="openrouter_docs",
        active=active,
        discovered=discovered,
        allowed_domains=["openrouter.ai"],
        allow_patterns=[r"^https://openrouter\.ai/docs"],
    )


def _plan(
    *,
    active: list[dict[str, object]],
    discovered: list[DiscoveredDocument],
    service_id: str = "example",
    source_id: str = "example_docs",
    allowed_domains: list[str] | None = None,
    allow_patterns: list[str] | None = None,
):
    scope = _scope(service_id, source_id, allowed_domains=allowed_domains, allow_patterns=allow_patterns)
    snapshot = build_discovered_snapshot(
        scope=scope,
        workspace_id="workspace-1",
        workspace_name="team",
        discovered=discovered,
    )
    return build_reconciliation_plan(
        scope=scope,
        inventory=_inventory(active),
        snapshot=snapshot,
    )


def _scope(
    service_id: str,
    source_id: str,
    *,
    allowed_domains: list[str] | None = None,
    allow_patterns: list[str] | None = None,
) -> SourceScope:
    return SourceScope(
        service_id=service_id,
        display_name=service_id.replace("_", " ").title(),
        source_id=source_id,
        source_title=source_id.replace("_", " ").title(),
        source_type="external_docs",
        registered=True,
        source_config={
            "source_id": source_id,
            "allowed_domains": allowed_domains or ["example.com"],
            "start_urls": ["https://example.com/docs"],
            "allow_patterns": allow_patterns or [r"^https://example\.com/docs"],
            "deny_patterns": [r"/login"],
            "max_pages": 25,
        },
    )


def _inventory(documents: list[dict[str, object]]) -> SourceInventory:
    return SourceInventory(
        workspace_id="workspace-1",
        workspace_name="team",
        documents=tuple(documents),
        document_cards=(),
        sections=(),
        chunks=(),
        term_statistics_count=0,
    )


def _doc(document_id: str, key: str, *, title: str | None = None) -> dict[str, object]:
    return {
        "id": document_id,
        "workspace_id": "workspace-1",
        "source_type": "external_docs",
        "document_key": key,
        "title": title or key.rsplit("/", 1)[-1].replace("-", " ").title(),
        "version": 1,
        "status": "active",
        "content_hash": f"hash-{document_id}",
        "metadata": {"ingestion": {"signature": f"sig-{document_id}"}},
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:01:00Z",
    }


def _discovered(key: str, *, title: str | None = None) -> DiscoveredDocument:
    return DiscoveredDocument(
        document_key=key,
        canonical_url=key,
        title=title or key.rsplit("/", 1)[-1].replace("-", " ").title(),
        content_hash=f"hash-{key.rsplit('/', 1)[-1]}",
    )


def _snapshot(discovered: list[DiscoveredDocument]) -> dict[str, object]:
    return build_discovered_snapshot(
        scope=_scope("example", "example_docs"),
        workspace_id="workspace-1",
        workspace_name="team",
        discovered=discovered,
    )


def _verify(snapshot: dict[str, object]):
    return verify_discovered_snapshot(
        snapshot,
        scope=_scope("example", "example_docs"),
        workspace_id="workspace-1",
        workspace_name="team",
    )


def _item(plan, key: str):
    for item in plan.items:
        if item.document_key == key:
            return item
    raise AssertionError(f"missing item: {key}")


def _classification(plan, key: str) -> str:
    return _item(plan, key).classification
