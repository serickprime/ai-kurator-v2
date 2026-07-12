from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import cleanup_incomplete_external_doc_draft as cli
from app.db.repositories import DocumentRepository
from app.docs_registry.incomplete_draft_cleanup import (
    build_incomplete_draft_cleanup_plan,
    execute_incomplete_draft_cleanup,
    format_incomplete_draft_cleanup_plan_text,
)
from app.docs_registry.reprocessing_plan import (
    SourceInventory,
    SourceScope,
    _payload_checksum,
    build_baseline_manifest,
    build_reprocessing_plan,
)


def test_valid_preview_is_ready_and_performs_no_writes() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    repo = FakeCleanupRepository(fixture.live_inventory)

    assert plan.readiness is True
    assert plan.execution_requested is False
    assert plan.writes_enabled is False
    assert plan.fetch_requested is False
    assert plan.reprocessing_requested is False
    assert repo.delete_calls == []


def test_text_preview_contains_exact_target_and_protected_state() -> None:
    plan = _plan(_fixture())

    text = format_incomplete_draft_cleanup_plan_text(plan)

    assert "Incomplete External Doc Draft Cleanup Plan" in text
    assert "target: draft-doc" in text
    assert "target status/version: draft/2" in text
    assert "protected active: active-doc" in text
    assert "allowed draft-only delta: yes" in text
    assert "Supabase writes: disabled" in text


def test_json_preview_has_stable_structured_fields() -> None:
    plan = _plan(_fixture())
    payload = plan.to_dict()

    assert payload["target_count"] == 1
    assert payload["target"]["document_id"] == "draft-doc"  # type: ignore[index]
    assert payload["protected_active"]["document_id"] == "active-doc"  # type: ignore[index]
    assert payload["allowed_delta"]["sections"] == 2  # type: ignore[index]
    assert payload["term_statistics_refresh"] is False
    assert payload["expected_confirmation_phrase"] == plan.expected_confirmation_phrase


def test_execution_without_confirmation_is_blocked() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    repo = FakeCleanupRepository(fixture.live_inventory)

    result = asyncio.run(
        execute_incomplete_draft_cleanup(
            plan=plan,
            repository=repo,
            load_post_cleanup_inventory=repo.load_inventory,
            scope=fixture.scope,
            backup_manifest=fixture.backup,
            confirmation_phrase="",
        )
    )

    assert result.status == "blocked"
    assert repo.delete_calls == []


