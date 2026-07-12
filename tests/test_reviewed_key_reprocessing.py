from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import pytest

from scripts import reprocess_reviewed_external_docs as cli
from app.docs_registry.reconciliation_plan import REVIEW_SCHEMA_VERSION, _payload_checksum
from app.docs_registry.reprocessing_plan import (
    SourceInventory,
    SourceScope,
    build_baseline_manifest,
    build_reprocessing_plan,
)
from app.docs_registry.reviewed_key_reprocessing import (
    NoTermStatisticsRefreshRepository,
    ReprocessingExecutionResult,
    ReviewedExternalDocsReprocessingError,
    build_reviewed_external_docs_reprocessing_plan,
    execute_reviewed_external_docs_reprocessing,
    format_reprocessing_plan_text,
    validate_reprocessed_target,
)
from app.external_docs.extractor import ExternalDocsExtractor
from app.external_docs.types import CrawledPage, ExternalDocSource, ExternalDocsIndexResult, ExtractedPage


def test_valid_preview_with_two_reviewed_keep_active_targets() -> None:
    fixture = _fixture()
    plan = _plan(fixture)

    assert plan.readiness is True
    assert plan.target_count == 2
    assert [target.reviewed_decision.owner_decision for target in plan.targets] == ["keep_active", "keep_active"]
    assert plan.full_source_crawl == "disabled"
    assert plan.arbitrary_urls == "disabled"
    assert plan.automatic_execution_allowed is False


def test_cli_parse_single_document_id_normalizes_to_tuple_contract() -> None:
    args = cli.parse_args(
        [
            "--service",
            "example",
            "--review",
            "review.json",
            "--document-id",
            "doc-id-one",
        ]
    )

    assert args.document_id == ["doc-id-one"]
    assert tuple(args.document_id or ()) == ("doc-id-one",)


def test_cli_parse_two_document_ids_preserves_order() -> None:
    args = cli.parse_args(
        [
            "--service",
            "example",
            "--review",
            "review.json",
            "--document-id",
            "doc-id-one",
            "--document-id",
            "doc-id-two",
        ]
    )

    assert args.document_id == ["doc-id-one", "doc-id-two"]
    assert tuple(args.document_id or ()) == ("doc-id-one", "doc-id-two")


def test_cli_parse_empty_target_set_stays_valid_for_service_validation() -> None:
    args = cli.parse_args(["--service", "example", "--review", "review.json"])

    assert args.document_id is None
    assert tuple(args.document_id or ()) == ()


def test_cli_preview_path_passes_repeated_document_ids_without_execution(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}
    plan = _plan(_fixture())

    async def fake_build_live_plan(args):
        captured["document_id"] = args.document_id
        return plan, FakeClosableClient(), _source()

    def fail_execution(**_kwargs):
        raise AssertionError("execution must not run without --confirm-reprocess-reviewed")

    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "reprocess_reviewed_external_docs.py",
            "--service",
            "example",
            "--review",
            "review.json",
            "--document-id",
            "doc-id-one",
            "--document-id",
            "doc-id-two",
        ],
    )
    monkeypatch.setattr(cli, "_build_live_plan", fake_build_live_plan)
    monkeypatch.setattr(cli, "execute_reviewed_external_docs_reprocessing", fail_execution)

    code = asyncio.run(cli.main_async())

    assert code == 0
    assert captured["document_id"] == ["doc-id-one", "doc-id-two"]
    assert "mode: read-only" in capsys.readouterr().out


