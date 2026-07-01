"""Detect service mentions from configured aliases."""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.service_registry.types import ServiceDefinition, ServiceMention


@dataclass(frozen=True)
class _AliasPattern:
    service: ServiceDefinition
    alias: str
    pattern: re.Pattern[str]


class ServiceDetector:
    """Find known service aliases in arbitrary text."""

    def __init__(self, services: tuple[ServiceDefinition, ...] | list[ServiceDefinition]) -> None:
        self._patterns = tuple(
            _AliasPattern(service=service, alias=alias, pattern=_compile_alias(alias))
            for service in services
            for alias in sorted(service.aliases, key=len, reverse=True)
        )

    def detect(self, text: str) -> tuple[ServiceMention, ...]:
        """Return one best mention per service."""
        best_by_service: dict[str, ServiceMention] = {}
        source = str(text or "")
        for item in self._patterns:
            match = item.pattern.search(source)
            if not match:
                continue
            mention = ServiceMention(
                service_id=item.service.service_id,
                display_name=item.service.display_name,
                matched_alias=match.group(0),
                confidence=_confidence(item.alias),
                start=match.start(),
                end=match.end(),
            )
            previous = best_by_service.get(item.service.service_id)
            if previous is None or (mention.confidence, len(mention.matched_alias)) > (
                previous.confidence,
                len(previous.matched_alias),
            ):
                best_by_service[item.service.service_id] = mention
        return tuple(sorted(best_by_service.values(), key=lambda mention: (mention.start, mention.service_id)))


def detect_service_mentions(text: str, services: tuple[ServiceDefinition, ...]) -> tuple[ServiceMention, ...]:
    """Convenience wrapper for one-off detection."""
    return ServiceDetector(services).detect(text)


def _compile_alias(alias: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in alias.strip().split()]
    escaped = r"\s+".join(parts)
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", flags=re.IGNORECASE | re.UNICODE)


def _confidence(alias: str) -> float:
    clean = alias.strip()
    if len(clean) >= 8:
        return 0.98
    if len(clean) >= 4:
        return 0.95
    return 0.9
