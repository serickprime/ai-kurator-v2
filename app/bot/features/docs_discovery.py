"""Thin Telegram surface for unknown-service docs discovery."""

from __future__ import annotations

from typing import Any, Protocol

from telegram import Update

from app.docs_registry.discovery import DISCOVERY_USER_MESSAGE, LOW_CONFIDENCE_OWNER_MESSAGE, DocsDiscoveryOutcome


class DocsDiscoveryReader(Protocol):
    """Service interface used by Telegram text handling."""

    async def discover_from_question(
        self,
        question: str,
        *,
        workspace_id: str,
        requested_by_user_id: int | None = None,
    ) -> DocsDiscoveryOutcome:
        """Create or reuse one suggestion when a safe unknown docs candidate is found."""


async def maybe_send_docs_discovery_suggestion(
    update: Update,
    *,
    discovery_service: DocsDiscoveryReader | None,
    question: str,
    workspace_id: str,
    requested_by_user_id: int | None,
    is_owner_or_admin: bool,
    reply_markup: Any | None = None,
) -> bool:
    """Handle unknown-service discovery before normal RAG when a suggestion is created."""
    if update.message is None or discovery_service is None:
        return False
    try:
        outcome = await discovery_service.discover_from_question(
            question,
            workspace_id=workspace_id,
            requested_by_user_id=requested_by_user_id,
        )
    except Exception:  # noqa: BLE001 - discovery must never break normal Telegram runtime
        return False
    if outcome.suggestion is not None:
        await update.message.reply_text(DISCOVERY_USER_MESSAGE, reply_markup=reply_markup)
        return True
    if is_owner_or_admin and outcome.handled and outcome.reason == "low_confidence":
        await update.message.reply_text(LOW_CONFIDENCE_OWNER_MESSAGE, reply_markup=reply_markup)
        return True
    return False
