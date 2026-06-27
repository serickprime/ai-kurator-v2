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
        pdf_render_scale: float = 1.4,
        pdf_min_image_area_ratio: float = 0.03,
        pdf_max_vision_pages: int = 0,
    ) -> None:
        self._vision_describer = vision_describer
        self._vision_enabled = vision_enabled
        self._pdf_render_scale = pdf_render_scale
        self._pdf_min_image_area_ratio = pdf_min_image_area_ratio
        self._pdf_max_vision_pages = pdf_max_vision_pages

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
            metadata={"extension": path.suffix.lower(), "original_filename": path.name},
        )

    def _load_json(self, path: Path) -> LoadedDocument:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        metadata: dict[str, Any] = {"extension": ".json", "original_filename": path.name}
        try:
            data = json.loads(raw)
            metadata["json_valid"] = True
        except json.JSONDecodeError as exc:
            metadata["json_valid"] = False
            metadata["json_error"] = str(exc)
            title = path.stem.replace("_", " ").replace("-", " ").strip() or path.name
            structured_text = "\n".join(
                [
                    f"# {title}",
                    "",
                    f"Source file: {path.name}",
                    "",
                    "## Raw JSON-like text",
                    raw,
                ]
            ).strip()
            return LoadedDocument(
                path=path,
                source_type="json",
                filename=path.name,
                title=title,
                structured_text=structured_text,
                pages=(LoadedPage(page_number=None, text=raw, metadata={"json_valid": False}),),
                metadata=metadata,
            )

        if isinstance(data, dict) and isinstance(data.get("nodes"), list):
            metadata["looks_like_n8n_workflow"] = True
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
            metadata=metadata,
        )

    async def _load_pdf(self, path: Path) -> LoadedDocument:
        pymupdf_document = await self._try_load_pdf_with_pymupdf(path)
        if pymupdf_document is not None:
            return pymupdf_document

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
            "original_filename": path.name,
            "page_count": len(reader.pages),
            "vision_enabled": self._vision_enabled,
            "textified_source": "pypdf_text_layer",
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
            metadata={
                "extension": path.suffix.lower(),
                "original_filename": path.name,
                "vision_enabled": bool(description),
            },
        )

    async def _try_load_pdf_with_pymupdf(self, path: Path) -> LoadedDocument | None:
        try:
            import fitz
        except ImportError:
            return None

        pages: list[LoadedPage] = []
        parts = [f"# {path.stem}", "", f"Source file: {path.name}", ""]
        metadata: dict[str, Any] = {
            "extension": ".pdf",
            "original_filename": path.name,
            "vision_enabled": self._vision_enabled,
            "textified_source": "pymupdf_layout_text",
            "pdf_inline_images_described": 0,
            "pdf_vision_errors": [],
        }
        with tempfile.TemporaryDirectory(prefix="ai-kurator-pdf-images-") as tmpdir:
            temp_dir = Path(tmpdir)
            with fitz.open(path) as document:
                total_pages = len(document)
                metadata["page_count"] = total_pages
                pages_to_process = (
                    total_pages
                    if self._pdf_max_vision_pages <= 0
                    else min(total_pages, self._pdf_max_vision_pages)
                )
                for page_number, page in enumerate(document, start=1):
                    page_text = await self._pymupdf_page_text(
                        page=page,
                        page_number=page_number,
                        temp_dir=temp_dir,
                        describe_images=page_number <= pages_to_process,
                        metadata=metadata,
                    )
                    if not page_text.strip():
                        page_text = "[No text layer extracted on this page.]"
                    page_body = f"[[page:{page_number}]]\n## Page {page_number}\n\n{page_text}".strip()
                    pages.append(
                        LoadedPage(
                            page_number=page_number,
                            text=page_body,
                            metadata={"has_text_layer": "[No text layer" not in page_body},
                        )
                    )
                    parts.append(page_body)

        if not metadata["pdf_vision_errors"]:
            metadata.pop("pdf_vision_errors", None)
        return LoadedDocument(
            path=path,
            source_type="pdf",
            filename=path.name,
            title=path.stem,
            structured_text="\n\n".join(parts).strip(),
            pages=tuple(pages),
            metadata=metadata,
        )

    async def _pymupdf_page_text(
        self,
        *,
        page: Any,
        page_number: int,
        temp_dir: Path,
        describe_images: bool,
        metadata: dict[str, Any],
    ) -> str:
        blocks = (page.get_text("dict", sort=True) or {}).get("blocks") or []
        parts: list[str] = []
        image_index = 0
        for block in sorted(blocks, key=_block_sort_key):
            block_type = block.get("type")
            bbox = block.get("bbox") or []
            if block_type == 0:
                text = _extract_text_block(block)
                if text:
                    parts.append(text)
            elif block_type == 1 and describe_images:
                if not self._vision_enabled or self._vision_describer is None or len(bbox) != 4:
                    continue
                if not _is_significant_image_block(page, bbox, self._pdf_min_image_area_ratio):
                    continue
                image_index += 1
                description = await self._describe_pymupdf_image(
                    page=page,
                    bbox=bbox,
                    page_number=page_number,
                    image_index=image_index,
                    temp_dir=temp_dir,
                    metadata=metadata,
                )
                if description:
                    parts.append(description)
        if not parts:
            return page.get_text("text", sort=True).strip()
        return "\n\n".join(parts)

    async def _describe_pymupdf_image(
        self,
        *,
        page: Any,
        bbox: list[float],
        page_number: int,
        image_index: int,
        temp_dir: Path,
        metadata: dict[str, Any],
    ) -> str:
        try:
            import fitz
        except ImportError:
            return ""

        rect = fitz.Rect(bbox)
        page_rect = page.rect
        clip = fitz.Rect(
            max(page_rect.x0, rect.x0 - 8),
            max(page_rect.y0, rect.y0 - 8),
            min(page_rect.x1, rect.x1 + 8),
            min(page_rect.y1, rect.y1 + 8),
        )
        image_path = temp_dir / f"page_{page_number:04d}_image_{image_index:02d}.jpg"
        try:
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(self._pdf_render_scale, self._pdf_render_scale),
                clip=clip,
                alpha=False,
            )
            pixmap.save(image_path)
            description = ""
            if self._vision_describer is not None:
                description = (await self._vision_describer.describe_image(image_path)).strip()
            if not description:
                return ""
            metadata["pdf_inline_images_described"] = int(metadata.get("pdf_inline_images_described") or 0) + 1
            return f"[image {image_index} on page {page_number}]\n{description}"
        except Exception as exc:  # noqa: BLE001
            metadata.setdefault("pdf_vision_errors", []).append(
                {"page": page_number, "image": image_index, "error": str(exc)}
            )
            return ""