def test_cli_help_keeps_repeated_document_id_and_no_arbitrary_url(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--help"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "--document-id DOCUMENT_ID" in output
    assert "--confirm-reprocess-reviewed" in output
    assert "--confirmation-phrase CONFIRMATION_PHRASE" in output
    assert "--confirmation-phrase-stdin" in output
    assert "process" in output
    assert "command line" in output
    assert "--url" not in output


def test_preview_json_keeps_exact_confirmation_phrase_available() -> None:
    plan = _plan(_fixture())

    payload = plan.to_dict()

    assert payload["expected_confirmation_phrase"] == plan.expected_confirmation_phrase


def test_execution_json_plan_redacts_confirmation_phrase() -> None:
    plan = _plan(_fixture())

    payload = plan.to_dict(include_confirmation_phrase=False)

    assert payload["expected_confirmation_phrase"] == "<REDACTED>"
    assert plan.expected_confirmation_phrase not in json.dumps(payload)


def test_preview_text_keeps_exact_confirmation_phrase_available() -> None:
    plan = _plan(_fixture())

    text = format_reprocessing_plan_text(plan)

    assert plan.expected_confirmation_phrase in text


def test_execution_text_redacts_confirmation_phrase() -> None:
    plan = _plan(_fixture())

    text = format_reprocessing_plan_text(plan, include_confirmation_phrase=False)

    assert "expected confirmation phrase: <REDACTED>" in text
    assert plan.expected_confirmation_phrase not in text


def test_preview_performs_no_fetch_and_no_writes() -> None:
    fixture = _fixture()
    fetcher = FakeFetcher({})
    indexer = FakeIndexer()

    plan = _plan(fixture)

    assert plan.readiness is True
    assert fetcher.calls == []
    assert indexer.calls == []


def test_cli_execution_json_reads_confirmation_from_stdin_and_redacts_output(monkeypatch, capsys) -> None:
    plan = _plan(_fixture(), document_ids=("target-a",), max_target_count=1)
    captured: dict[str, object] = {}

    async def fake_build_live_plan(_args):
        return plan, FakeClosableClient(), _source()

    async def fake_execute(**kwargs):
        captured["confirmation_phrase"] = kwargs["confirmation_phrase"]
        return _execution_result(plan, status="reprocessed")

    _patch_cli_execution_services(monkeypatch, fake_build_live_plan=fake_build_live_plan, fake_execute=fake_execute)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "reprocess_reviewed_external_docs.py",
            "--service",
            "example",
            "--review",
            "review.json",
            "--document-id",
            "target-a",
            "--max-targets",
            "1",
            "--format",
            "json",
            "--confirm-reprocess-reviewed",
            "--confirmation-phrase-stdin",
        ],
    )
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(plan.expected_confirmation_phrase + "\n"))

    code = asyncio.run(cli.main_async())

    captured_output = capsys.readouterr()
    payload = json.loads(captured_output.out)
    assert code == 0
    assert captured["confirmation_phrase"] == plan.expected_confirmation_phrase
    assert payload["plan"]["expected_confirmation_phrase"] == "<REDACTED>"
    assert payload["confirmation"] == {"source": "stdin", "accepted": True}
    assert plan.expected_confirmation_phrase not in captured_output.out
    assert plan.expected_confirmation_phrase not in captured_output.err


def test_cli_execution_text_partial_failure_redacts_confirmation_phrase(monkeypatch, capsys) -> None:
    plan = _plan(_fixture(), document_ids=("target-a",), max_target_count=1)

    async def fake_build_live_plan(_args):
        return plan, FakeClosableClient(), _source()

    async def fake_execute(**_kwargs):
        return _execution_result(plan, status="partial_failure", partial_failure=True, rollback_required=True)

    _patch_cli_execution_services(monkeypatch, fake_build_live_plan=fake_build_live_plan, fake_execute=fake_execute)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "reprocess_reviewed_external_docs.py",
            "--service",
            "example",
            "--review",
            "review.json",
            "--document-id",
            "target-a",
            "--max-targets",
            "1",
            "--confirm-reprocess-reviewed",
            "--confirmation-phrase-stdin",
        ],
    )
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(plan.expected_confirmation_phrase + "\n"))

    code = asyncio.run(cli.main_async())

    captured_output = capsys.readouterr()
    assert code == 2
    assert "expected confirmation phrase: <REDACTED>" in captured_output.out
    assert "confirmation source: stdin" in captured_output.out
    assert plan.expected_confirmation_phrase not in captured_output.out
    assert plan.expected_confirmation_phrase not in captured_output.err


def test_cli_preview_with_stdin_flag_does_not_read_stdin(monkeypatch, capsys) -> None:
    plan = _plan(_fixture())

    class FailingStdin:
        def readline(self) -> str:
            raise AssertionError("preview must not read stdin")

    async def fake_build_live_plan(_args):
        return plan, FakeClosableClient(), _source()

    def fail_execution(**_kwargs):
        raise AssertionError("preview must not execute")

    monkeypatch.setattr(cli, "_build_live_plan", fake_build_live_plan)
    monkeypatch.setattr(cli, "execute_reviewed_external_docs_reprocessing", fail_execution)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "reprocess_reviewed_external_docs.py",
            "--service",
            "example",
            "--review",
            "review.json",
            "--document-id",
            "target-a",
            "--confirmation-phrase-stdin",
        ],
    )
    monkeypatch.setattr(cli.sys, "stdin", FailingStdin())

    code = asyncio.run(cli.main_async())

    assert code == 0
    assert plan.expected_confirmation_phrase in capsys.readouterr().out


