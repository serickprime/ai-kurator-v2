from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.db.repositories import DocsCandidateSuggestionRepository
from app.db.supabase_client import SupabaseRequestError
from app.docs_registry.candidate_suggestions import DocsCandidateSuggestionService


def test_repository_creates_gets_lists_and_reuses_pending_suggestion() -> None:
    client = FakeSuggestionClient()
    repository = DocsCandidateSuggestionRepository(client)  # type: ignore[arg-type]

    created = asyncio.run(
        repository.create_pending(
            workspace_id="workspace-1",
            service_id="demo_service",
            display_name="Demo Service",
            aliases=("demo", "demo", ""),
            official_url="https://docs.example.com/start/",
            allowed_domain="DOCS.EXAMPLE.COM",
            source_query="how to use demo",
            discovery_reason="test",
            confidence=2.0,
            risk_level="low",
            requested_by_user_id=42,
        )
    )

    assert created.status == "pending"
    assert created.preview_status == "not_run"
    assert created.confidence == 1.0
    assert created.aliases == ("demo",)
    assert created.allowed_domain == "docs.example.com"

    fetched = asyncio.run(repository.get(created.id))
    existing = asyncio.run(
        repository.find_by_service_url(
            workspace_id="workspace-1",
            service_id="Demo Service",
            official_url="https://docs.example.com/start",
        )
    )
    reused = asyncio.run(
        repository.create_pending(
            workspace_id="workspace-1",
            service_id="demo service",
            display_name="Demo Service",
            aliases=("demo",),
            official_url="https://docs.example.com/start",
            allowed_domain="docs.example.com",
            source_query="again",
            discovery_reason="test",
            confidence=0.5,
            risk_level="low",
            requested_by_user_id=None,
        )
    )
    pending = asyncio.run(repository.list_pending("workspace-1"))

    assert fetched is not None
    assert fetched.id == created.id
    assert existing is not None
    assert existing.id == created.id
    assert reused.id == created.id
    assert [suggestion.id for suggestion in pending] == [created.id]
    assert len(client.inserts) == 1


def test_repository_saves_preview_updates_status_and_rejects() -> None:
    client = FakeSuggestionClient()
    repository = DocsCandidateSuggestionRepository(client)  # type: ignore[arg-type]
    suggestion = asyncio.run(_create_demo(repository))

    previewed = asyncio.run(
        repository.save_preview_result(
            suggestion.id,
            preview_status="ok",
            preview_result={"pages_found": 3},
        )
    )
    approved = asyncio.run(repository.update_status(suggestion.id, "approved", reviewed_by_user_id=7))
    activated = asyncio.run(
        repository.save_activation_result(
            suggestion.id,
            activation_result={"status": "activated", "indexed_new": 1},
            status="activated",
            reviewed_by_user_id=9,
        )
    )
    rejected = asyncio.run(
        repository.reject(
            suggestion.id,
            reviewed_by_user_id=8,
            rejection_reason="not needed",
        )
    )

    assert previewed.status == "preview_ready"
    assert previewed.preview_status == "ok"
    assert previewed.preview_result == {"pages_found": 3}
    assert approved.status == "approved"
    assert approved.reviewed_by_user_id == 7
    assert approved.reviewed_at is not None
    assert activated.status == "activated"
    assert activated.reviewed_by_user_id == 9
    assert activated.metadata["activation_result"] == {"status": "activated", "indexed_new": 1}
    assert rejected.status == "rejected"
    assert rejected.reviewed_by_user_id == 8
    assert rejected.rejection_reason == "not needed"


def test_repository_lists_failed_suggestions_for_retry() -> None:
    client = FakeSuggestionClient()
    repository = DocsCandidateSuggestionRepository(client)  # type: ignore[arg-type]
    suggestion = asyncio.run(_create_demo(repository))

    failed = asyncio.run(
        repository.save_preview_result(
            suggestion.id,
            preview_status="failed",
            preview_result={"status": "failed", "error": "RuntimeError"},
            status="failed",
        )
    )
    reviewable = asyncio.run(repository.list_pending("workspace-1"))

    assert failed.status == "failed"
    assert failed.preview_status == "failed"
    assert [item.id for item in reviewable] == [suggestion.id]


def test_repository_recovers_from_unique_duplicate_race() -> None:
    client = FakeSuggestionClient(
        rows=(
            _row(
                id="existing",
                workspace_id="workspace-1",
                service_id="demo_service",
                official_url="https://docs.example.com/start",
            ),
        ),
        empty_select_calls=1,
        duplicate_once=True,
    )
    repository = DocsCandidateSuggestionRepository(client)  # type: ignore[arg-type]

    suggestion = asyncio.run(
        repository.create_pending(
            workspace_id="workspace-1",
            service_id="demo_service",
            display_name="Demo Service",
            aliases=("demo",),
            official_url="https://docs.example.com/start/",
            allowed_domain="docs.example.com",
            source_query="query",
            discovery_reason="test",
            confidence=0.8,
            risk_level="low",
            requested_by_user_id=None,
        )
    )

    assert suggestion.id == "existing"
    assert len(client.inserts) == 1


