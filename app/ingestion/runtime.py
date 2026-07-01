"""Runtime builder for Telegram material ingestion."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.db.repositories import DocumentRepository
from app.db.supabase_client import SupabaseClient
from app.ingestion.document_cards import DocumentCardBuilder
from app.ingestion.indexing import IndexingService
from app.ingestion.loaders import FileLoader
from app.llm.embeddings import OllamaEmbeddingClient

if TYPE_CHECKING:
    from app.config import Settings

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionRuntimeValidation:
    """Result of validating settings required for upload ingestion."""

    missing: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """Return true when upload ingestion can be built."""
        return not self.missing

    @property
    def message(self) -> str:
        """Return concise setup diagnostics."""
        if self.ready:
            return "Upload ingestion configuration is complete."
        return "Upload ingestion disabled: missing config " + ", ".join(self.missing)


@dataclass
class IngestionRuntimeResources:
    """Long-lived clients owned by upload ingestion runtime."""

    supabase: SupabaseClient
    embedding_client: OllamaEmbeddingClient

    async def close(self) -> None:
        """Close owned HTTP clients."""
        await self.supabase.close()
        await self.embedding_client.close()


@dataclass
class IngestionRuntime:
    """Built upload ingestion runtime."""

    service: IndexingService
    repository: DocumentRepository
    resources: IngestionRuntimeResources
    validation: IngestionRuntimeValidation

    async def close(self) -> None:
        """Close owned runtime resources."""
        await self.resources.close()


def validate_ingestion_config(settings: "Settings") -> IngestionRuntimeValidation:
    """Validate settings required for Telegram material upload ingestion."""
    missing: list[str] = []
    _require("SUPABASE_URL", settings.supabase_url, missing)
    _require("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key, missing)
    _require("EMBEDDING_PROVIDER", settings.embedding_provider, missing)
    _require("EMBEDDING_MODEL", settings.embedding_model, missing)
    _require("EMBEDDING_DIM", settings.embedding_dim, missing)

    provider = settings.embedding_provider.strip().lower()
    if provider not in {"local", "ollama"}:
        missing.append("EMBEDDING_PROVIDER=local")
    if provider in {"local", "ollama"}:
        _require("OLLAMA_BASE_URL", settings.ollama_base_url, missing)
    if settings.embedding_dim != 1024:
        missing.append("EMBEDDING_DIM=1024")

    return IngestionRuntimeValidation(missing=tuple(dict.fromkeys(missing)))


def build_ingestion_runtime_from_settings(
    settings: "Settings",
    *,
    vision_describer: Any | None = None,
) -> IngestionRuntime | None:
    """Build upload ingestion runtime, returning None when config is incomplete."""
    validation = validate_ingestion_config(settings)
    if not validation.ready:
        LOGGER.warning(validation.message)
        return None

    try:
        supabase = SupabaseClient(settings)
        embedding_client = OllamaEmbeddingClient(settings)
        repository = DocumentRepository(supabase)
        service = IndexingService(
            repository=repository,
            embedding_client=embedding_client,
            loader=FileLoader(
                vision_describer=vision_describer,
                vision_enabled=bool(settings.vision_enabled and vision_describer is not None),
            ),
            card_builder=DocumentCardBuilder(),
        )
        return IngestionRuntime(
            service=service,
            repository=repository,
            resources=IngestionRuntimeResources(
                supabase=supabase,
                embedding_client=embedding_client,
            ),
            validation=validation,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram startup should keep running
        LOGGER.warning("Upload ingestion disabled: %s", exc)
        return None


def build_ingestion_service_from_settings(settings: "Settings") -> IndexingService | None:
    """Build only the ingestion service for compatibility with simple callers."""
    runtime = build_ingestion_runtime_from_settings(settings)
    return runtime.service if runtime is not None else None


def _require(name: str, value: object, missing: list[str]) -> None:
    text = "" if value is None else str(value).strip()
    if not text or text.startswith("replace_with") or "your-project-ref" in text:
        missing.append(name)
