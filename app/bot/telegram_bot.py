"""Telegram application factory."""

import logging

from telegram.request import HTTPXRequest
from telegram.ext import Application, ApplicationBuilder

from app.bot.access import parse_telegram_ids
from app.bot.base_status import BaseStatusProvider
from app.bot.handlers import BotServices, register_handlers
from app.bot.materials import MaterialsProvider
from app.config import Settings
from app.docs_registry.activation import DocsActivationService
from app.external_docs.extractor import ExternalDocsExtractor
from app.external_docs.indexer import ExternalDocsIndexer
from app.ingestion.runtime import build_ingestion_runtime_from_settings, validate_ingestion_config
from app.llm.model_router import ModelRouter, ModelRouterConfig
from app.llm.openrouter_client import OpenRouterClient
from app.llm.vision import VisionTextifier
from app.rag.runtime import build_rag_runtime_from_settings, validate_runtime_config
from app.service_registry.provider import ServiceDocsStatusProvider

LOGGER = logging.getLogger(__name__)


def build_application(settings: Settings) -> Application:
    """Build the Telegram application and register handlers."""
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .request(_telegram_request())
        .get_updates_request(_telegram_request())
        .post_shutdown(_shutdown_runtime_services)
        .build()
    )
    register_handlers(application, _build_services(settings))
    return application


def _build_services(settings: Settings) -> BotServices:
    model_config = ModelRouterConfig.from_settings(settings)
    vision_textifier = None
    if settings.vision_enabled:
        openrouter_client = OpenRouterClient(settings)
        model_router = ModelRouter(openrouter_client, model_config)
        vision_textifier = VisionTextifier(settings, model_router=model_router)

    rag_runtime = build_rag_runtime_from_settings(settings)
    validation = validate_runtime_config(settings)
    ingestion_runtime = build_ingestion_runtime_from_settings(settings, vision_describer=vision_textifier)
    ingestion_validation = validate_ingestion_config(settings)
    service_docs_status_provider = None
    base_status_provider = None
    materials_provider = None
    docs_activation_service = None
    status_client = None
    if rag_runtime is not None:
        status_client = rag_runtime.resources.supabase
    elif ingestion_runtime is not None:
        status_client = ingestion_runtime.resources.supabase
    if status_client is not None:
        service_docs_status_provider = ServiceDocsStatusProvider(status_client)
        base_status_provider = BaseStatusProvider(
            status_client,
            service_status_provider=service_docs_status_provider,
        )
        materials_provider = MaterialsProvider(status_client)
    if ingestion_runtime is not None:
        docs_activation_service = DocsActivationService(
            extractor=ExternalDocsExtractor(),
            indexer=ExternalDocsIndexer(
                repository=ingestion_runtime.repository,
                embedding_client=ingestion_runtime.resources.embedding_client,
            ),
            workspace=settings.default_workspace_name,
        )
    rag_disabled_reason = ""
    if rag_runtime is None:
        rag_disabled_reason = (
            "RAG v2 pipeline не подключён: не хватает настроек окружения. Проверьте .env."
        )
        if validation.missing:
            rag_disabled_reason += " Не хватает: " + ", ".join(validation.missing) + "."
        LOGGER.warning("RAG pipeline disabled: missing config %s", ", ".join(validation.missing) or "unknown")

    ingestion_disabled_reason = ""
    if ingestion_runtime is None:
        ingestion_disabled_reason = (
            "Загрузка материалов не подключена: не хватает настроек окружения. Проверьте .env."
        )
        if ingestion_validation.missing:
            ingestion_disabled_reason += " Не хватает: " + ", ".join(ingestion_validation.missing) + "."
        LOGGER.warning(
            "Upload ingestion disabled: missing config %s",
            ", ".join(ingestion_validation.missing) or "unknown",
        )

    return BotServices(
        rag_pipeline=rag_runtime.pipeline if rag_runtime is not None else None,
        rag_runtime=rag_runtime,
        rag_disabled_reason=rag_disabled_reason,
        rag_missing_config=validation.missing,
        ingestion_service=ingestion_runtime.service if ingestion_runtime is not None else None,
        ingestion_runtime=ingestion_runtime,
        ingestion_disabled_reason=ingestion_disabled_reason,
        ingestion_missing_config=ingestion_validation.missing,
        service_docs_status_provider=service_docs_status_provider,
        docs_activation_service=docs_activation_service,
        base_status_provider=base_status_provider,
        materials_provider=materials_provider,
        conversation_repo=rag_runtime.conversation_repo if rag_runtime is not None else None,
        vision_textifier=vision_textifier,
        owner_ids=parse_telegram_ids(settings.owner_ids),
        admin_ids=parse_telegram_ids(settings.admin_ids),
        default_workspace_id=settings.default_workspace_id,
        default_workspace_name=settings.default_workspace_name,
        embedding_model=settings.embedding_model,
        reranker_mode=settings.reranker_mode,
        schema_version=settings.schema_version,
        model_lists={
            "free": model_config.free_text,
            "cheap": model_config.cheap_text,
            "quality": model_config.quality_text,
        },
    )


def _telegram_request() -> HTTPXRequest:
    """Create Telegram HTTP client without inheriting system proxy settings."""
    return HTTPXRequest(httpx_kwargs={"trust_env": False})


async def _shutdown_runtime_services(application: Application) -> None:
    """Close runtime resources created for Telegram services."""
    services = application.bot_data.get("services")
    runtime = getattr(services, "rag_runtime", None)
    if runtime is not None:
        await runtime.close()
    ingestion_runtime = getattr(services, "ingestion_runtime", None)
    if ingestion_runtime is not None:
        await ingestion_runtime.close()
    docs_activation_service = getattr(services, "docs_activation_service", None)
    if docs_activation_service is not None and hasattr(docs_activation_service, "close"):
        await docs_activation_service.close()
    vision_textifier = getattr(services, "vision_textifier", None)
    if vision_textifier is not None:
        await vision_textifier.close()
