"""Safe web discovery for unknown official documentation candidates."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import httpx

from app.db.repositories import DocsCandidateSuggestion
from app.docs_registry.candidates import DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG, load_docs_source_candidates_config
from app.docs_registry.models import DocsSourceCandidate, DocsSourceCandidatesConfig
from app.service_registry.types import ServiceDocsStatus

MAX_SEARCH_RESULTS = 5
DISCOVERY_SEARCH_TEMPLATE = "{service} official documentation"
DISCOVERY_API_SEARCH_TEMPLATE = "{service} official API documentation"
DISCOVERY_USER_MESSAGE = "Документация этого сервиса пока не подключена. Я отправил предложение администратору."
LOW_CONFIDENCE_OWNER_MESSAGE = "Официальный источник документации для этого сервиса не подтверждён."

_SERVICE_ID_RE = re.compile(r"[^a-z0-9]+")
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_SECRET_LIKE_RE = re.compile(r"\b(?:sk|pk|ghp|xoxb|bot)[-_A-Za-z0-9]{18,}\b", re.I)
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{28,}\b")
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{2,31}\b")
_CAMEL_OR_BRAND_RE = re.compile(r"[A-Z][a-z0-9]+[A-Z0-9][A-Za-z0-9-]*|[A-Z][A-Za-z0-9-]{2,31}")

_ORDINARY_WORDS = frozenset(
    {
        "api",
        "sdk",
        "webhook",
        "webhooks",
        "docs",
        "documentation",
        "developer",
        "developers",
        "service",
        "setup",
        "connect",
        "integration",
        "help",
        "how",
        "what",
        "where",
        "when",
        "why",
        "как",
        "что",
        "где",
        "когда",
        "почему",
        "можно",
        "нужно",
        "сервис",
        "документация",
        "интеграция",
        "подключить",
        "настроить",
    }
)
_DOCS_MARKERS = frozenset({"docs", "documentation", "developer", "developers", "api", "reference", "manual"})
_TITLE_PRIORITY_MARKERS = frozenset({"api", "documentation", "developer", "developers", "reference", "guide"})
_URL_PRIORITY_MARKERS = ("/docs", "/developers", "/api", "/reference")
_SNIPPET_PRIORITY_MARKERS = (
    "official documentation",
    "api documentation",
    "api reference",
    "developer documentation",
    "developers documentation",
    "reference documentation",
)
_FORBIDDEN_PATH_PARTS = frozenset({"login", "account", "admin", "dashboard"})
_FORBIDDEN_HOSTS = frozenset(
    {
        "medium.com",
        "reddit.com",
        "www.reddit.com",
        "stackoverflow.com",
        "stackexchange.com",
        "news.ycombinator.com",
        "dev.to",
        "hashnode.dev",
        "quora.com",
        "blogspot.com",
        "wordpress.com",
    }
)
_FORBIDDEN_HOST_PARTS = ("forum.", "forums.", "community.", "discourse.")
_FORBIDDEN_PATH_MARKERS = ("/blog", "/forum", "/forums", "/community", "/issues", "/pull")


@dataclass(frozen=True)
class DocumentationSearchResult:
    """One result from a configured documentation search provider."""

    title: str
    url: str
    snippet: str = ""
    final_url: str = ""
    score: float | None = None


class DocumentationSearchProvider(Protocol):
    """Search provider interface for official docs discovery."""

    async def search(self, query: str, *, limit: int = MAX_SEARCH_RESULTS) -> tuple[DocumentationSearchResult, ...]:
        """Return candidate results for a search query."""


class HttpDocumentationSearchProvider:
    """Tavily-compatible HTTP JSON search provider used only when explicitly configured."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.strip()
        self._api_key = api_key.strip()
        self._client = client or httpx.AsyncClient(timeout=timeout, trust_env=False)
        self._owns_client = client is None

    async def search(self, query: str, *, limit: int = MAX_SEARCH_RESULTS) -> tuple[DocumentationSearchResult, ...]:
        """Call a configured Tavily-compatible JSON search endpoint."""
        if not self._base_url or not self._api_key:
            return ()
        max_results = min(max(limit, 1), MAX_SEARCH_RESULTS)
        try:
            response = await self._client.post(
                self._base_url,
                json={
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                },
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code in {401, 429}:
                return ()
            response.raise_for_status()
            payload = response.json()
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, ValueError):
            return ()
        return _search_results_from_json(payload)[:MAX_SEARCH_RESULTS]

    async def close(self) -> None:
        """Close owned HTTP resources."""
        if self._owns_client:
            await self._client.aclose()


