"""Logging setup."""

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging for local bot runtime."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
