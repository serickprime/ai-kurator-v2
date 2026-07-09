"""Read-only service-aware docs suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from app.docs_registry.candidates import DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG, load_docs_source_candidates_config
from app.docs_registry.models import DocsSourceCandidate
from app.rag.query_enrichment import DEFAULT_QUERY_GLOSSARY_CONFIG, QueryGlossaryConfigError, load_query_glossary_config
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG, load_service_registry_config
from app.service_registry.detector import ServiceDetector
from app.service_registry.types import ServiceDefinition, ServiceDocsStatus, ServiceMention

DEFAULT_SERVICE_SUGGESTION_ALIASES_CONFIG = Path("config/service_suggestion_aliases.yaml")

ServiceSuggestionStatus = Literal[
    "supported-active",
    "known-docs-inactive",
    "known-docs-missing",
    "unknown-service",
    "ambiguous",
    "no-suggestion",
]


@dataclass(frozen=True)
class ServiceSuggestion:
    """Owner/admin preview for a service docs suggestion."""

    canonical_service_id: str
    display_name: str
    matched_aliases: tuple[str, ...]
    question_excerpt: str
    reason: str
    confidence: float
    service_known: bool
    docs_registered: bool
    docs_active: bool
    current_status: ServiceSuggestionStatus
    suggested_action: str
    owner_review_required: bool
    active_context_services: tuple[str, ...] = ()
    auto_activation_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "canonical_service_id": self.canonical_service_id,
            "display_name": self.display_name,
            "matched_aliases": list(self.matched_aliases),
            "question_excerpt": self.question_excerpt,
            "reason": self.reason,
            "confidence": self.confidence,
            "service_known": self.service_known,
            "docs_registered": self.docs_registered,
            "docs_active": self.docs_active,
            "current_status": self.current_status,
            "suggested_action": self.suggested_action,
            "owner_review_required": self.owner_review_required,
            "active_context_services": list(self.active_context_services),
            "auto_activation_allowed": self.auto_activation_allowed,
        }


@dataclass(frozen=True)
class ServiceSuggestionCatalog:
    """Merged read-only service detection catalog."""

    services: tuple[ServiceDefinition, ...]
    docs_candidate_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _DetectedState:
    mention: ServiceMention
    service: ServiceDefinition
    status: ServiceDocsStatus | None
    current_status: ServiceSuggestionStatus
    docs_registered: bool
    docs_active: bool


class ServiceSuggestionEngine:
    """Detect service mentions and produce safe owner/admin suggestions."""

    def __init__(
        self,
        catalog: ServiceSuggestionCatalog,
        *,
        statuses: Iterable[ServiceDocsStatus] = (),
    ) -> None:
        self._catalog = catalog
        self._services_by_id = {service.service_id: service for service in catalog.services}
        self._status_by_id = {status.service_id: status for status in statuses}
        self._detector = ServiceDetector(catalog.services)

    def suggest(self, question: str) -> ServiceSuggestion:
        """Return one read-only suggestion, or a no-suggestion status."""
        excerpt = _excerpt(question)
        mentions = self._detector.detect(question)
        if not mentions:
            return _unknown_suggestion(excerpt)

        states = tuple(self._state(mention) for mention in mentions if mention.service_id in self._services_by_id)
        if not states:
            return _unknown_suggestion(excerpt)

        non_active = tuple(state for state in states if state.current_status != "supported-active")
        if len(non_active) == 1:
            active_context = _active_context_services(states, non_active[0])
            return self._suggestion_for_state(
                non_active[0],
                excerpt=excerpt,
                reason_suffix=_active_context_reason(active_context),
                active_context_services=active_context,
            )
        if len(non_active) > 1:
            return _ambiguous_suggestion(states, excerpt)

        if len(states) == 1:
            return self._suggestion_for_state(states[0], excerpt=excerpt)
        return _ambiguous_suggestion(states, excerpt)

    def _state(self, mention: ServiceMention) -> _DetectedState:
        service = self._services_by_id[mention.service_id]
        status = self._status_by_id.get(service.service_id)
        docs_registered = _docs_registered(service, status, self._catalog.docs_candidate_ids)
        docs_active = bool(status and status.docs_status == "indexed")
        if docs_active:
            current_status: ServiceSuggestionStatus = "supported-active"
        elif docs_registered:
            current_status = "known-docs-inactive"
        else:
            current_status = "known-docs-missing"
        return _DetectedState(
            mention=mention,
            service=service,
            status=status,
            current_status=current_status,
            docs_registered=docs_registered,
            docs_active=docs_active,
        )

    def _suggestion_for_state(
        self,
        state: _DetectedState,
        *,
        excerpt: str,
        reason_suffix: str = "",
        active_context_services: tuple[str, ...] = (),
    ) -> ServiceSuggestion:
        reason = _reason_for_state(state)
        if reason_suffix:
            reason = f"{reason} {reason_suffix}"
        return ServiceSuggestion(
            canonical_service_id=state.service.service_id,
            display_name=state.service.display_name,
            matched_aliases=(state.mention.matched_alias,),
            question_excerpt=excerpt,
            reason=reason,
            confidence=round(state.mention.confidence, 3),
            service_known=True,
            docs_registered=state.docs_registered,
            docs_active=state.docs_active,
            current_status=state.current_status,
            suggested_action=_suggested_action(state, self._catalog.docs_candidate_ids),
            owner_review_required=state.current_status in {"known-docs-inactive", "known-docs-missing"},
            active_context_services=active_context_services,
            auto_activation_allowed=False,
        )


def load_service_suggestion_catalog(
    *,
    registry_config_path: Path | str | None = DEFAULT_SERVICE_REGISTRY_CONFIG,
    docs_candidates_config_path: Path | str | None = DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG,
    suggestion_aliases_config_path: Path | str | None = DEFAULT_SERVICE_SUGGESTION_ALIASES_CONFIG,
    query_glossary_config_path: Path | str | None = DEFAULT_QUERY_GLOSSARY_CONFIG,
) -> ServiceSuggestionCatalog:
    """Load service aliases from registry, candidates, glossary, and suggestion config."""
    services_by_id: dict[str, ServiceDefinition] = {}
    candidate_ids: set[str] = set()

    if registry_config_path is not None:
        for service in load_service_registry_config(registry_config_path).services:
            _merge_service(services_by_id, service)

    if docs_candidates_config_path is not None:
        for candidate in load_docs_source_candidates_config(docs_candidates_config_path).candidates:
            candidate_ids.add(candidate.service_id)
            _merge_service(services_by_id, _candidate_definition(candidate))

    if query_glossary_config_path is not None:
        try:
            glossary = load_query_glossary_config(query_glossary_config_path)
        except QueryGlossaryConfigError:
            glossary = None
        if glossary is not None:
            for service in glossary.services:
                _merge_service(
                    services_by_id,
                    ServiceDefinition(
                        service_id=service.service_id,
                        display_name=service.display_name,
                        aliases=service.aliases,
                        docs_source=None,
                        status="not_configured",
                    ),
                )

    if suggestion_aliases_config_path is not None:
        path = Path(suggestion_aliases_config_path)
        if path.exists():
            for service in load_service_registry_config(path).services:
                _merge_service(services_by_id, service)

    return ServiceSuggestionCatalog(
        services=tuple(sorted(services_by_id.values(), key=lambda service: service.service_id)),
        docs_candidate_ids=frozenset(candidate_ids),
    )


def format_service_suggestion_report(suggestion: ServiceSuggestion, *, runtime_status: str = "not_checked") -> str:
    """Return an owner-friendly text report."""
    lines = [
        "Service-aware suggestion",
        "",
        "- mode: read-only",
        f"- runtime status: {runtime_status}",
        f"- detected service: {_display_service(suggestion)}",
        f"- confidence: {suggestion.confidence:.2f}",
        f"- matched aliases: {_join_or_none(suggestion.matched_aliases)}",
        f"- active context: {_join_or_none(suggestion.active_context_services)}",
        f"- current status: {suggestion.current_status}",
        f"- service known: {_yes_no(suggestion.service_known)}",
        f"- docs registered: {_yes_no(suggestion.docs_registered)}",
        f"- docs active: {_yes_no(suggestion.docs_active)}",
        f"- owner review required: {_yes_no(suggestion.owner_review_required)}",
        "- auto activation: disabled",
        f"- suggested next action: {suggestion.suggested_action}",
        f"- reason: {suggestion.reason}",
    ]
    return "\n".join(lines)


def _merge_service(target: dict[str, ServiceDefinition], incoming: ServiceDefinition) -> None:
    current = target.get(incoming.service_id)
    if current is None:
        target[incoming.service_id] = incoming
        return

    aliases = tuple(dict.fromkeys((*current.aliases, *incoming.aliases)))
    docs_source = current.docs_source or incoming.docs_source
    status = current.status
    if status == "not_configured" and incoming.status != "not_configured":
        status = incoming.status
    display_name = current.display_name if current.display_name != current.service_id else incoming.display_name
    target[incoming.service_id] = ServiceDefinition(
        service_id=current.service_id,
        display_name=display_name,
        aliases=aliases,
        docs_source=docs_source,
        status=status,
    )


def _candidate_definition(candidate: DocsSourceCandidate) -> ServiceDefinition:
    return ServiceDefinition(
        service_id=candidate.service_id,
        display_name=candidate.display_name,
        aliases=candidate.aliases,
        docs_source=candidate.docs_source,
        status="needs_review",
    )


def _docs_registered(
    service: ServiceDefinition,
    status: ServiceDocsStatus | None,
    candidate_ids: frozenset[str],
) -> bool:
    return bool(
        service.docs_source
        or service.service_id in candidate_ids
        or (status and (status.docs_source or status.docs_source_configured))
    )


def _reason_for_state(state: _DetectedState) -> str:
    if state.current_status == "supported-active":
        return "The detected service already has active docs; continue the regular RAG flow."
    if state.current_status == "known-docs-inactive":
        return "The service is known, but docs are not active for normal answers yet."
    if state.current_status == "known-docs-missing":
        return "The service alias is known, but no curated docs source is registered."
    return "No owner action is available for this status."


def _suggested_action(state: _DetectedState, candidate_ids: frozenset[str]) -> str:
    if state.current_status == "supported-active":
        return "continue_regular_rag"
    if state.current_status == "known-docs-inactive" and state.service.service_id in candidate_ids:
        return f"owner/admin may run read-only preview later: /docs_preview {state.service.service_id}"
    if state.current_status == "known-docs-inactive":
        return "owner/admin should review docs registry status before any activation"
    if state.current_status == "known-docs-missing":
        return "owner/admin should add a curated docs candidate before any preview or activation"
    return "no_action"


def _active_context_services(states: tuple[_DetectedState, ...], target: _DetectedState) -> tuple[str, ...]:
    return tuple(
        state.service.display_name
        for state in states
        if state.service.service_id != target.service.service_id and state.current_status == "supported-active"
    )


def _active_context_reason(active_context: tuple[str, ...]) -> str:
    if not active_context:
        return ""
    return f"Active service mention kept as context: {', '.join(active_context)}."


def _unknown_suggestion(excerpt: str) -> ServiceSuggestion:
    return ServiceSuggestion(
        canonical_service_id="",
        display_name="",
        matched_aliases=(),
        question_excerpt=excerpt,
        reason="No configured service alias matched the question with enough confidence.",
        confidence=0.0,
        service_known=False,
        docs_registered=False,
        docs_active=False,
        current_status="unknown-service",
        suggested_action="no_action",
        owner_review_required=False,
        auto_activation_allowed=False,
    )


def _ambiguous_suggestion(states: tuple[_DetectedState, ...], excerpt: str) -> ServiceSuggestion:
    aliases = tuple(state.mention.matched_alias for state in states)
    services = ", ".join(f"{state.service.display_name} ({state.current_status})" for state in states)
    return ServiceSuggestion(
        canonical_service_id="",
        display_name="",
        matched_aliases=aliases,
        question_excerpt=excerpt,
        reason=f"Multiple service aliases matched; clarify the intended docs target first: {services}.",
        confidence=0.5,
        service_known=True,
        docs_registered=any(state.docs_registered for state in states),
        docs_active=any(state.docs_active for state in states),
        current_status="ambiguous",
        suggested_action="clarify_target_service",
        owner_review_required=False,
        auto_activation_allowed=False,
    )


def _excerpt(text: str, *, limit: int = 180) -> str:
    return " ".join(str(text or "").split())[:limit]


def _display_service(suggestion: ServiceSuggestion) -> str:
    if not suggestion.canonical_service_id:
        return "none"
    return f"{suggestion.display_name} ({suggestion.canonical_service_id})"


def _join_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
