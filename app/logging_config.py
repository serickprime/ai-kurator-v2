"""Logging setup."""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_TELEGRAM_BOT_TOKEN_RE = re.compile(r"bot[0-9]{6,}(?::|%3[Aa])[A-Za-z0-9_-]+")


def configure_logging(level: str = "INFO", log_dir: str | Path = "logs") -> None:
    """Configure console and file logging for local bot runtime."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(_level(level))

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(_level(level))

    app_file = RotatingFileHandler(
        log_path / "app.log",
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    app_file.setFormatter(formatter)
    app_file.setLevel(_level(level))

    error_file = RotatingFileHandler(
        log_path / "errors.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    error_file.setFormatter(formatter)
    error_file.setLevel(logging.ERROR)

    root.addHandler(console)
    root.addHandler(app_file)
    root.addHandler(error_file)


def _level(level: str) -> int:
    return getattr(logging, (level or "INFO").upper(), logging.INFO)


class RedactingFormatter(logging.Formatter):
    """Formatter that keeps secrets out of local logs."""

    def format(self, record: logging.LogRecord) -> str:
        return _redact_secrets(super().format(record))


def _redact_secrets(text: str) -> str:
    return _TELEGRAM_BOT_TOKEN_RE.sub("bot<redacted>", text)
