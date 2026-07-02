"""Models for External Docs Registry v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RiskLevel = Literal["low", "medium", "review"]
DocsPreviewStatus = Literal["ok", "failed", "needs_review"]


@dataclass(frozen=True)
class DocsSourceCandidate:
    """One curated candidate for a future official docs source."""

    service_id: str
    display_name: str
    aliases: tuple[str, ...]
    docs_source: str
    official_start_urls: tuple[str, ...]
    allowed_domains: tuple[str, ...]
    allow_patterns: tuple[str, ...]
    deny_patterns: tuple[str, ...]
    max_pages: int
    crawl_depth: int
    risk_level: RiskLevel
    notes: str = ""


@dataclass(frozen=True)
class DocsSourceCandidatesConfig:
    """Loaded candidates catalog."""

    candidates: tuple[DocsSourceCandidate, ...]

    def candidate(self, service_id: str) -> DocsSourceCandidate:
        """Return one candidate by service id."""
        needle = service_id.strip().casefold()
        for candidate in self.candidates:
            if candidate.service_id.casefold() == needle:
                return candidate
        raise KeyError(service_id)


@dataclass(frozen=True)
class DocsCandidatePreviewResult:
    """Safe dry-run result for one curated docs candidate."""

    service_id: str
    display_name: str
    docs_source: str
    allowed_domains: tuple[str, ...]
    start_urls: tuple[str, ...]
    pages_checked: int
    pages_found: int
    sample_titles: tuple[str, ...] = ()
    sample_urls: tuple[str, ...] = ()
    status: DocsPreviewStatus = "failed"
    warnings: tuple[str, ...] = ()
    risk_level: RiskLevel = "review"
    notes: str = ""
