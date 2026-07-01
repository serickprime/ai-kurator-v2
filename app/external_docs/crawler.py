"""Whitelisted static HTML crawler for external documentation."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
import logging
from urllib.parse import urldefrag, urljoin

import httpx

from app.external_docs.policy import is_url_allowed
from app.external_docs.types import CrawledPage, ExternalDocSource

LOGGER = logging.getLogger(__name__)
USER_AGENT = "ai-kurator-v2 external-docs-indexer/1.0"
MAX_HTML_BYTES = 2_000_000


class ExternalDocsCrawler:
    """Crawl one whitelisted documentation source without executing JavaScript."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
        retries: int = 1,
        max_html_bytes: int = MAX_HTML_BYTES,
    ) -> None:
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        self._retries = max(retries, 0)
        self._max_html_bytes = max_html_bytes

    async def close(self) -> None:
        """Close the owned HTTP client."""
        if self._owned_client:
            await self._client.aclose()

    async def crawl(self, source: ExternalDocSource, *, limit: int | None = None) -> list[CrawledPage]:
        """Fetch HTML pages from a whitelisted source."""
        max_pages = min(limit or source.max_pages, source.max_pages)
        queue: deque[tuple[str, int]] = deque((url, 0) for url in source.start_urls)
        seen: set[str] = set()
        pages: list[CrawledPage] = []

        while queue and len(pages) < max_pages:
            url, depth = queue.popleft()
            clean_url = normalize_url(url)
            if clean_url in seen or not is_url_allowed(source, clean_url):
                continue
            seen.add(clean_url)

            page = await self.fetch_page(source, clean_url, depth=depth)
            if page is None:
                continue
            pages.append(page)
            if depth >= source.crawl_depth:
                continue
            for link in extract_links(page.html, base_url=clean_url):
                if len(seen) + len(queue) >= max_pages * 8:
                    break
                normalized = normalize_url(link)
                if normalized not in seen and is_url_allowed(source, normalized):
                    queue.append((normalized, depth + 1))
        return pages

    async def fetch_page(self, source: ExternalDocSource, url: str, *, depth: int = 0) -> CrawledPage | None:
        """Fetch one page and return HTML only when it passes the safety policy."""
        if not is_url_allowed(source, url):
            return None
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = await self._client.get(url)
                content_type = response.headers.get("content-type", "")
                if response.status_code >= 400:
                    LOGGER.info("external docs fetch skipped %s status=%s", url, response.status_code)
                    return None
                if content_type and "html" not in content_type.lower():
                    return None
                if len(response.content) > self._max_html_bytes:
                    LOGGER.info("external docs fetch skipped %s because HTML is too large", url)
                    return None
                return CrawledPage(
                    source_name=source.name,
                    url=str(response.url),
                    html=response.text,
                    status_code=response.status_code,
                    content_type=content_type,
                    fetched_at=datetime.now(timezone.utc),
                    depth=depth,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self._retries:
                    LOGGER.warning("external docs fetch failed for %s: %s", url, exc)
        if last_error:
            LOGGER.debug("external docs final fetch error for %s: %s", url, last_error)
        return None


def normalize_url(url: str) -> str:
    """Return URL without fragments."""
    clean, _fragment = urldefrag(url.strip())
    return clean.rstrip("/") + ("/" if clean.endswith("/") else "")


def extract_links(html: str, *, base_url: str) -> list[str]:
    """Extract href links from HTML."""
    parser = _LinkParser(base_url=base_url)
    parser.feed(html)
    return parser.links


class _LinkParser(HTMLParser):
    def __init__(self, *, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {key.lower(): value for key, value in attrs}
        href = attrs_dict.get("href")
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            return
        self.links.append(urljoin(self.base_url, href))
