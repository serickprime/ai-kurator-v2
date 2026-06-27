"""Evaluation report helpers."""

from pathlib import Path


def write_report(path: Path, content: str) -> None:
    """Write an evaluation report."""
    path.write_text(content, encoding="utf-8")
