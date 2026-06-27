from app.rag.evidence_pack import EvidencePackBuilder
from app.rag.types import EvidenceSpan


def test_evidence_pack_sources_are_derived_from_items() -> None:
    spans = (
        EvidenceSpan(
            evidence_id="ev-1",
            document_id="doc-1",
            document_title="Lesson 1",
            text="Use a service role key only on the server.",
            locator="p. 3",
        ),
        EvidenceSpan(
            evidence_id="ev-2",
            document_id="doc-1",
            document_title="Lesson 1",
            text="Never expose service role keys to clients.",
            locator="p. 4",
        ),
    )

    pack = EvidencePackBuilder().build(spans)

    assert pack.source_document_ids == ("doc-1",)
    assert {source.document_id for source in pack.sources()} == {"doc-1"}