def test_cli_rejects_confirmation_argument_and_stdin_together(capsys) -> None:
    sentinel = "SENTINEL-CONFIRMATION-PHRASE"

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(
            [
                "--service",
                "example",
                "--review",
                "review.json",
                "--confirm-reprocess-reviewed",
                "--confirmation-phrase",
                sentinel,
                "--confirmation-phrase-stdin",
            ]
        )

    captured_output = capsys.readouterr()
    assert exc_info.value.code == 2
    assert sentinel not in captured_output.out
    assert sentinel not in captured_output.err


def test_cli_execution_without_phrase_source_is_blocked() -> None:
    args = cli.parse_args(
        [
            "--service",
            "example",
            "--review",
            "review.json",
            "--confirm-reprocess-reviewed",
        ]
    )

    with pytest.raises(ReviewedExternalDocsReprocessingError, match="confirmation_phrase_required"):
        cli._read_execution_confirmation(args)


def test_cli_execution_empty_stdin_is_blocked(monkeypatch) -> None:
    args = cli.parse_args(
        [
            "--service",
            "example",
            "--review",
            "review.json",
            "--confirm-reprocess-reviewed",
            "--confirmation-phrase-stdin",
        ]
    )
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("\n"))

    with pytest.raises(ReviewedExternalDocsReprocessingError, match="confirmation_phrase_stdin_empty"):
        cli._read_execution_confirmation(args)


def test_cli_execution_eof_stdin_is_blocked(monkeypatch) -> None:
    args = cli.parse_args(
        [
            "--service",
            "example",
            "--review",
            "review.json",
            "--confirm-reprocess-reviewed",
            "--confirmation-phrase-stdin",
        ]
    )
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))

    with pytest.raises(ReviewedExternalDocsReprocessingError, match="confirmation_phrase_stdin_empty"):
        cli._read_execution_confirmation(args)


def test_legacy_command_line_confirmation_path_still_resolves() -> None:
    args = cli.parse_args(
        [
            "--service",
            "example",
            "--review",
            "review.json",
            "--confirm-reprocess-reviewed",
            "--confirmation-phrase",
            "legacy-phrase",
        ]
    )

    phrase, source = cli._read_execution_confirmation(args)

    assert phrase == "legacy-phrase"
    assert source == "argument"


def test_missing_fresh_post_archive_backup_blocks() -> None:
    plan = _plan(_fixture(), backup=None)

    assert plan.readiness is False
    assert "fresh_post_archive_backup_required" in plan.blockers


def test_stale_inventory_fingerprint_blocks() -> None:
    fixture = _fixture()
    stale_inventory = _with_mcp_status(fixture.inventory, status="active")
    stale_plan = build_reprocessing_plan(scope=fixture.scope, inventory=stale_inventory)
    stale_backup = build_baseline_manifest(plan=stale_plan, inventory=stale_inventory, include_rows=True)

    plan = _plan(fixture, backup=stale_backup)

    assert plan.readiness is False
    assert "fresh_post_archive_backup_required" in plan.blockers
    assert "baseline fingerprint changed" in plan.blockers


def test_invalid_review_checksum_blocks() -> None:
    fixture = _fixture()
    review = dict(fixture.review)
    review["decisions"] = []

    plan = _plan(replace(fixture, review=review))

    assert plan.readiness is False
    assert "reviewed artifact checksum mismatch" in plan.blockers


@pytest.mark.parametrize("decision", ["superseded_by", "archive_candidate", "needs_more_review"])
def test_non_keep_active_decisions_block_reprocessing(decision: str) -> None:
    fixture = _fixture(decisions=("keep_active", decision))

    plan = _plan(fixture)

    assert plan.readiness is False
    assert f"reviewed decision blocks reprocessing: {decision}" in plan.blockers


def test_target_id_key_mismatch_blocks() -> None:
    plan = _plan(_fixture(), document_ids=("target-a", "missing"))

    assert plan.readiness is False
    assert "target must match exactly one active document: missing" in plan.blockers


def test_status_version_hash_signature_drift_blocks() -> None:
    fixture = _fixture()
    inventory = _replace_doc(fixture.inventory, "target-a", {"version": 2})
    fixture = replace(fixture, inventory=inventory, current_plan=build_reprocessing_plan(scope=fixture.scope, inventory=inventory))

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "active versions changed" in plan.blockers


def test_target_not_present_in_review_blocks() -> None:
    fixture = _fixture(omit_second_review=True)

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "reviewed artifact does not contain target key" in plan.blockers


