"""Evaluation metrics for evidence-first RAG."""


def source_precision(used_source_ids: set[str], shown_source_ids: set[str]) -> float:
    """Measure how many shown sources were actually used."""
    if not shown_source_ids:
        return 1.0
    return len(used_source_ids & shown_source_ids) / len(shown_source_ids)
