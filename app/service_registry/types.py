"""Dataclasses for service/docs registry status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ServiceConfigStatus = Literal["enabled", "not_configured", "disabled", "needs_review"]
ServiceDocsFinalStatus = Literal["indexed", "configured_not_indexed", "not_configured", "disabled", "needs_review"]


@dataclass(frozen=True)
class ServiceDefinition:
    """One service entry from config/service_docs_registry.yaml."""

    service_id: str
    display_name: str
    aliases: tuple[str, ...]
    docs_source: str | None = None
    status: ServiceConfigStatus = "not_configured"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "service_id": self.service_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "docs_source": self.docs_source,
            "status": self.status,
        }


@dataclass(frozen=True)
class ServiceRegistryConfig:
    """Loaded service registry config."""

    services: tuple[ServiceDefinition, ...]

    def service(self, service_id_or_alias: str) -> ServiceDefinition:
        """Return a service by id or alias."""
        needle = service_id_or_alias.strip().casefold()
        for service in self.services:
            if service.service_id.casefold() == needle:
                return service
            if any(alias.casefold() == needle for alias in service.aliases):
                return service
        raise KeyError(service_id_or_alias)


@dataclass(frozen=True)
class ServiceMention:
    """Detected service mention in text."""

    service_id: str
    display_name: str
    matched_alias: str
    confidence: float
    start: int = 0
    end: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "service_id": self.service_id,
            "display_name": self.display_name,
            "matched_alias": self.matched_alias,
            "confidence": self.confidence,
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True)
class ServiceDocsStatus:
    """Registry, indexing, and quality status for one service docs source."""

    service_id: str
    display_name: str
    aliases: tuple[str, ...]
    docs_source: str | None
    configured_status: ServiceConfigStatus
    docs_status: ServiceDocsFinalStatus
    active_docs_count: int = 0
    active_chunks_count: int = 0
    quality_status: str = "none"
    mention_count: int | None = None
    docs_source_configured: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "service_id": self.service_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "docs_source": self.docs_source,
            "configured_status": self.configured_status,
            "docs_status": self.docs_status,
            "active_docs": self.active_docs_count,
            "active_chunks": self.active_chunks_count,
            "quality": self.quality_status,
            "mention_count": self.mention_count,
            "docs_source_configured": self.docs_source_configured,
            "notes": list(self.notes),
        }