def test_duplicate_target_blocks() -> None:
    plan = _plan(_fixture(), document_ids=("target-a", "target-a"))

    assert plan.readiness is False
    assert "duplicate target document IDs are not allowed" in plan.blockers


def test_empty_target_set_blocks() -> None:
    plan = _plan(_fixture(), document_ids=())

    assert plan.readiness is False
    assert "target set must not be empty" in plan.blockers


def test_target_count_above_max_blocks() -> None:
    plan = _plan(_fixture(), max_target_count=1)

    assert plan.readiness is False
    assert "target count exceeds max target count: 1" in plan.blockers


def test_arbitrary_url_option_absent_from_text_output() -> None:
    text = format_reprocessing_plan_text(_plan(_fixture()))

    assert "arbitrary URLs: disabled" in text
    assert "--url" not in text
    assert "--crawl" not in text


def test_url_outside_registry_scope_blocks() -> None:
    fixture = _fixture(
        allow_openrouter=False,
        keys=("https://openrouter.ai/docs/app-attribution", "https://openrouter.ai/docs/features/service-tiers"),
    )

    plan = _plan(fixture)

    assert plan.readiness is False
    assert "target URL is outside registered source scope" in plan.blockers


def test_redirect_to_different_canonical_key_blocks_before_writes() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    source = _source()
    fetcher = FakeFetcher({target.document.resolved_fetch_url: _page(target.document.resolved_fetch_url) for target in plan.targets})
    extractor = FakeExtractor(
        {
            plan.targets[0].document.resolved_fetch_url: _extracted(
                source_url=plan.targets[0].document.resolved_fetch_url,
                canonical_url="https://docs.example.com/other",
                text="HTTP-Referer X-OpenRouter-Title attribution categories",
            )
        }
    )
    indexer = FakeIndexer()

    result = asyncio.run(
        execute_reviewed_external_docs_reprocessing(
            plan=plan,
            fetcher=fetcher,
            extractor=extractor,
            indexer=indexer,
            term_repository=indexer,
            confirmation_phrase=plan.expected_confirmation_phrase,
            source=source,
        )
    )

    assert result.status == "blocked"
    assert indexer.calls == []


def test_fake_exact_key_fetch_touches_only_selected_targets() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    result, fetcher, _indexer = _execute_success(plan)

    assert result.status == "reprocessed"
    assert fetcher.calls == [target.document.resolved_fetch_url for target in plan.targets]


def test_full_source_crawler_discovery_not_called() -> None:
    plan = _plan(_fixture())
    result, fetcher, _indexer = _execute_success(plan)

    assert result.status == "reprocessed"
    assert fetcher.crawl_calls == []


def test_generic_cleaner_removes_boilerplate_and_preserves_terms() -> None:
    target = _plan(_fixture()).targets[0]
    page = CrawledPage(
        source_name="example_docs",
        url=target.document.resolved_fetch_url,
        html="""
        <html><head><link rel="canonical" href="https://docs.example.com/app-attribution"></head>
        <body>
        <main>
        <h1>App Attribution</h1>
        <p>Use HTTP-Referer and X-OpenRouter-Title for attribution categories.</p>
        <p>This page is also available as markdown: llms.txt</p>
        </main>
        </body></html>
        """,
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )
    extracted = ExternalDocsExtractor().extract(page)

    validation = validate_reprocessed_target(target=target, extracted=extracted, source=_source())

    assert validation["boilerplate_removed"] is True
    assert "llms.txt" not in extracted.structured_text


def test_useful_content_preservation_failure_blocks_before_writes() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    source = _source()
    fetcher = FakeFetcher({target.document.resolved_fetch_url: _page(target.document.resolved_fetch_url) for target in plan.targets})
    extractor = FakeExtractor(
        {
            plan.targets[0].document.resolved_fetch_url: _extracted(
                source_url=plan.targets[0].document.resolved_fetch_url,
                canonical_url=plan.targets[0].document.document_key,
                text="missing required terms",
            )
        }
    )
    indexer = FakeIndexer()

    result = asyncio.run(
        execute_reviewed_external_docs_reprocessing(
            plan=plan,
            fetcher=fetcher,
            extractor=extractor,
            indexer=indexer,
            term_repository=indexer,
            confirmation_phrase=plan.expected_confirmation_phrase,
            source=source,
        )
    )

    assert result.status == "blocked"
    assert "no writes performed" in result.blockers[0]
    assert indexer.calls == []


def test_one_target_pre_validation_failure_causes_zero_writes_for_all_targets() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    result, _fetcher, indexer = _execute_with_texts(plan, {plan.targets[1].document.document_key: "service_tier only"})

    assert result.status == "blocked"
    assert indexer.calls == []


