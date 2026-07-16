from pathlib import Path
import sys
import unicodedata

import pytest

from app.rag.query_enrichment import (
    DEFAULT_QUERY_GLOSSARY_CONFIG,
    QueryEnricher,
    QueryGlossaryConfigError,
    load_query_glossary_config,
)


UNICODE_PD_CHARACTERS = tuple(
    chr(codepoint)
    for codepoint in range(sys.maxunicode + 1)
    if unicodedata.category(chr(codepoint)) == "Pd"
)
DASH_RUN_SAMPLES = ("--", "\ufe63\uff0d", "-\u2014\u2212")


def test_query_glossary_loads_default_config() -> None:
    config = load_query_glossary_config(DEFAULT_QUERY_GLOSSARY_CONFIG)

    assert {service.service_id for service in config.services} >= {
        "telegram_bot_api",
        "openrouter",
        "n8n",
        "supabase",
    }


def test_telegram_bot_api_send_message_query_is_enriched() -> None:
    enrichment = QueryEnricher.default().enrich("как отправить сообщение через Telegram Bot API?")

    assert enrichment.service_ids == ("telegram_bot_api",)
    assert "sendMessage" in enrichment.exact_terms
    assert "chat_id" in enrichment.config_terms
    assert "text" in enrichment.config_terms
    assert any(facet.role == "platform" and facet.text == "Telegram Bot API" for facet in enrichment.facets)


def test_telegram_bot_api_existing_sendmessage_query_does_not_duplicate_terms() -> None:
    enrichment = QueryEnricher.default().enrich("как использовать sendMessage в Telegram Bot API? chat_id text")

    assert enrichment.exact_terms.count("sendMessage") == 1
    assert enrichment.config_terms.count("chat_id") == 1
    assert enrichment.config_terms.count("text") == 1


def test_telegram_bot_api_webhook_query_is_enriched() -> None:
    enrichment = QueryEnricher.default().enrich("как настроить вебхук в Telegram Bot API?")

    assert enrichment.service_ids == ("telegram_bot_api",)
    assert "setWebhook" in enrichment.exact_terms
    assert "webhook" in enrichment.config_terms
    assert "url" in enrichment.config_terms


def test_openrouter_api_key_query_is_enriched() -> None:
    enrichment = QueryEnricher.default().enrich("как подключить openrouter api ключ?")

    assert enrichment.service_ids == ("openrouter",)
    assert "API key" in enrichment.exact_terms
    assert "base_url" in enrichment.config_terms
    assert "Authorization" in enrichment.config_terms
    assert "Bearer" in enrichment.config_terms


@pytest.mark.parametrize(
    "object_phrase",
    (
        "HTTP-запрос",
        "HTTP‑запрос",
        "HTTP–запрос",
        "HTTP—запрос",
        "HTTP запрос",
    ),
)
def test_n8n_http_request_query_normalizes_dash_variants(object_phrase: str) -> None:
    enrichment = QueryEnricher.default().enrich(f"как отправить {object_phrase} из n8n?")

    assert enrichment.service_ids == ("n8n",)
    assert "HTTP Request node" in enrichment.exact_terms
    assert "method" in enrichment.config_terms
    assert "headers" in enrichment.config_terms
    assert "body" in enrichment.config_terms
    assert [(anchor.service_id, anchor.term) for anchor in enrichment.object_anchors] == [
        ("n8n", "HTTP Request node")
    ]


def test_n8n_post_request_query_uses_documentation_object_anchor() -> None:
    enrichment = QueryEnricher.default().enrich("Как в n8n отправить POST-запрос?")

    assert enrichment.service_ids == ("n8n",)
    assert enrichment.exact_terms == ("HTTP Request node",)
    assert [(anchor.service_id, anchor.term, anchor.matched_variant) for anchor in enrichment.object_anchors] == [
        ("n8n", "HTTP Request node", "post запрос")
    ]


