"""Application entry point."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bot.telegram_bot import build_application
from app.config import get_settings
from app.logging_config import configure_logging


def main() -> None:
    """Start the Telegram bot."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_dir)
    application = build_application(settings)
    application.run_polling()


if __name__ == "__main__":
    main()
