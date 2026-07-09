from pathlib import Path

from app.service_registry.suggestions import (
    ServiceSuggestionCatalog,
    ServiceSuggestionEngine,
    format_service_suggestion_report,
    load_service_suggestion_catalog,
)
from app.service_registry.types import ServiceDefinition, ServiceDocsStatus


def test_active_supported_service_does_not_create_owner_suggestion() -> None:
    engine = ServiceSuggestionEngine(
        ServiceSuggestionCatalog(services=(_service("telegram_bot_api", "Telegram Bot API", ("telegram bot api",)),)),
        statuses=(_status("telegram_bot_api", docs_status="indexed", docs_source="telegram_bot_api_docs"),),
    )

    suggestion = engine.suggest("как отправить сообщение через Telegram Bot API")

    assert suggestion.canonical_service_id == "telegram_bot_api"
    assert suggestion.current_status == "supported-active"
    assert suggestion.docs_active is True
    assert suggestion.owner_review_required is False
    assert suggestion.auto_activation_allowed is False
    assert suggestion.suggested_action == "continue_regular_rag"


def test_known_service_without_active_docs_creates_review_preview() -> None:
    engine = ServiceSuggestionEngine(
        ServiceSuggestionCatalog(services=(_service("fake_service", "Fake Service", ("fake service",)),)),
        statuses=(_status("fake_service", docs_status="configured_not_indexed", docs_source="fake_docs"),),
    )

    suggestion = engine.suggest("как подключить Fake Service")

    assert suggestion.canonical_service_id == "fake_service"
    assert suggestion.current_status == "known-docs-inactive"
    assert suggestion.docs_registered is True
    assert suggestion.docs_active is False
    assert suggestion.owner_review_required is True
    assert suggestion.auto_activation_allowed is False


def test_docs_candidate_without_active_docs_points_to_read_only_preview() -> None:
    engine = ServiceSuggestionEngine(
        ServiceSuggestionCatalog(
            services=(_service("candidate_service", "Candidate Service", ("candidate service",), docs_source="candidate_docs"),),
            docs_candidate_ids=frozenset({"candidate_service"}),
        ),
        statuses=(),
    )

    suggestion = engine.suggest("как подключить Candidate Service")

    assert suggestion.current_status == "known-docs-inactive"
    assert suggestion.suggested_action == "owner/admin may run read-only preview later: /docs_preview candidate_service"
    assert suggestion.auto_activation_allowed is False


def test_unknown_service_does_not_create_false_high_confidence_detection() -> None:
    engine = ServiceSuggestionEngine(
        ServiceSuggestionCatalog(services=(_service("known", "Known", ("known",)),)),
        statuses=(),
    )

    suggestion = engine.suggest("как работать с каким-то новым сервисом")

    assert suggestion.current_status == "unknown-service"
    assert suggestion.service_known is False
    assert suggestion.confidence == 0.0
    assert suggestion.owner_review_required is False
    assert suggestion.auto_activation_allowed is False


def test_ambiguous_missing_services_are_not_actionable() -> None:
    engine = ServiceSuggestionEngine(
        ServiceSuggestionCatalog(
            services=(
                _service("stripe", "Stripe", ("stripe",)),
                _service("notion", "Notion", ("notion",)),
            )
        ),
        statuses=(),
    )

    suggestion = engine.suggest("как связать Stripe и Notion")

    assert suggestion.current_status == "ambiguous"
    assert suggestion.suggested_action == "clarify_target_service"
    assert suggestion.owner_review_required is False
    assert suggestion.auto_activation_allowed is False
    assert suggestion.confidence < 0.9


def test_missing_service_wins_over_active_context_service() -> None:
    engine = ServiceSuggestionEngine(
        ServiceSuggestionCatalog(
            services=(
                _service("n8n", "n8n", ("n8n",), docs_source="n8n_docs"),
                _service("stripe", "Stripe", ("stripe",)),
            )
        ),
        statuses=(_status("n8n", docs_status="indexed", docs_source="n8n_docs"),),
    )

    suggestion = engine.suggest("как подключить Stripe в n8n")

    assert suggestion.canonical_service_id == "stripe"
    assert suggestion.current_status == "known-docs-missing"
    assert suggestion.owner_review_required is True
    assert suggestion.active_context_services == ("n8n",)
    assert "n8n" in suggestion.reason


def test_alias_detection_maps_user_name_to_canonical_service() -> None:
    engine = ServiceSuggestionEngine(
        ServiceSuggestionCatalog(services=(_service("airtable", "Airtable", ("airtable", "airtable webhook")),)),
        statuses=(),
    )

    suggestion = engine.suggest("как настроить Airtable webhook")

    assert suggestion.canonical_service_id == "airtable"
    assert suggestion.display_name == "Airtable"
    assert suggestion.current_status == "known-docs-missing"
    assert suggestion.matched_aliases == ("Airtable webhook",)


def test_new_service_alias_can_be_added_through_config_without_python_changes(tmp_path: Path) -> None:
    path = tmp_path / "service_suggestion_aliases.yaml"
    path.write_text(
        """services:
  - service_id: future_tool
    display_name: Future Tool
    aliases:
      - future tool
      - ft api
    docs_source: null
    status: not_configured
""",
        encoding="utf-8",
    )
    catalog = load_service_suggestion_catalog(
        registry_config_path=None,
        docs_candidates_config_path=None,
        suggestion_aliases_config_path=path,
        query_glossary_config_path=None,
    )

    suggestion = ServiceSuggestionEngine(catalog).suggest("как подключить Future Tool")

    assert suggestion.canonical_service_id == "future_tool"
    assert suggestion.current_status == "known-docs-missing"
    assert suggestion.auto_activation_allowed is False


def test_report_clearly_says_read_only_and_auto_activation_disabled() -> None:
    engine = ServiceSuggestionEngine(
        ServiceSuggestionCatalog(services=(_service("stripe", "Stripe", ("stripe",)),)),
        statuses=(),
    )

    report = format_service_suggestion_report(engine.suggest("как подключить Stripe"), runtime_status="unavailable")

    assert "- mode: read-only" in report
    assert "- auto activation: disabled" in report
    assert "owner review required: yes" in report


def _service(
    service_id: str,
    display_name: str,
    aliases: tuple[str, ...],
    *,
    docs_source: str | None = None,
) -> ServiceDefinition:
    return ServiceDefinition(
        service_id=service_id,
        display_name=display_name,
        aliases=aliases,
        docs_source=docs_source,
        status="enabled" if docs_source else "not_configured",
    )


def _status(service_id: str, *, docs_status: str, docs_source: str | None) -> ServiceDocsStatus:
    return ServiceDocsStatus(
        service_id=service_id,
        display_name=service_id,
        aliases=(service_id,),
        docs_source=docs_source,
        configured_status="enabled" if docs_source else "not_configured",
        docs_status=docs_status,  # type: ignore[arg-type]
        active_docs_count=1 if docs_status == "indexed" else 0,
        active_chunks_count=3 if docs_status == "indexed" else 0,
        docs_source_configured=bool(docs_source),
    )
