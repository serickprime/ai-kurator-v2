from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import relocate_reviewed_external_doc as cli
from app.docs_registry.canonical_relocation import (
    CANONICAL_RELOCATION_REVIEW_SCHEMA_VERSION,
    build_reviewed_canonical_relocation_plan,
    execute_reviewed_canonical_relocation,
    format_canonical_relocation_plan_text,
    validate_relocated_canonical_page,
)
from app.docs_registry.reconciliation_plan import _payload_checksum
from app.docs_registry.reprocessing_plan import (
    SourceInventory,
    SourceScope,
    build_baseline_manifest,
    build_reprocessing_plan,
)
from app.external_docs.extractor import ExternalDocsExtractor
from app.external_docs.types import CrawledPage, ExternalDocSource, ExternalDocsIndexResult, ExtractedPage


def test_valid_preview_for_one_reviewed_relocation() -> None:
    plan = _plan(_fixture())

    assert plan.readiness is True
    assert plan.target_count == 1
    assert plan.old_document is not None
    assert plan.new_canonical is not None
    assert plan.new_canonical.document_key == "https://docs.example.com/guides/service-tiers"
    assert plan.new_canonical.expected_version == 1


def test_preview_performs_no_fetch_or_writes() -> None:
    fetcher = FakeFetcher({})
    indexer = FakeIndexer()
    repo = FakeRelocationRepository()

    plan = _plan(_fixture())

    assert plan.readiness is True
    assert fetcher.calls == []
    assert indexer.calls == []
    assert repo.archive_calls == []


def test_target_count_must_equal_one() -> None:
    plan = _plan(_fixture(), document_id="missing")

    assert plan.readiness is False
    assert "target count must equal one" in plan.blockers


@pytest.mark.parametrize("status", ["draft", "needs_more_review", "rejected"])
def test_non_reviewed_artifact_status_blocks(status: str) -> None:
    fixture = _fixture(owner_review_status=status)

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "owner_review_status must be reviewed" in plan.blockers


def test_invalid_checksum_blocks() -> None:
    fixture = _fixture()
    artifact = dict(fixture.artifact)
    artifact["rationale"] = "changed"

    plan = _plan(replace(fixture, artifact=artifact))

    assert plan.readiness is False
    assert "relocation review checksum mismatch" in plan.blockers


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("service_id", "wrong", "relocation review service_id does not match scope"),
        ("source_id", "wrong_docs", "relocation review source_id does not match scope"),
        ("workspace_id", "workspace-2", "relocation review workspace_id does not match runtime"),
    ],
)
def test_wrong_service_source_workspace_blocks(field: str, value: str, expected: str) -> None:
    artifact = dict(_fixture().artifact)
    artifact[field] = value
    artifact["checksum"] = _payload_checksum(artifact)

    plan = _plan(replace(_fixture(), artifact=artifact))

    assert plan.readiness is False
    assert expected in plan.blockers


@pytest.mark.parametrize(
    ("old_update", "expected"),
    [
        ({"document_id": "different"}, "old document ID does not match relocation review"),
        ({"document_key": "https://docs.example.com/old-other"}, "old document_key does not match relocation review"),
    ],
)
def test_old_document_id_key_mismatch_blocks(old_update: dict[str, object], expected: str) -> None:
    fixture = _fixture()
    artifact = dict(fixture.artifact)
    old = dict(artifact["old_document"])  # type: ignore[index]
    old.update(old_update)
    artifact["old_document"] = old
    artifact["checksum"] = _payload_checksum(artifact)

    plan = _plan(replace(fixture, artifact=artifact))

    assert plan.readiness is False
    assert expected in plan.blockers


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"status": "archived"}, "old status drift detected"),
        ({"version": 2}, "old version drift detected"),
        ({"content_hash": "changed"}, "old content hash drift detected"),
        ({"metadata": {"source_name": "example_docs", "ingestion": {"signature": "changed"}}}, "old ingestion signature drift detected"),
    ],
)
def test_old_status_version_hash_signature_drift_blocks(updates: dict[str, object], expected: str) -> None:
    fixture = _fixture()
    inventory = _replace_doc(fixture.inventory, "old-service-tiers", updates)
    fixture = replace(fixture, inventory=inventory, current_plan=build_reprocessing_plan(scope=fixture.scope, inventory=inventory))

    plan = _plan(fixture)

    assert plan.readiness is False
    assert expected in plan.blockers


