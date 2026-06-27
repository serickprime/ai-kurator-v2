"""Text extraction facade for PDFs, images, and forwarded content."""

from pathlib import Path

from app.ingestion.loaders import FileLoader, LoadedDocument


class Textifier:
    """Converts source material into normalized structured text."""

    def __init__(self, loader: FileLoader | None = None) -> None:
        self._loader = loader or FileLoader()

    async def textify_document(self, path: Path) -> LoadedDocument:
        """Extract structured text and metadata from a source file."""
        return await self._loader.load(path)

    async def textify(self, path: Path) -> str:
        """Extract structured text from a source file."""
        return (await self.textify_document(path)).structured_text
