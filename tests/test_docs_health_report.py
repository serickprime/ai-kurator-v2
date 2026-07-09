from datetime import datetime, timezone

from app.service_registry.docs_health import (
    DocsHealthPolicy,
    build_docs_health_report,
    filter_docs_health_report,
    load_docs_health_policy,
)
from app.service_registry.types import ServiceDocsStatus


NOW = datetime(2026, 7, 9, tzinfo=timezone.utc)


def test_healthy_active_source_is_not_stale() -> None:
    report = build_docs_health_report(
        statuses=(_status("n8n", "n8n_docs"),),
        documents=[_doc("doc-1", "n8n_docs", updated_at="2026-07-01T00:00:00Z")],
        policy=DocsHealthPolicy(default_stale_after_days=30),
        runtime_status="available",
        now=NOW,
    )

    source = report.sources[0]

    assert source.current_status == "healthy"
    assert source.stale_status == "fresh"
    assert source.owner_review_required is False
    assert source.automatic_refresh_allowed is False


def test_active_stale_source_keeps_stale_separate_from_operational_status() -> None:
    report = build_docs_health_report(
        statuses=(_status("openrouter", "openrouter_docs"),),
        documents=[_doc("doc-1", "openrouter_docs", updated_at="2026-04-01T00:00:00Z")],
        policy=DocsHealthPolicy(default_stale_after_days=30),
        runtime_status="available",
        now=NOW,
    )

    source = report.sources[0]

    assert source.current_status == "stale"
    assert source.stale_status == "stale"
    assert "threshold is 30 days" in source.stale_reason
    assert source.owner_review_required is True
    assert source.automatic_refresh_allowed is False


def test_last_known_failed_source_reports_failure_without_fixing_it() -> None:
    report = build_docs_health_report(
        statuses=(
            _status(
                "telegram_bot_api",
                "telegram_bot_api_docs",
                docs_status="indexed",
                quality="FAIL",
                notes=("quality gate returned FAIL", "active docs without source_url/canonical_url"),
            ),
        ),
        documents=[_doc("doc-1", "telegram_bot_api_docs", updated_at="2026-07-01T00:00:00Z")],
        policy=DocsHealthPolicy(default_stale_after_days=30),
        runtime_status="available",
        now=NOW,
    )

    source = report.sources[0]

    assert source.current_status == "failed"
    assert "quality gate returned FAIL" in source.status_reason
    assert source.suggested_next_action == "review last quality errors before any explicit refresh"
    assert source.automatic_refresh_allowed is False


def test_inactive_source_is_not_healthy() -> None:
    report = build_docs_health_report(
        statuses=(
            _status(
                "stripe",
                "stripe_docs",
                docs_status="configured_not_indexed",
                active_docs=0,
                active_chunks=0,
                quality="none",
            ),
        ),
        documents=[],
        policy=DocsHealthPolicy(default_stale_after_days=30),
        runtime_status="available",
        now=NOW,
    )

    source = report.sources[0]

    assert source.current_status == "inactive"
    assert source.stale_status == "not_applicable"
    assert source.owner_review_required is True


def test_missing_timestamp_is_unknown_not_false_stale_or_failed() -> None:
    report = build_docs_health_report(
        statuses=(_status("supabase", "supabase_docs"),),
        documents=[_doc("doc-1", "supabase_docs", updated_at=None)],
        policy=DocsHealthPolicy(default_stale_after_days=30),
        runtime_status="available",
        now=NOW,
    )

    source = report.sources[0]

    assert source.current_status == "healthy"
    assert source.stale_status == "unknown"
    assert source.age_days is None
    assert "timestamp not available" in source.stale_reason


def test_runtime_unavailable_does_not_mark_sources_failed() -> None:
    report = build_docs_health_report(
        statuses=(_status("n8n", "n8n_docs"),),
        documents=[],
        policy=DocsHealthPolicy(default_stale_after_days=30),
        runtime_status="unavailable: ConnectError",
        now=NOW,
    )

    source = report.sources[0]

    assert source.current_status == "unknown"
    assert source.stale_status == "unknown"
    assert "runtime status could not be verified" in source.stale_reason
    assert "ConnectError" in report.runtime_status


def test_report_filtering_by_service_status_and_stale_only() -> None:
    report = build_docs_health_report(
        statuses=(
            _status("n8n", "n8n_docs"),
            _status("openrouter", "openrouter_docs"),
            _status("telegram_bot_api", "telegram_bot_api_docs", quality="FAIL"),
        ),
        documents=[
            _doc("doc-1", "n8n_docs", updated_at="2026-07-01T00:00:00Z"),
            _doc("doc-2", "openrouter_docs", updated_at="2026-04-01T00:00:00Z"),
            _doc("doc-3", "telegram_bot_api_docs", updated_at="2026-07-01T00:00:00Z"),
        ],
        policy=DocsHealthPolicy(default_stale_after_days=30),
        runtime_status="available",
        now=NOW,
    )

    assert [row.service_id for row in filter_docs_health_report(report, service="openrouter").sources] == [
        "openrouter"
    ]
    assert [row.service_id for row in filter_docs_health_report(report, status="failed").sources] == [
        "telegram_bot_api"
    ]
    assert [row.service_id for row in filter_docs_health_report(report, stale_only=True).sources] == [
        "openrouter"
    ]


def test_policy_threshold_can_be_changed_by_config(tmp_path) -> None:
    policy_path = tmp_path / "docs_health_policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "default_stale_after_days: 90",
                "sources:",
                "  - source_id: openrouter_docs",
                "    stale_after_days: 7",
            ]
        ),
        encoding="utf-8",
    )

    policy = load_docs_health_policy(policy_path)

    assert policy.default_stale_after_days == 90
    assert policy.threshold_for(service_id="openrouter", source_id="openrouter_docs") == 7


def _status(
    service_id: str,
    docs_source: str,
    *,
    docs_status: str = "indexed",
    active_docs: int = 1,
    active_chunks: int = 10,
    quality: str = "PASS",
    notes: tuple[str, ...] = (),
) -> ServiceDocsStatus:
    return ServiceDocsStatus(
        service_id=service_id,
        display_name=service_id.replace("_", " ").title(),
        aliases=(service_id,),
        docs_source=docs_source,
        configured_status="enabled",
        docs_status=docs_status,  # type: ignore[arg-type]
        active_docs_count=active_docs,
        active_chunks_count=active_chunks,
        quality_status=quality,
        docs_source_configured=True,
        notes=notes,
    )


def _doc(document_id: str, source_name: str, *, updated_at: str | None) -> dict[str, object]:
    row: dict[str, object] = {
        "id": document_id,
        "filename": f"{document_id}.html",
        "document_key": f"https://docs.example.com/{document_id}",
        "title": f"Page {document_id}",
        "status": "active",
        "metadata": {
            "source_name": source_name,
            "source_url": f"https://docs.example.com/{document_id}",
            "canonical_url": f"https://docs.example.com/{document_id}",
        },
    }
    if updated_at is not None:
        row["updated_at"] = updated_at
    return row
