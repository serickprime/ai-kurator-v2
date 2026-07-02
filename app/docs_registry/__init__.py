"""External docs registry helpers."""

from app.docs_registry.candidates import load_docs_source_candidates_config
from app.docs_registry.models import DocsSourceCandidate, DocsSourceCandidatesConfig

__all__ = [
    "DocsSourceCandidate",
    "DocsSourceCandidatesConfig",
    "load_docs_source_candidates_config",
]
