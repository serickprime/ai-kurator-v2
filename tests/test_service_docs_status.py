import json

from app.service_registry.status import build_service_docs_statuses, count_service_mentions, status_payload
from app.service_registry.types import ServiceDefinition


def test_service_docs_status_marks_missing_docs_source_not_configured() -> None:
    services = (
        ServiceDefinition(
            service_id="flutterflow",
            display_name="FlutterFlow",
            aliases=("flutterflow",),
            docs_source=None,
            status="not_configured",
        ),
    )

    statuses = build_service_docs_statuses(
        services=services,
        configured_docs_sources=("n8n_docs",),
        documents=[],
        chunks=[],
    )

    assert statuses[0].docs_status == "not_configured"
    assert statuses[0].quality_status == "none"


def test_service_docs_status_marks_active_docs_indexed() -> None:
    services = (
        ServiceDefinition(
            service_id="n8n",
            display_name="n8n",
            aliases=("n8n",),
            docs_source="n8n_docs",
            status="enabled",
        ),
    )
    documents = [_doc("doc-1", "n8n_docs")]
    chunks = [_chunk("doc-1", "Nodes are the building blocks of workflows in n8n.")]

    statuses = build_service_docs_statuses(
        services=services,
        configured_docs_sources=("n8n_docs",),
        documents=documents,
        chunks=chunks,
    )

    assert statuses[0].docs_status == "indexed"
    assert statuses[0].active_docs_count == 1
    assert statuses[0].active_chunks_count == 1
    assert statuses[0].quality_status == "PASS"


def test_service_docs_status_marks_configured_source_without_docs_not_indexed() -> None:
    services = (
        ServiceDefinition(
            service_id="docs",
            display_name="Docs",
            aliases=("docs",),
            docs_source="docs_source",
            status="enabled",
        ),
    )

    statuses = build_service_docs_statuses(
        services=services,
        configured_docs_sources=("docs_source",),
        documents=[],
        chunks=[],
    )

    assert statuses[0].docs_status == "configured_not_indexed"


def test_service_docs_status_does_not_use_disabled_source() -> None:
    services = (
        ServiceDefinition(
            service_id="n8n",
            display_name="n8n",
            aliases=("n8n",),
            docs_source="n8n_docs",
            status="disabled",
        ),
    )

    statuses = build_service_docs_statuses(
        services=services,
        configured_docs_sources=("n8n_docs",),
        documents=[_doc("doc-1", "n8n_docs")],
        chunks=[_chunk("doc-1", "Useful n8n documentation text with enough words.")],
    )

    assert statuses[0].docs_status == "disabled"
    assert statuses[0].quality_status == "none"


def test_service_docs_status_json_output_is_valid() -> None:
    statuses = build_service_docs_statuses(
        services=(
            ServiceDefinition(
                service_id="supabase",
                display_name="Supabase",
                aliases=("supabase", "супабейс"),
                docs_source="supabase_docs",
                status="enabled",
            ),
        ),
        configured_docs_sources=("supabase_docs",),
        documents=[_doc("doc-1", "supabase_docs")],
        chunks=[
            _chunk(
                "doc-1",
                "Supabase projects group database, API, authentication, and storage settings for one application.",
            )
        ],
        mention_counts={"supabase": 3},
    )

    payload = status_payload(statuses)
    decoded = json.loads(json.dumps(payload, ensure_ascii=False))

    assert decoded["services"][0]["docs_status"] == "indexed"
    assert decoded["services"][0]["mention_count"] == 3


def test_service_docs_status_counts_mentions() -> None:
    services = (
        ServiceDefinition(
            service_id="supabase",
            display_name="Supabase",
            aliases=("supabase", "супабейс"),
            docs_source="supabase_docs",
            status="enabled",
        ),
    )

    counts = count_service_mentions(
        services=services,
        corpus_rows=[
            {"title": "Supabase intro"},
            {"content": "Как настроить супабейс?"},
            {"content": "No known service here."},
        ],
    )

    assert counts == {"supabase": 2}


def _doc(document_id: str, source_name: str) -> dict[str, object]:
    return {
        "id": document_id,
        "filename": f"{document_id}.html",
        "document_key": f"https://docs.example.com/{document_id}",
        "title": f"Page {document_id}",
        "status": "active",
        "metadata": {
            "source_name": source_name,
            "source_url": f"https://docs.example.com/{document_id}",
            "canonical_url": f"https://docs.example.com/{document_id}",
        },
    }


def _chunk(document_id: str, content: str) -> dict[str, object]:
    return {
        "id": f"chunk-{document_id}",
        "document_id": document_id,
        "chunk_index": 0,
        "heading": "Overview",
        "content": content,
        "metadata": {},
    }
