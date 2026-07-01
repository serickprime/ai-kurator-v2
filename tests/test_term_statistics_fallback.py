import asyncio

import httpx

from app.db.repositories import DocumentRepository
from app.db.supabase_client import SupabaseRequestError, _json_response
from app.rag.document_router import SupabaseDocumentCardStore


class MissingTermStatsClient:
    async def select(self, table: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        del params
        if table == "term_statistics":
            raise SupabaseRequestError(404, '{"code":"PGRST205","message":"Could not find the table"}')
        return []

    async def rpc(self, function_name: str, payload: dict[str, object]) -> list[dict[str, object]]:
        del payload
        if function_name == "refresh_term_statistics":
            raise SupabaseRequestError(404, '{"code":"PGRST202","message":"Could not find the function"}')
        return []


class RefreshingClient:
    async def rpc(self, function_name: str, payload: dict[str, object]) -> list[dict[str, object]]:
        del payload
        assert function_name == "refresh_term_statistics"
        return [{"refresh_term_statistics": 12}]


def test_document_card_store_term_statistics_missing_returns_empty_list() -> None:
    store = SupabaseDocumentCardStore(MissingTermStatsClient())

    rows = asyncio.run(store.list_term_statistics(workspace_id="workspace-1"))
    rows_again = asyncio.run(store.list_term_statistics(workspace_id="workspace-1"))

    assert rows == []
    assert rows_again == []


def test_document_repository_term_statistics_missing_is_fallback() -> None:
    repository = DocumentRepository(MissingTermStatsClient())  # type: ignore[arg-type]

    refreshed = asyncio.run(repository.refresh_term_statistics("workspace-1"))
    rows = asyncio.run(repository.list_term_statistics("workspace-1"))

    assert refreshed == -1
    assert rows == []


def test_refresh_term_statistics_returns_created_row_count() -> None:
    repository = DocumentRepository(RefreshingClient())  # type: ignore[arg-type]

    refreshed = asyncio.run(repository.refresh_term_statistics("workspace-1"))

    assert refreshed == 12


def test_supabase_rpc_scalar_response_is_wrapped() -> None:
    response = httpx.Response(
        200,
        json=12,
        request=httpx.Request("POST", "https://example.test/rest/v1/rpc/refresh_term_statistics"),
    )

    assert _json_response(response) == [{"result": 12}]
