"""Runtime builder for wiring RAG v2 into the Telegram bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from app.db.repositories import ConversationRepository, EvidenceLogRepository
from app.db.supabase_client import SupabaseClient
from app.llm.embeddings import OllamaEmbeddingClient
from app.llm.model_router import ModelRoutedAnswerClient, ModelRouter, ModelRouterConfig
from app.llm.openrouter_client import OpenRouterClient
from app.rag.answer_generator import AnswerGenerator
from app.rag.claim_verifier import ClaimVerifier
from app.rag.document_router import DocumentRouter, SupabaseDocumentCardStore
from app.rag.evidence_pack import EvidencePackBuilder
from app.rag.evidence_retriever import EvidenceRetriever, SupabaseEvidenceChunkStore
from app.rag.pipeline import EvidenceFirstRagPipeline
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.reranker import EvidenceReranker

if TYPE_CHECKING:
    from app.config import Settings

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeConfigValidation:
    """Result of validating settings needed for Telegram RAG runtime."""

    missing: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """Return true when RAG runtime can be built."""
        return not self.missing

    @property
    def message(self) -> str:
        """Return a concise diagnostic message."""
        if self.ready:
            return "RAG runtime configuration is complete."
        return "RAG pipeline disabled: missing config " + ", ".join(self.missing)


@dataclass
class RagRuntimeResources:
    """Long-lived clients owned by the Telegram application."""

    supabase: SupabaseClient
    openrouter_client: OpenRouterClient
    embedding_client: OllamaEmbeddingClient

    async def close(self) -> None:
        """Close long-lived HTTP clients."""
        await self.supabase.close()
        await self.openrouter_client.close()
        await self.embedding_client.close()


@dataclass
class RagRuntime:
    """Built RAG runtime and the resources it owns."""

    pipeline: EvidenceFirstRagPipeline
    resources: RagRuntimeResources
    conversation_repo: ConversationRepository
    validation: RuntimeConfigValidation

    async def close(self) -> None:
        """Close owned runtime resources."""
        await self.resources.close()


def validate_runtime_config(settings: "Settings") -> RuntimeConfigValidation:
    """Validate settings required for the normal Telegram RAG v2 runtime."""
    missing: list[str] = []
    warnings: list[str] = []

    _require("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token, missing)
    _require("SUPABASE_URL", settings.supabase_url, missing)
    _require("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key, missing)
    _require("DEFAULT_WORKSPACE_ID", settings.default_workspace_id, missing)
    _require("OPENROUTER_API_KEY", settings.openrouter_api_key, missing)
    _require("OPENROUTER_DEFAULT_MODEL", settings.openrouter_default_model, missing)
    _require("EMBEDDING_PROVIDER", settings.embedding_provider, missing)
    _require("EMBEDDING_MODEL", settings.embedding_model, missing)
    _require("EMBEDDING_DIM", settings.embedding_dim, missing)

    if settings.rag_pipeline_version != "v2":
        missing.append("RAG_PIPELINE_VERSION=v2")

    provider = settings.embedding_provider.strip().lower()
    if provider not in {"local", "ollama"}:
        missing.append("EMBEDDING_PROVIDER=local")
    if provider in {"local", "ollama"}:
        _require("OLLAMA_BASE_URL", settings.ollama_base_url, missing)
    if settings.embedding_dim != 1024:
        missing.append("EMBEDDING_DIM=1024")
    if settings.default_workspace_id and not _is_uuid(settings.default_workspace_id):
        missing.append("DEFAULT_WORKSPACE_ID must be a UUID")
    if not settings.owner_ids.strip():
        warnings.append("OWNER_IDS is empty; bot access is open unless restricted elsewhere.")

    return RuntimeConfigValidation(
        missing=tuple(dict.fromkeys(missing)),
        warnings=tuple(warnings),
    )


def build_rag_pipeline_from_settings(settings: "Settings") -> EvidenceFirstRagPipeline | None:
    """Build the RAG pipeline from settings, returning None when config is incomplete."""
    runtime = build_rag_runtime_from_settings(settings)
    return runtime.pipeline if runtime is not None else None


def build_rag_runtime_from_settings(settings: "Settings") -> RagRuntime | None:
    """Build RAG runtime dependencies for Telegram without making network calls."""
    validation = validate_runtime_config(settings)
    if not validation.ready:
        LOGGER.warning(validation.message)
        return None

    try:
        supabase = SupabaseClient(settings)
        openrouter_client = OpenRouterClient(settings)
        embedding_client = OllamaEmbeddingClient(settings)

        model_router = ModelRouter(openrouter_client, ModelRouterConfig.from_settings(settings))
        answer_client = ModelRoutedAnswerClient(model_router)
        card_store = SupabaseDocumentCardStore(supabase)
        chunk_store = SupabaseEvidenceChunkStore(supabase)

        pipeline = EvidenceFirstRagPipeline(
            analyzer=QuestionAnalyzer(),
            router=DocumentRouter(
                store=card_store,
                embedding_client=embedding_client,
            ),
            retriever=EvidenceRetriever(
                chunk_store=chunk_store,
                embedding_client=embedding_client,
                workspace_id=settings.default_workspace_id,
            ),
            reranker=EvidenceReranker(),
            pack_builder=EvidencePackBuilder(),
            answer_generator=AnswerGenerator(answer_client),
            verifier=ClaimVerifier(),
            logger=EvidenceLogRepository(supabase),
        )

        return RagRuntime(
            pipeline=pipeline,
            resources=RagRuntimeResources(
                supabase=supabase,
                openrouter_client=openrouter_client,
                embedding_client=embedding_client,
            ),
            conversation_repo=ConversationRepository(supabase),
            validation=validation,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram startup should explain RAG setup gaps
        LOGGER.warning("RAG pipeline disabled: %s", exc)
        return None


def _require(name: str, value: object, missing: list[str]) -> None:
    text = "" if value is None else str(value).strip()
    if not text or text.startswith("replace_with") or "your-project-ref" in text:
        missing.append(name)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True