def is_supported_file(path: Path) -> bool:
    """Return true when a file extension is supported by the loader."""
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def detect_file_type(filename: str) -> str:
    """Return a simple extension-based file type."""
    return Path(filename).suffix.lower().lstrip(".") or "unknown"


def is_image(filename: str) -> bool:
    """Return true for supported image filenames."""
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def load_text_file(path: Path) -> str:
    """Load a UTF-8 text file."""
    return path.read_text(encoding="utf-8", errors="ignore")


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


def _block_sort_key(block: dict[str, Any]) -> tuple[float, float]:
    bbox = block.get("bbox") or [0, 0, 0, 0]
    return float(bbox[1] or 0), float(bbox[0] or 0)


def _extract_text_block(block: dict[str, Any]) -> str:
    lines: list[str] = []
    for line in block.get("lines") or []:
        spans = line.get("spans") or []
        line_text = "".join(span.get("text") or "" for span in spans).strip()
        if line_text:
            lines.append(line_text)
    return "\n".join(lines).strip()


def _is_significant_image_block(page: Any, bbox: list[float], min_area_ratio: float) -> bool:
    rect = page.rect
    x0, y0, x1, y1 = [float(value) for value in bbox]
    width = max(x1 - x0, 0)
    height = max(y1 - y0, 0)
    if width < 80 or height < 50:
        return False
    page_area = max(float(rect.width * rect.height), 1.0)
    return (width * height / page_area) >= min_area_ratio
