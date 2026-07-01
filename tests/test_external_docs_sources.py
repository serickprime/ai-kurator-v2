from app.rag.evidence_pack import build_sources
from app.rag.source_labels import SourceLabelBuilder
from app.rag.types import EvidencePack, SourceRef


def test_external_docs_source_label_uses_title_and_canonical_url() -> None:
    label = SourceLabelBuilder().build(
        SourceRef(
            document_id="doc-1",
            document_title="HTTP Request node",
            locator="Authentication",
            metadata={
                "source_kind": "external_docs",
                "filename": "http-request.html",
                "canonical_url": "https://docs.example.com/integrations/http-request",
            },
        )
    )

    assert label == "HTTP Request node — Authentication (https://docs.example.com/integrations/http-request)"


def test_external_docs_final_sources_only_from_evidence_pack() -> None:
    pack = EvidencePack(
        source_matches=(
            SourceRef(
                document_id="accepted-doc",
                document_title="Accepted docs page",
                locator="Setup",
                source_uri="https://docs.example.com/setup",
            ),
        )
    )
    discarded_candidate = SourceRef(
        document_id="discarded-doc",
        document_title="Discarded raw crawl candidate",
        locator="Raw HTML",
        source_uri="https://docs.example.com/raw",
    )

    sources = build_sources(pack)

    assert sources == ["Accepted docs page — Setup (https://docs.example.com/setup)"]
    assert discarded_candidate.document_title not in sources
