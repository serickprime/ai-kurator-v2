"""Minimal Supabase REST client wrapper."""

from types import TracebackType

import httpx

from app.config import Settings


class SupabaseClient:
    """Small async HTTP client for Supabase server-side calls."""

    def __init__(self, settings: Settings) -> None:
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
