"""Safe dry-run previews for curated external docs candidates."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlparse

from app.docs_registry.candidates import load_docs_source_candidates_config
from app.docs_registry.models import DocsCandidatePreviewResult, DocsSourceCandidate, DocsSourceCandidatesConfig
from app.external_docs.crawler import ExternalDocsCrawler
from app.external_docs.policy import is_url_allowed
from app.external_docs.types import CrawledPage, ExternalDocSource

MAX_PREVIEW_PAGES = 5


class DocsCandidatePreviewError(ValueError):
    """Base error for preview input or runtime failures."""


class DocsCandidateNotFoundError(DocsCandidatePreviewError):
    """Raised when a candidate cannot be found by service id or alias."""


class ArbitraryDocsUrlError(DocsCandidatePreviewError):
    """Raised when a user tries to preview an arbitrary URL."""


class DocsCrawler(Protocol):
    """Minimal crawler interface for dry-run preview."""

    async def crawl(self, source: ExternalDocSource, *, limit: int | None = None) -> list[CrawledPage]:
        """Fetch pages from an already whitelisted source."""


class DocsCandidatePreviewService:
    """Build a safe preview for one curated docs source candidate."""

    def __init__(
        self,
        *,
        candidates_config: DocsSourceCandidatesConfig | None = None,
        crawler: DocsCrawler | None = None,
    ) -> None:
        self._candidates_config = candidates_config
        self._crawler = crawler

    async def preview(self, service_id_or_alias: str, *, limit: int = MAX_PREVIEW_PAGES) -> DocsCandidatePreviewResult:
        """Run a read-only preview crawl for one curated candidate."""
        candidate = self._find_candidate(service_id_or_alias)
        source = candidate_to_external_doc_source(candidate)
        warnings = list(_candidate_warnings(candidate))
        for url in source.start_urls:
            if not is_url_allowed(source, url):
                warnings.append(f"start URL rejected by policy: {url}")
                return _result(candidate, status="failed", warnings=tuple(warnings), pages_checked=0, pages_found=0)

        page_limit = max(1, min(limit, MAX_PREVIEW_PAGES))
        crawler = self._crawler or ExternalDocsCrawler(retries=0)
        close_crawler = self._crawler is None and hasattr(crawler, "close")
        try:
            pages = await crawler.crawl(source, limit=page_limit)
        except Exception as exc:  # noqa: BLE001 - preview should return a safe failed result
            warnings.append(f"ошибка загрузки: {exc}")
            return _result(candidate, status="failed", warnings=tuple(warnings), pages_checked=page_limit, pages_found=0)
        finally:
            if close_crawler:
                await crawler.close()  # type: ignore[attr-defined]

        pages = pages[:page_limit]
        sample_titles = tuple(_page_title(page) for page in pages)
        sample_urls = tuple(page.url for page in pages)
        if pages:
            status = "needs_review" if candidate.risk_level == "review" else "ok"
        else:
            status = "needs_review" if candidate.risk_level == "review" else "failed"
            warnings.append("не найдено страниц")
        return _result(
            candidate,
            status=status,
            warnings=tuple(warnings),
            pages_checked=page_limit,
            pages_found=len(pages),
            sample_titles=sample_titles,
            sample_urls=sample_urls,
        )

    def _find_candidate(self, service_id_or_alias: str) -> DocsSourceCandidate:
        query = service_id_or_alias.strip()
        if _looks_like_url(query):
            raise ArbitraryDocsUrlError("Произвольные URL нельзя проверять. Используйте сервис из /docs.")
        needle = query.casefold()
        for candidate in self._config().candidates:
            if candidate.service_id.casefold() == needle:
                return candidate
            if any(alias.casefold() == needle for alias in candidate.aliases):
                return candidate
        raise DocsCandidateNotFoundError(query)

    def _config(self) -> DocsSourceCandidatesConfig:
        if self._candidates_config is not None:
            return self._candidates_config
        return load_docs_source_candidates_config()


def candidate_to_external_doc_source(candidate: DocsSourceCandidate) -> ExternalDocSource:
    """Convert a curated candidate into a whitelisted external docs source."""
    return ExternalDocSource(
        name=candidate.docs_source,
        source_kind="external_docs",
        allowed_domains=candidate.allowed_domains,
        start_urls=candidate.official_start_urls,
        allow_patterns=candidate.allow_patterns,
        deny_patterns=candidate.deny_patterns,
        crawl_depth=candidate.crawl_depth,
        max_pages=min(candidate.max_pages, MAX_PREVIEW_PAGES),
        refresh_days=14,
    )


def _result(
    candidate: DocsSourceCandidate,
    *,
    status: str,
    warnings: tuple[str, ...],
    pages_checked: int,
    pages_found: int,
    sample_titles: tuple[str, ...] = (),
    sample_urls: tuple[str, ...] = (),
) -> DocsCandidatePreviewResult:
    return DocsCandidatePreviewResult(
        service_id=candidate.service_id,
        display_name=candidate.display_name,
        docs_source=candidate.docs_source,
        allowed_domains=candidate.allowed_domains,
        start_urls=candidate.official_start_urls,
        pages_checked=pages_checked,
        pages_found=pages_found,
        sample_titles=sample_titles,
        sample_urls=sample_urls,
        status=status,  # type: ignore[arg-type]
        warnings=warnings,
        risk_level=candidate.risk_level,
        notes=candidate.notes,
    )


def _candidate_warnings(candidate: DocsSourceCandidate) -> tuple[str, ...]:
    if candidate.risk_level == "review":
        return ("нужна ручная проверка",)
    return ()


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _page_title(page: CrawledPage) -> str:
    parser = _TitleParser()
    parser.feed(page.html or "")
    return parser.title.strip() or page.url


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self._parts: list[str] = []

    @property
    def title(self) -> str:
        return " ".join(part.strip() for part in self._parts if part.strip())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._parts.append(data)
