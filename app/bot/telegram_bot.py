"""Telegram application factory."""

from telegram.ext import Application, ApplicationBuilder

from app.bot.handlers import register_handlers
from app.config import Settings


def build_application(settings: Settings) -> Application:
    """Build the Telegram application and register handlers."""
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    register_handlers(application)
    return application
