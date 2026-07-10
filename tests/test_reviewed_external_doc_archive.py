from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import pytest

from app.db.repositories import DocumentRepository
from app.docs_registry.external_doc_archive import (
    build_reviewed_external_doc_archive_plan,
    execute_reviewed_external_doc_archive,
    format_archive_plan_text,
)
from app.docs_registry.reconciliation_plan import REVIEW_SCHEMA_VERSION, _payload_checksum
from app.docs_registry.reprocessing_plan import (
    SourceInventory,
    SourceScope,
    build_baseline_manifest,
    build_reprocessing_plan,
)


def test_valid_preview_requires_exact_target_and_keeps_automatic_archive_disabled() -> None:
    fixture = _fixture()
    plan = _archive_plan(fixture)

    assert plan.readiness is True
    assert plan.target is not None
    assert plan.target.document_id == "old-doc"
    assert plan.successor is not None
    assert plan.successor.document_id == "new-doc"
    assert plan.reviewed_decision is not None
    assert plan.reviewed_decision.owner_decision == "superseded_by"
    assert plan.automatic_archive_allowed is False
    assert plan.expected_write_scope["documents"] == ("update exactly one external_docs row from active to archived",)


def test_no_backup_blocks_without_writes() -> None:
    fixture = _fixture()
    plan = _archive_plan(fixture, backup=None)

    assert plan.readiness is False
    assert "fresh_post_activation_backup_required" in plan.blockers