def test_valid_fake_execution_creates_new_versions_for_selected_keys() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer = _execute_success(plan)

    assert result.status == "reprocessed"
    assert sorted(indexer.created_versions) == sorted((target.document.document_key, target.expected_future_version) for target in plan.targets)


def test_previous_versions_archived_only_for_selected_keys() -> None:
    plan = _plan(_fixture())
    _result, _fetcher, indexer = _execute_success(plan)

    assert sorted(indexer.archived_keys) == sorted(target.document.document_key for target in plan.targets)


def test_other_source_documents_unchanged() -> None:
    plan = _plan(_fixture())
    _result, _fetcher, indexer = _execute_success(plan)

    assert "https://docs.example.com/other" not in indexer.archived_keys


def test_archived_mcp_server_unchanged() -> None:
    plan = _plan(_fixture())
    _result, _fetcher, indexer = _execute_success(plan)

    assert "https://docs.example.com/mcp-server" not in indexer.archived_keys


def test_mcp_successor_unchanged() -> None:
    plan = _plan(_fixture())
    _result, _fetcher, indexer = _execute_success(plan)

    assert "https://docs.example.com/guides/overview/mcp-server" not in indexer.archived_keys


def test_duplicate_active_keys_remain_zero_in_preview() -> None:
    plan = _plan(_fixture())

    assert plan.readiness is True
    assert "duplicate active document keys must be zero" not in plan.blockers


def test_term_statistics_refresh_called_once_after_full_success() -> None:
    plan = _plan(_fixture())
    result, _fetcher, indexer = _execute_success(plan)

    assert result.term_statistics_status == "updated: 77"
    assert indexer.refresh_calls == ["workspace-1"]


def test_refresh_failure_returns_partial_failure() -> None:
    plan = _plan(_fixture())
    source = _source()
    fetcher = _success_fetcher(plan)
    indexer = FakeIndexer(refresh_error=RuntimeError("boom"))

    result = asyncio.run(
        execute_reviewed_external_docs_reprocessing(
            plan=plan,
            fetcher=fetcher,
            extractor=FakeExtractor(_success_extracts(plan)),
            indexer=indexer,
            term_repository=indexer,
            confirmation_phrase=plan.expected_confirmation_phrase,
            source=source,
        )
    )

    assert result.status == "partial_failure"
    assert result.rollback_required is True
    assert result.automatic_retry is False
    assert result.automatic_rollback is False


def test_partial_target_indexing_failure_returns_partial_failure_and_rollback_required() -> None:
    plan = _plan(_fixture())
    source = _source()
    indexer = FakeIndexer(fail_key=plan.targets[1].document.document_key)

    result = asyncio.run(
        execute_reviewed_external_docs_reprocessing(
            plan=plan,
            fetcher=_success_fetcher(plan),
            extractor=FakeExtractor(_success_extracts(plan)),
            indexer=indexer,
            term_repository=indexer,
            confirmation_phrase=plan.expected_confirmation_phrase,
            source=source,
        )
    )

    assert result.status == "partial_failure"
    assert result.rollback_required is True
    assert result.changed_keys == (plan.targets[0].document.document_key,)


def test_no_automatic_retry() -> None:
    plan = _plan(_fixture())
    indexer = FakeIndexer(fail_key=plan.targets[0].document.document_key)

    result = asyncio.run(
        execute_reviewed_external_docs_reprocessing(
            plan=plan,
            fetcher=_success_fetcher(plan),
            extractor=FakeExtractor(_success_extracts(plan)),
            indexer=indexer,
            term_repository=indexer,
            confirmation_phrase=plan.expected_confirmation_phrase,
            source=_source(),
        )
    )

    assert result.automatic_retry is False
    assert indexer.calls.count(plan.targets[0].document.document_key) == 1


def test_no_automatic_rollback() -> None:
    plan = _plan(_fixture())
    result, _fetcher, _indexer = _execute_success(plan)

    assert result.automatic_rollback is False


def test_child_rows_not_physically_deleted() -> None:
    plan = _plan(_fixture())
    _result, _fetcher, indexer = _execute_success(plan)

    assert indexer.deleted_child_rows == []


def test_exampledocs_generic_scenario_passes() -> None:
    fixture = _fixture(service_id="exampledocs", source_id="exampledocs_docs")
    plan = _plan(fixture)

    assert plan.service_id == "exampledocs"
    assert plan.source_id == "exampledocs_docs"
    assert plan.readiness is True


