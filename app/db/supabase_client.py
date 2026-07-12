"""Minimal Supabase REST client wrapper."""

from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.config import Settings


class SupabaseClient:
    """Small async HTTP client for Supabase server-side calls."""

    def __init__(self, settings: "Settings") -> None:
        if not settings.supabase_url:
            raise RuntimeError("SUPABASE_URL is required")
        if not settings.supabase_service_role_key:
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required")

        self._base_url = settings.supabase_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
            },
            timeout=30.0,
            trust_env=False,
        )

    async def select(
        self,
        table: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Select rows from a Supabase table through PostgREST."""
        response = await self._client.get(f"/rest/v1/{table}", params=params)
        return _json_response(response)

    async def insert(
        self,
        table: str,
        payload: dict[str, Any] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Insert rows and return their representation."""
        response = await self._client.post(
            f"/rest/v1/{table}",
            json=payload,
            headers={"Prefer": "return=representation"},
        )
        return _json_response(response)

    async def update(
        self,
        table: str,
        payload: dict[str, Any],
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Update rows and return their representation."""
        response = await self._client.patch(
            f"/rest/v1/{table}",
            params=params,
            json=payload,
            headers={"Prefer": "return=representation"},
        )
        return _json_response(response)

    async def delete(
        self,
        table: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Delete rows and return their representation."""
        response = await self._client.delete(
            f"/rest/v1/{table}",
            params=params,
            headers={"Prefer": "return=representation"},
        )
        return _json_response(response)

    async def rpc(
        self,
        function_name: str,
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Call a Postgres function through Supabase PostgREST RPC."""
        response = await self._client.post(
            f"/rest/v1/rpc/{function_name}",
            json=payload or {},
        )
        return _json_response(response)

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()

    async def __aenter__(self) -> "SupabaseClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.close()


class SupabaseRequestError(RuntimeError):
    """HTTP error returned by Supabase/PostgREST."""

    def __init__(self, status_code: int, body: str, *, path: str = "") -> None:
        self.status_code = status_code
        self.body = body
        self.path = path
        super().__init__(f"Supabase request failed: {status_code} {body}")

    @property
    def is_missing_relation(self) -> bool:
        """Return true for missing table/function/schema-cache errors."""
        lowered = self.body.lower()
        return self.status_code == 404 and (
            "pgrst" in lowered
            or "could not find" in lowered
            or "not found" in lowered
            or "schema cache" in lowered
        )


def _json_response(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SupabaseRequestError(
            exc.response.status_code,
            exc.response.text,
            path=str(exc.request.url),
        ) from exc

    if not response.content:
        return []
    data = response.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    if isinstance(data, (str, int, float, bool)) or data is None:
        return [{"result": data}]
    raise RuntimeError("Unexpected Supabase response shape")
