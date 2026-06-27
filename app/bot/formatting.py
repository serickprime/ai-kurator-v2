"""Telegram answer formatting helpers."""

from __future__ import annotations

from collections.abc import Sequence

from app.db.repositories import UserSettings
from app.rag.types import SourceRef


def format_sources(sources: Sequence[SourceRef]) -> str:
    """Format source references for Telegram answers."""
    if not sources:
        return ""

    lines = ["Источники:"]
    for index, source in enumerate(sources, start=1):
        locator = f", {source.locator}" if source.locator else ""
        lines.append(f"{index}. {source.document_title}{locator}")
    return "\n".join(lines)


def format_status(
    *,
    workspace: str,
    role: str,
    settings: UserSettings,
    embedding_model: str,
    reranker_mode: str,
    answer_models: Sequence[str],
    supabase_connected: bool,
    schema_version: str,
) -> str:
    """Format compact bot status."""
    models = ", ".join(answer_models) if answer_models else "не настроены"
    supabase = "да" if supabase_connected else "нет"
    debug = "вкл" if settings.debug_mode else "выкл"
    return "\n".join(
        [
            f"Workspace: {workspace}",
            f"Роль: {role}",
            f"Режим ответа: {settings.answer_mode}",
            f"Vision: {settings.vision_mode}",
            f"Debug: {debug}",
            f"Embeddings: {embedding_model}",
            f"Reranker: {reranker_mode}",
            f"Модели ответа: {models}",
            f"Supabase connected: {supabase}",
            f"Schema: {schema_version}",
        ]
    )
