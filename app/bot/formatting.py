"""Telegram answer formatting helpers."""

from collections.abc import Sequence

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
