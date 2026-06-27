from app.eval.metrics import source_precision
from app.rag.types import EvidencePack, EvidenceSpan


def test_sources_only_include_used_evidence_documents() -> None:
    pack = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-1",
                document_id="doc-used",
                document_title="Used lesson",
                text="Evidence that supports the answer.",
            ),
        )
    )

    shown_source_ids = {source.document_id for source in pack.sources()}

    assert shown_source_ids == {"doc-used"}
    assert source_precision({"doc-used"}, shown_source_ids) == 1.0
