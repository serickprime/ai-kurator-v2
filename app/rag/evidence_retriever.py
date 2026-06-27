"""Evidence retrieval scoped to selected documents."""

from app.rag.types import DocumentCandidate, EvidenceSpan, QuestionAnalysis


class EvidenceRetriever:
    """Retrieve evidence spans only inside selected documents."""

    async def retrieve(
        self,
        analysis: QuestionAnalysis,
        documents: tuple[DocumentCandidate, ...],
    ) -> tuple[EvidenceSpan, ...]:
        """Return evidence spans from the routed document set."""
        raise NotImplementedError("Evidence retrieval is not implemented yet")
