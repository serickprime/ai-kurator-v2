"""Indexing orchestration placeholders."""

from pathlib import Path


class IndexingService:
    """Coordinates loading, textification, card creation, and indexing."""

    async def ingest_path(self, path: Path) -> str:
        """Ingest one file and return its document id."""
        raise NotImplementedError
