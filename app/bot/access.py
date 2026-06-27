"""Telegram user access helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class BotUserStore(Protocol):
    """Minimal async bot-user store."""

    async def get_user(self, telegram_user_id: int) -> dict[str, Any] | None:
        """Return a bot user row."""


@dataclass(frozen=True)
class UserAccessPolicy:
    """Role checks for server-side Telegram commands."""

    owner_ids: tuple[int, ...] = ()
    fallback_admin_ids: tuple[int, ...] = ()

    def is_owner(self, telegram_user_id: int | None) -> bool:
        """Return true when a Telegram id is configured as owner."""
        return bool(telegram_user_id and telegram_user_id in set(self.owner_ids))

    def open_access(self) -> bool:
        """Return true when no explicit owner/admin allow-list is configured."""
        return not self.owner_ids and not self.fallback_admin_ids

    async def is_allowed(self, telegram_user_id: int | None, store: BotUserStore | None = None) -> bool:
        """Return true when the user may talk to the bot."""
        if not telegram_user_id:
            return False
        if self.is_owner(telegram_user_id):
            return True
        if store is not None:
            try:
                user = await store.get_user(telegram_user_id)
            except Exception:
                user = None
            if user:
                return bool(user.get("is_active")) and str(user.get("role") or "user") in {"owner", "admin", "user"}
        return self.open_access() or telegram_user_id in set(self.fallback_admin_ids)

    async def role_for(self, telegram_user_id: int | None, store: BotUserStore | None = None) -> str:
        """Return a display role for status/debug commands."""
        if self.is_owner(telegram_user_id):
            return "owner"
        if not telegram_user_id:
            return "none"
        if store is not None:
            try:
                user = await store.get_user(telegram_user_id)
            except Exception:
                user = None
            if user and user.get("is_active"):
                return str(user.get("role") or "user")
        if self.open_access():
            return "user"
        return "admin" if telegram_user_id in set(self.fallback_admin_ids) else "none"


def parse_telegram_ids(value: str) -> tuple[int, ...]:
    """Parse a comma-separated Telegram id list."""
    ids: list[int] = []
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError:
            continue
    return tuple(dict.fromkeys(ids))
