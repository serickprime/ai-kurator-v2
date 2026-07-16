from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from typing import Any

import httpx
import pytest

from app.db.repositories import DocsCandidateSuggestion
from app.docs_registry.candidate_suggestions import DocsCandidateSuggestionService
from app.docs_registry.discovery import (
    DocumentationSearchResult,
    DocsDiscoveryService,
    HttpDocumentationSearchProvider,
)
from app.docs_registry.models import DocsSourceCandidate, DocsSourceCandidatesConfig
from app.service_registry.types import ServiceDocsStatus


def test_unknown_service_calls_exactly_one_search_and_creates_pending() -> None:
    search = FakeSearchProvider((_valid_result(),))
    repo = FakeSuggestionRepository()
    service = _service(search, repo=repo)

    outcome = asyncio.run(
        service.discover_from_question(
            "Как подключить AcmePay webhooks?",
            workspace_id="workspace-1",
            requested_by_user_id=42,
        )
    )

    assert search.calls == [("AcmePay official documentation", 5)]
    assert outcome.suggestion is not None
    assert outcome.suggestion.status == "pending"
    assert outcome.suggestion.risk_level == "review"
    assert outcome.suggestion.allowed_domain == "docs.acmepay.com"
    assert repo.inserts[0]["source_query"] == "AcmePay official documentation"
    assert "Как подключить" not in repo.inserts[0]["source_query"]


def test_api_question_uses_safe_api_documentation_query_without_full_question() -> None:
    search = FakeSearchProvider((_valid_result(),))
    repo = FakeSuggestionRepository()
    service = _service(search, repo=repo)
    question = "Как получить список записей через AcmePay API?"

    asyncio.run(service.discover_from_question(question, workspace_id="workspace-1", requested_by_user_id=42))

    assert search.calls == [("AcmePay official API documentation", 5)]
    assert question not in [call[0] for call in search.calls]
    assert repo.inserts[0]["source_query"] == "AcmePay official API documentation"


def test_more_specific_api_documentation_is_selected_over_support_home() -> None:
    search = FakeSearchProvider(
        (
            DocumentationSearchResult(
                title="Airtable Support | Home",
                url="https://support.airtable.com/docs",
                snippet="Airtable support helps users browse help articles and contact support.",
                score=0.99,
            ),
            DocumentationSearchResult(
                title="Airtable Web API Reference",
                url="https://airtable.com/developers/web/api/list-records",
                snippet="Official Airtable API reference documentation for listing records with requests and responses.",
                score=0.75,
            ),
        )
    )
    repo = FakeSuggestionRepository()
    service = _service(search, repo=repo)

    outcome = asyncio.run(
        service.discover_from_question(
            "Как получить список записей через Airtable API?",
            workspace_id="workspace-1",
            requested_by_user_id=42,
        )
    )

    assert outcome.suggestion is not None
    assert outcome.suggestion.official_url == "https://airtable.com/developers/web/api/list-records"
    assert repo.inserts[0]["allowed_domain"] == "airtable.com"
    assert repo.inserts[0]["source_query"] == "Airtable official API documentation"


def test_known_active_service_does_not_call_search() -> None:
    search = FakeSearchProvider((_valid_result(),))
    repo = FakeSuggestionRepository()
    service = _service(search, repo=repo, statuses=(_status("acmepay", "AcmePay", docs_status="indexed"),))

    outcome = asyncio.run(service.discover_from_question("Как подключить AcmePay?", workspace_id="workspace-1"))

    assert outcome.reason == "known_active"
    assert search.calls == []
    assert repo.inserts == []


def test_curated_candidate_does_not_call_search() -> None:
    search = FakeSearchProvider((_valid_result(),))
    repo = FakeSuggestionRepository()
    service = _service(search, repo=repo, candidates=(_candidate(),))

    outcome = asyncio.run(service.discover_from_question("Как подключить AcmePay?", workspace_id="workspace-1"))

    assert outcome.reason == "curated"
    assert search.calls == []
    assert repo.inserts == []


def test_ordinary_word_and_random_token_do_not_call_search() -> None:
    search = FakeSearchProvider((_valid_result(),))
    service = _service(search, repo=FakeSuggestionRepository())

    ordinary = asyncio.run(service.discover_from_question("Как настроить webhooks?", workspace_id="workspace-1"))
    token = asyncio.run(
        service.discover_from_question("randomtokenwithmanycharacters1234567890", workspace_id="workspace-1")
    )

    assert ordinary.reason == "no_confident_service"
    assert token.reason == "no_confident_service"
    assert search.calls == []


def test_private_or_local_url_is_rejected() -> None:
    outcome, repo = _discover_with_result(DocumentationSearchResult("AcmePay Documentation", "https://127.0.0.1/docs", "AcmePay API docs"))

    assert outcome.reason == "low_confidence"
    assert repo.inserts == []