class DocsDiscoveryRepository(Protocol):
    """Repository methods needed for discovery dedupe/cooldown."""

    async def recent_for_service(
        self,
        *,
        workspace_id: str,
        service_id: str,
        limit: int = 10,
    ) -> tuple[DocsCandidateSuggestion, ...]:
        """Return recent suggestions for a service."""


class DocsDiscoverySuggestionService(Protocol):
    """Suggestion service method used by discovery."""

    async def create_or_reuse_pending_from_discovered_candidate(
        self,
        *,
        workspace_id: str,
        service_id: str,
        display_name: str,
        aliases: tuple[str, ...],
        official_url: str,
        allowed_domain: str,
        source_query: str,
        discovery_reason: str,
        confidence: float,
        requested_by_user_id: int | None,
        metadata: dict[str, object] | None = None,
    ) -> Any:
        """Create or reuse a pending discovered candidate."""


class DocsDiscoveryStatusProvider(Protocol):
    """Read active docs status before searching."""

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        """Return service/docs status rows."""


@dataclass(frozen=True)
class DocsDiscoveryOutcome:
    """Result of one unknown-service discovery attempt."""

    handled: bool
    reason: str
    service_name: str = ""
    service_id: str = ""
    suggestion: DocsCandidateSuggestion | None = None
    created: bool = False

    @property
    def user_message(self) -> str:
        """Return the safe regular-user message for created/reused suggestions."""
        return DISCOVERY_USER_MESSAGE


@dataclass(frozen=True)
class _EvaluatedResult:
    title: str
    url: str
    snippet: str
    allowed_domain: str
    confidence: float
    score: float | None = None


ConfigLoader = Callable[[Path | str], DocsSourceCandidatesConfig]


