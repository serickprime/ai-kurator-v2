"""Evidence reranking."""

from collections.abc import Sequence

from app.rag.types import EvidenceSpan


class EvidenceReranker:
    """Order evidence spans before packing."""

    def rerank(self, spans: Sequence[EvidenceSpan]) -> tuple[EvidenceSpan, ...]:
        """Return spans in their current order until a real reranker is added."""
        return tuple(spans)