def test_new_key_equals_old_key_blocks() -> None:
    fixture = _fixture(new_key="https://docs.example.com/service-tiers")

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "new canonical key must differ from old key" in plan.blockers


def test_missing_fresh_backup_blocks() -> None:
    plan = _plan(_fixture(), backup=None)

    assert plan.readiness is False
    assert "fresh_backup_required" in plan.blockers


def test_stale_inventory_fingerprint_blocks() -> None:
    fixture = _fixture()
    stale_inventory = _replace_doc(fixture.inventory, "protected-mcp-old", {"status": "active"})
    stale_plan = build_reprocessing_plan(scope=fixture.scope, inventory=stale_inventory)
    stale_backup = build_baseline_manifest(plan=stale_plan, inventory=stale_inventory, include_rows=True)

    plan = _plan(fixture, backup=stale_backup)

    assert plan.readiness is False
    assert "inventory_drift_detected" in plan.blockers
    assert "baseline fingerprint changed" in plan.blockers


def test_new_active_key_collision_blocks() -> None:
    plan = _plan(_fixture(collision_status="active"))

    assert plan.readiness is False
    assert "new_key_active_collision" in plan.blockers


def test_new_archived_key_collision_blocks() -> None:
    plan = _plan(_fixture(collision_status="archived"))

    assert plan.readiness is False
    assert "new_key_archived_collision" in plan.blockers


def test_foreign_scope_collision_blocks() -> None:
    plan = _plan(_fixture(collision_status="foreign"))

    assert plan.readiness is False
    assert "new_key_foreign_scope_collision" in plan.blockers


def test_duplicate_active_key_blocks() -> None:
    fixture = _fixture(duplicate_active=True)

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "duplicate_active_key_detected" in plan.blockers


def test_new_url_outside_registry_scope_blocks() -> None:
    fixture = _fixture(new_key="https://outside.example.net/guides/service-tiers", fetch_url="https://outside.example.net/guides/service-tiers")

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "new canonical URL is outside registered source scope" in plan.blockers


