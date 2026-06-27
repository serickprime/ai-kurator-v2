"""Telegram application factory."""

from telegram.ext import Application, ApplicationBuilder

from app.bot.handlers import BotServices, register_handlers
from app.config import Settings
from app.llm.model_router import ModelRouter, ModelRouterConfig
from app.llm.openrouter_client import OpenRouterClient
from app.llm.vision import VisionTextifier


def build_application(settings: Settings) -> Application:
    """Build the Telegram application and register handlers."""
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    register_handlers(application, _build_services(settings))
    return application


def _build_services(settings: Settings) -> BotServices:
    model_config = ModelRouterConfig.from_settings(settings)
    vision_textifier = None
    if settings.vision_enabled:
        openrouter_client = OpenRouterClient(settings)
        model_router = ModelRouter(openrouter_client, model_config)
        vision_textifier = VisionTextifier(settings, model_router=model_router)

    return BotServices(
        vision_textifier=vision_textifier,
        owner_ids=_split_ints(settings.owner_ids),
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


def _split_ints(value: str) -> tuple[int, ...]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            continue
    return tuple(result)
