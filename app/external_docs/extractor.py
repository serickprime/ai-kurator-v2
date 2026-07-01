"""Extract clean structured text from whitelisted external docs HTML."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import re
from urllib.parse import urljoin

from app.external_docs.types import CrawledPage, ExtractedPage
from app.ingestion.text_normalizer import TextNormalizer, clean_heading

SKIP_TAGS = {"aside", "footer", "form", "header", "nav", "noscript", "script", "style", "svg"}
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"}
EMPTY_ANCHOR_RE = re.compile(r"\s*<a\b[^>]*>\s*</a>\s*", re.IGNORECASE)
COPY_ONLY_FENCE_RE = re.compile(r"```\s*Copy\s*```", re.IGNORECASE)
GENERATOR_BOILERPLATE_RE = re.compile(
    r"\bFor the complete documentation index,\s*see\s+llms\.txt\s*\.\s*"
    r"|\bThis page is also available as Markdown\s*\.\s*",
    re.IGNORECASE,
)
PREVIOUS_NEXT_NAV_RE = re.compile(r"\bPrevious\b.+\bNext\b", re.IGNORECASE)
FOOTER_START_RE = re.compile(r"^(?:Last updated\b.*|Was this helpful\??)$", re.IGNORECASE)


class ExternalDocsExtractor:
    """Convert fetched HTML pages into clean structured text."""

    def extract(self, page: CrawledPage) -> ExtractedPage:
        """Extract page title, canonical URL, headings, and body text."""
        parser = _HtmlTextParser(base_url=page.url)
        parser.feed(page.html)
        title = clean_heading(parser.title, fallback=_title_from_url(page.url))
        canonical_url = parser.canonical_url or page.url
        blocks = parser.blocks
        if title and (not blocks or blocks[0] != f"# {title}"):
            blocks = [f"# {title}", *blocks]
        structured_text = TextNormalizer().normalize(_strip_generated_markup_noise("\n\n".join(blocks)))
        content_hash = _content_hash(title=title, canonical_url=canonical_url, structured_text=structured_text)
        return ExtractedPage(
            source_name=page.source_name,
            source_url=page.url,
            canonical_url=canonical_url,
            title=title,
            structured_text=structured_text,
            content_hash=content_hash,
            headings=tuple(parser.headings),
            crawled_at=page.fetched_at or datetime.now(timezone.utc),
            metadata={
                "status_code": page.status_code,
                "content_type": page.content_type,
                "depth": page.depth,
            },
        )


class _HtmlTextParser(HTMLParser):
    def __init__(self, *, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.canonical_url = ""
        self.headings: list[str] = []
        self.blocks: list[str] = []
        self._skip_depth = 0
        self._tag_stack: list[str] = []
        self._buffer: list[str] = []
        self._title_buffer: list[str] = []
        self._code_buffer: list[str] = []
        self._in_title = False
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {key.lower(): value for key, value in attrs}
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "link" and (attrs_dict.get("rel") or "").lower() == "canonical" and attrs_dict.get("href"):
            self.canonical_url = urljoin(self.base_url, str(attrs_dict["href"]))
            return
        if tag == "title":
            self._in_title = True
            self._title_buffer = []
            return
        if tag == "pre":
            self._flush_text()
            self._in_pre = True
            self._code_buffer = []
            return
        if tag in BLOCK_TAGS:
            self._flush_text()
            self._tag_stack.append(tag)
        elif tag == "br":
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
            self.title = _clean_inline(" ".join(self._title_buffer))
            return
        if tag == "pre" and self._in_pre:
            code = "\n".join(line.rstrip() for line in "".join(self._code_buffer).splitlines()).strip()
            if code and code.casefold() != "copy":
                self.blocks.append(f"```\n{code}\n```")
            self._code_buffer = []
            self._in_pre = False
            return
        if self._tag_stack and tag == self._tag_stack[-1]:
            self._flush_text(tag=self._tag_stack.pop())

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_buffer.append(data)
            return
        if self._in_pre:
            self._code_buffer.append(data)
            return
        self._append(data)

    def _append(self, text: str) -> None:
        if text:
            self._buffer.append(text)

    def _flush_text(self, tag: str | None = None) -> None:
        text = _clean_inline(" ".join(self._buffer))
        self._buffer = []
        if not text:
            return
        if tag and tag.startswith("h") and tag[1:].isdigit():
            level = min(max(int(tag[1:]), 1), 6)
            heading = clean_heading(text, fallback="")
            if heading:
                self.headings.append(heading)
                self.blocks.append(f"{'#' * level} {heading}")
            return
        if tag == "li":
            self.blocks.append(f"- {text}")
            return
        self.blocks.append(text)


def _clean_inline(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _strip_generated_markup_noise(text: str) -> str:
    """Remove docs-generator markup that is not useful evidence."""
    cleaned = EMPTY_ANCHOR_RE.sub(" ", text or "")
    cleaned = COPY_ONLY_FENCE_RE.sub(" ", cleaned)
    cleaned = GENERATOR_BOILERPLATE_RE.sub(" ", cleaned)
    return _strip_generated_navigation_tail(cleaned)


def _strip_generated_navigation_tail(text: str) -> str:
    """Remove generic docs navigation/footer blocks from extracted text."""
    lines: list[str] = []
    dropping_footer = False
    for line in str(text or "").splitlines():
        clean = _clean_inline(line)
        if dropping_footer:
            continue
        if clean and FOOTER_START_RE.match(clean):
            dropping_footer = True
            continue
        if clean and PREVIOUS_NEXT_NAV_RE.search(clean):
            continue
        lines.append(line)
    return "\n".join(lines)


def _title_from_url(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").strip() or "External documentation"


def _content_hash(*, title: str, canonical_url: str, structured_text: str) -> str:
    digest = sha256()
    for value in (title, canonical_url, structured_text):
        digest.update(b"\0")
        digest.update(value.encode("utf-8", errors="replace"))
    return digest.hexdigest()
