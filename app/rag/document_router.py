"""Document router for document-first retrieval."""

from app.rag.types import DocumentCandidate, QuestionAnalysis


class DocumentRouter:
    """Select a small set of likely documents before evidence retrieval."""

    async def route(self, analysis: QuestionAnalysis) -> tuple[DocumentCandidate, ...]:
        """Return candidate documents for a question analysis."""
        raise NotImplementedError("Document routing is not implemented yet")