def test_no_openrouter_specific_production_branching() -> None:
    text = __import__("pathlib").Path("app/docs_registry/reviewed_key_reprocessing.py").read_text(encoding="utf-8")

    assert 'service_id == "openrouter"' not in text
    assert "openrouter_docs" not in text


def test_cli_preview_default_text() -> None:
    text = format_reprocessing_plan_text(_plan(_fixture()))

    assert "mode: read-only" in text
    assert "fetch/reprocessing: not performed" in text


def test_missing_explicit_confirmation_prevents_execution() -> None:
    plan = _plan(_fixture())
    fetcher = _success_fetcher(plan)
    indexer = FakeIndexer()

    result = asyncio.run(
        execute_reviewed_external_docs_reprocessing(
            plan=plan,
            fetcher=fetcher,
            extractor=FakeExtractor(_success_extracts(plan)),
            indexer=indexer,
            term_repository=indexer,
            confirmation_phrase="",
            source=_source(),
        )
    )

    assert result.status == "blocked"
    assert "confirmation_phrase_mismatch" in result.blockers
    assert fetcher.calls == []
    assert indexer.calls == []
    assert indexer.refresh_calls == []


def test_wrong_confirmation_phrase_does_not_leak_or_fetch() -> None:
    plan = _plan(_fixture())
    sentinel = "SENTINEL-WRONG-CONFIRMATION-PHRASE"
    fetcher = _success_fetcher(plan)
    indexer = FakeIndexer()

    result = asyncio.run(
        execute_reviewed_external_docs_reprocessing(
            plan=plan,
            fetcher=fetcher,
            extractor=FakeExtractor(_success_extracts(plan)),
            indexer=indexer,
            term_repository=indexer,
            confirmation_phrase=sentinel,
            source=_source(),
        )
    )

    serialized = json.dumps(result.to_dict())
    assert result.status == "blocked"
    assert result.blockers == ("confirmation_phrase_mismatch",)
    assert sentinel not in serialized
    assert plan.expected_confirmation_phrase not in serialized
    assert fetcher.calls == []
    assert indexer.calls == []
    assert indexer.refresh_calls == []


def test_pre_write_validation_failure_result_does_not_include_confirmation_phrase() -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    result, _fetcher, _indexer = _execute_with_texts(
        plan,
        {plan.targets[0].document.document_key: "missing required terms"},
    )

    assert result.status == "blocked"
    assert plan.expected_confirmation_phrase not in json.dumps(result.to_dict())


def test_cli_json_output_is_structured() -> None:
    payload = _plan(_fixture()).to_dict()

    assert payload["mode"] == "read-only"
    assert payload["target_count"] == 2
    assert payload["full_source_crawl_disabled"] is True


def test_expected_validation_errors_have_clean_messages() -> None:
    plan = _plan(_fixture(), backup=None)

    assert plan.readiness is False
    assert all("\n" not in blocker for blocker in plan.blockers)


def test_no_term_statistics_wrapper_hides_indexer_refresh() -> None:
    repo = FakeIndexer()
    wrapped = NoTermStatisticsRefreshRepository(repo)

    assert getattr(wrapped, "index_page", None) is not None
    assert getattr(wrapped, "refresh_term_statistics", None) is None


def test_openrouter_pilot_fixture_blocks_without_fresh_post_archive_backup() -> None:
    fixture = _fixture(
        service_id="openrouter",
        source_id="openrouter_docs",
        keys=(
            "https://openrouter.ai/docs/app-attribution",
            "https://openrouter.ai/docs/features/service-tiers",
        ),
        target_ids=(
            "6e6552a0-1cf4-432a-86c8-5cae1a615cb3",
            "09038305-f448-4819-a51c-f48d6ebbf090",
        ),
        source_domains=("openrouter.ai",),
        allow_patterns=(r"^https://openrouter\.ai/docs",),
    )

    plan = _plan(
        fixture,
        backup=None,
        document_ids=("6e6552a0-1cf4-432a-86c8-5cae1a615cb3", "09038305-f448-4819-a51c-f48d6ebbf090"),
    )

    assert plan.target_count == 2
    assert plan.targets[0].reviewed_decision.owner_decision == "keep_active"
    assert plan.full_source_crawl == "disabled"
    assert "fresh_post_archive_backup_required" in plan.blockers