class DocsDiscoveryService:
    """Create pending docs suggestions from carefully validated search results."""

    def __init__(
        self,
        *,
        search_provider: DocumentationSearchProvider,
        suggestion_repository: DocsDiscoveryRepository,
        suggestion_service: DocsDiscoverySuggestionService,
        status_provider: DocsDiscoveryStatusProvider | None = None,
        candidates_config_path: Path | str = DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG,
        candidates_config_loader: ConfigLoader = load_docs_source_candidates_config,
    ) -> None:
        self._search_provider = search_provider
        self._suggestion_repository = suggestion_repository
        self._suggestion_service = suggestion_service
        self._status_provider = status_provider
        self._candidates_config_path = candidates_config_path
        self._candidates_config_loader = candidates_config_loader

    async def discover_from_question(
        self,
        question: str,
        *,
        workspace_id: str,
        requested_by_user_id: int | None = None,
    ) -> DocsDiscoveryOutcome:
        """Run at most one search request and create one pending suggestion if safe."""
        service_name = detect_unknown_service_name(question)
        if not service_name:
            return DocsDiscoveryOutcome(handled=False, reason="no_confident_service")
        service_id = normalize_service_id(service_name)
        if not service_id:
            return DocsDiscoveryOutcome(handled=False, reason="invalid_service")
        if await self._has_active_docs(service_name, service_id):
            return DocsDiscoveryOutcome(handled=False, reason="known_active", service_name=service_name, service_id=service_id)
        if self._has_curated_candidate(service_name, service_id):
            return DocsDiscoveryOutcome(handled=False, reason="curated", service_name=service_name, service_id=service_id)
        if await self._has_existing_suggestion(workspace_id=workspace_id, service_id=service_id):
            return DocsDiscoveryOutcome(handled=False, reason="existing_suggestion", service_name=service_name, service_id=service_id)

        search_query = build_discovery_search_query(question, service_name)
        results = await self._search_provider.search(search_query, limit=MAX_SEARCH_RESULTS)
        evaluated = choose_official_docs_result(service_name, results[:MAX_SEARCH_RESULTS])
        if evaluated is None:
            return DocsDiscoveryOutcome(handled=True, reason="low_confidence", service_name=service_name, service_id=service_id)

        ensure_result = await self._suggestion_service.create_or_reuse_pending_from_discovered_candidate(
            workspace_id=workspace_id,
            service_id=service_id,
            display_name=service_name,
            aliases=(service_name, service_id),
            official_url=evaluated.url,
            allowed_domain=evaluated.allowed_domain,
            source_query=search_query,
            discovery_reason="web_discovery_official_docs_candidate",
            confidence=evaluated.confidence,
            requested_by_user_id=requested_by_user_id,
            metadata=_discovered_candidate_metadata(service_id, evaluated),
        )
        return DocsDiscoveryOutcome(
            handled=True,
            reason="created" if bool(getattr(ensure_result, "created", False)) else "reused",
            service_name=service_name,
            service_id=service_id,
            suggestion=getattr(ensure_result, "suggestion", None),
            created=bool(getattr(ensure_result, "created", False)),
        )

    async def close(self) -> None:
        """Close provider resources if available."""
        close = getattr(self._search_provider, "close", None)
        if close is not None:
            await close()

    async def _has_active_docs(self, service_name: str, service_id: str) -> bool:
        if self._status_provider is None:
            return False
        statuses = await self._status_provider.list_statuses(scan_corpus=False)
        return any(_status_matches_service(status, service_name, service_id) and _status_has_active_docs(status) for status in statuses)

    def _has_curated_candidate(self, service_name: str, service_id: str) -> bool:
        try:
            candidates = self._candidates_config_loader(self._candidates_config_path).candidates
        except Exception:
            return False
        return any(_candidate_matches_service(candidate, service_name, service_id) for candidate in candidates)

    async def _has_existing_suggestion(self, *, workspace_id: str, service_id: str) -> bool:
        recent = await self._suggestion_repository.recent_for_service(
            workspace_id=workspace_id,
            service_id=service_id,
            limit=10,
        )
        return any(suggestion.status in {"pending", "preview_ready", "approved", "rejected", "failed", "activated"} for suggestion in recent)


