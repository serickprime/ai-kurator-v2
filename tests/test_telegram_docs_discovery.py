from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.bot.handlers import BotServices, handle_text
from app.db.repositories import DocsCandidateSuggestion
from app.docs_registry.discovery import (
    DISCOVERY_USER_MESSAGE,
    LOW_CONFIDENCE_OWNER_MESSAGE,
    DocsDiscoveryOutcome,
)


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.message_id = 101
        self.replies: list[str] = []
        self.reply_markups: list[object] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


class FakeDiscoveryService:
    def __init__(self, outcome: DocsDiscoveryOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str, int | None]] = []

    async def discover_from_question(
        self,
        question: str,
        *,
        workspace_id: str,
        requested_by_user_id: int | None = None,
    ) -> DocsDiscoveryOutcome:
        self.calls.append((question, workspace_id, requested_by_user_id))
        return self.outcome


class FailingDiscoveryService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int | None]] = []

    async def discover_from_question(
        self,
        question: str,
        *,
        workspace_id: str,
        requested_by_user_id: int | None = None,
    ) -> DocsDiscoveryOutcome:
        self.calls.append((question, workspace_id, requested_by_user_id))
        raise RuntimeError("search unavailable")


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


def test_regular_user_gets_rag_answer_and_safe_discovery_notice() -> None:
    discovery = FakeDiscoveryService(
        DocsDiscoveryOutcome(
            handled=True,
            reason="created",
            service_name="AcmePay",
            service_id="acmepay",
            suggestion=_suggestion(),
            created=True,
        )
    )
    rag = FakeRagPipeline()
    services = BotServices(
        docs_discovery_service=discovery,
        rag_pipeline=rag,
        default_workspace_id="workspace-1",
    )
    message = FakeMessage("Как подключить AcmePay webhooks?")

    asyncio.run(handle_text(_update(message, user_id=42), _context(services)))

    assert discovery.calls == [("Как подключить AcmePay webhooks?", "workspace-1", 42)]
    assert rag.calls == ["Как подключить AcmePay webhooks?"]
    assert message.replies == ["RAG answer", DISCOVERY_USER_MESSAGE]
    assert message.replies[-1] == DISCOVERY_USER_MESSAGE
    assert "https://" not in message.replies[-1]
    assert "confidence" not in message.replies[-1].casefold()
    assert "AcmePay" not in message.replies[-1]


def test_owner_low_confidence_notice_does_not_replace_rag_answer() -> None:
    discovery = FakeDiscoveryService(
        DocsDiscoveryOutcome(
            handled=True,
            reason="low_confidence",
            service_name="CloudDesk",
            service_id="clouddesk",
        )
    )
    rag = FakeRagPipeline()
    services = BotServices(
        docs_discovery_service=discovery,
        rag_pipeline=rag,
        default_workspace_id="workspace-1",
        owner_ids=(42,),
    )
    message = FakeMessage("Как подключить CloudDesk?")

    asyncio.run(handle_text(_update(message, user_id=42), _context(services)))

    assert rag.calls == ["Как подключить CloudDesk?"]
    assert message.replies == ["RAG answer", LOW_CONFIDENCE_OWNER_MESSAGE]


def test_regular_user_low_confidence_result_keeps_only_rag_answer() -> None:
    discovery = FakeDiscoveryService(
        DocsDiscoveryOutcome(
            handled=True,
            reason="low_confidence",
            service_name="CloudDesk",
            service_id="clouddesk",
        )
    )
    rag = FakeRagPipeline()
    services = BotServices(
        docs_discovery_service=discovery,
        rag_pipeline=rag,
        default_workspace_id="workspace-1",
    )
    message = FakeMessage("Как подключить CloudDesk?")

    asyncio.run(handle_text(_update(message, user_id=42), _context(services)))

    assert rag.calls == ["Как подключить CloudDesk?"]
    assert message.replies == ["RAG answer"]


def test_discovery_failure_does_not_block_rag_answer() -> None:
    discovery = FailingDiscoveryService()
    rag = FakeRagPipeline()
    services = BotServices(
        docs_discovery_service=discovery,
        rag_pipeline=rag,
        default_workspace_id="workspace-1",
    )
    message = FakeMessage("Как подключить FutureService?")

    asyncio.run(handle_text(_update(message, user_id=42), _context(services)))

    assert discovery.calls == [("Как подключить FutureService?", "workspace-1", 42)]
    assert rag.calls == ["Как подключить FutureService?"]
    assert message.replies == ["RAG answer"]


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)


def _suggestion() -> DocsCandidateSuggestion:
    return DocsCandidateSuggestion(
        id="suggestion-1",
        workspace_id="workspace-1",
        service_id="acmepay",
        display_name="AcmePay",
        aliases=("AcmePay",),
        official_url="https://docs.acmepay.com/docs",
        allowed_domain="docs.acmepay.com",
        source_query="AcmePay official documentation",
        discovery_reason="web_discovery_official_docs_candidate",
        confidence=0.68,
        risk_level="review",
        status="pending",
        preview_status="not_run",
    )