def test_forbidden_path_is_rejected() -> None:
    outcome, repo = _discover_with_result(
        DocumentationSearchResult("AcmePay Documentation", "https://docs.acmepay.com/login", "AcmePay API docs")
    )

    assert outcome.reason == "low_confidence"
    assert repo.inserts == []


def test_forum_or_blog_result_is_rejected() -> None:
    outcome, repo = _discover_with_result(
        DocumentationSearchResult("AcmePay Documentation", "https://medium.com/acmepay/docs", "AcmePay API docs")
    )

    assert outcome.reason == "low_confidence"
    assert repo.inserts == []


def test_low_confidence_result_does_not_create_suggestion() -> None:
    outcome, repo = _discover_with_result(
        DocumentationSearchResult("API Reference", "https://docs.acmepay.com/docs", "General developer reference")
    )

    assert outcome.reason == "low_confidence"
    assert repo.inserts == []


def test_duplicate_existing_suggestion_does_not_create_or_search() -> None:
    existing = _suggestion(service_id="acmepay", official_url="https://docs.acmepay.com/docs")
    search = FakeSearchProvider((_valid_result(),))
    repo = FakeSuggestionRepository((existing,))
    service = _service(search, repo=repo)

    outcome = asyncio.run(service.discover_from_question("Как подключить AcmePay?", workspace_id="workspace-1"))

    assert outcome.reason == "existing_suggestion"
    assert search.calls == []
    assert repo.inserts == []


def test_http_search_provider_uses_tavily_post_contract_and_maps_results() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "AcmePay Documentation",
                        "url": "https://docs.acmepay.com/docs",
                        "content": "AcmePay API reference and developer documentation.",
                        "score": 0.9,
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HttpDocumentationSearchProvider(
        base_url="https://api.tavily.com/search",
        api_key="test-key",
        client=client,
    )

    results = asyncio.run(provider.search("AcmePay official documentation", limit=99))
    asyncio.run(client.aclose())

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.tavily.com/search"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content.decode("utf-8")) == {
        "query": "AcmePay official documentation",
        "max_results": 5,
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    assert results == (
        DocumentationSearchResult(
            title="AcmePay Documentation",
            url="https://docs.acmepay.com/docs",
            snippet="AcmePay API reference and developer documentation.",
            score=0.9,
        ),
    )


def test_tavily_score_is_saved_as_compact_metadata_signal() -> None:
    outcome, repo = _discover_with_result(
        DocumentationSearchResult(
            title="AcmePay Documentation",
            url="https://docs.acmepay.com/docs",
            snippet="AcmePay API reference and developer documentation.",
            score=0.9,
        )
    )

    assert outcome.suggestion is not None
    assert repo.inserts[0]["confidence"] == 0.9
    assert repo.inserts[0]["metadata"]["search_result"]["score"] == 0.9


@pytest.mark.parametrize("status_code", [401, 429])
def test_http_search_provider_returns_empty_for_auth_or_rate_limit(status_code: int) -> None:
    provider, client = _http_provider_returning(httpx.Response(status_code, json={"error": "nope"}))

    results = asyncio.run(provider.search("AcmePay official documentation"))
    asyncio.run(client.aclose())

    assert results == ()


def test_http_search_provider_returns_empty_for_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HttpDocumentationSearchProvider(base_url="https://api.tavily.com/search", api_key="test-key", client=client)

    results = asyncio.run(provider.search("AcmePay official documentation"))
    asyncio.run(client.aclose())

    assert results == ()


def test_http_search_provider_returns_empty_for_invalid_json_or_empty_results() -> None:
    invalid_provider, invalid_client = _http_provider_returning(httpx.Response(200, text="not json"))
    empty_provider, empty_client = _http_provider_returning(httpx.Response(200, json={"results": []}))

    invalid = asyncio.run(invalid_provider.search("AcmePay official documentation"))
    empty = asyncio.run(empty_provider.search("AcmePay official documentation"))
    asyncio.run(invalid_client.aclose())
    asyncio.run(empty_client.aclose())

    assert invalid == ()
    assert empty == ()


class FakeSearchProvider:
    def __init__(self, results: tuple[DocumentationSearchResult, ...]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, limit: int = 5) -> tuple[DocumentationSearchResult, ...]:
        self.calls.append((query, limit))
        return self.results


