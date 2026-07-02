from pathlib import Path

import pytest

from app.rag.query_enrichment import (
    DEFAULT_QUERY_GLOSSARY_CONFIG,
    QueryEnricher,
    QueryGlossaryConfigError,
    load_query_glossary_config,
)


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


def test_n8n_http_request_query_is_enriched() -> None:
    enrichment = QueryEnricher.default().enrich("как отправить запрос к api в n8n?")

    assert enrichment.service_ids == ("n8n",)
    assert "HTTP Request node" in enrichment.exact_terms
    assert "method" in enrichment.config_terms
    assert "headers" in enrichment.config_terms
    assert "body" in enrichment.config_terms


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
