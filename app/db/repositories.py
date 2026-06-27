"""Repository layer placeholders for Supabase access."""

from app.db.supabase_client import SupabaseClient


class DocumentRepository:
    """Database access for documents, versions, cards, and indexed units."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client


class ConversationRepository:
    """Database access for Telegram conversations and messages."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client
