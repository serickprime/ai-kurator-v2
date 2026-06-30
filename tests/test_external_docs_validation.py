import json

from app.external_docs.chunk_quality import is_low_value_external_chunk
from app.external_docs.validation import validate_external_docs


def test_external_docs_validation_raw_html_fails() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "<div class='content'>Raw HTML should not be indexed.</div>")],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1
    assert any("raw HTML" in failure for failure in result.failures)


def test_external_docs_validation_missing_url_fails() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1", metadata={"source_name": "future_docs"})],
        chunks=[_chunk("doc-1", "Clean evidence text with enough useful words for validation.")],
    )

    assert result.quality == "FAIL"
    assert result.metrics["missing_url_docs"] == 1
    assert result.metrics["source_labels_without_url"] == 1


def test_external_docs_validation_allows_html_inside_fenced_code() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "Render the component with this example.\n\n```\n<div>Loading...</div>\n```",
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0
    assert result.metrics["code_blocks_count"] == 1


def test_external_docs_validation_does_not_warn_for_short_technical_chunks() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "###### Terminal\n\n```\nnpm run dev\n```", heading="Terminal")],
    )

    assert result.quality == "PASS"
    assert result.metrics["very_short_chunks"] == 0
    assert result.metrics["chunks_without_useful_text"] == 0


def test_external_docs_chunk_quality_is_source_agnostic() -> None:
    assert is_low_value_external_chunk("### AI Tools", heading="AI Tools")
    assert is_low_value_external_chunk("###### Project URL\n\nNo project found", heading="Project URL")
    assert not is_low_value_external_chunk("```\nnpm run dev\n```", heading="Terminal")
    assert not is_low_value_external_chunk("GET /rest/v1/items", heading="API")
    assert not is_low_value_external_chunk("PUBLIC_API_URL=https://docs.example.com", heading="Config")


def test_external_docs_validation_duplicate_active_versions_fail() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[
            _doc("doc-1", canonical_url="https://docs.example.com/same"),
            _doc("doc-2", canonical_url="https://docs.example.com/same"),
        ],
        chunks=[
            _chunk("doc-1", "Clean evidence text with enough useful words for validation."),
            _chunk("doc-2", "Another clean evidence paragraph with enough useful words."),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["duplicate_active_versions"] == 1


def test_external_docs_validation_title_only_chunks_warn() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "# Overview", heading="Overview"),
            _chunk("doc-1", "Useful reference text with enough words to avoid being empty."),
        ],
    )

    assert result.quality == "WARN"
    assert not result.failures
    assert result.metrics["title_only_chunks"] == 1
    assert any("title-only" in warning for warning in result.warnings)


def test_external_docs_validation_clean_source_passes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "This page explains a product concept with enough useful text for grounded answers.",
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.failures == ()
    assert result.warnings == ()


def test_external_docs_validation_json_output_is_valid() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "Clean evidence text with enough useful words for validation.")],
    )

    encoded = json.dumps(result.to_dict())
    decoded = json.loads(encoded)

    assert decoded["source_name"] == "future_docs"
    assert decoded["quality"] == "PASS"


def test_external_docs_validation_thresholds_are_source_agnostic() -> None:
    result = validate_external_docs(
        source_name="another_vendor_docs",
        documents=[_doc("doc-1", source_name="another_vendor_docs")],
        chunks=[
            _chunk("doc-1", "# API", heading="API"),
            _chunk("doc-1", "Useful vendor-neutral documentation text with enough words."),
        ],
    )

    assert result.quality == "WARN"
    assert result.metrics["title_only_chunks_ratio"] == 0.5
    assert result.samples["title_only_chunks"]


def _doc(
    document_id: str,
    *,
    source_name: str = "future_docs",
    canonical_url: str = "https://docs.example.com/page",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    row_metadata = {
        "source_name": source_name,
        "source_url": canonical_url,
        "canonical_url": canonical_url,
    }
    if metadata is not None:
        row_metadata = metadata
    return {
        "id": document_id,
        "filename": f"{document_id}.html",
        "document_key": canonical_url,
        "title": f"Page {document_id}",
        "status": "active",
        "metadata": row_metadata,
    }


def _chunk(document_id: str, content: str, *, heading: str = "Overview") -> dict[str, object]:
    return {
        "id": f"chunk-{document_id}",
        "document_id": document_id,
        "chunk_index": 0,
        "content": content,
        "heading": heading,
        "metadata": {},
    }