def detect_unknown_service_name(question: str) -> str:
    """Return one confident service-looking name, or an empty string."""
    text = " ".join(str(question or "").split())
    if not text or _URL_RE.search(text) or _EMAIL_RE.search(text) or _UUID_RE.search(text) or _SECRET_LIKE_RE.search(text):
        return ""
    if _LONG_TOKEN_RE.search(text):
        return ""
    candidates: list[str] = []
    for match in _LATIN_WORD_RE.finditer(text):
        token = match.group(0).strip("-")
        if not _looks_like_service_token(token):
            continue
        candidates.append(token)
    unique = tuple(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else ""


def normalize_service_id(service_name: str) -> str:
    """Normalize a display service name into a stable suggestion service id."""
    clean = _SERVICE_ID_RE.sub("_", service_name.casefold()).strip("_")
    return clean[:80]


def build_discovery_search_query(question: str, service_name: str) -> str:
    """Build a safe search query without sending the full user question."""
    if _question_mentions_api(question):
        return DISCOVERY_API_SEARCH_TEMPLATE.format(service=service_name)
    return DISCOVERY_SEARCH_TEMPLATE.format(service=service_name)


def choose_official_docs_result(
    service_name: str,
    results: tuple[DocumentationSearchResult, ...] | list[DocumentationSearchResult],
) -> _EvaluatedResult | None:
    """Choose the strongest safe official docs result from up to five results."""
    best: tuple[float, _EvaluatedResult] | None = None
    for index, result in enumerate(tuple(results)[:MAX_SEARCH_RESULTS]):
        evaluated = _evaluate_result(service_name, result)
        if evaluated is None:
            continue
        score = _result_selection_score(evaluated, original_score=result.score, index=index)
        if best is None or score > best[0]:
            best = (score, evaluated)
    return best[1] if best is not None else None


def _evaluate_result(service_name: str, result: DocumentationSearchResult) -> _EvaluatedResult | None:
    parsed = urlparse(result.url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return None
    if not _is_public_hostname(host):
        return None
    if _has_forbidden_path(parsed.path):
        return None
    if _is_forbidden_host_or_path(host, parsed.path):
        return None
    if result.final_url and not _redirect_host_allowed(result.url, result.final_url):
        return None
    title = " ".join(str(result.title or "").split())
    snippet = " ".join(str(result.snippet or "").split())
    if not _text_mentions_service(f"{title} {snippet}", service_name):
        return None
    if not _has_docs_marker(f"{title} {snippet} {parsed.path}"):
        return None
    return _EvaluatedResult(
        title=title[:160],
        url=result.url.rstrip("/") or result.url,
        snippet=snippet[:240],
        allowed_domain=host,
        confidence=_confidence_from_score(result.score),
        score=result.score,
    )


def _result_selection_score(evaluated: _EvaluatedResult, *, original_score: float | None, index: int) -> float:
    parsed = urlparse(evaluated.url)
    path = (parsed.path or "/").casefold()
    host = (parsed.hostname or "").casefold()
    title = evaluated.title.casefold()
    snippet = evaluated.snippet.casefold()
    score = _confidence_from_score(original_score) * 10.0
    score += max(0, MAX_SEARCH_RESULTS - index) * 0.01

    if any(marker in title for marker in _TITLE_PRIORITY_MARKERS):
        score += 3.0
    if any(marker in path for marker in _URL_PRIORITY_MARKERS):
        score += 3.0
    if any(marker in snippet for marker in _SNIPPET_PRIORITY_MARKERS):
        score += 2.0
    if "api" in title and "api" in path:
        score += 2.0

    if title.endswith(" home") or title.endswith("| home") or title.endswith("- home"):
        score -= 4.0
    if _looks_like_support_help_root(host, path):
        score -= 4.0
    if not _has_specific_docs_path(path):
        score -= 2.0
    if _snippet_is_general_support(snippet):
        score -= 2.0
    return score


def _question_mentions_api(question: str) -> bool:
    return bool(re.search(r"\bapi\b", str(question or ""), flags=re.IGNORECASE))


def _looks_like_support_help_root(host: str, path: str) -> bool:
    if not (host.startswith(("support.", "help.")) or ".support." in host or ".help." in host):
        return False
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if len(segments) <= 1:
        return True
    return len(segments) <= 2 and not any(marker.strip("/") in segments for marker in _URL_PRIORITY_MARKERS)


def _has_specific_docs_path(path: str) -> bool:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    return len(segments) >= 2 and any(marker in path for marker in _URL_PRIORITY_MARKERS)


def _snippet_is_general_support(snippet: str) -> bool:
    support_terms = ("support", "help center", "help articles", "browse articles", "customer support")
    specific_terms = ("api", "documentation", "developer", "reference", "endpoint", "request", "record")
    return any(term in snippet for term in support_terms) and not any(term in snippet for term in specific_terms)


def _search_results_from_json(payload: Any) -> tuple[DocumentationSearchResult, ...]:
    rows: Any
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("items") or payload.get("data") or ()
    else:
        rows = payload
    results: list[DocumentationSearchResult] = []
    if not isinstance(rows, list):
        return ()
    for row in rows[:MAX_SEARCH_RESULTS]:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or row.get("link") or row.get("href") or "").strip()
        if not url:
            continue
        results.append(
            DocumentationSearchResult(
                title=str(row.get("title") or row.get("name") or "").strip(),
                url=url,
                snippet=str(
                    row.get("snippet") or row.get("content") or row.get("description") or row.get("summary") or ""
                ).strip(),
                final_url=str(row.get("final_url") or row.get("resolved_url") or "").strip(),
                score=_float_or_none(row.get("score")),
            )
        )
    return tuple(results)


def _confidence_from_score(score: float | None) -> float:
    if score is None:
        return 0.68
    return max(0.0, min(float(score), 1.0))


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _looks_like_service_token(token: str) -> bool:
    clean = token.strip("-")
    lower = clean.casefold()
    if lower in _ORDINARY_WORDS or len(clean) < 3:
        return False
    if clean.isdigit():
        return False
    if clean.islower() and not re.search(r"\d", clean):
        return False
    return bool(_CAMEL_OR_BRAND_RE.fullmatch(clean))


def _is_public_hostname(host: str) -> bool:
    if host in {"localhost"} or host.endswith((".local", ".internal", ".test", ".invalid")):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _has_forbidden_path(path: str) -> bool:
    parts = {part.casefold() for part in path.split("/") if part}
    return bool(parts & _FORBIDDEN_PATH_PARTS)


def _is_forbidden_host_or_path(host: str, path: str) -> bool:
    if host in _FORBIDDEN_HOSTS or host.endswith(tuple("." + item for item in _FORBIDDEN_HOSTS)):
        return True
    if host == "github.com" or host == "gitlab.com" or host == "bitbucket.org":
        return True
    if any(part in host for part in _FORBIDDEN_HOST_PARTS):
        return True
    clean_path = "/" + path.strip("/").casefold()
    return any(marker in clean_path for marker in _FORBIDDEN_PATH_MARKERS)


def _redirect_host_allowed(original_url: str, final_url: str) -> bool:
    original = (urlparse(original_url).hostname or "").casefold()
    final = (urlparse(final_url).hostname or "").casefold()
    if not original or not final:
        return False
    return final == original or final.endswith("." + original) or original.endswith("." + final)


def _text_mentions_service(text: str, service_name: str) -> bool:
    haystack = _compact_alnum(text)
    service = _compact_alnum(service_name)
    if service and service in haystack:
        return True
    return any(token and token in haystack for token in _split_service_tokens(service_name))


def _has_docs_marker(text: str) -> bool:
    lower = text.casefold()
    return any(marker in lower for marker in _DOCS_MARKERS)


def _split_service_tokens(service_name: str) -> tuple[str, ...]:
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", service_name).split()
    return tuple(_compact_alnum(part) for part in parts if len(part) >= 3)


def _compact_alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _status_matches_service(status: ServiceDocsStatus, service_name: str, service_id: str) -> bool:
    needles = {service_id, service_name.casefold(), normalize_service_id(service_name)}
    values = {
        status.service_id.casefold(),
        normalize_service_id(status.service_id),
        status.display_name.casefold(),
        normalize_service_id(status.display_name),
        *(alias.casefold() for alias in status.aliases),
        *(normalize_service_id(alias) for alias in status.aliases),
    }
    return bool(needles & values)


def _status_has_active_docs(status: ServiceDocsStatus) -> bool:
    return (
        status.docs_status == "indexed"
        or int(status.active_docs_count or 0) > 0
        or int(status.active_chunks_count or 0) > 0
    )


def _candidate_matches_service(candidate: DocsSourceCandidate, service_name: str, service_id: str) -> bool:
    needles = {service_id, service_name.casefold(), normalize_service_id(service_name)}
    values = {
        candidate.service_id.casefold(),
        normalize_service_id(candidate.service_id),
        candidate.display_name.casefold(),
        normalize_service_id(candidate.display_name),
        *(alias.casefold() for alias in candidate.aliases),
        *(normalize_service_id(alias) for alias in candidate.aliases),
    }
    return bool(needles & values)


def _discovered_candidate_metadata(service_id: str, evaluated: _EvaluatedResult) -> dict[str, object]:
    escaped_url = re.escape(evaluated.url.rstrip("/"))
    escaped_domain = re.escape(evaluated.allowed_domain)
    return {
        "source": "web_discovery",
        "docs_source": f"{service_id}_docs",
        "official_start_urls": [evaluated.url],
        "allowed_domains": [evaluated.allowed_domain],
        "allow_patterns": [rf"^{escaped_url}(?:/|$)"],
        "deny_patterns": [r"/login(?:/|$)", r"/account(?:/|$)", r"/admin(?:/|$)", r"/dashboard(?:/|$)"],
        "max_pages": 25,
        "crawl_depth": 1,
        "search_result": {
            "title": evaluated.title,
            "snippet": evaluated.snippet,
            "domain": evaluated.allowed_domain,
            "score": evaluated.score,
        },
        "notes": f"Discovered docs candidate on {evaluated.allowed_domain}",
        "domain_policy": rf"^https://{escaped_domain}/",
    }
