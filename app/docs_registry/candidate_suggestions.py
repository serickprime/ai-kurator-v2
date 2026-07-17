"""Persistent owner-review suggestions for curated docs candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.db.repositories import DocsCandidateSuggestion, DocsCandidateSuggestionRepository
from app.docs_registry.candidates import DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG, load_docs_source_candidates_config
from app.docs_registry.models import DocsSourceCandidate, DocsSourceCandidatesConfig

DEFAULT_DISCOVERY_REASON = "curated_docs_source_candidate"


@dataclass(frozen=True)
class DocsCandidateSuggestionEnsureResult:
    """Result of creating or reusing a persistent docs candidate suggestion."""

    suggestion: DocsCandidateSuggestion
    created: bool


ConfigLoader = Callable[[Path | str], DocsSourceCandidatesConfig]


class DocsCandidateSuggestionService:
    """Create pending review records from the curated docs candidates catalog."""

    def __init__(
        self,
        repository: DocsCandidateSuggestionRepository,
        *,
        candidates_config_path: Path | str = DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG,
        config_loader: ConfigLoader = load_docs_source_candidates_config,
    ) -> None:
        self._repository = repository
        self._candidates_config_path = candidates_config_path
        self._config_loader = config_loader

    async def create_or_reuse_pending_from_candidate(
        self,
        *,
        workspace_id: str,
        service_id: str,
        source_query: str = "",
        requested_by_user_id: int | None = None,
        confidence: float = 1.0,
        discovery_reason: str = DEFAULT_DISCOVERY_REASON,
    ) -> DocsCandidateSuggestionEnsureResult:
        """Create a pending suggestion from a curated candidate, or reuse an existing one."""
        candidate = self._load_candidate(service_id)
        official_url = candidate.official_start_urls[0]
        allowed_domain = candidate.allowed_domains[0]
        existing = await self._repository.find_by_service_url(
            workspace_id=workspace_id,
            service_id=candidate.service_id,
            official_url=official_url,
        )
        if existing is not None:
            return DocsCandidateSuggestionEnsureResult(suggestion=existing, created=False)

        suggestion = await self._repository.create_pending(
            workspace_id=workspace_id,
            service_id=candidate.service_id,
            display_name=candidate.display_name,
            aliases=candidate.aliases,
            official_url=official_url,
            allowed_domain=allowed_domain,
            source_query=source_query,
            discovery_reason=discovery_reason,
            confidence=confidence,
            risk_level=candidate.risk_level,
            requested_by_user_id=requested_by_user_id,
            metadata=_candidate_metadata(candidate),
        )
        return DocsCandidateSuggestionEnsureResult(suggestion=suggestion, created=True)

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
    ) -> DocsCandidateSuggestionEnsureResult:
        """Create or reuse a pending suggestion from a validated discovery result."""
        existing = await self._repository.find_by_service_url(
            workspace_id=workspace_id,
            service_id=service_id,
            official_url=official_url,
        )
        if existing is not None:
            return DocsCandidateSuggestionEnsureResult(suggestion=existing, created=False)

        suggestion = await self._repository.create_pending(
            workspace_id=workspace_id,
            service_id=service_id,
            display_name=display_name,
            aliases=aliases,
            official_url=official_url,
            allowed_domain=allowed_domain,
            source_query=source_query,
            discovery_reason=discovery_reason,
            confidence=confidence,
            risk_level="review",
            requested_by_user_id=requested_by_user_id,
            metadata=metadata or {},
        )
        return DocsCandidateSuggestionEnsureResult(suggestion=suggestion, created=True)

    def _load_candidate(self, service_id: str) -> DocsSourceCandidate:
        return self._config_loader(self._candidates_config_path).candidate(service_id)


def _candidate_metadata(candidate: DocsSourceCandidate) -> dict[str, object]:
    return {
        "source": "docs_source_candidates.yaml",
        "docs_source": candidate.docs_source,
        "official_start_urls": list(candidate.official_start_urls),
        "allowed_domains": list(candidate.allowed_domains),
        "allow_patterns": list(candidate.allow_patterns),
        "deny_patterns": list(candidate.deny_patterns),
        "max_pages": candidate.max_pages,
        "crawl_depth": candidate.crawl_depth,
        "notes": candidate.notes,
    }
