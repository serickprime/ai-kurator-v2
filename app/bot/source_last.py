"""Telegram helpers for last-answer source management."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class LastAnswerSource:
    """Compact source row shown for the last RAG answer."""

    source_id: str
    document_id: str
    title: str
    source_type: str
    source_origin: str
    chunks_count: int = 0
    source_uri: str = ""
    is_external: bool = False


def source_refs_to_debug_payload(sources: Iterable[Any]) -> list[dict[str, object]]:
    """Serialize SourceRef-like objects into process-local Telegram state."""
    payload: list[dict[str, object]] = []
    for source in sources:
        metadata = getattr(source, "metadata", {}) or {}
        payload.append(
            {
                "document_id": str(getattr(source, "document_id", "") or ""),
                "document_title": str(getattr(source, "document_title", "") or ""),
                "locator": getattr(source, "locator", None),
                "source_uri": str(getattr(source, "source_uri", "") or ""),
                "evidence_id": getattr(source, "evidence_id", None),
                "metadata": dict(metadata) if isinstance(metadata, dict) else {},
            }
        )
    return payload


def last_answer_sources_from_debug(debug: dict[str, Any]) -> tuple[LastAnswerSource, ...]:
    """Build display sources from the last Telegram debug payload."""
    source_refs = debug.get("source_refs")
    if not isinstance(source_refs, list):
        return ()
    evidence_counts = _evidence_counts(debug)
    result: list[LastAnswerSource] = []
    seen: set[str] = set()
    for raw_source in source_refs:
        if not isinstance(raw_source, dict):
            continue
        document_id = str(raw_source.get("document_id") or "").strip()
        if not document_id or document_id in seen:
            continue
        metadata = raw_source.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        source_type = _source_type(raw_source, metadata)
        is_external = _is_external_source(source_type, metadata)
        source_id = _source_id(document_id, metadata, is_external=is_external)
        result.append(
            LastAnswerSource(
                source_id=source_id,
                document_id=document_id,
                title=_source_title(raw_source, metadata, fallback=source_id),
                source_type=source_type or ("external_docs" if is_external else "uploaded"),
                source_origin="official" if is_external else "uploaded",
                chunks_count=evidence_counts.get(document_id, 0),
                source_uri=_source_uri(raw_source, metadata),
                is_external=is_external,
            )
        )
        seen.add(document_id)
    return tuple(result)


def find_last_answer_source(
    sources: tuple[LastAnswerSource, ...],
    source_id_or_prefix: str,
) -> LastAnswerSource | None:
    """Find one source by displayed id or document UUID prefix."""
    needle = source_id_or_prefix.strip().casefold()
    if not needle:
        return None
    for source in sources:
        if source.source_id.casefold() == needle:
            return source
        if source.document_id.casefold().startswith(needle):
            return source
    return None


def format_last_answer_sources(sources: tuple[LastAnswerSource, ...]) -> str:
    """Format sources from the last answer without raw JSON or long UUIDs."""
    if not sources:
        return "Пока нет данных об источниках последнего ответа."

    lines = ["Источники последнего ответа:"]
    archive_commands: list[str] = []
    for index, source in enumerate(sources, start=1):
        lines.extend(
            [
                "",
                f"{index}. [{source.source_id}] {source.title}",
                f"   тип: {source.source_type or 'unknown'}",
            ]
        )
        if source.chunks_count:
            lines.append(f"   chunks: {source.chunks_count}")
        lines.append(f"   источник: {source.source_origin}")
        if source.source_uri:
            lines.append(f"   url: {source.source_uri}")
        if not source.is_external:
            archive_commands.append(source.source_id)

    command_lines: list[str] = []
    if archive_commands:
        first = archive_commands[0]
        command_lines.extend(
            [
                f"- /material {first} — карточка материала",
                f"- /archive_source {first} — архивировать источник, если это uploaded/local материал",
            ]
        )
    if command_lines:
        lines.extend(["", "Команды:", *command_lines])
    return "\n".join(lines)


def format_source_archived(title: str) -> str:
    """Format successful source archive message."""
    clean_title = title.strip() or "unknown"
    return f"Источник архивирован: {clean_title}. Он больше не будет использоваться в ответах."


def _evidence_counts(debug: dict[str, Any]) -> dict[str, int]:
    rag = debug.get("rag")
    if not isinstance(rag, dict):
        return {}
    evidence = rag.get("accepted_evidence")
    if not isinstance(evidence, list):
        return {}
    counts: Counter[str] = Counter()
    for item in evidence:
        if isinstance(item, dict):
            document_id = str(item.get("document_id") or "").strip()
            if document_id:
                counts[document_id] += 1
    return dict(counts)


def _source_type(raw_source: dict[str, Any], metadata: dict[str, Any]) -> str:
    for key in ("source_type", "source_kind"):
        value = str(metadata.get(key) or raw_source.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_external_source(source_type: str, metadata: dict[str, Any]) -> bool:
    lowered = source_type.casefold()
    if lowered in {"external_docs", "official_docs"}:
        return True
    source_kind = str(metadata.get("source_kind") or "").strip().casefold()
    return source_kind in {"external_docs", "official_docs"}


def _source_id(document_id: str, metadata: dict[str, Any], *, is_external: bool) -> str:
    if is_external:
        for key in ("source_name", "docs_source"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value[:40]
    return document_id[:8]


def _source_title(raw_source: dict[str, Any], metadata: dict[str, Any], *, fallback: str) -> str:
    for value in (
        raw_source.get("document_title"),
        metadata.get("document_title"),
        metadata.get("title"),
        metadata.get("filename"),
        metadata.get("source_name"),
    ):
        clean = str(value or "").strip()
        if clean:
            return clean[:120]
    return fallback


def _source_uri(raw_source: dict[str, Any], metadata: dict[str, Any]) -> str:
    for value in (
        raw_source.get("source_uri"),
        metadata.get("canonical_url"),
        metadata.get("source_url"),
    ):
        clean = str(value or "").strip()
        if clean:
            return clean
    return ""
