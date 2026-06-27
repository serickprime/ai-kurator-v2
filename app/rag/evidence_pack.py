"""Evidence pack construction."""

from collections.abc import Sequence

from app.rag.types import EvidencePack, EvidenceSpan


class EvidencePackBuilder:
    """Build the narrow context passed to answer generation."""

    def build(self, spans: Sequence[EvidenceSpan], max_items: int = 12) -> EvidencePack:
        """Build a compact evidence pack from reranked spans."""
        selected = tuple(span for span in spans if span.text.strip())[:max_items]
        return EvidencePack(items=selected)
