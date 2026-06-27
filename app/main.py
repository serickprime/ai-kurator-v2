"""Application entry point."""

from app.bot.telegram_bot import build_application
from app.config import get_settings
from app.logging_config import configure_logging


def main() -> None:
    """Start the Telegram bot."""
    settings = get_settings()
    configure_logging(settings.log_level)
    application = build_application(settings)
    application.run_polling()


if __name__ == "__main__":
    main()
