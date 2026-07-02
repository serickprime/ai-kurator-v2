import asyncio
from datetime import datetime, timezone

import pytest

from app.docs_registry.models import DocsSourceCandidate, DocsSourceCandidatesConfig
from app.docs_registry.preview import (
    ArbitraryDocsUrlError,
    DocsCandidatePreviewService,
    candidate_to_external_doc_source,
)
from app.external_docs.types import CrawledPage, ExternalDocSource


class FakeCrawler:
    def __init__(self, pages: list[CrawledPage] | None = None) -> None:
        self.pages = pages or []
        self.calls: list[tuple[ExternalDocSource, int | None]] = []
        self.mutation_calls: list[str] = []

    async def crawl(self, source: ExternalDocSource, *, limit: int | None = None) -> list[CrawledPage]:
        self.calls.append((source, limit))
        return self.pages

    async def index(self) -> None:
        self.mutation_calls.append("index")

    async def write(self) -> None:
        self.mutation_calls.append("write")


def test_preview_finds_candidate_by_service_id() -> None:
    crawler = FakeCrawler([_page("https://docs.example.com/start", "Overview")])
    service = DocsCandidatePreviewService(candidates_config=_config(_candidate()), crawler=crawler)

    result = asyncio.run(service.preview("claude_code"))

    assert result.service_id == "claude_code"
    assert result.status == "ok"
    assert result.pages_found == 1
    assert result.sample_titles == ("Overview",)


def test_preview_finds_candidate_by_alias() -> None:
    crawler = FakeCrawler([_page("https://docs.example.com/start", "Install")])
    service = DocsCandidatePreviewService(candidates_config=_config(_candidate()), crawler=crawler)

    result = asyncio.run(service.preview("claude cli"))

    assert result.display_name == "Claude Code"
    assert result.sample_titles == ("Install",)


def test_preview_rejects_arbitrary_url() -> None:
    service = DocsCandidatePreviewService(candidates_config=_config(_candidate()), crawler=FakeCrawler())

    with pytest.raises(ArbitraryDocsUrlError):
        asyncio.run(service.preview("https://not-allowed.example.com/docs"))


def test_preview_uses_only_allowed_candidate_domains() -> None:
    crawler = FakeCrawler([_page("https://docs.example.com/start", "Overview")])
    service = DocsCandidatePreviewService(candidates_config=_config(_candidate()), crawler=crawler)

    asyncio.run(service.preview("claude_code"))

    source, _limit = crawler.calls[-1]
    assert source.allowed_domains == ("docs.example.com",)
    assert source.start_urls == ("https://docs.example.com/start",)


def test_preview_limits_pages_to_five() -> None:
    crawler = FakeCrawler([_page(f"https://docs.example.com/{index}", f"Page {index}") for index in range(8)])
    service = DocsCandidatePreviewService(candidates_config=_config(_candidate()), crawler=crawler)

    result = asyncio.run(service.preview("claude_code", limit=20))

    assert crawler.calls[-1][1] == 5
    assert result.pages_checked == 5
    assert result.pages_found == 5


def test_preview_does_not_call_indexer_or_supabase_writes() -> None:
    crawler = FakeCrawler([_page("https://docs.example.com/start", "Overview")])
    service = DocsCandidatePreviewService(candidates_config=_config(_candidate()), crawler=crawler)

    asyncio.run(service.preview("claude_code"))

    assert crawler.mutation_calls == []


def test_preview_returns_needs_review_when_no_pages_for_review_candidate() -> None:
    candidate = _candidate(risk_level="review")
    service = DocsCandidatePreviewService(candidates_config=_config(candidate), crawler=FakeCrawler([]))

    result = asyncio.run(service.preview("claude_code"))

    assert result.status == "needs_review"
    assert result.pages_found == 0
    assert "не найдено страниц" in result.warnings
    assert "нужна ручная проверка" in result.warnings


def test_candidate_to_external_source_uses_curated_limits_and_policy() -> None:
    source = candidate_to_external_doc_source(_candidate(max_pages=50, crawl_depth=3))

    assert source.name == "claude_code_docs"
    assert source.max_pages == 5
    assert source.crawl_depth == 3
    assert source.allowed_domains == ("docs.example.com",)


def _config(candidate: DocsSourceCandidate) -> DocsSourceCandidatesConfig:
    return DocsSourceCandidatesConfig(candidates=(candidate,))


def _candidate(
    *,
    max_pages: int = 25,
    crawl_depth: int = 2,
    risk_level: str = "low",
) -> DocsSourceCandidate:
    return DocsSourceCandidate(
        service_id="claude_code",
        display_name="Claude Code",
        aliases=("claude code", "claude cli"),
        docs_source="claude_code_docs",
        official_start_urls=("https://docs.example.com/start",),
        allowed_domains=("docs.example.com",),
        allow_patterns=(r"^https://docs\.example\.com/",),
        deny_patterns=("/login",),
        max_pages=max_pages,
        crawl_depth=crawl_depth,
        risk_level=risk_level,  # type: ignore[arg-type]
        notes="test",
    )


def _page(url: str, title: str) -> CrawledPage:
    return CrawledPage(
        source_name="claude_code_docs",
        url=url,
        html=f"<html><head><title>{title}</title></head><body>Body</body></html>",
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )
