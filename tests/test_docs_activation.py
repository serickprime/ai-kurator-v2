import asyncio
from datetime import datetime, timezone

import pytest

from app.docs_registry.activation import (
    ArbitraryDocsActivationUrlError,
    DocsActivationCandidateNotFoundError,
    DocsActivationPolicyError,
    DocsActivationService,
    build_activation_quality_gate,
    candidate_to_activation_source,
)
from app.docs_registry.models import DocsSourceCandidate, DocsSourceCandidatesConfig
from app.external_docs.types import CrawledPage, ExternalDocSource, ExternalDocsIndexResult, ExtractedPage


class FakeCrawler:
    def __init__(self, pages: list[CrawledPage] | None = None) -> None:
        self.pages = pages or []
        self.calls: list[tuple[ExternalDocSource, int | None]] = []

    async def crawl(self, source: ExternalDocSource, *, limit: int | None = None) -> list[CrawledPage]:
        self.calls.append((source, limit))
        return self.pages


class FakeExtractor:
    def __init__(self) -> None:
        self.calls: list[CrawledPage] = []

    def extract(self, page: CrawledPage) -> ExtractedPage:
        self.calls.append(page)
        return _extracted_page(page.url)


class FakeIndexer:
    def __init__(self, result: ExternalDocsIndexResult | None = None) -> None:
        self.result = result or ExternalDocsIndexResult(
            source_name="openrouter_docs",
            url="https://openrouter.ai/docs",
            document_id="doc-1",
            document_key="https://openrouter.ai/docs",
            version=1,
            chunks_count=7,
        )
        self.calls: list[tuple[ExtractedPage, ExternalDocSource, str]] = []

    async def index_page(
        self,
        page: ExtractedPage,
        source: ExternalDocSource,
        *,
        workspace: str = "team",
    ) -> ExternalDocsIndexResult:
        self.calls.append((page, source, workspace))
        return self.result


def test_activation_plan_for_openrouter_does_not_call_indexer_or_crawler() -> None:
    crawler = FakeCrawler([_crawled_page()])
    indexer = FakeIndexer()
    service = DocsActivationService(candidates_config=_config(_openrouter_candidate()), crawler=crawler, indexer=indexer)

    plan = service.plan("openrouter")

    assert plan.service_id == "openrouter"
    assert plan.docs_source == "openrouter_docs"
    assert plan.confirm_command == "/docs_activate openrouter confirm"
    assert crawler.calls == []
    assert indexer.calls == []


def test_activation_confirm_for_openrouter_runs_fake_activation_service() -> None:
    crawler = FakeCrawler([_crawled_page()])
    extractor = FakeExtractor()
    indexer = FakeIndexer()
    service = DocsActivationService(
        candidates_config=_config(_openrouter_candidate()),
        crawler=crawler,
        extractor=extractor,
        indexer=indexer,
        workspace="team",
    )

    result = asyncio.run(service.activate("openrouter"))

    assert crawler.calls[-1][0].name == "openrouter_docs"
    assert crawler.calls[-1][1] == 25
    assert len(extractor.calls) == 1
    assert len(indexer.calls) == 1
    assert indexer.calls[-1][2] == "team"
    assert result.fetched_pages == 1
    assert result.indexed_new == 1
    assert result.chunks_total == 7
    assert result.quality_gate.passed


def test_activation_rejects_arbitrary_url() -> None:
    service = DocsActivationService(candidates_config=_config(_openrouter_candidate()))

    with pytest.raises(ArbitraryDocsActivationUrlError):
        service.plan("https://example.com/docs")


def test_activation_rejects_unknown_candidate() -> None:
    service = DocsActivationService(candidates_config=_config(_openrouter_candidate()))

    with pytest.raises(DocsActivationCandidateNotFoundError):
        service.plan("missing")


def test_activation_allows_telegram_bot_api_candidate_in_mvp_allowlist() -> None:
    service = DocsActivationService(candidates_config=_config(_telegram_candidate()))

    plan = service.plan("telegram_bot_api")

    assert plan.service_id == "telegram_bot_api"
    assert plan.docs_source == "telegram_bot_api_docs"


def test_activation_rejects_non_allowlisted_candidate_in_mvp() -> None:
    service = DocsActivationService(candidates_config=_config(_ollama_candidate()))

    with pytest.raises(DocsActivationPolicyError):
        service.plan("ollama")