def test_suggestion_service_creates_or_reuses_candidate_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "docs_source_candidates.yaml"
    config_path.write_text(_candidate_catalog(), encoding="utf-8")
    client = FakeSuggestionClient()
    repository = DocsCandidateSuggestionRepository(client)  # type: ignore[arg-type]
    service = DocsCandidateSuggestionService(repository, candidates_config_path=config_path)

    created = asyncio.run(
        service.create_or_reuse_pending_from_candidate(
            workspace_id="workspace-1",
            service_id="demo",
            source_query="how to connect demo",
            requested_by_user_id=42,
            confidence=0.75,
        )
    )
    reused = asyncio.run(
        service.create_or_reuse_pending_from_candidate(
            workspace_id="workspace-1",
            service_id="demo",
            source_query="again",
        )
    )

    assert created.created is True
    assert created.suggestion.service_id == "demo"
    assert created.suggestion.display_name == "Demo"
    assert created.suggestion.official_url == "https://docs.example.com/start"
    assert created.suggestion.allowed_domain == "docs.example.com"
    assert created.suggestion.source_query == "how to connect demo"
    assert created.suggestion.metadata["docs_source"] == "demo_docs"
    assert created.suggestion.metadata["max_pages"] == 10
    assert reused.created is False
    assert reused.suggestion.id == created.suggestion.id
    assert len(client.inserts) == 1


def test_schema_and_migration_share_suggestion_dedupe_contract() -> None:
    schema = Path("app/db/schema.sql").read_text(encoding="utf-8")
    migration = Path("app/db/migrations/20260716_create_docs_candidate_suggestions.sql").read_text(encoding="utf-8")
    service_expr = "regexp_replace(lower(btrim(service_id)), '[^a-z0-9]+', '_', 'g')"
    url_expr = "lower(regexp_replace(btrim(official_url), '/+$', '', 'g'))"

    assert service_expr in schema
    assert service_expr in migration
    assert url_expr in schema
    assert url_expr in migration


async def _create_demo(repository: DocsCandidateSuggestionRepository):
    return await repository.create_pending(
        workspace_id="workspace-1",
        service_id="demo_service",
        display_name="Demo Service",
        aliases=("demo",),
        official_url="https://docs.example.com/start",
        allowed_domain="docs.example.com",
        source_query="query",
        discovery_reason="test",
        confidence=0.8,
        risk_level="low",
        requested_by_user_id=None,
    )


class FakeSuggestionClient:
    def __init__(
        self,
        *,
        rows: tuple[dict[str, Any], ...] = (),
        empty_select_calls: int = 0,
        duplicate_once: bool = False,
    ) -> None:
        self.rows = [deepcopy(row) for row in rows]
        self.empty_select_calls = empty_select_calls
        self.duplicate_once = duplicate_once
        self.inserts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    async def select(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        assert table == "docs_candidate_suggestions"
        if self.empty_select_calls > 0:
            self.empty_select_calls -= 1
            return []
        params = params or {}
        rows = list(self.rows)
        for key in ("id", "workspace_id", "service_id"):
            if key in params:
                rows = [row for row in rows if row.get(key) == _eq(params[key])]
        if "status" in params:
            status_filter = str(params["status"])
            if status_filter.startswith("eq."):
                rows = [row for row in rows if row.get("status") == _eq(status_filter)]
            elif status_filter.startswith("in.("):
                statuses = set(status_filter.removeprefix("in.(").removesuffix(")").split(","))
                rows = [row for row in rows if row.get("status") in statuses]
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        return deepcopy(rows)

    async def insert(self, table: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        assert table == "docs_candidate_suggestions"
        self.inserts.append(deepcopy(payload))
        if self.duplicate_once:
            self.duplicate_once = False
            raise SupabaseRequestError(409, '{"code":"23505","message":"duplicate key value"}')
        row = _row(
            id=f"suggestion-{len(self.rows) + 1}",
            workspace_id=str(payload["workspace_id"]),
            service_id=str(payload["service_id"]),
            official_url=str(payload["official_url"]),
        )
        row.update(payload)
        self.rows.append(row)
        return [deepcopy(row)]

    async def update(self, table: str, payload: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
        assert table == "docs_candidate_suggestions"
        self.updates.append(deepcopy(payload))
        target_id = _eq(params["id"])
        updated: list[dict[str, Any]] = []
        for row in self.rows:
            if row["id"] == target_id:
                row.update(payload)
                updated.append(deepcopy(row))
        return updated


def _row(
    *,
    id: str,
    workspace_id: str,
    service_id: str,
    official_url: str,
    **overrides: Any,
) -> dict[str, Any]:
    row = {
        "id": id,
        "workspace_id": workspace_id,
        "service_id": service_id,
        "display_name": "Demo Service",
        "aliases": ["demo"],
        "official_url": official_url,
        "allowed_domain": "docs.example.com",
        "source_query": "",
        "discovery_reason": "test",
        "confidence": 0.8,
        "risk_level": "low",
        "status": "pending",
        "preview_status": "not_run",
        "preview_result": {},
        "requested_by_user_id": None,
        "created_at": "2026-07-16T00:00:00+00:00",
        "updated_at": "2026-07-16T00:00:00+00:00",
        "reviewed_at": None,
        "reviewed_by_user_id": None,
        "rejection_reason": "",
        "metadata": {},
    }
    row.update(overrides)
    return row


def _eq(value: object) -> str:
    return str(value).removeprefix("eq.")


def _candidate_catalog() -> str:
    return """candidates:
  - service_id: demo
    display_name: Demo
    aliases:
      - demo
      - demo service
    docs_source: demo_docs
    official_start_urls:
      - https://docs.example.com/start
    allowed_domains:
      - docs.example.com
    allow_patterns:
      - "^https://docs\\.example\\.com/"
    deny_patterns:
      - "/login"
    max_pages: 10
    crawl_depth: 1
    risk_level: low
    notes: "test candidate"
"""