def test_arbitrary_url_cli_input_absent(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--help"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "--new-url" not in output
    assert "--new-key" not in output
    assert "--url" not in output


def test_controlled_fetch_requests_only_reviewed_new_url() -> None:
    plan = _plan(_fixture())
    result, fetcher, _indexer, _repo = _execute_success(plan)

    assert result.status == "relocated"
    assert fetcher.calls == [plan.new_canonical.fetch_url]  # type: ignore[union-attr]


def test_full_source_crawl_discovery_not_called() -> None:
    plan = _plan(_fixture())
    _result, fetcher, _indexer, _repo = _execute_success(plan)

    assert fetcher.crawl_calls == []


def test_final_url_mismatch_blocks_before_writes() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, repo = _execute_with_page(plan, final_url="https://evil.example.net/service-tiers")

    assert result.status == "blocked"
    assert indexer.calls == []
    assert repo.archive_calls == []


def test_canonical_tag_mismatch_blocks_before_writes() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, repo = _execute_with_page(plan, canonical_url="https://docs.example.com/third-key")

    assert result.status == "blocked"
    assert indexer.calls == []
    assert repo.archive_calls == []


def test_cleaner_removes_generic_boilerplate() -> None:
    plan = _plan(_fixture())
    page = CrawledPage(
        source_name="example_docs",
        url="https://docs.example.com/guides/service-tiers",
        html="""
        <html><head><link rel="canonical" href="https://docs.example.com/guides/service-tiers"></head>
        <body><main><h1>Service Tiers</h1>
        <p>service tiers service_tier priority latency limits routing examples</p>
        <p>This page is also available as markdown: llms.txt</p></main></body></html>
        """,
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )
    extracted = ExternalDocsExtractor().extract(page)

    validation = validate_relocated_canonical_page(plan=plan, extracted=extracted, final_url=page.url, source=_source())

    assert validation["boilerplate_removed"] is True
    assert "llms.txt" not in extracted.structured_text


def test_useful_term_loss_blocks_before_writes() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, repo = _execute_with_page(plan, text="service_tier only")

    assert result.status == "blocked"
    assert "validation failed before writes" in result.blockers[0]
    assert indexer.calls == []
    assert repo.archive_calls == []


def test_validation_failure_produces_zero_writes() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, repo = _execute_with_page(plan, canonical_url="https://docs.example.com/nope")

    assert result.status == "blocked"
    assert indexer.calls == []
    assert repo.archive_calls == []
    assert result.rollback_required is False


def test_valid_fake_relocation_creates_new_key_local_v1() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, _repo = _execute_success(plan)

    assert result.status == "relocated"
    assert result.new_version == 1
    assert indexer.created_versions == [(plan.new_canonical.document_key, 1)]  # type: ignore[union-attr]


def test_old_document_key_is_not_mutated_in_place() -> None:
    plan = _plan(_fixture())
    result, _fetcher, _indexer, repo = _execute_success(plan)

    assert result.old_document_key == "https://docs.example.com/service-tiers"
    assert repo.mutated_document_keys == []


def test_new_card_sections_chunks_created() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, _repo = _execute_success(plan)

    assert result.new_document_id == "new-doc-1"
    assert indexer.created_children == {"cards": 1, "sections": 3, "chunks": 4}


def test_old_child_rows_preserved() -> None:
    plan = _plan(_fixture())
    _result, _fetcher, indexer, repo = _execute_success(plan)

    assert indexer.deleted_child_rows == []
    assert repo.deleted_child_rows == []


def test_lineage_metadata_recorded() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, _repo = _execute_success(plan)

    assert result.lineage_metadata_status == "recorded"
    assert indexer.last_metadata["canonical_relocation"]["relocated_from_document_id"] == "old-service-tiers"


def test_old_exact_document_archived_only_after_new_active_exists() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, repo = _execute_success(plan)

    assert result.old_archive_rows_updated == 1
    assert indexer.calls == [plan.new_canonical.document_key]  # type: ignore[union-attr]
    assert repo.archive_calls == ["old-service-tiers"]


@pytest.mark.parametrize("rows_updated", [0, 2])
def test_archive_update_count_not_one_returns_partial_failure(rows_updated: int) -> None:
    plan = _plan(_fixture())
    result, _fetcher, _indexer, _repo = _execute_success(plan, archive_rows=rows_updated)

    assert result.status == "partial_failure"
    assert result.rollback_required is True
    assert result.old_archive_rows_updated == rows_updated


def test_new_creation_failure_leaves_old_active() -> None:
    plan = _plan(_fixture())
    result, _fetcher, _indexer, repo = _execute_success(plan, index_error="index failed")

    assert result.status == "partial_failure"
    assert result.old_status == "active"
    assert repo.archive_calls == []


def test_new_active_old_archive_failure_returns_rollback_required() -> None:
    plan = _plan(_fixture())
    result, _fetcher, _indexer, _repo = _execute_success(plan, archive_rows=0)

    assert result.status == "partial_failure"
    assert result.changed_keys == (plan.new_canonical.document_key,)  # type: ignore[union-attr]
    assert result.rollback_required is True


def test_term_statistics_refresh_called_once_after_full_success() -> None:
    plan = _plan(_fixture())
    result, _fetcher, _indexer, repo = _execute_success(plan)

    assert result.term_statistics_status == "updated: 77"
    assert repo.refresh_calls == ["workspace-1"]


def test_term_statistics_refresh_failure_returns_partial_failure() -> None:
    plan = _plan(_fixture())
    result, _fetcher, _indexer, _repo = _execute_success(plan, refresh_error=RuntimeError("boom"))

    assert result.status == "partial_failure"
    assert result.rollback_required is True
    assert result.failed_stage == "term_statistics_refresh"


def test_no_automatic_retry() -> None:
    plan = _plan(_fixture())
    result, fetcher, _indexer, _repo = _execute_success(plan, archive_rows=0)

    assert result.automatic_retry is False
    assert fetcher.calls == [plan.new_canonical.fetch_url]  # type: ignore[union-attr]


def test_no_automatic_rollback() -> None:
    plan = _plan(_fixture())
    result, _fetcher, _indexer, _repo = _execute_success(plan, archive_rows=0)

    assert result.automatic_rollback is False


def test_other_source_documents_unchanged() -> None:
    plan = _plan(_fixture())
    _result, _fetcher, indexer, repo = _execute_success(plan)

    assert "https://docs.example.com/other" not in indexer.created_keys
    assert repo.archive_calls == ["old-service-tiers"]


def test_protected_documents_unchanged() -> None:
    plan = _plan(_fixture())
    _result, _fetcher, indexer, repo = _execute_success(plan)

    assert "https://docs.example.com/mcp-server" not in indexer.created_keys
    assert "https://docs.example.com/guides/overview/mcp-server" not in indexer.created_keys
    assert repo.archive_calls == ["old-service-tiers"]


def test_exampledocs_generic_happy_path() -> None:
    fixture = _fixture(service_id="exampledocs", source_id="exampledocs_docs")
    plan = _plan(fixture)
    result, _fetcher, _indexer, _repo = _execute_success(plan)

    assert plan.service_id == "exampledocs"
    assert plan.source_id == "exampledocs_docs"
    assert result.status == "relocated"


def test_no_openrouter_specific_production_branching() -> None:
    text = Path("app/docs_registry/canonical_relocation.py").read_text(encoding="utf-8")

    assert 'service_id == "openrouter"' not in text
    assert "openrouter_docs" not in text


def test_cli_preview_default() -> None:
    text = format_canonical_relocation_plan_text(_plan(_fixture()))

    assert "mode: read-only" in text
    assert "relocation execution: not performed" in text


def test_cli_requires_exact_execution_flag_and_phrase() -> None:
    args = cli.parse_args(
        [
            "--service",
            "example",
            "--review",
            "review.json",
            "--document-id",
            "doc-1",
            "--confirm-relocate-reviewed",
            "--confirmation-phrase",
            "phrase",
        ]
    )

    assert args.confirm_relocate_reviewed is True
    assert args.confirmation_phrase == "phrase"


def test_missing_explicit_confirmation_prevents_execution() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer, repo = _execute_success(plan, confirmation_phrase="")

    assert result.status == "blocked"
    assert indexer.calls == []
    assert repo.archive_calls == []


def test_cli_accepts_one_exact_document_id_only(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(
            [
                "--service",
                "example",
                "--review",
                "review.json",
                "--document-id",
                "doc-1",
                "--document-id",
                "doc-2",
            ]
    )

    assert exc_info.value.code == 2
    assert "--document-id accepts exactly one value" in capsys.readouterr().err


def test_cli_rejects_batch_list_mode(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--service", "example", "--review", "review.json", "--document-id", "doc-1", "--batch"])

    assert exc_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_json_output_structured() -> None:
    payload = _plan(_fixture()).to_dict()

    assert payload["mode"] == "read-only"
    assert payload["target_count"] == 1
    assert payload["full_source_crawl"] == "disabled"


def test_expected_validation_errors_have_no_traceback() -> None:
    plan = _plan(_fixture(), backup=None)

    assert plan.readiness is False
    assert all("Traceback" not in blocker for blocker in plan.blockers)


def test_openrouter_pilot_fixture_blocks_without_dedicated_relocation_artifact() -> None:
    fixture = _fixture(
        service_id="openrouter",
        source_id="openrouter_docs",
        old_key="https://openrouter.ai/docs/features/service-tiers",
        new_key="https://openrouter.ai/docs/guides/features/service-tiers",
        fetch_url="https://openrouter.ai/docs/guides/features/service-tiers",
        target_id="09038305-f448-4819-a51c-f48d6ebbf090",
        source_domains=("openrouter.ai",),
        allow_patterns=(r"^https://openrouter\.ai/docs",),
    )
    artifact = {
        "schema_version": "docs-reconciliation-review-v1",
        "service_id": "openrouter",
        "source_id": "openrouter_docs",
        "workspace_id": "workspace-1",
        "owner_review_status": "reviewed",
        "relationship": {"decision": "keep_active"},
    }
    artifact["checksum"] = _payload_checksum(artifact)

    plan = _plan(replace(fixture, artifact=artifact), document_id="09038305-f448-4819-a51c-f48d6ebbf090")

    assert plan.target_count == 1
    assert "unsupported relocation review schema version" in plan.blockers
    assert plan.full_source_crawl == "disabled"


@dataclass(frozen=True)
class RelocationFixture:
    scope: SourceScope
    inventory: SourceInventory
    current_plan: object
    artifact: dict[str, object]
    backup: dict[str, object]


class FakeFetcher:
    def __init__(self, pages: dict[str, CrawledPage]) -> None:
        self.pages = pages
        self.calls: list[str] = []
        self.crawl_calls: list[str] = []

    async def fetch_page(self, source: ExternalDocSource, url: str, *, depth: int = 0) -> CrawledPage | None:
        self.calls.append(url)
        return self.pages.get(url)


class FakeExtractor:
    def __init__(self, pages: dict[str, ExtractedPage]) -> None:
        self.pages = pages

    def extract(self, page: CrawledPage) -> ExtractedPage:
        return self.pages[page.url]


class FakeIndexer:
    def __init__(self, *, error: str = "") -> None:
        self.error = error
        self.calls: list[str] = []
        self.created_versions: list[tuple[str, int]] = []
        self.created_keys: list[str] = []
        self.created_children: dict[str, int] = {}
        self.deleted_child_rows: list[str] = []
        self.last_metadata: dict[str, object] = {}

    async def index_page(
        self,
        page: ExtractedPage,
        source: ExternalDocSource,
        *,
        workspace: str = "team",
    ) -> ExternalDocsIndexResult:
        self.calls.append(page.canonical_url)
        self.last_metadata = page.metadata
        if self.error:
            return ExternalDocsIndexResult(source_name=source.name, url=page.source_url, document_key=page.canonical_url, error=self.error)
        self.created_versions.append((page.canonical_url, 1))
        self.created_keys.append(page.canonical_url)
        self.created_children = {"cards": 1, "sections": 3, "chunks": 4}
        return ExternalDocsIndexResult(
            source_name=source.name,
            url=page.source_url,
            document_id="new-doc-1",
            document_key=page.canonical_url,
            version=1,
            skipped=False,
            archived_old=False,
            sections_count=3,
            chunks_count=4,
        )


class FakeRelocationRepository:
    def __init__(self, *, archive_rows: int = 1, refresh_error: Exception | None = None) -> None:
        self.archive_rows = archive_rows
        self.refresh_error = refresh_error
        self.archive_calls: list[str] = []
        self.refresh_calls: list[str] = []
        self.deleted_child_rows: list[str] = []
        self.mutated_document_keys: list[str] = []

    async def archive_external_document_exact(self, **kwargs: object) -> int:
        self.archive_calls.append(str(kwargs["document_id"]))
        return self.archive_rows

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        self.refresh_calls.append(workspace_id)
        if self.refresh_error:
            raise self.refresh_error
        return 77


class FakeClosableClient:
    async def close(self) -> None:
        return None


def _fixture(
    *,
    service_id: str = "example",
    source_id: str = "example_docs",
    old_key: str = "https://docs.example.com/service-tiers",
    new_key: str = "https://docs.example.com/guides/service-tiers",
    fetch_url: str = "https://docs.example.com/guides/service-tiers",
    target_id: str = "old-service-tiers",
    source_domains: tuple[str, ...] = ("docs.example.com",),
    allow_patterns: tuple[str, ...] = (r"^https://docs\.example\.com/",),
    owner_review_status: str = "reviewed",
    collision_status: str = "",
    duplicate_active: bool = False,
) -> RelocationFixture:
    scope = _scope(service_id, source_id, source_domains=source_domains, allow_patterns=allow_patterns)
    inventory = _inventory(
        source_id=source_id,
        old_key=old_key,
        new_key=new_key,
        target_id=target_id,
        collision_status=collision_status,
        duplicate_active=duplicate_active,
    )
    current_plan = build_reprocessing_plan(scope=scope, inventory=inventory)
    artifact = _artifact(
        service_id=service_id,
        source_id=source_id,
        old_key=old_key,
        new_key=new_key,
        fetch_url=fetch_url,
        target_id=target_id,
        owner_review_status=owner_review_status,
    )
    fixture = RelocationFixture(scope=scope, inventory=inventory, current_plan=current_plan, artifact=artifact, backup={})
    return replace(fixture, backup=build_baseline_manifest(plan=current_plan, inventory=inventory, include_rows=True))


def _plan(
    fixture: RelocationFixture,
    *,
    backup: dict[str, object] | None | object = ...,
    document_id: str | None = None,
):
    return build_reviewed_canonical_relocation_plan(
        scope=fixture.scope,
        inventory=fixture.inventory,
        current_plan=fixture.current_plan,  # type: ignore[arg-type]
        relocation_artifact=fixture.artifact,
        backup_manifest=fixture.backup if backup is ... else backup,  # type: ignore[arg-type]
        document_id=document_id if document_id is not None else "old-service-tiers",
        generated_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )


def _execute_success(
    plan,
    *,
    confirmation_phrase: str | None = None,
    index_error: str = "",
    archive_rows: int = 1,
    refresh_error: Exception | None = None,
):
    return _execute_with_page(
        plan,
        confirmation_phrase=confirmation_phrase,
        index_error=index_error,
        archive_rows=archive_rows,
        refresh_error=refresh_error,
    )


def _execute_with_page(
    plan,
    *,
    final_url: str | None = None,
    canonical_url: str | None = None,
    text: str | None = None,
    confirmation_phrase: str | None = None,
    index_error: str = "",
    archive_rows: int = 1,
    refresh_error: Exception | None = None,
):
    assert plan.new_canonical is not None
    source = _source()
    fetch_url = plan.new_canonical.fetch_url
    page = _page(final_url or fetch_url)
    fetcher = FakeFetcher({fetch_url: page})
    extracted = _extracted(
        source_url=fetch_url,
        canonical_url=canonical_url or plan.new_canonical.document_key,
        text=text or "service tiers service_tier priority latency limits routing examples without boilerplate",
    )
    indexer = FakeIndexer(error=index_error)
    repo = FakeRelocationRepository(archive_rows=archive_rows, refresh_error=refresh_error)
    result = asyncio.run(
        execute_reviewed_canonical_relocation(
            plan=plan,
            fetcher=fetcher,
            extractor=FakeExtractor({page.url: extracted}),
            indexer=indexer,
            repository=repo,
            confirmation_phrase=plan.expected_confirmation_phrase if confirmation_phrase is None else confirmation_phrase,
            source=source,
        )
    )
    return result, fetcher, indexer, repo


def _scope(
    service_id: str,
    source_id: str,
    *,
    source_domains: tuple[str, ...],
    allow_patterns: tuple[str, ...],
) -> SourceScope:
    return SourceScope(
        service_id=service_id,
        display_name=service_id.title(),
        source_id=source_id,
        source_title=source_id.replace("_", " ").title(),
        source_type="active_candidate_docs",
        registered=True,
        source_config={
            "source_id": source_id,
            "source_kind": "external_docs",
            "allowed_domains": list(source_domains),
            "start_urls": ["https://docs.example.com/"],
            "allow_patterns": list(allow_patterns),
            "deny_patterns": ["/login"],
            "crawl_depth": 0,
            "max_pages": 2,
            "refresh_days": 14,
        },
    )


def _inventory(
    *,
    source_id: str,
    old_key: str,
    new_key: str,
    target_id: str,
    collision_status: str,
    duplicate_active: bool,
) -> SourceInventory:
    docs: list[dict[str, object]] = [
        _doc(target_id, old_key, source_id=source_id, title="Service Tiers"),
        _doc("app-attribution", "https://docs.example.com/app-attribution", source_id=source_id, title="App Attribution"),
        _doc("protected-mcp-old", "https://docs.example.com/mcp-server", source_id=source_id, title="MCP", status="archived"),
        _doc("protected-mcp-new", "https://docs.example.com/guides/overview/mcp-server", source_id=source_id, title="MCP"),
        _doc("other", "https://docs.example.com/other", source_id=source_id, title="Other"),
    ]
    if collision_status:
        status = "active" if collision_status in {"active", "foreign"} else "archived"
        collision_source = "foreign_docs" if collision_status == "foreign" else source_id
        docs.append(_doc("collision", new_key, source_id=collision_source, title="Collision", status=status))
    if duplicate_active:
        docs.append(_doc("duplicate", old_key, source_id=source_id, title="Duplicate"))
    cards = tuple(_card(row["id"]) for row in docs)
    sections = tuple(_section(row["id"], index) for row in docs for index in range(2))
    chunks = tuple(_chunk(row["id"], index) for row in docs for index in range(3))
    return SourceInventory(
        workspace_id="workspace-1",
        workspace_name="team",
        documents=tuple(docs),
        document_cards=cards,
        sections=sections,
        chunks=chunks,
        term_statistics_count=10,
    )


def _doc(document_id: str, key: str, *, source_id: str, title: str, status: str = "active") -> dict[str, object]:
    return {
        "id": document_id,
        "workspace_id": "workspace-1",
        "source_type": "external_docs",
        "filename": key.rsplit("/", 1)[-1] + ".html",
        "document_key": key,
        "title": title,
        "module": source_id,
        "version": 1,
        "status": status,
        "content_hash": f"hash-{document_id}",
        "metadata": {
            "source_name": source_id,
            "source_url": key,
            "canonical_url": key,
            "ingestion": {"signature": f"signature-{document_id}"},
        },
        "created_at": "2026-07-10T18:40:00+00:00",
        "updated_at": "2026-07-10T18:40:00+00:00",
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


def _artifact(
    *,
    service_id: str,
    source_id: str,
    old_key: str,
    new_key: str,
    fetch_url: str,
    target_id: str,
    owner_review_status: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": CANONICAL_RELOCATION_REVIEW_SCHEMA_VERSION,
        "generated_at": "2026-07-10T00:00:00Z",
        "service_id": service_id,
        "source_id": source_id,
        "workspace_id": "workspace-1",
        "workspace_name": "team",
        "owner_review_status": owner_review_status,
        "owner_decision_source": "owner",
        "reviewed_at": "2026-07-10T00:00:00Z",
        "rationale": "confirmed canonical relocation",
        "old_document": {
            "document_id": target_id,
            "document_key": old_key,
            "status": "active",
            "version": 1,
            "content_hash": f"hash-{target_id}",
            "ingestion_signature": f"signature-{target_id}",
        },
        "new_canonical": {
            "document_key": new_key,
            "fetch_url": fetch_url,
            "expected_source_id": source_id,
            "expected_workspace_id": "workspace-1",
            "expected_new_version_policy": "first_version_if_absent",
        },
        "relationship": {
            "decision": "canonical_relocation",
            "materially_equivalent": True,
            "relocation_confidence": "high",
            "content_intent": "Service Tiers",
            "required_useful_content_terms": ["service tiers", "service_tier", "priority", "latency", "limits", "routing"],
            "cleaner_expectations": ["generic boilerplate removed"],
        },
        "evidence": {
            "old_url_status": "404",
            "new_url_status": "200",
            "canonical_tag": new_key,
            "redirect_evidence": "none",
            "inventory_collision_status": "none",
        },
        "safety": {
            "automatic_execution_allowed": False,
        },
    }
    payload["checksum"] = _payload_checksum(payload)
    return payload


def _page(url: str) -> CrawledPage:
    return CrawledPage(
        source_name="example_docs",
        url=url,
        html="<html><main>content</main></html>",
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )


def _extracted(*, source_url: str, canonical_url: str, text: str) -> ExtractedPage:
    return ExtractedPage(
        source_name="example_docs",
        source_url=source_url,
        canonical_url=canonical_url,
        title=canonical_url.rsplit("/", 1)[-1],
        structured_text=text,
        content_hash=f"hash-{abs(hash(text))}",
        headings=("Service Tiers",),
        crawled_at=datetime.now(timezone.utc),
    )


def _source() -> ExternalDocSource:
    return ExternalDocSource(
        name="example_docs",
        source_kind="external_docs",
        allowed_domains=("docs.example.com",),
        start_urls=("https://docs.example.com/",),
        allow_patterns=(r"^https://docs\.example\.com/",),
        deny_patterns=(),
        crawl_depth=0,
        max_pages=1,
    )


def _replace_doc(inventory: SourceInventory, document_id: str, updates: dict[str, object]) -> SourceInventory:
    return replace(
        inventory,
        documents=tuple({**row, **updates} if row.get("id") == document_id else row for row in inventory.documents),
    )
