from app.rag.source_labels import SourceLabelBuilder
from app.rag.types import SourceRef


def test_source_label_builder_skips_boilerplate_locator() -> None:
    label = SourceLabelBuilder().build(
        SourceRef(
            document_id="doc-1",
            document_title="\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0444\u0430\u0439\u043b\u0430:",
            locator="\u041f\u0440\u043e\u0447\u0435\u0435",
            metadata={"filename": "lesson.md"},
        )
    )

    assert label == "lesson"


def test_source_label_builder_deduplicates_same_document_section() -> None:
    sources = [
        SourceRef(document_id="doc-1", document_title="lesson.md", locator="Setup", evidence_id="a"),
        SourceRef(document_id="doc-1", document_title="lesson.md", locator="Setup", evidence_id="b"),
        SourceRef(document_id="doc-1", document_title="lesson.md", locator="Install", evidence_id="c"),
        SourceRef(document_id="doc-1", document_title="lesson.md", locator="Check", evidence_id="d"),
        SourceRef(document_id="doc-1", document_title="lesson.md", locator="Extra", evidence_id="e"),
    ]

    labels = SourceLabelBuilder().build_many(sources)

    assert labels == ["lesson \u2014 Setup", "lesson \u2014 Install", "lesson \u2014 Check"]


def test_source_label_builder_builds_clean_document_debug_label() -> None:
    label = SourceLabelBuilder().build_document_label(
        {
            "document_id": "doc-1",
            "title": "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0444\u0430\u0439\u043b\u0430:",
            "filename": "CLn02_text_double_deep.txt",
        }
    )

    assert label == "CLn02_text_double_deep"
