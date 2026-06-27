from app.rag.types import DocumentCandidate


def test_document_candidate_carries_routing_reason() -> None:
    candidate = DocumentCandidate(
        document_id="doc-1",
        title="Supabase setup",
        reason="Matches document card keywords",
        score=0.91,
    )

    assert candidate.document_id == "doc-1"
    assert candidate.reason
    assert candidate.score > 0
