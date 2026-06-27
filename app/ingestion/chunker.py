"""Deterministic text splitting for indexed units."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TextUnit:
    """A searchable unit inside one document."""

    ordinal: int
    text: str
    locator: str | None = None
