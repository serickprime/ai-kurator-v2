"""Document card creation for document-first routing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentCard:
    """Compact routing representation of a document."""

    document_id: str
    title: str
    summary: str
    keywords: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
