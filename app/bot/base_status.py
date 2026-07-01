"""Read-only Telegram knowledge base status provider and formatter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.service_registry.provider import ServiceDocsStatusProvider
from app.service_registry.types import ServiceDocsStatus


class SupabaseReadClient(Protocol):
    """Small subset of Supabase client used by the base status provider."""

    async def select(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Select rows from a table."""


class ServiceStatusReader(Protocol):
    """Read service/docs status rows."""

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        """Return service/docs status rows."""


@dataclass(frozen=True)
class ExternalSourceStatus:
    """Compact external docs source status."""

    source_name: str
    active_docs_count: int = 0
    active_chunks_count: int = 0
    quality_status: str = "none"


@dataclass(frozen=True)
class RecentDocument:
    """Compact recent document label."""

    label: str
    source_type: str = ""


@dataclass(frozen=True)
class BaseStatus:
    """Knowledge base status for Telegram."""

    active_documents_count: int = 0
    active_chunks_count: int = 0
    uploaded_documents_count: int = 0
    external_documents_count: int = 0
    archived_documents_count: int = 0
    external_sources: tuple[ExternalSourceStatus, ...] = ()
    services: tuple[ServiceDocsStatus, ...] = ()
    recent_documents: tuple[RecentDocument, ...] = ()
    truncated: bool = False


class BaseStatusProvider:
    """Read-only provider for Telegram `/base_status`."""

    def __init__(
        self,
        client: SupabaseReadClient,
        *,
        service_status_provider: ServiceStatusReader | None = None,
        limit: int = 10000,
    ) -> None:
        self._client = client
        self._service_status_provider = service_status_provider or ServiceDocsStatusProvider(client)  # type: ignore[arg-type]
        self._limit = limit

    async def get_status(self) -> BaseStatus:
        """Collect a compact knowledge base status without mutating data."""
        documents = await self._load_documents()
        active_documents = [row for row in documents if row.get("status") == "active"]
        active_document_ids = [str(row.get("id") or "") for row in active_documents if row.get("id")]
        chunks = await _load_chunks(self._client, active_document_ids, limit=self._limit)
        services = await self._load_service_statuses()

        return BaseStatus(
            active_documents_count=len(active_documents),
            active_chunks_count=len(chunks),
            uploaded_documents_count=sum(1 for row in active_documents if str(row.get("source_type") or "") != "external_docs"),
            external_documents_count=sum(1 for row in active_documents if str(row.get("source_type") or "") == "external_docs"),
            archived_documents_count=sum(1 for row in documents if row.get("status") == "archived"),
            external_sources=_external_sources_from_services(services),
            services=services,
            recent_documents=_recent_documents(active_documents),
            truncated=len(documents) >= self._limit or len(chunks) >= self._limit,
        )

    async def _load_documents(self) -> list[dict[str, Any]]:
        return await self._client.select(
            "documents",
            params={
                "select": "id,filename,document_key,title,status,source_type,metadata,created_at,updated_at",
                "order": "updated_at.desc.nullslast,created_at.desc",
                "limit": str(self._limit),
            },
        )

    async def _load_service_statuses(self) -> tuple[ServiceDocsStatus, ...]:
        try:
            return await self._service_status_provider.list_statuses(scan_corpus=False)
        except Exception:  # noqa: BLE001 - base status should still show DB counts if registry status fails
            return ()


def format_base_status(status: BaseStatus) -> str:
    """Format base status for Telegram without raw JSON/dict output."""
    lines = [
        "База знаний:",
        "",
        f"Документы: {status.active_documents_count} active",
        f"Chunks: {status.active_chunks_count}",
        f"Uploaded docs: {status.uploaded_documents_count}",
        f"External docs: {status.external_documents_count}",
        f"Archived docs: {status.archived_documents_count}",
        "",
        "External docs:",
    ]
    if status.external_sources:
        for source in status.external_sources[:10]:
            quality = source.quality_status if source.quality_status not in {"", "none"} else "нет данных"
            lines.append(f"{source.source_name} — {source.active_docs_count} docs, {quality}")
    else:
        lines.append("нет данных")

    lines.extend(["", "Сервисы:"])
    if status.services:
        for service in status.services[:10]:
            lines.append(f"{service.display_name} — {_service_docs_phrase(service)}")
    else:
        lines.append("нет данных")

    lines.extend(["", "Последние загрузки:"])
    if status.recent_documents:
        for document in status.recent_documents[:5]:
            lines.append(f"- {document.label}")
    else:
        lines.append("нет данных")

    if status.truncated:
        lines.extend(["", "Статус обрезан по лимиту выборки."])
    return "\n".join(lines)


async def _load_chunks(
    client: SupabaseReadClient,
    document_ids: list[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in _batches([document_id for document_id in document_ids if document_id], 50):
        rows.extend(
            await client.select(
                "chunks",
                params={
                    "select": "id,document_id",
                    "document_id": f"in.({','.join(group)})",
                    "limit": str(limit),
                },
            )
        )
    return rows


def _external_sources_from_services(statuses: tuple[ServiceDocsStatus, ...]) -> tuple[ExternalSourceStatus, ...]:
    by_source: dict[str, ExternalSourceStatus] = {}
    for status in statuses:
        source_name = str(status.docs_source or "").strip()
        if not source_name:
            continue
        current = by_source.get(source_name)
        candidate = ExternalSourceStatus(
            source_name=source_name,
            active_docs_count=int(status.active_docs_count or 0),
            active_chunks_count=int(status.active_chunks_count or 0),
            quality_status=str(status.quality_status or "none"),
        )
        if current is None or candidate.active_docs_count > current.active_docs_count:
            by_source[source_name] = candidate
    return tuple(sorted(by_source.values(), key=lambda item: item.source_name.casefold()))


def _recent_documents(active_documents: list[dict[str, Any]], *, limit: int = 5) -> tuple[RecentDocument, ...]:
    result: list[RecentDocument] = []
    for row in active_documents[:limit]:
        label = _document_label(row)
        if label:
            result.append(RecentDocument(label=label, source_type=str(row.get("source_type") or "")))
    return tuple(result)


def _document_label(row: dict[str, Any]) -> str:
    for key in ("title", "filename", "document_key"):
        value = str(row.get(key) or "").strip()
        if value:
            return value[:120]
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        for key in ("title", "source_url", "canonical_url"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value[:120]
    return ""


def _service_docs_phrase(status: ServiceDocsStatus) -> str:
    if status.docs_status == "indexed":
        return "документация подключена"
    if status.docs_status == "not_configured":
        return "документация не подключена"
    if status.docs_status == "configured_not_indexed":
        return "документация настроена, но не проиндексирована"
    if status.docs_status == "disabled":
        return "документация отключена"
    return "документация требует проверки"


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
