from pathlib import Path

import pytest

from app.rag.glossary_candidates import discover_glossary_candidates, format_glossary_candidate_report
from app.rag.query_enrichment import QueryGlossaryConfig, QueryGlossaryRule, QueryGlossaryService
from scripts.suggest_query_glossary_candidates import _safe_output_path


def test_candidate_discovery_uses_fake_runtime_rows_without_live_supabase() -> None:
    report = discover_glossary_candidates(
        workspace="fake_workspace",
        existing_glossary=_existing_glossary(),
        evidence_logs=[
            {
                "question": "How do I format a Telegram message?",
                "question_analysis": {},
                "evidence_pack": {
                    "items": [
                        {
                            "evidence_id": "ev-1",
                            "document_title": "Telegram Bot API sendMessage",
                            "heading": "Formatting options",
                            "text": "Use parse_mode or message_entities with sendMessage.",
                            "metadata": {"service_ids": ["telegram_bot_api"], "source_name": "telegram_bot_api_docs"},
                        }
                    ]
                },
                "created_at": "2026-07-03T00:00:00Z",
            }
        ],
        chunks=[
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "heading": "Formatting options",
                "content": "Parameter parse_mode controls MarkdownV2 or HTML rendering for sendMessage.",
                "metadata": {"service_ids": ["telegram_bot_api"], "source_name": "telegram_bot_api_docs"},
            }
        ],
        documents=[_document()],
        limit=10,
    )

    assert report.candidates
    candidate = report.candidates[0]
    assert candidate.status == "suggested"
    assert candidate.service_id == "telegram_bot_api"
    assert candidate.source_id == "telegram_bot_api_docs"
    assert "parse_mode" in candidate.config_terms
    assert any("format" in phrase.lower() for phrase in candidate.user_phrases)


def test_candidate_discovery_skips_existing_query_glossary_duplicates() -> None:
    report = discover_glossary_candidates(
        workspace="fake_workspace",
        existing_glossary=_existing_glossary(),
        term_statistics=[
            {
                "term": "sendMessage",
                "normalized_term": "sendmessage",
                "document_frequency": 1,
                "chunk_frequency": 2,
                "term_type_guess": "function",
                "metadata": {"service_ids": ["telegram_bot_api"], "source_name": "telegram_bot_api_docs"},
            },
            {
                "term": "parse_mode",
                "normalized_term": "parse_mode",
                "document_frequency": 1,
                "chunk_frequency": 2,
                "term_type_guess": "identifier",
                "metadata": {"service_ids": ["telegram_bot_api"], "source_name": "telegram_bot_api_docs"},
            },
        ],
        limit=10,
    )

    all_terms = {term for candidate in report.candidates for term in (*candidate.technical_terms, *candidate.exact_terms, *candidate.config_terms)}
    assert "sendMessage" not in all_terms
    assert "parse_mode" in all_terms
    assert report.skipped_duplicates >= 1


def test_candidate_discovery_groups_by_service_source_and_topic() -> None:
    report = discover_glossary_candidates(
        workspace="fake_workspace",
        existing_glossary=QueryGlossaryConfig(services=()),
        documents=[_document(title="Telegram Messages", metadata={"service_ids": ["telegram_bot_api"], "source_name": "telegram_bot_api_docs"})],
        chunks=[
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "heading": "Message formatting",
                "content": "Parameter parse_mode controls MarkdownV2.",
                "metadata": {},
            },
            {
                "id": "chunk-2",
                "document_id": "doc-1",
                "heading": "Message formatting",
                "content": "Parameter message_entities controls custom text entities.",
                "metadata": {},
            },
        ],
        limit=10,
    )

    matches = [candidate for candidate in report.candidates if candidate.topic == "Message formatting"]
    assert len(matches) == 1
    assert matches[0].service_id == "telegram_bot_api"
    assert matches[0].source_id == "telegram_bot_api_docs"
    assert {"parse_mode", "message_entities"} <= set(matches[0].config_terms)


def test_candidate_discovery_extracts_endpoint_node_parameter_table_and_function_terms() -> None:
    report = discover_glossary_candidates(
        workspace="fake_workspace",
        existing_glossary=QueryGlossaryConfig(services=()),
        documents=[_document(metadata={"service_ids": ["n8n"], "source_name": "n8n_docs"})],
        chunks=[
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "heading": "HTTP Request node",
                "content": "Use HTTP Request node with POST /webhook/run, query_table, and match_documents().",
                "metadata": {},
            }
        ],
        limit=10,
    )

    terms = {term for candidate in report.candidates for term in (*candidate.technical_terms, *candidate.exact_terms, *candidate.config_terms)}
    assert "HTTP Request node" in terms
    assert "/webhook/run" in terms
    assert "query_table" in terms
    assert "match_documents()" in terms


def test_formatted_report_says_suggested_and_not_auto_applied() -> None:
    report = discover_glossary_candidates(
        workspace="fake_workspace",
        existing_glossary=QueryGlossaryConfig(services=()),
        chunks=[
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "heading": "API parameters",
                "content": "Parameter parse_mode controls message formatting.",
                "metadata": {"source_name": "telegram_bot_api_docs"},
            }
        ],
        limit=10,
    )

    output = format_glossary_candidate_report(report)

    assert "status: suggested" in output
    assert "not auto-applied" in output
    assert "review manually; do not auto-apply" in output


def test_script_rejects_output_to_query_glossary_config() -> None:
    with pytest.raises(SystemExit):
        _safe_output_path(Path("config/query_glossary.yaml"))


def _existing_glossary() -> QueryGlossaryConfig:
    return QueryGlossaryConfig(
        services=(
            QueryGlossaryService(
                service_id="telegram_bot_api",
                display_name="Telegram Bot API",
                aliases=("Telegram Bot API",),
                rules=(
                    QueryGlossaryRule(
                        phrases=("send a message",),
                        exact_terms=("sendMessage",),
                        config_terms=("chat_id", "text"),
                    ),
                ),
            ),
        )
    )


def _document(
    *,
    title: str = "Telegram Bot API sendMessage",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": "doc-1",
        "source_type": "external_docs",
        "filename": "telegram.md",
        "document_key": "telegram-doc",
        "title": title,
        "course": None,
        "module": None,
        "lesson": "Messages",
        "metadata": metadata or {"service_ids": ["telegram_bot_api"], "source_name": "telegram_bot_api_docs"},
    }
