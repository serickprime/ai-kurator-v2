"""In-memory Telegram user state for UX modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.db.repositories import UserSettings

if TYPE_CHECKING:
    from app.docs_registry.queue import DocsQueueReport


@dataclass
class BotUserState:
    """Short-lived Telegram UX state."""

    mode: str = "normal"
    uploaded_materials: int = 0
    active_conversation_id: str | None = None
    last_debug: dict[str, Any] = field(default_factory=dict)
    last_docs_queue_report: DocsQueueReport | None = None
    last_docs_queue_report_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryBotUserStateStore:
    """Simple process-local state store for Telegram UX mode."""

    def __init__(self) -> None:
        self._states: dict[int, BotUserState] = {}

    def get(self, telegram_user_id: int) -> BotUserState:
        """Return state for a user."""
        return self._states.setdefault(telegram_user_id, BotUserState())

    def set_mode(self, telegram_user_id: int, mode: str) -> BotUserState:
        """Set UX mode for a user."""
        state = self.get(telegram_user_id)
        state.mode = mode
        state.updated_at = datetime.now(timezone.utc)
        return state

    def clear_context(self, telegram_user_id: int) -> BotUserState:
        """Clear transient dialog context and upload counters."""
        state = self.get(telegram_user_id)
        state.mode = "normal"
        state.uploaded_materials = 0
        state.last_debug = {}
        state.active_conversation_id = None
        state.updated_at = datetime.now(timezone.utc)
        return state

    def set_docs_queue_report(self, telegram_user_id: int, report: DocsQueueReport) -> BotUserState:
        """Cache the latest docs activation queue report for one user."""
        state = self.get(telegram_user_id)
        now = datetime.now(timezone.utc)
        state.last_docs_queue_report = report
        state.last_docs_queue_report_at = now
        state.updated_at = now
        return state


class InMemoryUserSettingsRepository:
    """Process-local fallback repository for tests and local dry runs."""

    def __init__(self) -> None:
        self._settings: dict[int, UserSettings] = {}

    async def get(self, telegram_user_id: int) -> UserSettings:
        """Return settings for a user."""
        return self._settings.setdefault(telegram_user_id, UserSettings(telegram_user_id=telegram_user_id))

    async def save(self, settings: UserSettings) -> UserSettings:
        """Save settings for a user."""
        self._settings[settings.telegram_user_id] = settings
        return settings

    async def set_answer_mode(self, telegram_user_id: int, answer_mode: str) -> UserSettings:
        """Update answer mode."""
        current = await self.get(telegram_user_id)
        updated = UserSettings(
            telegram_user_id=telegram_user_id,
            answer_mode=answer_mode,
            vision_mode=current.vision_mode,
            debug_mode=current.debug_mode,
            selected_workspace_id=current.selected_workspace_id,
        )
        return await self.save(updated)

    async def set_vision_mode(self, telegram_user_id: int, vision_mode: str) -> UserSettings:
        """Update vision mode."""
        current = await self.get(telegram_user_id)
        updated = UserSettings(
            telegram_user_id=telegram_user_id,
            answer_mode=current.answer_mode,
            vision_mode=vision_mode,
            debug_mode=current.debug_mode,
            selected_workspace_id=current.selected_workspace_id,
        )
        return await self.save(updated)

    async def set_debug_mode(self, telegram_user_id: int, debug_mode: bool) -> UserSettings:
        """Update debug mode."""
        current = await self.get(telegram_user_id)
        updated = UserSettings(
            telegram_user_id=telegram_user_id,
            answer_mode=current.answer_mode,
            vision_mode=current.vision_mode,
            debug_mode=debug_mode,
            selected_workspace_id=current.selected_workspace_id,
        )
        return await self.save(updated)