def test_exact_valid_execution_deletes_only_draft_subtree() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    repo = FakeCleanupRepository(fixture.live_inventory)

    result = asyncio.run(
        execute_incomplete_draft_cleanup(
            plan=plan,
            repository=repo,
            load_post_cleanup_inventory=repo.load_inventory,
            scope=fixture.scope,
            backup_manifest=fixture.backup,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    assert result.status == "cleaned"
    assert result.rows_deleted == 1
    assert result.target_absent is True
    assert result.target_children_absent is True
    assert result.protected_active_unchanged is True
    assert result.source_matches_baseline is True
    assert repo.delete_calls == [
        {
            "document_id": "draft-doc",
            "workspace_id": "workspace-1",
            "document_key": "https://docs.example.com/reference",
            "source_id": "example_docs_source",
            "expected_version": 2,
            "expected_content_hash": "hash-draft",
            "expected_ingestion_signature": "sig-draft",
        }
    ]


def test_active_predecessor_remains_unchanged_after_fake_execution() -> None:
    fixture = _fixture()
    repo = FakeCleanupRepository(fixture.live_inventory)
    plan = _plan(fixture)

    asyncio.run(
        execute_incomplete_draft_cleanup(
            plan=plan,
            repository=repo,
            load_post_cleanup_inventory=repo.load_inventory,
            scope=fixture.scope,
            backup_manifest=fixture.backup,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    active = _document(repo.inventory, "active-doc")
    assert active["status"] == "active"
    assert _counts(repo.inventory, "active-doc") == (1, 2, 2)


def test_post_cleanup_inventory_matches_baseline() -> None:
    fixture = _fixture()
    repo = FakeCleanupRepository(fixture.live_inventory)
    plan = _plan(fixture)

    result = asyncio.run(
        execute_incomplete_draft_cleanup(
            plan=plan,
            repository=repo,
            load_post_cleanup_inventory=repo.load_inventory,
            scope=fixture.scope,
            backup_manifest=fixture.backup,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    assert result.source_matches_baseline is True
    assert len(repo.inventory.documents) == 2
    assert len(repo.inventory.document_cards) == 2
    assert len(repo.inventory.sections) == 3
    assert len(repo.inventory.chunks) == 3


@pytest.mark.parametrize("status", ["active", "archived", "deleted"])
def test_non_draft_target_status_is_blocked(status: str) -> None:
    fixture = _fixture(draft_status=status)
    plan = _plan(fixture)

    assert plan.readiness is False
    assert "target_status_must_be_draft" in plan.blockers


def test_target_id_not_found_is_blocked() -> None:
    plan = _plan(_fixture(), document_ids=("missing-doc",))

    assert plan.readiness is False
    assert "target document ID not found" in plan.blockers


def test_more_than_one_document_id_is_rejected() -> None:
    plan = _plan(_fixture(), document_ids=("draft-doc", "other-draft"))

    assert plan.readiness is False
    assert "target count must be exactly one" in plan.blockers


def test_key_without_active_predecessor_is_blocked() -> None:
    fixture = _fixture(draft_key="https://docs.example.com/missing-active")
    plan = _plan(fixture)

    assert plan.readiness is False
    assert "protected_active_document_not_unique" in plan.blockers


def test_confirmation_phrase_changes_when_child_count_drifts() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    drifted = _with_extra_draft_section(fixture.live_inventory)
    drifted_fixture = replace(
        fixture,
        live_inventory=drifted,
        current_plan=build_reprocessing_plan(scope=fixture.scope, inventory=drifted),
    )
    drifted_plan = _plan(drifted_fixture)

    assert drifted_plan.readiness is True
    assert drifted_plan.target is not None
    assert drifted_plan.target.children.sections == 3
    assert drifted_plan.expected_confirmation_phrase != plan.expected_confirmation_phrase


def test_protected_active_predecessor_drift_is_blocked() -> None:
    fixture = _fixture(active_hash="changed")
    plan = _plan(fixture)

    assert plan.readiness is False
    assert "protected_active_document_changed" in plan.blockers


def test_invalid_backup_checksum_is_blocked() -> None:
    fixture = _fixture()
    backup = dict(fixture.backup)
    backup["counts"] = {**backup["counts"], "chunks": 999}
    fixture = replace(fixture, backup=backup)

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "manifest checksum mismatch" in plan.blockers


def test_non_rollback_capable_backup_is_blocked() -> None:
    fixture = _fixture(include_rows=False)
    plan = _plan(fixture)

    assert plan.readiness is False
    assert "manifest is not rollback-capable" in plan.blockers


def test_wrong_backup_scope_is_blocked() -> None:
    fixture = _fixture()
    backup = dict(fixture.backup)
    backup["source_id"] = "wrong_source"
    backup["checksum"] = _payload_checksum(backup)
    fixture = replace(fixture, backup=backup)

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "manifest source_id does not match expected source" in plan.blockers


def test_backup_already_contains_target_id_is_blocked() -> None:
    fixture = _fixture()
    rows = dict(fixture.backup["rows"])
    rows["documents"] = [*rows["documents"], _document(fixture.live_inventory, "draft-doc")]
    backup = {**fixture.backup, "rows": rows}
    backup["checksum"] = _payload_checksum(backup)

    plan = _plan(replace(fixture, backup=backup))

    assert plan.readiness is False
    assert "backup_already_contains_target_id" in plan.blockers


def test_broader_source_drift_is_blocked() -> None:
    fixture = _fixture(extra_live_doc=True)
    plan = _plan(fixture)

    assert plan.readiness is False
    assert "unexpected_broader_drift" in plan.blockers


def test_draft_only_delta_is_accepted() -> None:
    plan = _plan(_fixture())

    assert plan.readiness is True
    assert plan.allowed_delta is not None
    assert plan.allowed_delta.documents == 1
    assert plan.allowed_delta.document_cards == 1
    assert plan.allowed_delta.sections == 2
    assert plan.allowed_delta.chunks == 0


def test_arbitrary_url_input_is_unavailable(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--help"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "--url" not in output
    assert "--document-id" in output


def test_full_source_cleanup_is_unavailable(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--help"])

    output = capsys.readouterr().out
    assert "--all" not in output
    assert "--source-wide" not in output


def test_no_fetch_crawl_or_reprocessing_calls_occur_in_preview() -> None:
    plan = _plan(_fixture())

    assert plan.fetch_requested is False
    assert plan.reprocessing_requested is False
    assert plan.execution_requested is False


def test_term_statistics_refresh_is_never_called() -> None:
    fixture = _fixture()
    repo = FakeCleanupRepository(fixture.live_inventory)
    plan = _plan(fixture)

    result = asyncio.run(
        execute_incomplete_draft_cleanup(
            plan=plan,
            repository=repo,
            load_post_cleanup_inventory=repo.load_inventory,
            scope=fixture.scope,
            backup_manifest=fixture.backup,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    assert result.term_statistics_status == "not run"
    assert repo.refresh_calls == []


def test_delete_failure_returns_structured_partial_failure() -> None:
    fixture = _fixture()
    repo = FakeCleanupRepository(fixture.live_inventory, delete_error=RuntimeError("boom"))
    plan = _plan(fixture)

    result = asyncio.run(
        execute_incomplete_draft_cleanup(
            plan=plan,
            repository=repo,
            load_post_cleanup_inventory=repo.load_inventory,
            scope=fixture.scope,
            backup_manifest=fixture.backup,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    assert result.status == "partial_failure"
    assert result.partial_failure is True
    assert result.rollback_required is True


def test_no_automatic_retry() -> None:
    plan = _plan(_fixture())

    assert plan.automatic_retry is False


def test_no_automatic_rollback() -> None:
    plan = _plan(_fixture())

    assert plan.automatic_rollback is False


def test_exact_confirmation_phrase_is_required_and_state_bound() -> None:
    plan = _plan(_fixture())

    assert plan.expected_confirmation_phrase.startswith(
        "cleanup-incomplete-external-doc-draft:example_docs_source:draft-doc:"
    )
    assert len(plan.expected_confirmation_phrase.rsplit(":", 1)[-1]) == 12


def test_cli_help_documents_preview_default_and_safety_boundaries(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--help"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "Preview or execute exact cleanup" in output
    assert "--confirm-cleanup-incomplete-draft" in output
    assert "--confirmation-phrase" in output
    assert "--backup" in output


def test_incident_like_shape_preview_ready() -> None:
    fixture = _fixture(draft_sections=594, draft_chunks=0)
    plan = _plan(fixture)

    assert plan.readiness is True
    assert plan.target is not None
    assert plan.target.children.cards == 1
    assert plan.target.children.sections == 594
    assert plan.target.children.chunks == 0
    assert plan.expected_post_cleanup_counts == plan.baseline_counts


def test_cli_preview_path_does_not_execute(monkeypatch, capsys, tmp_path: Path) -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    captured: dict[str, object] = {}

    async def fake_build_live_plan(args):
        captured["document_id"] = args.document_id
        return plan, FakeClosableClient(), fixture.scope, fixture.backup, object()

    async def fail_execution(**_kwargs):
        raise AssertionError("execution must not run in preview")

    backup_path = tmp_path / "backup.json"
    backup_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "cleanup_incomplete_external_doc_draft.py",
            "--service",
            "example_docs",
            "--backup",
            str(backup_path),
            "--document-id",
            "draft-doc",
        ],
    )
    monkeypatch.setattr(cli, "_build_live_plan", fake_build_live_plan)
    monkeypatch.setattr(cli, "execute_incomplete_draft_cleanup", fail_execution)

    code = asyncio.run(cli.main_async())

    assert code == 0
    assert captured["document_id"] == ["draft-doc"]
    assert "mode: read-only" in capsys.readouterr().out


def test_repository_exact_delete_filters_all_required_fields() -> None:
    client = FakeDeleteClient()
    repository = DocumentRepository(client)  # type: ignore[arg-type]

    rows = asyncio.run(
        repository.delete_incomplete_external_document_draft_exact(
            document_id="draft-doc",
            workspace_id="workspace-1",
            document_key="https://docs.example.com/reference",
            source_id="example_docs_source",
            expected_version=2,
            expected_content_hash="hash-draft",
            expected_ingestion_signature="sig-draft",
        )
    )

    assert rows == 1
    assert client.delete_calls == [
        (
            "documents",
            {
                "id": "eq.draft-doc",
                "workspace_id": "eq.workspace-1",
                "document_key": "eq.https://docs.example.com/reference",
                "source_type": "eq.external_docs",
                "metadata->>source_name": "eq.example_docs_source",
                "status": "eq.draft",
                "version": "eq.2",
                "content_hash": "eq.hash-draft",
                "metadata->ingestion->>signature": "eq.sig-draft",
            },
        )
    ]


@dataclass(frozen=True)
class Fixture:
    scope: SourceScope
    baseline_inventory: SourceInventory
    live_inventory: SourceInventory
    current_plan: object
    backup: dict[str, object]


class FakeCleanupRepository:
    def __init__(self, inventory: SourceInventory, *, delete_error: Exception | None = None) -> None:
        self.inventory = inventory
        self.delete_error = delete_error
        self.delete_calls: list[dict[str, object]] = []
        self.refresh_calls: list[str] = []

    async def delete_incomplete_external_document_draft_exact(self, **kwargs: object) -> int:
        self.delete_calls.append(dict(kwargs))
        if self.delete_error is not None:
            raise self.delete_error
        document_id = str(kwargs["document_id"])
        doc = _document(self.inventory, document_id)
        if doc.get("status") != "draft":
            return 0
        self.inventory = replace(
            self.inventory,
            documents=tuple(row for row in self.inventory.documents if row.get("id") != document_id),
            document_cards=tuple(row for row in self.inventory.document_cards if row.get("document_id") != document_id),
            sections=tuple(row for row in self.inventory.sections if row.get("document_id") != document_id),
            chunks=tuple(row for row in self.inventory.chunks if row.get("document_id") != document_id),
        )
        return 1

    async def load_inventory(self) -> SourceInventory:
        return self.inventory


class FakeClosableClient:
    async def close(self) -> None:
        return None


class FakeDeleteClient:
    def __init__(self) -> None:
        self.delete_calls: list[tuple[str, dict[str, object]]] = []

    async def delete(self, table: str, params: dict[str, object]) -> list[dict[str, object]]:
        self.delete_calls.append((table, dict(params)))
        return [{"id": "draft-doc"}]


def _fixture(
    *,
    draft_status: str = "draft",
    draft_key: str = "https://docs.example.com/reference",
    active_hash: str = "hash-active",
    include_rows: bool = True,
    extra_live_doc: bool = False,
    draft_sections: int = 2,
    draft_chunks: int = 0,
) -> Fixture:
    scope = SourceScope(
        service_id="example_docs",
        display_name="Example Docs",
        source_id="example_docs_source",
        source_title="Example Docs Source",
        source_type="active_candidate_docs",
        registered=True,
        source_config={
            "allowed_domains": ["docs.example.com"],
            "start_urls": ["https://docs.example.com/reference"],
            "allow_patterns": [],
            "deny_patterns": [],
            "source_kind": "external_docs",
            "max_pages": 5,
        },
    )
    baseline = SourceInventory(
        workspace_id="workspace-1",
        workspace_name="team",
        documents=(
            _doc("active-doc", "https://docs.example.com/reference", version=1, status="active", content_hash="hash-active", signature="sig-active"),
            _doc("other-doc", "https://docs.example.com/other", version=1, status="active", content_hash="hash-other", signature="sig-other"),
        ),
        document_cards=(
            _card("card-active", "active-doc"),
            _card("card-other", "other-doc"),
        ),
        sections=(
            _section("section-active-1", "active-doc", 0),
            _section("section-active-2", "active-doc", 1),
            _section("section-other-1", "other-doc", 0),
        ),
        chunks=(
            _chunk("chunk-active-1", "active-doc", "section-active-1", 0),
            _chunk("chunk-active-2", "active-doc", "section-active-2", 1),
            _chunk("chunk-other-1", "other-doc", "section-other-1", 0),
        ),
        term_statistics_count=10,
    )
    baseline_plan = build_reprocessing_plan(scope=scope, inventory=baseline)
    backup = build_baseline_manifest(plan=baseline_plan, inventory=baseline, include_rows=include_rows)
    draft_children = _draft_children(draft_sections=draft_sections, draft_chunks=draft_chunks)
    live_docs = (
        _replace_doc_row(baseline.documents[0], {"content_hash": active_hash}),
        baseline.documents[1],
        _doc("draft-doc", draft_key, version=2, status=draft_status, content_hash="hash-draft", signature="sig-draft"),
    )
    if extra_live_doc:
        live_docs = (*live_docs, _doc("unexpected-doc", "https://docs.example.com/unexpected", version=1, status="active"))
    live = SourceInventory(
        workspace_id="workspace-1",
        workspace_name="team",
        documents=live_docs,
        document_cards=(*baseline.document_cards, _card("card-draft", "draft-doc")),
        sections=(*baseline.sections, *draft_children["sections"]),
        chunks=(*baseline.chunks, *draft_children["chunks"]),
        term_statistics_count=10,
    )
    current_plan = build_reprocessing_plan(scope=scope, inventory=live)
    return Fixture(scope=scope, baseline_inventory=baseline, live_inventory=live, current_plan=current_plan, backup=backup)


def _plan(fixture: Fixture, *, document_ids: tuple[str, ...] = ("draft-doc",)):
    return build_incomplete_draft_cleanup_plan(
        scope=fixture.scope,
        inventory=fixture.live_inventory,
        current_plan=fixture.current_plan,  # type: ignore[arg-type]
        backup_manifest=fixture.backup,
        document_ids=document_ids,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _doc(
    document_id: str,
    key: str,
    *,
    version: int,
    status: str,
    content_hash: str = "hash",
    signature: str = "sig",
) -> dict[str, object]:
    return {
        "id": document_id,
        "workspace_id": "workspace-1",
        "source_type": "external_docs",
        "filename": key.rsplit("/", 1)[-1],
        "document_key": key,
        "title": key,
        "course": None,
        "module": "example_docs_source",
        "lesson": None,
        "version": version,
        "status": status,
        "content_hash": content_hash,
        "metadata": {
            "source_name": "example_docs_source",
            "source_url": key,
            "canonical_url": key,
            "ingestion": {"signature": signature},
        },
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _card(card_id: str, document_id: str) -> dict[str, object]:
    return {
        "id": card_id,
        "document_id": document_id,
        "workspace_id": "workspace-1",
        "summary": "summary",
        "topics": ["reference"],
        "questions_answered": ["How to use reference?"],
        "entities": ["Example"],
        "task_types": ["reference"],
        "not_about": [],
        "quality_score": 0.9,
        "card_embedding": "[1,2,3]",
        "metadata": {},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _section(section_id: str, document_id: str, index: int) -> dict[str, object]:
    return {
        "id": section_id,
        "document_id": document_id,
        "workspace_id": "workspace-1",
        "section_index": index,
        "heading": f"Section {index}",
        "summary": "summary",
        "page_start": None,
        "page_end": None,
        "metadata": {},
        "section_embedding": "[1,2,3]",
    }


def _chunk(chunk_id: str, document_id: str, section_id: str, index: int) -> dict[str, object]:
    return {
        "id": chunk_id,
        "document_id": document_id,
        "section_id": section_id,
        "workspace_id": "workspace-1",
        "chunk_index": index,
        "content": "useful content",
        "embedding": "[1,2,3]",
        "token_count": 2,
        "page": None,
        "heading": "Heading",
        "metadata": {},
        "created_at": "2026-01-01T00:00:00Z",
    }


def _draft_children(*, draft_sections: int, draft_chunks: int) -> dict[str, tuple[dict[str, object], ...]]:
    sections = tuple(_section(f"section-draft-{index}", "draft-doc", index) for index in range(draft_sections))
    chunks = tuple(
        _chunk(f"chunk-draft-{index}", "draft-doc", f"section-draft-{index % max(draft_sections, 1)}", index)
        for index in range(draft_chunks)
    )
    return {"sections": sections, "chunks": chunks}


def _document(inventory: SourceInventory, document_id: str) -> dict[str, object]:
    matches = [row for row in inventory.documents if row.get("id") == document_id]
    assert len(matches) == 1
    return matches[0]


def _counts(inventory: SourceInventory, document_id: str) -> tuple[int, int, int]:
    return (
        sum(1 for row in inventory.document_cards if row.get("document_id") == document_id),
        sum(1 for row in inventory.sections if row.get("document_id") == document_id),
        sum(1 for row in inventory.chunks if row.get("document_id") == document_id),
    )


def _replace_doc_row(row: dict[str, object], updates: dict[str, object]) -> dict[str, object]:
    value = dict(row)
    value.update(updates)
    return value


def _with_extra_draft_section(inventory: SourceInventory) -> SourceInventory:
    return replace(
        inventory,
        sections=(*inventory.sections, _section("section-draft-extra", "draft-doc", 99)),
    )