class FakeSuggestionRepository:
    def __init__(self, rows: tuple[DocsCandidateSuggestion, ...] = ()) -> None:
        self.rows = {row.id: row for row in rows}
        self.inserts: list[dict[str, Any]] = []

    async def recent_for_service(
        self,
        *,
        workspace_id: str,
        service_id: str,
        limit: int = 10,
    ) -> tuple[DocsCandidateSuggestion, ...]:
        del workspace_id, limit
        return tuple(row for row in self.rows.values() if row.service_id == service_id)

    async def find_by_service_url(
        self,
        *,
        workspace_id: str,
        service_id: str,
        official_url: str,
    ) -> DocsCandidateSuggestion | None:
        del workspace_id
        normalized_url = official_url.rstrip("/")
        for row in self.rows.values():
            if row.service_id == service_id and row.official_url.rstrip("/") == normalized_url:
                return row
        return None

    async def create_pending(self, **payload: Any) -> DocsCandidateSuggestion:
        self.inserts.append(deepcopy(payload))
        suggestion = _suggestion(
            id=f"suggestion-{len(self.rows) + 1}",
            service_id=str(payload["service_id"]),
            display_name=str(payload["display_name"]),
            official_url=str(payload["official_url"]),
            allowed_domain=str(payload["allowed_domain"]),
            source_query=str(payload["source_query"]),
            discovery_reason=str(payload["discovery_reason"]),
            confidence=float(payload["confidence"]),
            risk_level=str(payload["risk_level"]),
            metadata=dict(payload.get("metadata") or {}),
        )
        self.rows[suggestion.id] = suggestion
        return suggestion


class FakeStatusProvider:
    def __init__(self, statuses: tuple[ServiceDocsStatus, ...]) -> None:
        self.statuses = statuses

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        del scan_corpus, service
        return self.statuses


def _service(
    search: FakeSearchProvider,
    *,
    repo: FakeSuggestionRepository,
    statuses: tuple[ServiceDocsStatus, ...] = (),
    candidates: tuple[DocsSourceCandidate, ...] = (),
) -> DocsDiscoveryService:
    return DocsDiscoveryService(
        search_provider=search,
        suggestion_repository=repo,
        suggestion_service=DocsCandidateSuggestionService(repo),  # type: ignore[arg-type]
        status_provider=FakeStatusProvider(statuses),
        candidates_config_loader=lambda _path: DocsSourceCandidatesConfig(candidates=candidates),
    )


def _discover_with_result(result: DocumentationSearchResult):
    repo = FakeSuggestionRepository()
    service = _service(FakeSearchProvider((result,)), repo=repo)
    outcome = asyncio.run(service.discover_from_question("Как подключить AcmePay?", workspace_id="workspace-1"))
    return outcome, repo


def _http_provider_returning(response: httpx.Response) -> tuple[HttpDocumentationSearchProvider, httpx.AsyncClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HttpDocumentationSearchProvider(base_url="https://api.tavily.com/search", api_key="test-key", client=client)
    return provider, client


def _valid_result() -> DocumentationSearchResult:
    return DocumentationSearchResult(
        title="AcmePay Documentation",
        url="https://docs.acmepay.com/docs",
        snippet="AcmePay API reference and developer documentation.",
    )


def _candidate() -> DocsSourceCandidate:
    return DocsSourceCandidate(
        service_id="acmepay",
        display_name="AcmePay",
        aliases=("acmepay",),
        docs_source="acmepay_docs",
        official_start_urls=("https://docs.acmepay.com/docs",),
        allowed_domains=("docs.acmepay.com",),
        allow_patterns=(r"^https://docs\.acmepay\.com/docs",),
        deny_patterns=("/login",),
        max_pages=25,
        crawl_depth=1,
        risk_level="review",
    )


def _status(service_id: str, display_name: str, *, docs_status: str) -> ServiceDocsStatus:
    return ServiceDocsStatus(
        service_id=service_id,
        display_name=display_name,
        aliases=(display_name,),
        docs_source=f"{service_id}_docs",
        configured_status="enabled",
        docs_status=docs_status,  # type: ignore[arg-type]
        active_docs_count=1,
        active_chunks_count=3,
    )


def _suggestion(
    *,
    id: str = "suggestion-existing",
    service_id: str = "acmepay",
    display_name: str = "AcmePay",
    official_url: str = "https://docs.acmepay.com/docs",
    allowed_domain: str = "docs.acmepay.com",
    source_query: str = "",
    discovery_reason: str = "test",
    confidence: float = 0.68,
    risk_level: str = "review",
    metadata: dict[str, Any] | None = None,
) -> DocsCandidateSuggestion:
    return DocsCandidateSuggestion(
        id=id,
        workspace_id="workspace-1",
        service_id=service_id,
        display_name=display_name,
        aliases=(display_name, service_id),
        official_url=official_url,
        allowed_domain=allowed_domain,
        source_query=source_query,
        discovery_reason=discovery_reason,
        confidence=confidence,
        risk_level=risk_level,
        status="pending",
        preview_status="not_run",
        metadata=metadata or {},
    )
