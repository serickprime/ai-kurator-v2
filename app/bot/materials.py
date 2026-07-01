"""Telegram-facing uploaded materials management."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol


class SupabaseMaterialClient(Protocol):
    """Small Supabase client subset used by material management."""

    async def select(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Select rows from a table."""

    async def update(
        self,
        table: str,
        payload: dict[str, Any],
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Update rows and return their representation."""


class MaterialNotFoundError(LookupError):
    """Raised when a material id/prefix is not found."""


class MaterialAmbiguousError(LookupError):
    """Raised when a short id matches multiple materials."""


class ExternalDocsArchiveError(RuntimeError):
    """Raised when Telegram tries to archive official/external docs."""


@dataclass(frozen=True)
class MaterialCard:
    """Compact uploaded material card shown in Telegram."""

    document_id: str
    title: str
    source_type: str
    status: str
    chunks_count: int = 0
    filename: str = ""
    document_key: str = ""
    created_at: str = ""
    updated_at: str = ""
    service_ids: tuple[str, ...] = ()
    service_mentions: tuple[dict[str, object], ...] = ()

    @property
    def short_id(self) -> str:
        """Return the displayed short id."""
        return self.document_id[:8]

    @property
    def service_labels(self) -> tuple[str, ...]:
        """Return readable service labels from metadata."""
        labels: dict[str, str] = {}
        for mention in self.service_mentions:
            service_id = str(mention.get("service_id") or "").strip()
            display_name = str(mention.get("display_name") or service_id).strip()
            if service_id:
                labels.setdefault(service_id, display_name or service_id)
        for service_id in self.service_ids:
            clean = str(service_id).strip()
            if clean:
                labels.setdefault(clean, clean)
        return tuple(label for _, label in sorted(labels.items(), key=lambda item: item[1].casefold()))


class MaterialsProvider:
    """Read/update provider for uploaded Telegram materials."""

    def __init__(self, client: SupabaseMaterialClient, *, limit: int = 10000) -> None:
        self._client = client
        self._limit = limit

    async def list_recent_materials(self, workspace_id: str, limit: int = 10) -> tuple[MaterialCard, ...]:
        """Return recent active user-uploaded materials, excluding external docs."""
        rows = await self._client.select(
            "documents",
            params={
                "select": _DOCUMENT_SELECT,
                "workspace_id": f"eq.{workspace_id}",
                "status": "eq.active",
                "source_type": "neq.external_docs",
                "order": "updated_at.desc.nullslast,created_at.desc",
                "limit": str(max(limit * 3, limit)),
            },
        )
        local_rows = [row for row in rows if not _is_external_document(row) and row.get("status") == "active"]
        selected = local_rows[:limit]
        counts = await self._chunk_counts([str(row.get("id") or "") for row in selected])
        return tuple(_material_card(row, chunks_count=counts.get(str(row.get("id") or ""), 0)) for row in selected)

    async def get_material(self, workspace_id: str, material_id_or_prefix: str) -> MaterialCard:
        """Return one active uploaded material by full id or displayed short prefix."""
        row = await self._resolve_document(workspace_id, material_id_or_prefix, include_external=False)
        counts = await self._chunk_counts([str(row.get("id") or "")])
        return _material_card(row, chunks_count=counts.get(str(row.get("id") or ""), 0))

    async def archive_material(self, workspace_id: str, material_id_or_prefix: str) -> MaterialCard:
        """Archive one active uploaded material without deleting chunks."""
        row = await self._resolve_document(workspace_id, material_id_or_prefix, include_external=True)
        if _is_external_document(row):
            raise ExternalDocsArchiveError("Official/external documentation cannot be archived through Telegram.")

        document_id = str(row.get("id") or "")
        updated = await self._client.update(
            "documents",
            {"status": "archived"},
            params={
                "id": f"eq.{document_id}",
                "workspace_id": f"eq.{workspace_id}",
                "status": "eq.active",
            },
        )
        archived = updated[0] if updated else {**row, "status": "archived"}
        counts = await self._chunk_counts([document_id])
        return _material_card(archived, chunks_count=counts.get(document_id, 0))

    async def _resolve_document(
        self,
        workspace_id: str,
        material_id_or_prefix: str,
        *,
        include_external: bool,
    ) -> dict[str, Any]:
        needle = material_id_or_prefix.strip()
        if not needle:
            raise MaterialNotFoundError("Material not found.")

        rows = await self._client.select(
            "documents",
            params={
                "select": _DOCUMENT_SELECT,
                "workspace_id": f"eq.{workspace_id}",
                "status": "eq.active",
                "order": "updated_at.desc.nullslast,created_at.desc",
                "limit": str(self._limit),
            },
        )
        candidates = [
            row
            for row in rows
            if row.get("status") == "active" and (include_external or not _is_external_document(row))
        ]
        matches = [row for row in candidates if str(row.get("id") or "").casefold().startswith(needle.casefold())]
        if not matches:
            raise MaterialNotFoundError("Material not found.")
        if len(matches) > 1:
            raise MaterialAmbiguousError("Material id is ambiguous.")
        return matches[0]

    async def _chunk_counts(self, document_ids: list[str]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for group in _batches([document_id for document_id in document_ids if document_id], 50):
            rows = await self._client.select(
                "chunks",
                params={
                    "select": "id,document_id",
                    "document_id": f"in.({','.join(group)})",
                    "limit": str(self._limit),
                },
            )
            counts.update(str(row.get("document_id") or "") for row in rows if row.get("document_id"))
        return dict(counts)


def format_materials_list(materials: tuple[MaterialCard, ...]) -> str:
    """Format recent materials list for Telegram."""
    lines = ["Материалы:"]
    if not materials:
        return "\n".join([*lines, "", "Загруженные материалы не найдены."])

    for index, material in enumerate(materials, start=1):
        lines.extend(
            [
                "",
                f"{index}. [{material.short_id}] {material.title}",
                f"   chunks: {material.chunks_count}",
                f"   services: {_services_text(material)}",
            ]
        )
    first = materials[0].short_id
    lines.extend(
        [
            "",
            "Команды:",
            f"- /material {first} — карточка",
            f"- /archive_material {first} — архивировать",
        ]
    )
    return "\n".join(lines)


def format_material_card(material: MaterialCard) -> str:
    """Format one material card."""
    return "\n".join(
        [
            "Материал:",
            f"ID: {material.short_id}",
            f"Название: {material.title}",
            f"Тип: {material.source_type or 'unknown'}",
            f"Chunks: {material.chunks_count}",
            f"Статус: {material.status}",
            f"Сервисы: {_services_text(material)}",
        ]
    )


def format_material_archived(material: MaterialCard) -> str:
    """Format successful archive message."""
    return f"Материал архивирован: {material.title}. Он больше не будет использоваться в ответах."


def _material_card(row: dict[str, Any], *, chunks_count: int) -> MaterialCard:
    metadata = _metadata(row)
    return MaterialCard(
        document_id=str(row.get("id") or ""),
        title=_document_label(row),
        source_type=str(row.get("source_type") or ""),
        status=str(row.get("status") or ""),
        chunks_count=chunks_count,
        filename=str(row.get("filename") or ""),
        document_key=str(row.get("document_key") or ""),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        service_ids=_service_ids(metadata),
        service_mentions=_service_mentions(metadata),
    )


def _document_label(row: dict[str, Any]) -> str:
    for key in ("title", "filename", "document_key"):
        value = str(row.get(key) or "").strip()
        if value:
            return value[:120]
    return "unknown"


def _is_external_document(row: dict[str, Any]) -> bool:
    source_type = str(row.get("source_type") or "").strip().casefold()
    if source_type in {"external_docs", "official_docs"}:
        return True
    metadata = _metadata(row)
    source_kind = str(metadata.get("source_kind") or "").strip().casefold()
    return source_kind in {"external_docs", "official_docs"}


def _services_text(material: MaterialCard) -> str:
    labels = material.service_labels
    return ", ".join(labels) if labels else "не найдены"


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _service_ids(metadata: dict[str, Any]) -> tuple[str, ...]:
    values = metadata.get("service_ids")
    if not isinstance(values, list):
        return ()
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _service_mentions(metadata: dict[str, Any]) -> tuple[dict[str, object], ...]:
    values = metadata.get("service_mentions")
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, dict))


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


_DOCUMENT_SELECT = "id,filename,document_key,title,status,source_type,metadata,created_at,updated_at"
