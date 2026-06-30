"""Telegram answer formatting helpers."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from app.db.repositories import UserSettings
from app.rag.source_labels import SourceLabelBuilder
from app.rag.types import SourceRef

COMMAND_RE = re.compile(
    r"^(?:python|pip|uv|npm|npx|node|git|docker|docker compose|cd|dir|ls|copy|set|"
    r"Invoke-|Get-|Start-|Stop-|Remove-|New-|curl|ssh|supabase|psql)\b",
    re.IGNORECASE,
)


def format_for_telegram(text: str) -> str:
    """Escape and lightly normalize model text for Telegram HTML parse mode."""
    blocks = _split_fenced_code(text or "")
    rendered: list[str] = []
    for kind, value in blocks:
        if kind == "code":
            code = value.strip("\n")
            if code:
                rendered.append(f"<pre><code>{html.escape(code)}</code></pre>")
        else:
            plain = _format_plain_text(value)
            if plain:
                rendered.append(plain)
    return "\n".join(rendered).strip()


def format_sources(sources: Sequence[SourceRef]) -> str:
    """Format source references for Telegram answers."""
    if not sources:
        return ""

    label_builder = SourceLabelBuilder()
    lines = ["Источники:"]
    for index, label in enumerate(label_builder.build_many(sources), start=1):
        lines.append(f"{index}. {label}")
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


def _split_fenced_code(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    position = 0
    for match in re.finditer(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", text, flags=re.DOTALL):
        if match.start() > position:
            parts.append(("text", text[position : match.start()]))
        parts.append(("code", match.group(1)))
        position = match.end()
    if position < len(text):
        parts.append(("text", text[position:]))
    return parts


def _format_plain_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = _humanize_line(_clean_markdown_line(raw_line.rstrip()))
        if not line:
            lines.append("")
        elif _looks_like_command(line):
            lines.append(f"<code>{html.escape(line)}</code>")
        else:
            lines.append(html.escape(line))
    return "\n".join(lines).strip()


def _clean_markdown_line(line: str) -> str:
    line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
    line = re.sub(r"^\s*[-*+]\s+", "- ", line)
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = re.sub(r"__(.*?)__", r"\1", line)
    line = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"\1", line)
    line = re.sub(r"(?<![\w])_(?!_)(.*?)(?<!_)_(?![\w])", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    return line


def _humanize_line(line: str) -> str:
    line = line.replace("\u00a0", " ").replace("\u202f", " ")
    line = line.translate(str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-"}))
    line = re.sub(r"\bпо\s*-\s*умолчанию\b", "по умолчанию", line, flags=re.IGNORECASE)
    line = re.sub(r"\bсразу\s*-\s*сразу\b", "сразу", line, flags=re.IGNORECASE)
    line = re.sub(r"\bочень\s*-\s*очень\b", "очень", line, flags=re.IGNORECASE)
    line = re.sub(r"\bбыстро\s*-\s*быстро\b", "быстро", line, flags=re.IGNORECASE)
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line.strip()


def _looks_like_command(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("$ ", "> ")):
        return True
    return bool(COMMAND_RE.match(stripped))