@dataclass(frozen=True)
class ReprocessFixture:
    scope: SourceScope
    inventory: SourceInventory
    current_plan: object
    review: dict[str, object]
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
    def __init__(self, *, fail_key: str = "", refresh_error: Exception | None = None) -> None:
        self.fail_key = fail_key
        self.refresh_error = refresh_error
        self.calls: list[str] = []
        self.created_versions: list[tuple[str, int]] = []
        self.archived_keys: list[str] = []
        self.refresh_calls: list[str] = []
        self.deleted_child_rows: list[str] = []

    async def index_page(
        self,
        page: ExtractedPage,
        source: ExternalDocSource,
        *,
        workspace: str = "team",
    ) -> ExternalDocsIndexResult:
        key = page.canonical_url
        self.calls.append(key)
        if key == self.fail_key:
            return ExternalDocsIndexResult(source_name=source.name, url=page.source_url, document_key=key, error="index failed")
        version = 2
        self.created_versions.append((key, version))
        self.archived_keys.append(key)
        return ExternalDocsIndexResult(
            source_name=source.name,
            url=page.source_url,
            document_id=f"new-{len(self.calls)}",
            document_key=key,
            version=version,
            skipped=False,
            archived_old=True,
            sections_count=3,
            chunks_count=4,
        )

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        self.refresh_calls.append(workspace_id)
        if self.refresh_error:
            raise self.refresh_error
        return 77


class FakeClosableClient:
    async def close(self) -> None:
        return None


class FakeAsyncClosable:
    async def close(self) -> None:
        return None


def _patch_cli_execution_services(monkeypatch, *, fake_build_live_plan, fake_execute) -> None:
    monkeypatch.setattr(cli, "_build_live_plan", fake_build_live_plan)
    monkeypatch.setattr(cli, "DocumentRepository", lambda _client: object())
    monkeypatch.setattr(cli, "OllamaEmbeddingClient", lambda _settings: FakeAsyncClosable())
    monkeypatch.setattr(cli, "ExternalDocsCrawler", lambda: FakeAsyncClosable())
    monkeypatch.setattr(cli, "ExternalDocsIndexer", lambda **_kwargs: object())
    monkeypatch.setattr(cli, "execute_reviewed_external_docs_reprocessing", fake_execute)


def _execution_result(
    plan,
    *,
    status: str,
    partial_failure: bool = False,
    rollback_required: bool = False,
) -> ReprocessingExecutionResult:
    changed = tuple(target.document.document_key for target in plan.targets) if status == "reprocessed" else ()
    return ReprocessingExecutionResult(
        status=status,
        target_count=plan.target_count,
        targets=(),
        changed_keys=changed,
        unchanged_keys=() if changed else tuple(target.document.document_key for target in plan.targets),
        failed_keys=(),
        term_statistics_status="updated: 77" if status == "reprocessed" else "not run",
        partial_failure=partial_failure,
        rollback_required=rollback_required,
        automatic_retry=False,
        automatic_rollback=False,
        timestamp="2026-07-10T00:00:00Z",
    )


def _fixture(
    *,
    service_id: str = "example",
    source_id: str = "example_docs",
    decisions: tuple[str, str] = ("keep_active", "keep_active"),
    omit_second_review: bool = False,
    allow_openrouter: bool = True,
    keys: tuple[str, str] = ("https://docs.example.com/app-attribution", "https://docs.example.com/service-tiers"),
    target_ids: tuple[str, str] = ("target-a", "target-b"),
    source_domains: tuple[str, ...] = ("docs.example.com",),
    allow_patterns: tuple[str, ...] = (r"^https://docs\.example\.com/",),
) -> ReprocessFixture:
    if allow_openrouter:
        source_domains = tuple(dict.fromkeys((*source_domains, "openrouter.ai")))
    scope = _scope(service_id, source_id, source_domains=source_domains, allow_patterns=allow_patterns)
    inventory = _inventory(source_id=source_id, keys=keys, target_ids=target_ids)
    current_plan = build_reprocessing_plan(scope=scope, inventory=inventory)
    review = _review(
        service_id=service_id,
        source_id=source_id,
        keys=keys,
        decisions=decisions,
        omit_second=omit_second_review,
    )
    fixture = ReprocessFixture(scope=scope, inventory=inventory, current_plan=current_plan, review=review, backup={})
    return replace(fixture, backup=build_baseline_manifest(plan=current_plan, inventory=inventory, include_rows=True))