def test_old_pre_activation_backup_blocks() -> None:
    fixture = _fixture()
    old_manifest = _manifest(fixture, generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    plan = _archive_plan(fixture, backup=old_manifest)

    assert plan.readiness is False
    assert "fresh_post_activation_backup_required" in plan.blockers


def test_invalid_backup_checksum_blocks() -> None:
    fixture = _fixture()
    backup = dict(fixture.backup)
    backup["counts"] = {**backup["counts"], "chunks": 999}

    plan = _archive_plan(fixture, backup=backup)

    assert plan.readiness is False
    assert "manifest checksum mismatch" in plan.blockers


@pytest.mark.parametrize("decision", ["keep_active", "needs_more_review"])
def test_non_archive_owner_decisions_block(decision: str) -> None:
    fixture = _fixture(decision=decision, successor_key="")
    plan = _archive_plan(fixture)

    assert plan.readiness is False
    assert f"reviewed decision blocks archive: {decision}" in plan.blockers


def test_target_id_mismatch_blocks() -> None:
    fixture = _fixture()
    plan = _archive_plan(fixture, document_id="missing-doc")

    assert plan.readiness is False
    assert "target count must be exactly one" in plan.blockers


def test_target_status_version_hash_or_signature_drift_blocks() -> None:
    fixture = _fixture()
    drifted = _replace_doc(fixture.inventory, "old-doc", {"version": 2})
    fixture = replace(fixture, inventory=drifted, current_plan=build_reprocessing_plan(scope=fixture.scope, inventory=drifted))

    plan = _archive_plan(fixture)

    assert plan.readiness is False
    assert "active versions changed" in plan.backup.blockers


def test_successor_missing_or_inactive_blocks() -> None:
    fixture = _fixture()
    inventory = _replace_doc(fixture.inventory, "new-doc", {"status": "archived"})
    fixture = replace(fixture, inventory=inventory, current_plan=build_reprocessing_plan(scope=fixture.scope, inventory=inventory))

    plan = _archive_plan(fixture)

    assert plan.readiness is False
    assert "successor must match exactly one active document" in plan.blockers


def test_multiple_targets_block() -> None:
    fixture = _fixture()
    duplicate = {**fixture.inventory.documents[0], "document_key": "https://docs.example.com/other"}
    inventory = replace(fixture.inventory, documents=(duplicate, *fixture.inventory.documents))
    fixture = replace(fixture, inventory=inventory, current_plan=build_reprocessing_plan(scope=fixture.scope, inventory=inventory))

    plan = _archive_plan(fixture)

    assert plan.readiness is False
    assert "target count must be exactly one" in plan.blockers


def test_duplicate_active_keys_block() -> None:
    fixture = _fixture()
    duplicate = {**fixture.inventory.documents[1], "id": "new-doc-duplicate"}
    inventory = replace(fixture.inventory, documents=(*fixture.inventory.documents, duplicate))
    fixture = replace(fixture, inventory=inventory, current_plan=build_reprocessing_plan(scope=fixture.scope, inventory=inventory))

    plan = _archive_plan(fixture)

    assert plan.readiness is False
    assert "duplicate active document keys must be zero" in plan.blockers


def test_missing_confirmation_does_not_write() -> None:
    fixture = _fixture()
    plan = _archive_plan(fixture)
    repo = FakeArchiveRepository()

    result = asyncio.run(
        execute_reviewed_external_doc_archive(plan=plan, repository=repo, confirmation_phrase="")
    )

    assert result.status == "blocked"
    assert repo.archive_calls == []


def test_valid_fake_execution_archives_one_row_and_refreshes_terms() -> None:
    fixture = _fixture()
    plan = _archive_plan(fixture)
    repo = FakeArchiveRepository(rows_updated=1, refreshed=123)

    result = asyncio.run(
        execute_reviewed_external_doc_archive(
            plan=plan,
            repository=repo,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    assert result.status == "archived"
    assert result.rows_updated == 1
    assert result.new_status == "archived"
    assert result.successor_unchanged is True
    assert result.term_statistics_status == "updated: 123"
    assert repo.archive_calls == [
        {
            "document_id": "old-doc",
            "workspace_id": "workspace-1",
            "document_key": "https://docs.example.com/old-topic",
            "source_id": "example_docs",
            "expected_version": 1,
        }
    ]
    assert repo.refresh_calls == ["workspace-1"]


@pytest.mark.parametrize("rows_updated", [0, 2])
def test_zero_or_multiple_updated_rows_fail_without_refresh(rows_updated: int) -> None:
    fixture = _fixture()
    plan = _archive_plan(fixture)
    repo = FakeArchiveRepository(rows_updated=rows_updated)

    result = asyncio.run(
        execute_reviewed_external_doc_archive(
            plan=plan,
            repository=repo,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    assert result.status == "failed"
    assert result.rows_updated == rows_updated
    assert repo.refresh_calls == []


def test_term_statistics_failure_returns_partial_failure_without_retry() -> None:
    fixture = _fixture()
    plan = _archive_plan(fixture)
    repo = FakeArchiveRepository(rows_updated=1, refresh_error=RuntimeError("boom"))

    result = asyncio.run(
        execute_reviewed_external_doc_archive(
            plan=plan,
            repository=repo,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    assert result.status == "partial_failure"
    assert result.partial_failure is True
    assert result.rollback_required is True
    assert repo.archive_calls
    assert repo.refresh_calls == ["workspace-1"]


def test_retrieval_status_semantics_are_document_status_based() -> None:
    plan = _archive_plan(_fixture())

    assert "status=active" in plan.retrieval_status_semantics


def test_neutral_exampledocs_logic_does_not_need_openrouter() -> None:
    fixture = _fixture(service_id="exampledocs", source_id="exampledocs_docs")
    plan = _archive_plan(fixture)

    assert plan.service_id == "exampledocs"
    assert plan.source_id == "exampledocs_docs"
    assert plan.readiness is True


def test_openrouter_pilot_fixture_blocks_without_fresh_backup() -> None:
    fixture = _fixture(
        service_id="openrouter",
        source_id="openrouter_docs",
        old_id="dd227f3b-8a5b-4541-b129-0acc9092e57b",
        new_id="032402fb-91cd-4c9d-9bcc-29c2790f511a",
        old_key="https://openrouter.ai/docs/mcp-server",
        new_key="https://openrouter.ai/docs/guides/overview/mcp-server",
    )
    plan = _archive_plan(fixture, backup=None, document_id="dd227f3b-8a5b-4541-b129-0acc9092e57b")

    assert plan.target is not None
    assert plan.target.document_id == "dd227f3b-8a5b-4541-b129-0acc9092e57b"
    assert plan.successor is not None
    assert plan.successor.document_id == "032402fb-91cd-4c9d-9bcc-29c2790f511a"
    assert plan.readiness is False
    assert "fresh_post_activation_backup_required" in plan.blockers


def test_cli_text_mentions_preview_and_no_execution() -> None:
    text = format_archive_plan_text(_archive_plan(_fixture()))

    assert "mode: read-only" in text
    assert "target count: 1" in text
    assert "automatic archive: disabled" in text
    assert "archive execution: not performed" in text
    assert "--archive" not in text


def test_repository_exact_archive_filters_all_required_fields() -> None:
    client = FakeSupabaseClient()
    repo = DocumentRepository(client)  # type: ignore[arg-type]

    rows = asyncio.run(
        repo.archive_external_document_exact(
            document_id="doc-1",
            workspace_id="workspace-1",
            document_key="https://docs.example.com/old-topic",
            source_id="example_docs",
            expected_version=3,
        )
    )

    assert rows == 1
    assert client.update_calls == [
        (
            "documents",
            {"status": "archived"},
            {
                "id": "eq.doc-1",
                "workspace_id": "eq.workspace-1",
                "document_key": "eq.https://docs.example.com/old-topic",
                "source_type": "eq.external_docs",
                "metadata->>source_name": "eq.example_docs",
                "status": "eq.active",
                "version": "eq.3",
            },
        )
    ]


def test_safety_no_crawler_activation_indexer_or_delete_calls() -> None:
    fixture = _fixture()
    plan = _archive_plan(fixture)
    repo = FakeArchiveRepository(rows_updated=1)

    asyncio.run(
        execute_reviewed_external_doc_archive(
            plan=plan,
            repository=repo,
            confirmation_phrase=plan.expected_confirmation_phrase,
        )
    )

    assert repo.delete_calls == []
    assert repo.crawler_calls == []
    assert repo.activation_calls == []
    assert repo.indexer_calls == []


@dataclass(frozen=True)
class ArchiveFixture:
    scope: SourceScope
    inventory: SourceInventory
    current_plan: object
    review: dict[str, object]
    backup: dict[str, object]


class FakeArchiveRepository:
    def __init__(self, *, rows_updated: int = 1, refreshed: int = 1, refresh_error: Exception | None = None) -> None:
        self.rows_updated = rows_updated
        self.refreshed = refreshed
        self.refresh_error = refresh_error
        self.archive_calls: list[dict[str, object]] = []
        self.refresh_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.crawler_calls: list[str] = []
        self.activation_calls: list[str] = []
        self.indexer_calls: list[str] = []

    async def archive_external_document_exact(self, **kwargs: object) -> int:
        self.archive_calls.append(dict(kwargs))
        return self.rows_updated

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        self.refresh_calls.append(workspace_id)
        if self.refresh_error:
            raise self.refresh_error
        return self.refreshed


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.update_calls: list[tuple[str, dict[str, object], dict[str, object]]] = []

    async def update(self, table: str, payload: dict[str, object], params: dict[str, object]):
        self.update_calls.append((table, payload, params))
        return [{"id": "doc-1"}]


def _fixture(
    *,
    service_id: str = "example",
    source_id: str = "example_docs",
    old_id: str = "old-doc",
    new_id: str = "new-doc",
    old_key: str = "https://docs.example.com/old-topic",
    new_key: str = "https://docs.example.com/guides/old-topic",
    decision: str = "superseded_by",
    successor_key: str | None = None,
) -> ArchiveFixture:
    scope = _scope(service_id, source_id)
    inventory = _inventory(
        source_id=source_id,
        old_id=old_id,
        new_id=new_id,
        old_key=old_key,
        new_key=new_key,
    )
    current_plan = build_reprocessing_plan(scope=scope, inventory=inventory)
    successor = new_key if successor_key is None else successor_key
    review = _review(
        service_id=service_id,
        source_id=source_id,
        document_key=old_key,
        decision=decision,
        successor_key=successor,
    )
    fixture = ArchiveFixture(
        scope=scope,
        inventory=inventory,
        current_plan=current_plan,
        review=review,
        backup={},
    )
    return replace(fixture, backup=_manifest(fixture))


def _archive_plan(
    fixture: ArchiveFixture,
    *,
    backup: dict[str, object] | None | object = ...,
    document_id: str | None = None,
):
    backup_manifest = fixture.backup if backup is ... else backup
    return build_reviewed_external_doc_archive_plan(
        scope=fixture.scope,
        inventory=fixture.inventory,
        current_plan=fixture.current_plan,  # type: ignore[arg-type]
        reviewed_artifact=fixture.review,
        backup_manifest=backup_manifest,  # type: ignore[arg-type]
        document_id=document_id or str(fixture.inventory.documents[0]["id"]),
        generated_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )


def _manifest(fixture: ArchiveFixture, *, generated_at: datetime | None = None) -> dict[str, object]:
    return build_baseline_manifest(
        plan=fixture.current_plan,  # type: ignore[arg-type]
        inventory=fixture.inventory,
        include_rows=True,
        generated_at=generated_at or datetime(2026, 7, 11, tzinfo=timezone.utc),
    )


def _scope(service_id: str, source_id: str) -> SourceScope:
    return SourceScope(
        service_id=service_id,
        display_name=service_id.title(),
        source_id=source_id,
        source_title=source_id.replace("_", " ").title(),
        source_type="active_candidate_docs",
        registered=True,
        source_config={
            "source_id": source_id,
            "allowed_domains": ["docs.example.com", "openrouter.ai"],
            "allow_patterns": [r"^https://docs\.example\.com/", r"^https://openrouter\.ai/docs"],
            "deny_patterns": ["/login"],
        },
    )


def _inventory(
    *,
    source_id: str,
    old_id: str,
    new_id: str,
    old_key: str,
    new_key: str,
) -> SourceInventory:
    old = _doc(old_id, old_key, source_id=source_id, title="Old Topic")
    new = _doc(new_id, new_key, source_id=source_id, title="Old Topic")
    docs = (old, new)
    cards = tuple(_card(row["id"]) for row in docs)
    sections = tuple(_section(row["id"], index) for row in docs for index in range(2))
    chunks = tuple(_chunk(row["id"], index) for row in docs for index in range(3))
    return SourceInventory(
        workspace_id="workspace-1",
        workspace_name="team",
        documents=docs,
        document_cards=cards,
        sections=sections,
        chunks=chunks,
        term_statistics_count=10,
    )


def _doc(document_id: str, key: str, *, source_id: str, title: str) -> dict[str, object]:
    return {
        "id": document_id,
        "workspace_id": "workspace-1",
        "source_type": "external_docs",
        "filename": key.rsplit("/", 1)[-1] + ".html",
        "document_key": key,
        "title": title,
        "module": source_id,
        "version": 1,
        "status": "active",
        "content_hash": f"hash-{document_id}",
        "metadata": {
            "source_name": source_id,
            "ingestion": {"signature": f"signature-{document_id}"},
        },
        "created_at": "2026-07-09T20:00:00+00:00",
        "updated_at": "2026-07-09T20:00:00+00:00",
    }


def _card(document_id: object) -> dict[str, object]:
    return {
        "id": f"card-{document_id}",
        "document_id": document_id,
        "workspace_id": "workspace-1",
        "summary": "summary",
        "card_embedding": [0.1, 0.2],
        "metadata": {},
    }


def _section(document_id: object, index: int) -> dict[str, object]:
    return {
        "id": f"section-{document_id}-{index}",
        "document_id": document_id,
        "workspace_id": "workspace-1",
        "section_index": index,
        "heading": f"Heading {index}",
        "section_embedding": [0.1, 0.2],
        "metadata": {},
    }


def _chunk(document_id: object, index: int) -> dict[str, object]:
    return {
        "id": f"chunk-{document_id}-{index}",
        "document_id": document_id,
        "workspace_id": "workspace-1",
        "section_id": f"section-{document_id}-0",
        "chunk_index": index,
        "heading": f"Heading {index}",
        "content": f"Useful content {index}",
        "embedding": [0.1, 0.2],
        "metadata": {},
    }


def _review(
    *,
    service_id: str,
    source_id: str,
    document_key: str,
    decision: str,
    successor_key: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "generated_at": "2026-07-10T00:00:00Z",
        "repository": "serickprime/ai-kurator-v2",
        "service_id": service_id,
        "source_id": source_id,
        "workspace_id": "workspace-1",
        "workspace_name": "team",
        "snapshot_fingerprint": "snapshot",
        "active_inventory_fingerprint": "active",
        "mode": "owner-review-required",
        "automatic_archive_allowed": False,
        "review_status": "reviewed",
        "decisions": [
            {
                "document_key": document_key,
                "classification": "possible_superseded",
                "successor_candidates": [successor_key] if successor_key else [],
                "owner_decision": decision,
                "owner_successor": successor_key or None,
                "review_status": "reviewed",
                "allowed_decisions": ["keep_active", "archive_candidate", "superseded_by", "needs_more_review"],
                "notes": "",
            }
        ],
    }
    payload["checksum"] = _payload_checksum(payload)
    return payload


def _replace_doc(inventory: SourceInventory, document_id: str, updates: dict[str, object]) -> SourceInventory:
    rows = tuple(
        {**row, **updates} if row.get("id") == document_id else row
        for row in inventory.documents
    )
    return replace(inventory, documents=rows)
