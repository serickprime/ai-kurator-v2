"""Vision model adapter placeholders."""

from pathlib import Path


class VisionTextifier:
    """Extract text from screenshots and images with a vision model."""

    async def textify_image(self, path: Path) -> str:
        """Return text extracted from an image."""
        raise NotImplementedError("Vision textification is not implemented yet")
