"""Runtime provider for service/docs registry status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db.supabase_client import SupabaseClient
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG, load_external_docs_config
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG, load_service_registry_config
from app.service_registry.status import build_service_docs_statuses, count_service_mentions, count_service_metadata
from app.service_registry.types import ServiceDefinition, ServiceDocsStatus


class ServiceDocsStatusProvider:
    """Read-only service/docs status provider backed by Supabase."""

    def __init__(
        self,
        client: SupabaseClient,
        *,
        registry_config_path: Path | str = DEFAULT_SERVICE_REGISTRY_CONFIG,
        external_config_path: Path | str = DEFAULT_EXTERNAL_DOCS_CONFIG,
        limit: int = 10000,
    ) -> None:
        self._client = client
        self._registry_config_path = registry_config_path
        self._external_config_path = external_config_path
        self._limit = limit

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        """Return registry status rows without crawling or syncing docs."""
        registry = load_service_registry_config(self._registry_config_path)
        external_config = load_external_docs_config(self._external_config_path)
        services = _filter_services(registry.services, service)

        external_documents = await _load_external_documents(self._client, limit=self._limit)
        active_external_ids = [
            str(row.get("id") or "")
            for row in external_documents
            if row.get("status") == "active"
        ]
        external_chunks = await _load_chunks(self._client, active_external_ids, limit=self._limit)
        mention_counts: dict[str, int] | None = None
        detected_document_counts: dict[str, int] = {}
        detected_chunk_counts: dict[str, int] = {}

        if scan_corpus:
            corpus_documents = await _load_active_documents(self._client, limit=self._limit)
            active_ids = [str(row.get("id") or "") for row in corpus_documents]
            corpus_chunks = await _load_chunks(self._client, active_ids, limit=self._limit)
            cards = await _load_document_cards(self._client, active_ids, limit=self._limit)
            mention_counts = count_service_mentions(
                services=registry.services,
                corpus_rows=[*corpus_documents, *cards, *corpus_chunks],
            )
            detected_document_counts, detected_chunk_counts = count_service_metadata(
                documents=corpus_documents,
                chunks=corpus_chunks,
            )

        return build_service_docs_statuses(
            services=services,
            configured_docs_sources=(source.name for source in external_config.sources),
            documents=external_documents,
            chunks=external_chunks,
            mention_counts=mention_counts,
            detected_document_counts=detected_document_counts,
            detected_chunk_counts=detected_chunk_counts,
        )


async def _load_external_documents(client: SupabaseClient, *, limit: int) -> list[dict[str, Any]]:
    return await client.select(
        "documents",
        params={
            "select": "id,filename,document_key,title,status,metadata,updated_at",
            "source_type": "eq.external_docs",
            "limit": str(limit),
        },
    )


async def _load_active_documents(client: SupabaseClient, *, limit: int) -> list[dict[str, Any]]:
    return await client.select(
        "documents",
        params={
            "select": "id,filename,title,course,module,lesson,status,source_type,metadata",
            "status": "eq.active",
            "limit": str(limit),
        },
    )


async def _load_chunks(client: SupabaseClient, document_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for group in _batches([document_id for document_id in document_ids if document_id], 20):
        chunks.extend(
            await client.select(
                "chunks",
                params={
                    "select": "id,document_id,chunk_index,content,heading,metadata",
                    "document_id": f"in.({','.join(group)})",
                    "limit": str(limit),
                },
            )
        )
    return chunks


async def _load_document_cards(client: SupabaseClient, document_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in _batches([document_id for document_id in document_ids if document_id], 20):
        rows.extend(
            await client.select(
                "document_cards",
                params={
                    "select": (
                        "document_id,summary,topics,questions_answered,entities,task_types,not_about,metadata"
                    ),
                    "document_id": f"in.({','.join(group)})",
                    "limit": str(limit),
                },
            )
        )
    return rows


def _filter_services(services: tuple[ServiceDefinition, ...], query: str | None) -> tuple[ServiceDefinition, ...]:
    if not query:
        return services
    needle = query.strip().casefold()
    result = tuple(
        service
        for service in services
        if service.service_id.casefold() == needle
        or service.display_name.casefold() == needle
        or any(alias.casefold() == needle for alias in service.aliases)
    )
    if not result:
        raise KeyError(query)
    return result


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