def test_activation_quality_gate_passes_for_successful_openrouter_run() -> None:
    plan = DocsActivationService(candidates_config=_config(_openrouter_candidate())).plan("openrouter")
    source = candidate_to_activation_source(_openrouter_candidate())

    gate = build_activation_quality_gate(
        plan=plan,
        source=source,
        fetched_pages=3,
        indexed_new=2,
        skipped_unchanged=1,
        failed=0,
        chunks_total=12,
        crawled_urls=("https://openrouter.ai/docs/quickstart",),
    )

    assert gate.passed
    assert gate.quality == "PASS"


def test_activation_quality_gate_fails_when_pages_found_is_zero() -> None:
    plan = DocsActivationService(candidates_config=_config(_openrouter_candidate())).plan("openrouter")

    gate = build_activation_quality_gate(
        plan=plan,
        source=candidate_to_activation_source(_openrouter_candidate()),
        fetched_pages=0,
        indexed_new=0,
        skipped_unchanged=0,
        failed=0,
        chunks_total=0,
    )

    assert not gate.passed
    assert "no pages fetched" in gate.failures


def test_activation_quality_gate_fails_for_wrong_domain() -> None:
    plan = DocsActivationService(candidates_config=_config(_openrouter_candidate())).plan("openrouter")

    gate = build_activation_quality_gate(
        plan=plan,
        source=candidate_to_activation_source(_openrouter_candidate()),
        fetched_pages=1,
        indexed_new=1,
        skipped_unchanged=0,
        failed=0,
        chunks_total=4,
        crawled_urls=("https://evil.example.com/docs",),
    )

    assert not gate.passed
    assert "URL outside allowed domains" in gate.failures


def _config(*candidates: DocsSourceCandidate) -> DocsSourceCandidatesConfig:
    return DocsSourceCandidatesConfig(candidates=candidates)


def _openrouter_candidate() -> DocsSourceCandidate:
    return DocsSourceCandidate(
        service_id="openrouter",
        display_name="OpenRouter",
        aliases=("openrouter", "open router"),
        docs_source="openrouter_docs",
        official_start_urls=("https://openrouter.ai/docs",),
        allowed_domains=("openrouter.ai",),
        allow_patterns=(r"^https://openrouter\.ai/docs",),
        deny_patterns=("/login", "/account"),
        max_pages=25,
        crawl_depth=2,
        risk_level="low",
        notes="test",
    )


def _telegram_candidate() -> DocsSourceCandidate:
    return DocsSourceCandidate(
        service_id="telegram_bot_api",
        display_name="Telegram Bot API",
        aliases=("telegram bot api",),
        docs_source="telegram_bot_api_docs",
        official_start_urls=("https://core.telegram.org/bots/api",),
        allowed_domains=("core.telegram.org",),
        allow_patterns=(r"^https://core\.telegram\.org/bots",),
        deny_patterns=("/login",),
        max_pages=20,
        crawl_depth=1,
        risk_level="low",
        notes="test",
    )


def _ollama_candidate() -> DocsSourceCandidate:
    return DocsSourceCandidate(
        service_id="ollama",
        display_name="Ollama",
        aliases=("ollama",),
        docs_source="ollama_docs",
        official_start_urls=("https://docs.ollama.com/",),
        allowed_domains=("docs.ollama.com",),
        allow_patterns=(r"^https://docs\.ollama\.com/",),
        deny_patterns=("/login",),
        max_pages=25,
        crawl_depth=2,
        risk_level="low",
        notes="test",
    )


def _crawled_page(url: str = "https://openrouter.ai/docs") -> CrawledPage:
    return CrawledPage(
        source_name="openrouter_docs",
        url=url,
        html="<html><head><title>OpenRouter docs</title></head><body>Docs</body></html>",
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )


def _extracted_page(url: str) -> ExtractedPage:
    return ExtractedPage(
        source_name="openrouter_docs",
        source_url=url,
        canonical_url=url,
        title="OpenRouter docs",
        structured_text="# OpenRouter docs\n\nUse the API with supported models.",
        content_hash="hash-1",
        headings=("OpenRouter docs",),
        crawled_at=datetime.now(timezone.utc),
    )