@pytest.mark.parametrize("dash", UNICODE_PD_CHARACTERS)
def test_n8n_http_request_query_normalizes_all_unicode_dash_punctuation(dash: str) -> None:
    context = QueryEnricher.default().build_context(f"Как отправить HTTP{dash}запрос из n8n?")

    assert context.confirmed_service_ids == ("n8n",)
    assert "HTTP Request node" in context.exact_terms
    assert [(anchor.service_id, anchor.term, anchor.matched_variant) for anchor in context.glossary_object_anchors] == [
        ("n8n", "HTTP Request node", "http запрос")
    ]


def test_n8n_http_request_query_normalizes_minus_sign() -> None:
    enrichment = QueryEnricher.default().enrich("Как отправить HTTP\u2212запрос из n8n?")

    assert enrichment.service_ids == ("n8n",)
    assert "HTTP Request node" in enrichment.exact_terms
    assert enrichment.object_anchors[0].matched_variant == "http запрос"


@pytest.mark.parametrize("dash", ("\ufe58", "\ufe63", "\uff0d"))
def test_n8n_http_request_query_normalizes_confirmed_problematic_unicode_dashes(dash: str) -> None:
    enrichment = QueryEnricher.default().enrich(f"Как отправить HTTP{dash}запрос из n8n?")

    assert enrichment.service_ids == ("n8n",)
    assert "HTTP Request node" in enrichment.exact_terms
    assert [(anchor.service_id, anchor.term) for anchor in enrichment.object_anchors] == [
        ("n8n", "HTTP Request node")
    ]


@pytest.mark.parametrize("dash_run", DASH_RUN_SAMPLES)
def test_n8n_http_request_query_normalizes_dash_runs(dash_run: str) -> None:
    enrichment = QueryEnricher.default().enrich(f"Как отправить HTTP{dash_run}запрос из n8n?")

    assert enrichment.service_ids == ("n8n",)
    assert "HTTP Request node" in enrichment.exact_terms
    assert enrichment.object_anchors[0].matched_variant == "http запрос"


def test_n8n_unicode_dash_anchor_keeps_provenance() -> None:
    context = QueryEnricher.default().build_context("Как отправить HTTP\ufe63запрос из n8n?")

    assert context.confirmed_service_ids == ("n8n",)
    anchor = context.glossary_object_anchors[0]
    assert anchor.service_id == "n8n"
    assert anchor.term == "HTTP Request node"
    assert anchor.canonical_term == "HTTP Request node"
    assert anchor.matched_variant == "http запрос"
    assert anchor.rule_id == "n8n:rule:1"
    assert anchor.provenance == "query_glossary"


@pytest.mark.parametrize(
    "question",
    (
        "Как отправить HTTP-запрос из n8n?",
        "Как отправить HTTP‑запрос из n8n?",
        "Как отправить HTTP–запрос из n8n?",
        "Как отправить HTTP—запрос из n8n?",
        "Как отправить HTTP запрос из n8n?",
        "Как в n8n отправить POST-запрос?",
        "Как отправить запрос к API из n8n?",
    ),
)
def test_n8n_existing_http_request_variants_still_match(question: str) -> None:
    enrichment = QueryEnricher.default().enrich(question)

    assert enrichment.service_ids == ("n8n",)
    assert "HTTP Request node" in enrichment.exact_terms
    assert [(anchor.service_id, anchor.term) for anchor in enrichment.object_anchors] == [
        ("n8n", "HTTP Request node")
    ]


@pytest.mark.parametrize(
    "question",
    (
        "Как отправить POST-запрос?",
        "Как отправить POST-запрос через OpenRouter?",
        "Как отправить HTTP﹣запрос?",
        "Как отправить HTTP﹣запрос через OpenRouter?",
    ),
)
def test_n8n_object_anchor_requires_matching_service_context(question: str) -> None:
    enrichment = QueryEnricher.default().enrich(question)

    assert all(anchor.service_id != "n8n" for anchor in enrichment.object_anchors)
    assert "HTTP Request node" not in enrichment.exact_terms