def _plan(
    fixture: ReprocessFixture,
    *,
    backup: dict[str, object] | None | object = ...,
    document_ids: tuple[str, ...] | None = None,
    max_target_count: int = 2,
):
    return build_reviewed_external_docs_reprocessing_plan(
        scope=fixture.scope,
        inventory=fixture.inventory,
        current_plan=fixture.current_plan,  # type: ignore[arg-type]
        reviewed_artifact=fixture.review,
        backup_manifest=fixture.backup if backup is ... else backup,  # type: ignore[arg-type]
        document_ids=document_ids if document_ids is not None else ("target-a", "target-b"),
        max_target_count=max_target_count,
        generated_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )


def _execute_success(plan):
    return _execute_with_texts(plan, {})


def _execute_with_texts(plan, overrides: dict[str, str]):
    source = _source()
    fetcher = _success_fetcher(plan)
    extracts = _success_extracts(plan)
    for key, text in overrides.items():
        for target in plan.targets:
            if target.document.document_key == key:
                extracts[target.document.resolved_fetch_url] = _extracted(
                    source_url=target.document.resolved_fetch_url,
                    canonical_url=key,
                    text=text,
                )
    indexer = FakeIndexer()
    result = asyncio.run(
        execute_reviewed_external_docs_reprocessing(
            plan=plan,
            fetcher=fetcher,
            extractor=FakeExtractor(extracts),
            indexer=indexer,
            term_repository=indexer,
            confirmation_phrase=plan.expected_confirmation_phrase,
            source=source,
        )
    )
    return result, fetcher, indexer


def _success_fetcher(plan) -> FakeFetcher:
    return FakeFetcher({target.document.resolved_fetch_url: _page(target.document.resolved_fetch_url) for target in plan.targets})


def _success_extracts(plan) -> dict[str, ExtractedPage]:
    result: dict[str, ExtractedPage] = {}
    for target in plan.targets:
        terms = " ".join(target.document.required_terms)
        result[target.document.resolved_fetch_url] = _extracted(
            source_url=target.document.resolved_fetch_url,
            canonical_url=target.document.document_key,
            text=f"Useful updated documentation {terms} without generator page template garbage.",
        )
    return result


def _source() -> ExternalDocSource:
    return ExternalDocSource(
        name="example_docs",
        source_kind="external_docs",
        allowed_domains=("docs.example.com", "openrouter.ai"),
        start_urls=("https://docs.example.com/",),
        allow_patterns=(r"^https://docs\.example\.com/", r"^https://openrouter\.ai/docs"),
        deny_patterns=(),
        crawl_depth=0,
        max_pages=2,
    )


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


def _inventory(*, source_id: str, keys: tuple[str, str], target_ids: tuple[str, str]) -> SourceInventory:
    docs = (
        _doc(target_ids[0], keys[0], source_id=source_id, title="App Attribution"),
        _doc(target_ids[1], keys[1], source_id=source_id, title="Service Tiers"),
        _doc("mcp-old", "https://docs.example.com/mcp-server", source_id=source_id, title="MCP Server", status="archived"),
        _doc("mcp-new", "https://docs.example.com/guides/overview/mcp-server", source_id=source_id, title="MCP Server"),
        _doc("other", "https://docs.example.com/other", source_id=source_id, title="Other"),
    )
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


def _review(
    *,
    service_id: str,
    source_id: str,
    keys: tuple[str, str],
    decisions: tuple[str, str],
    omit_second: bool,
) -> dict[str, object]:
    rows = [
        {
            "document_key": keys[0],
            "classification": "active_missing_from_snapshot",
            "owner_decision": decisions[0],
            "review_status": "reviewed",
            "required_content_terms": ["HTTP-Referer", "X-OpenRouter-Title", "attribution", "categories"],
            "allowed_decisions": ["keep_active", "archive_candidate", "superseded_by", "needs_more_review"],
            "notes": "",
        }
    ]
    if not omit_second:
        rows.append(
            {
                "document_key": keys[1],
                "classification": "active_missing_from_snapshot",
                "owner_decision": decisions[1],
                "review_status": "reviewed",
                "required_content_terms": ["service tiers", "service_tier", "priority", "latency", "limits", "routing"],
                "allowed_decisions": ["keep_active", "archive_candidate", "superseded_by", "needs_more_review"],
                "notes": "",
            }
        )
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
        "decisions": rows,
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
        headings=("Heading",),
        crawled_at=datetime.now(timezone.utc),
    )


def _replace_doc(inventory: SourceInventory, document_id: str, updates: dict[str, object]) -> SourceInventory:
    return replace(
        inventory,
        documents=tuple({**row, **updates} if row.get("id") == document_id else row for row in inventory.documents),
    )


def _with_mcp_status(inventory: SourceInventory, *, status: str) -> SourceInventory:
    return _replace_doc(inventory, "mcp-old", {"status": status})
