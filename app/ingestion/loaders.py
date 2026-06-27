"""File and message loaders for ingestion."""

from pathlib import Path


def load_text_file(path: Path) -> str:
    """Load a UTF-8 text file."""
    return path.read_text(encoding="utf-8")