def test_supabase_vector_search_query_is_enriched() -> None:
    enrichment = QueryEnricher.default().enrich("как сделать векторный поиск по документам в Supabase?")

    assert enrichment.service_ids == ("supabase",)
    assert "pgvector" in enrichment.exact_terms
    assert "match_documents" in enrichment.exact_terms
    assert "embeddings" in enrichment.config_terms
    assert "similarity search" in enrichment.config_terms


def test_unrelated_query_is_not_enriched() -> None:
    enrichment = QueryEnricher.default().enrich("как ухаживать за растением зимой?")

    assert enrichment.is_empty


def test_query_enricher_supports_future_services_from_yaml_without_python_changes(tmp_path: Path) -> None:
    path = tmp_path / "query_glossary.yaml"
    path.write_text(
        """fake_service:
  aliases:
    - Fake Service
  rules:
    - phrases:
        - создать объект
      exact_terms:
        - createObject
      config_terms:
        - object_id
""",
        encoding="utf-8",
    )

    enrichment = QueryEnricher.from_config(path, strict=True).enrich("как создать объект в Fake Service?")

    assert enrichment.service_ids == ("fake_service",)
    assert "createObject" in enrichment.exact_terms
    assert "object_id" in enrichment.config_terms
    assert any(facet.role == "platform" and facet.text == "Fake Service" for facet in enrichment.facets)


def test_query_enricher_supports_generic_object_anchors_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "query_glossary.yaml"
    path.write_text(
        """example_service:
  display_name: ExampleService
  aliases:
    - ExampleService
  rules:
    - phrases:
        - ключ доступа
      object_anchors:
        - Access Key
""",
        encoding="utf-8",
    )

    enrichment = QueryEnricher.from_config(path, strict=True).enrich(
        "Как настроить ключ-доступа в ExampleService?"
    )

    assert enrichment.service_ids == ("example_service",)
    assert enrichment.exact_terms == ("Access Key",)
    assert [(anchor.service_id, anchor.term, anchor.matched_variant) for anchor in enrichment.object_anchors] == [
        ("example_service", "Access Key", "ключ доступа")
    ]


def test_query_enricher_supports_generic_unicode_dash_object_anchor_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "query_glossary.yaml"
    path.write_text(
        """example_service:
  display_name: ExampleService
  aliases:
    - ExampleService
  rules:
    - phrases:
        - ключ доступа
      object_anchors:
        - Access Key
""",
        encoding="utf-8",
    )

    enrichment = QueryEnricher.from_config(path, strict=True).enrich(
        "Как настроить ключ\ufe63доступа в ExampleService?"
    )

    assert enrichment.service_ids == ("example_service",)
    assert enrichment.exact_terms == ("Access Key",)
    assert [(anchor.service_id, anchor.term, anchor.matched_variant) for anchor in enrichment.object_anchors] == [
        ("example_service", "Access Key", "ключ доступа")
    ]


def test_query_enricher_missing_config_is_safe(tmp_path: Path) -> None:
    enricher = QueryEnricher.from_config(tmp_path / "missing.yaml")

    assert enricher.enrich("как отправить сообщение через Telegram Bot API?").is_empty


def test_query_enricher_invalid_config_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "query_glossary.yaml"
    path.write_text("not-a-valid-list-item\n", encoding="utf-8")

    enricher = QueryEnricher.from_config(path)

    assert enricher.enrich("как отправить сообщение через Telegram Bot API?").is_empty


def test_query_glossary_loader_can_raise_in_strict_context(tmp_path: Path) -> None:
    path = tmp_path / "query_glossary.yaml"
    path.write_text("telegram_bot_api:\n  aliases:\n    - Telegram Bot API\n", encoding="utf-8")

    with pytest.raises(QueryGlossaryConfigError):
        QueryEnricher.from_config(path, strict=True)
