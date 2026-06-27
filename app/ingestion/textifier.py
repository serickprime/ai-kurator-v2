"""Text extraction adapters for PDFs, images, and forwarded content."""

from pathlib import Path


class Textifier:
    """Converts source material into normalized text."""

    async def textify(self, path: Path) -> str:
        """Extract text from a source file."""
        raise NotImplementedError
