"""Controlled activation flow for curated external docs candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from app.docs_registry.candidates import load_docs_source_candidates_config
from app.docs_registry.models import DocsSourceCandidate, DocsSourceCandidatesConfig
from app.external_docs.crawler import ExternalDocsCrawler
from app.external_docs.extractor import ExternalDocsExtractor
from app.external_docs.policy import is_url_allowed
from app.external_docs.types import CrawledPage, ExternalDocSource, ExternalDocsIndexResult, ExtractedPage

ALLOWED_ACTIVATION_SERVICE_ID = "openrouter"
ALLOWED_ACTIVATION_DOCS_SOURCE = "openrouter_docs"


class DocsActivationError(ValueError):
    """Base error for activation input, policy, or runtime failures."""


class DocsActivationCandidateNotFoundError(DocsActivationError):
    """Raised when a candidate cannot be found by service id or alias."""


class ArbitraryDocsActivationUrlError(DocsActivationError):
    """Raised when a user tries to activate arbitrary URL input."""


class DocsActivationPolicyError(DocsActivationError):
    """Raised when a candidate is not allowed by the MVP activation policy."""


class DocsActivationRuntimeUnavailableError(DocsActivationError):
    """Raised when activation dependencies are not wired at runtime."""


class DocsActivationCrawler(Protocol):
    """Crawler interface required for controlled activation."""

    async def crawl(self, source: ExternalDocSource, *, limit: int | None = None) -> list[CrawledPage]:
        """Fetch whitelisted pages."""


class DocsActivationExtractor(Protocol):
    """Extractor interface required for controlled activation."""

    def extract(self, page: CrawledPage) -> ExtractedPage:
        """Extract structured text from one crawled page."""


class DocsActivationIndexer(Protocol):
    """Indexer interface required for controlled activation."""

    async def index_page(
        self,
        page: ExtractedPage,
        source: ExternalDocSource,
        *,
        workspace: str = "team",
    ) -> ExternalDocsIndexResult:
        """Index one extracted page."""


@dataclass(frozen=True)
class DocsActivationPlan:
    """Safe activation plan shown before any indexing happens."""

    service_id: str
    display_name: str
    docs_source: str
    allowed_domains: tuple[str, ...]
    start_urls: tuple[str, ...]
    max_pages: int
    crawl_depth: int
    risk_level: str
    confirm_command: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocsActivationQualityGate:
    """Quality gate verdict for one controlled activation run."""

    passed: bool
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def quality(self) -> str:
        """Return human-readable verdict label."""
        return "PASS" if self.passed else "FAIL"


@dataclass(frozen=True)
class DocsActivationResult:
    """Result of a confirmed controlled activation run."""

    plan: DocsActivationPlan
    fetched_pages: int = 0
    indexed_new: int = 0
    skipped_unchanged: int = 0
    archived_old: int = 0
    failed: int = 0
    chunks_total: int = 0
    errors: tuple[str, ...] = ()
    quality_gate: DocsActivationQualityGate = DocsActivationQualityGate(passed=False)


class DocsActivationService:
    """Controlled activation service for curated docs candidates."""

    def __init__(
        self,
        *,
        candidates_config: DocsSourceCandidatesConfig | None = None,
        crawler: DocsActivationCrawler | None = None,
        extractor: DocsActivationExtractor | None = None,
        indexer: DocsActivationIndexer | None = None,
        workspace: str = "team",
    ) -> None:
        self._candidates_config = candidates_config
        self._crawler = crawler
        self._extractor = extractor or ExternalDocsExtractor()
        self._indexer = indexer
        self._workspace = workspace
        self._owns_crawler = crawler is None

    def plan(self, service_id_or_alias: str) -> DocsActivationPlan:
        """Build an activation plan without crawling, indexing, or writing."""
        candidate = self._find_candidate(service_id_or_alias)
        _validate_candidate_policy(candidate)
        source = candidate_to_activation_source(candidate)
        warnings = list(_source_policy_warnings(source))
        if warnings:
            raise DocsActivationPolicyError("; ".join(warnings))
        return DocsActivationPlan(
            service_id=candidate.service_id,
            display_name=candidate.display_name,
            docs_source=candidate.docs_source,
            allowed_domains=candidate.allowed_domains,
            start_urls=candidate.official_start_urls,
            max_pages=candidate.max_pages,
            crawl_depth=candidate.crawl_depth,
            risk_level=candidate.risk_level,
            confirm_command=f"/docs_activate {candidate.service_id} confirm",
        )

    async def activate(self, service_id_or_alias: str) -> DocsActivationResult:
        """Run controlled activation after an explicit owner/admin confirm."""
        plan = self.plan(service_id_or_alias)
        candidate = self._find_candidate(service_id_or_alias)
        source = candidate_to_activation_source(candidate)
        if self._indexer is None:
            raise DocsActivationRuntimeUnavailableError("Activation indexer is not configured.")

        crawler = self._crawler or ExternalDocsCrawler()
        close_crawler = self._crawler is None and hasattr(crawler, "close")
        try:
            pages = await crawler.crawl(source, limit=source.max_pages)
        finally:
            if close_crawler:
                await crawler.close()  # type: ignore[attr-defined]
        indexed_new = 0
        skipped_unchanged = 0
        archived_old = 0
        failed = 0
        chunks_total = 0
        errors: list[str] = []
        crawled_urls: list[str] = [page.url for page in pages]

        for crawled in pages:
            try:
                extracted = self._extractor.extract(crawled)
                result = await self._indexer.index_page(extracted, source, workspace=self._workspace)
            except Exception as exc:  # noqa: BLE001 - one page should not abort a controlled run
                failed += 1
                errors.append(f"{crawled.url}: {_safe_error(exc)}")
                continue
            if result.error:
                failed += 1
                errors.append(result.error[:300])
            elif result.skipped:
                skipped_unchanged += 1
            else:
                indexed_new += 1
            if result.archived_old:
                archived_old += 1
            chunks_total += int(result.chunks_count or 0)

        quality_gate = build_activation_quality_gate(
            plan=plan,
            source=source,
            fetched_pages=len(pages),
            indexed_new=indexed_new,
            skipped_unchanged=skipped_unchanged,
            failed=failed,
            chunks_total=chunks_total,
            crawled_urls=tuple(crawled_urls),
        )
        return DocsActivationResult(
            plan=plan,
            fetched_pages=len(pages),
            indexed_new=indexed_new,
            skipped_unchanged=skipped_unchanged,
            archived_old=archived_old,
            failed=failed,
            chunks_total=chunks_total,
            errors=tuple(errors[:10]),
            quality_gate=quality_gate,
        )

    async def close(self) -> None:
        """Close owned crawler resources."""
        if self._owns_crawler and self._crawler is not None and hasattr(self._crawler, "close"):
            await self._crawler.close()  # type: ignore[attr-defined]

    def _find_candidate(self, service_id_or_alias: str) -> DocsSourceCandidate:
        query = service_id_or_alias.strip()
        if _looks_like_url(query):
            raise ArbitraryDocsActivationUrlError(query)
        needle = query.casefold()
        for candidate in self._config().candidates:
            if candidate.service_id.casefold() == needle:
                return candidate
            if any(alias.casefold() == needle for alias in candidate.aliases):
                return candidate
        raise DocsActivationCandidateNotFoundError(query)

    def _config(self) -> DocsSourceCandidatesConfig:
        if self._candidates_config is not None:
            return self._candidates_config
        return load_docs_source_candidates_config()


def candidate_to_activation_source(candidate: DocsSourceCandidate) -> ExternalDocSource:
    """Convert a curated candidate into an activation source without preview caps."""
    return ExternalDocSource(
        name=candidate.docs_source,
        source_kind="external_docs",
        allowed_domains=candidate.allowed_domains,
        start_urls=candidate.official_start_urls,
        allow_patterns=candidate.allow_patterns,
        deny_patterns=candidate.deny_patterns,
        crawl_depth=candidate.crawl_depth,
        max_pages=candidate.max_pages,
        refresh_days=14,
    )


def build_activation_quality_gate(
    *,
    plan: DocsActivationPlan,
    source: ExternalDocSource,
    fetched_pages: int,
    indexed_new: int,
    skipped_unchanged: int,
    failed: int,
    chunks_total: int,
    crawled_urls: tuple[str, ...] = (),
) -> DocsActivationQualityGate:
    """Return PASS/FAIL for a controlled activation result."""
    failures: list[str] = []
    warnings: list[str] = []

    if plan.service_id != ALLOWED_ACTIVATION_SERVICE_ID:
        failures.append("source is not openrouter")
    if plan.docs_source != ALLOWED_ACTIVATION_DOCS_SOURCE or source.name != ALLOWED_ACTIVATION_DOCS_SOURCE:
        failures.append("source_name is not openrouter_docs")
    if plan.risk_level != "low":
        failures.append("candidate is not low risk")
    if fetched_pages <= 0:
        failures.append("no pages fetched")
    if fetched_pages > 0 and failed >= fetched_pages:
        failures.append("all pages failed")
    if indexed_new + skipped_unchanged <= 0:
        failures.append("no pages indexed or skipped unchanged")
    if indexed_new > 0 and chunks_total <= 0:
        failures.append("new indexed documents have no chunks")
    for url in (*source.start_urls, *crawled_urls):
        if not _url_domain_allowed(url, source.allowed_domains):
            failures.append("URL outside allowed domains")
            break
    if failed > 0 and failed < max(fetched_pages, 1):
        warnings.append("some pages failed but at least one page was indexed or unchanged")

    return DocsActivationQualityGate(
        passed=not failures,
        failures=tuple(dict.fromkeys(failures)),
        warnings=tuple(warnings),
    )


def _validate_candidate_policy(candidate: DocsSourceCandidate) -> None:
    if candidate.service_id != ALLOWED_ACTIVATION_SERVICE_ID:
        raise DocsActivationPolicyError("В MVP подключение разрешено только для OpenRouter.")
    if candidate.docs_source != ALLOWED_ACTIVATION_DOCS_SOURCE:
        raise DocsActivationPolicyError("OpenRouter candidate must use openrouter_docs source.")
    if candidate.risk_level != "low":
        raise DocsActivationPolicyError("Candidate risk level must be low.")


def _source_policy_warnings(source: ExternalDocSource) -> tuple[str, ...]:
    warnings: list[str] = []
    for url in source.start_urls:
        if not is_url_allowed(source, url):
            warnings.append(f"start URL rejected by policy: {url}")
    return tuple(warnings)


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _url_domain_allowed(url: str, domains: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return bool(host) and any(host == domain or host.endswith("." + domain) for domain in domains)


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300] or exc.__class__.__name__
