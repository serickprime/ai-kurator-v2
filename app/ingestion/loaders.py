"""File loaders and textifiers for ingestion."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
JSON_EXTENSIONS = {".json"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | JSON_EXTENSIONS | PDF_EXTENSIONS | IMAGE_EXTENSIONS


class VisionDescriber(Protocol):
    """Optional image description adapter."""

    async def describe_image(self, path: Path) -> str:
        """Return a natural-language description of an image."""


@dataclass(frozen=True)
class LoadedPage:
    """Structured page-level text from a source document."""

    page_number: int | None
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedDocument:
    """Structured text and metadata extracted from one file."""

    path: Path
    source_type: str
    filename: str
    title: str
    structured_text: str
    pages: tuple[LoadedPage, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


class UnsupportedFileTypeError(ValueError):
    """Raised when ingestion receives an unsupported file type."""


class FileLoader:
    """Load supported material files into structured text."""

    def __init__(
        self,
        vision_describer: VisionDescriber | None = None,
        vision_enabled: bool = False,
    ) -> None:
        self._vision_describer = vision_describer
        self._vision_enabled = vision_enabled

    async def load(self, path: Path) -> LoadedDocument:
        """Load a file into a structured document."""
        path = path.resolve()
        suffix = path.suffix.lower()

        if suffix in TEXT_EXTENSIONS:
            return self._load_text(path)
        if suffix in JSON_EXTENSIONS:
            return self._load_json(path)
        if suffix in PDF_EXTENSIONS:
            return await self._load_pdf(path)
        if suffix in IMAGE_EXTENSIONS:
            return await self._load_image(path)

        raise UnsupportedFileTypeError(f"Unsupported file type: {path.suffix}")

    def _load_text(self, path: Path) -> LoadedDocument:
        text = load_text_file(path)
        title = _title_from_text_or_filename(text, path)
        structured_text = "\n".join(
            [
                f"# {title}",
                "",
                f"Source file: {path.name}",
                "",
                text.strip(),
            ]
        ).strip()
        return LoadedDocument(
            path=path,
            source_type="markdown" if path.suffix.lower() in {".md", ".markdown"} else "text",
            filename=path.name,
            title=title,
            structured_text=structured_text,
            pages=(LoadedPage(page_number=None, text=text),),
            metadata={"extension": path.suffix.lower()},
        )

    def _load_json(self, path: Path) -> LoadedDocument:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        title = _json_title(data) or path.stem.replace("_", " ").replace("-", " ").strip()
        lines = [f"# {title}", "", f"Source file: {path.name}", "", "## Structured JSON"]
        lines.extend(_flatten_json(data))
        structured_text = "\n".join(lines).strip()
        return LoadedDocument(
            path=path,
            source_type="json",
            filename=path.name,
            title=title,
            structured_text=structured_text,
            pages=(LoadedPage(page_number=None, text="\n".join(lines[4:])),),
            metadata={"extension": ".json"},
        )

    async def _load_pdf(self, path: Path) -> LoadedDocument:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF ingestion requires the pypdf package") from exc

        reader = PdfReader(str(path))
        pages: list[LoadedPage] = []
        parts = [f"# {path.stem}", "", f"Source file: {path.name}", ""]

        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            image_descriptions = await self._describe_pdf_images(page, index)
            page_parts = [f"[[page:{index}]]", f"## Page {index}"]
            if text:
                page_parts.append(text)
            if image_descriptions:
                page_parts.append("### Visual evidence")
                page_parts.extend(image_descriptions)
            if not text and not image_descriptions:
                page_parts.append("[No text layer extracted on this page.]")

            page_text = "\n\n".join(page_parts)
            pages.append(
                LoadedPage(
                    page_number=index,
                    text=page_text,
                    metadata={"has_text_layer": bool(text), "image_descriptions": len(image_descriptions)},
                )
            )
            parts.append(page_text)

        metadata = {
            "extension": ".pdf",
            "page_count": len(reader.pages),
            "vision_enabled": self._vision_enabled,
        }
        return LoadedDocument(
            path=path,
            source_type="pdf",
            filename=path.name,
            title=path.stem,
            structured_text="\n\n".join(parts).strip(),
            pages=tuple(pages),
            metadata=metadata,
        )

    async def _describe_pdf_images(self, page: Any, page_number: int) -> list[str]:
        if not self._vision_enabled or self._vision_describer is None:
            return []

        descriptions: list[str] = []
        images = getattr(page, "images", []) or []
        if not images:
            return descriptions

        with tempfile.TemporaryDirectory(prefix="ai-kurator-pdf-images-") as tmpdir:
            tmp_path = Path(tmpdir)
            for image_index, image in enumerate(images, start=1):
                data = getattr(image, "data", None)
                name = getattr(image, "name", None) or f"page-{page_number}-image-{image_index}.bin"
                if not data or len(data) < 20_000:
                    continue

                image_path = tmp_path / name
                image_path.write_bytes(data)
                try:
                    description = (await self._vision_describer.describe_image(image_path)).strip()
                except Exception as exc:  # noqa: BLE001 - external vision adapters should not break ingestion
                    description = f"[Vision description failed for image {image_index}: {exc}]"
                if description:
                    descriptions.append(f"Image {image_index} on page {page_number}: {description}")
        return descriptions

    async def _load_image(self, path: Path) -> LoadedDocument:
        title = path.stem.replace("_", " ").replace("-", " ").strip() or path.name
        description = ""
        if self._vision_enabled and self._vision_describer is not None:
            description = (await self._vision_describer.describe_image(path)).strip()

        body = description or "Image file without a vision description."
        structured_text = "\n".join(
            [
                f"# {title}",
                "",
                f"Source file: {path.name}",
                "",
                "[[page:1]]",
                "## Image 1",
                body,
            ]
        )
        return LoadedDocument(
            path=path,
            source_type="image",
            filename=path.name,
            title=title,
            structured_text=structured_text,
            pages=(LoadedPage(page_number=1, text=body, metadata={"vision_enabled": bool(description)}),),
            metadata={"extension": path.suffix.lower(), "vision_enabled": bool(description)},
        )


def is_supported_file(path: Path) -> bool:
    """Return true when a file extension is supported by the loader."""
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def load_text_file(path: Path) -> str:
    """Load a UTF-8 text file."""
    return path.read_text(encoding="utf-8")


def _title_from_text_or_filename(text: str, path: Path) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith("#"):
            return clean.lstrip("#").strip() or path.stem
        if clean:
            return clean[:120]
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name


def _json_title(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("title", "name", "filename"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _flatten_json(data: Any, prefix: str = "") -> list[str]:
    if isinstance(data, dict):
        lines: list[str] = []
        for key, value in data.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            lines.extend(_flatten_json(value, child_prefix))
        return lines
    if isinstance(data, list):
        lines = []
        for index, value in enumerate(data):
            child_prefix = f"{prefix}[{index}]"
            lines.extend(_flatten_json(value, child_prefix))
        return lines

    value = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    return [f"- {prefix}: {value}"]
