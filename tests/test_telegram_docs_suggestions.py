from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from app.bot.handlers import BotServices, docs_suggestions_callback, docs_suggestions_command, handle_text, help_command
from app.db.repositories import DocsCandidateSuggestion
from app.db.supabase_client import SupabaseRequestError
from app.docs_registry.activation import DocsActivationPlan, DocsActivationQualityGate, DocsActivationResult
from app.docs_registry.models import DocsCandidatePreviewResult


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.message_id = 42
        self.replies: list[str] = []
        self.reply_markups: list[object] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


class FakeCallbackQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = FakeMessage("callback-source")
        self.answered = False
        self.answer_texts: list[str] = []
        self.edits: list[str] = []
        self.edit_markups: list[object] = []

    async def answer(self, text: str | None = None, **kwargs: object) -> None:
        del kwargs
        self.answered = True
        if text:
            self.answer_texts.append(text)

    async def edit_message_text(self, text: str, **kwargs: object) -> None:
        self.edits.append(text)
        self.edit_markups.append(kwargs.get("reply_markup"))


class FakeSuggestionsRepository:
    def __init__(
        self,
        suggestions: tuple[DocsCandidateSuggestion, ...] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.suggestions = {suggestion.id: suggestion for suggestion in suggestions}
        self.error = error
        self.list_calls = 0
        self.get_calls: list[str] = []
        self.preview_saves: list[dict[str, Any]] = []
        self.reject_calls: list[dict[str, Any]] = []
        self.activation_saves: list[dict[str, Any]] = []

    async def list_pending(self, workspace_id: str, *, limit: int = 10) -> tuple[DocsCandidateSuggestion, ...]:
        del workspace_id, limit
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return tuple(
            suggestion
            for suggestion in self.suggestions.values()
            if suggestion.status in {"pending", "preview_ready", "failed"}
        )

    async def get(self, suggestion_id: str) -> DocsCandidateSuggestion | None:
        self.get_calls.append(suggestion_id)
        if self.error is not None:
            raise self.error
        return self.suggestions.get(suggestion_id)

    async def save_preview_result(
        self,
        suggestion_id: str,
        *,
        preview_status: str,
        preview_result: dict[str, Any],
        status: str | None = None,
    ) -> DocsCandidateSuggestion:
        self.preview_saves.append(
            {
                "suggestion_id": suggestion_id,
                "preview_status": preview_status,
                "preview_result": deepcopy(preview_result),
                "status": status,
            }
        )
        suggestion = self.suggestions[suggestion_id]
        updated = _replace_suggestion(
            suggestion,
            status=status or ("failed" if preview_status == "failed" else "preview_ready"),
            preview_status=preview_status,
            preview_result=preview_result,
        )
        self.suggestions[suggestion_id] = updated
        return updated

    async def save_activation_result(
        self,
        suggestion_id: str,
        *,
        activation_result: dict[str, Any],
        status: str,
        reviewed_by_user_id: int | None = None,
    ) -> DocsCandidateSuggestion:
        self.activation_saves.append(
            {
                "suggestion_id": suggestion_id,
                "activation_result": deepcopy(activation_result),
                "status": status,
                "reviewed_by_user_id": reviewed_by_user_id,
            }
        )
        suggestion = self.suggestions[suggestion_id]
        metadata = dict(suggestion.metadata)
        metadata["activation_result"] = deepcopy(activation_result)
        updated = _replace_suggestion(
            suggestion,
            status=status,
            reviewed_by_user_id=reviewed_by_user_id,
            reviewed_at="2026-07-16T00:00:00+00:00",
            metadata=metadata,
        )
        self.suggestions[suggestion_id] = updated
        return updated

    async def reject(
        self,
        suggestion_id: str,
        *,
        reviewed_by_user_id: int,
        rejection_reason: str = "",
    ) -> DocsCandidateSuggestion:
        self.reject_calls.append(
            {
                "suggestion_id": suggestion_id,
                "reviewed_by_user_id": reviewed_by_user_id,
                "rejection_reason": rejection_reason,
            }
        )
        suggestion = self.suggestions[suggestion_id]
        updated = _replace_suggestion(
            suggestion,
            status="rejected",
            reviewed_by_user_id=reviewed_by_user_id,
            reviewed_at="2026-07-16T00:00:00+00:00",
            rejection_reason=rejection_reason,
        )
        self.suggestions[suggestion_id] = updated
        return updated


class FakePreviewService:
    def __init__(self, result: DocsCandidatePreviewResult | None = None, error: Exception | None = None) -> None:
        self.result = result or _preview_result()
        self.error = error
        self.calls: list[tuple[str, int]] = []
        self.activation_calls: list[str] = []
        self.indexing_calls: list[str] = []

    async def preview(self, service_id_or_alias: str, *, limit: int = 5) -> DocsCandidatePreviewResult:
        self.calls.append((service_id_or_alias, limit))
        if self.error is not None:
            raise self.error
        return self.result

    async def activate(self) -> None:
        self.activation_calls.append("activate")
        raise AssertionError("preview must not activate")

    async def index(self) -> None:
        self.indexing_calls.append("index")
        raise AssertionError("preview must not index")


class FakeActivationService:
    def __init__(self, result: DocsActivationResult | None = None, error: Exception | None = None) -> None:
        self.result = result or _activation_result()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def activate_candidate(self, candidate_id: str, candidate: Any, *, policy: Any) -> DocsActivationResult:
        self.calls.append({"candidate_id": candidate_id, "candidate": candidate, "policy": policy})
        if self.error is not None:
            raise self.error
        return self.result


def test_owner_sees_pending_suggestions() -> None:
    repo = FakeSuggestionsRepository((_suggestion(), _suggestion(id="previewed", status="preview_ready")))
    services = _services(repo, owner_ids=(7,))
    message = FakeMessage("/docs_suggestions")

    asyncio.run(docs_suggestions_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "Docs suggestions" in reply
    assert "Demo Service" in reply
    assert "status=pending" in reply
    assert "status=preview_ready" in reply
    assert "docs.example.com" in reply
    assert message.reply_markups[-1] is not None


def test_admin_sees_pending_suggestions() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    services = _services(repo, admin_ids=(7,))
    message = FakeMessage("/docs_suggestions")

    asyncio.run(docs_suggestions_command(_update(message, user_id=7), _context(services)))

    assert repo.list_calls == 1
    assert "Demo Service" in message.replies[-1]


def test_regular_user_is_denied_without_technical_data() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    services = _services(repo, owner_ids=(1,))
    message = FakeMessage("/docs_suggestions")

    asyncio.run(docs_suggestions_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert repo.list_calls == 0
    assert "available to the bot owner/admin" in reply
    assert "Demo Service" not in reply
    assert "docs.example.com" not in reply


def test_candidate_card_does_not_show_raw_metadata() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    services = _services(repo, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:open:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    card = query.edits[-1]
    assert "Docs suggestion: Demo Service" in card
    assert "Candidate source: curated" in card
    assert "secret_internal_value" not in card
    assert "metadata" not in card
    assert "{" not in card


def test_preview_uses_existing_preview_service_and_saves_success() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    preview = FakePreviewService()
    services = _services(repo, preview_service=preview, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:preview:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert preview.calls == [("demo_service", 5)]
    assert repo.preview_saves[0]["preview_status"] == "ok"
    assert repo.preview_saves[0]["preview_result"]["pages_found"] == 2
    assert "Preview status: ok" in query.edits[-1]


def test_preview_failure_is_saved_as_failed() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    preview = FakePreviewService(error=RuntimeError("network down"))
    services = _services(repo, preview_service=preview, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:preview:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert repo.preview_saves[0]["preview_status"] == "failed"
    assert repo.preview_saves[0]["status"] == "failed"
    assert repo.preview_saves[0]["preview_result"]["error"] == "RuntimeError"
    assert "Preview status: failed" in query.edits[-1]
    assert "Preview error: RuntimeError" in query.edits[-1]


def test_failed_preview_stays_in_suggestions_list() -> None:
    repo = FakeSuggestionsRepository((_suggestion(status="failed", preview_status="failed"),))
    services = _services(repo, owner_ids=(7,))
    message = FakeMessage("/docs_suggestions")

    asyncio.run(docs_suggestions_command(_update(message, user_id=7), _context(services)))

    assert "Demo Service" in message.replies[-1]
    assert "status=failed" in message.replies[-1]
    assert "preview=failed" in message.replies[-1]


def test_failed_candidate_can_be_previewed_again() -> None:
    repo = FakeSuggestionsRepository((_suggestion(status="failed", preview_status="failed"),))
    preview = FakePreviewService()
    services = _services(repo, preview_service=preview, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:preview:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert preview.calls == [("demo_service", 5)]
    assert repo.preview_saves[-1]["preview_status"] == "ok"
    assert repo.suggestions["suggestion-123456"].status == "preview_ready"
    assert "Preview status: ok" in query.edits[-1]


def test_failed_candidate_has_retry_and_reject_but_no_add_button() -> None:
    repo = FakeSuggestionsRepository((_suggestion(status="failed", preview_status="failed"),))
    services = _services(repo, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:open:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    keyboard = query.edit_markups[-1].inline_keyboard
    buttons = [button for row in keyboard for button in row]
    assert any(button.text == "Проверить снова" for button in buttons)
    assert any(button.callback_data == "docs_suggest:reject:suggestion-123456" for button in buttons)
    assert not any(button.callback_data == "docs_suggest:approve:suggestion-123456" for button in buttons)


def test_preview_does_not_call_activation_or_indexing() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    preview = FakePreviewService()
    services = _services(repo, preview_service=preview, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:preview:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert preview.activation_calls == []
    assert preview.indexing_calls == []


def test_reject_saves_status_and_reviewer() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    services = _services(repo, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:reject:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert repo.reject_calls == [
        {
            "suggestion_id": "suggestion-123456",
            "reviewed_by_user_id": 7,
            "rejection_reason": "rejected_by_owner",
        }
    ]
    assert "Status: rejected" in query.edits[-1]


def test_rejected_suggestion_disappears_from_pending_list() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    services = _services(repo, owner_ids=(7,))
    reject_query = FakeCallbackQuery("docs_suggest:reject:suggestion-123456")
    list_message = FakeMessage("/docs_suggestions")

    asyncio.run(docs_suggestions_callback(_callback_update(reject_query, user_id=7), _context(services)))
    asyncio.run(docs_suggestions_command(_update(list_message, user_id=7), _context(services)))

    assert "No pending suggestions." in list_message.replies[-1]
    assert "Demo Service" not in list_message.replies[-1]


def test_callback_without_rights_is_denied_and_does_not_mutate() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    preview = FakePreviewService()
    services = _services(repo, preview_service=preview, owner_ids=(1,))
    query = FakeCallbackQuery("docs_suggest:preview:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert query.answered
    assert "available to the bot owner/admin" in query.edits[-1]
    assert repo.get_calls == []
    assert repo.preview_saves == []
    assert preview.calls == []


def test_non_owner_cannot_approve() -> None:
    repo = FakeSuggestionsRepository((_suggestion(status="preview_ready", preview_status="ok"),))
    activation = FakeActivationService()
    services = _services(repo, activation_service=activation, owner_ids=(1,))
    query = FakeCallbackQuery("docs_suggest:approve:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert "available to the bot owner/admin" in query.edits[-1]
    assert repo.get_calls == []
    assert activation.calls == []
    assert repo.activation_saves == []


def test_approve_without_preview_is_blocked() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    activation = FakeActivationService()
    services = _services(repo, activation_service=activation, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:approve:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert "Adding is blocked" in query.edits[-1]
    assert activation.calls == []
    assert repo.activation_saves == []


def test_preview_ready_can_be_confirmed_and_uses_activation_service() -> None:
    repo = FakeSuggestionsRepository((_suggestion(status="preview_ready", preview_status="ok"),))
    activation = FakeActivationService()
    services = _services(repo, activation_service=activation, owner_ids=(7,))
    approve_query = FakeCallbackQuery("docs_suggest:approve:suggestion-123456")
    confirm_query = FakeCallbackQuery("docs_suggest:confirm_add:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(approve_query, user_id=7), _context(services)))
    asyncio.run(docs_suggestions_callback(_callback_update(confirm_query, user_id=7), _context(services)))

    assert "Confirm adding documentation" in approve_query.edits[-1]
    assert activation.calls[0]["candidate_id"] == "suggestion-123456"
    assert activation.calls[0]["candidate"].official_start_urls == ("https://docs.example.com/start",)
    assert activation.calls[0]["policy"].candidate_id == "suggestion-123456"
    assert repo.activation_saves[-1]["status"] == "activated"
    assert repo.activation_saves[-1]["reviewed_by_user_id"] == 7
    assert "Status: activated" in confirm_query.edits[-1]


def test_approve_does_not_activate_before_confirmation() -> None:
    repo = FakeSuggestionsRepository((_suggestion(status="preview_ready", preview_status="ok"),))
    activation = FakeActivationService()
    services = _services(repo, activation_service=activation, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:approve:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert activation.calls == []
    assert repo.activation_saves == []


def test_activation_failure_is_saved_safely() -> None:
    repo = FakeSuggestionsRepository((_suggestion(status="preview_ready", preview_status="ok"),))
    activation = FakeActivationService(error=RuntimeError("secret stack trace"))
    services = _services(repo, activation_service=activation, owner_ids=(7,))
    query = FakeCallbackQuery("docs_suggest:confirm_add:suggestion-123456")

    asyncio.run(docs_suggestions_callback(_callback_update(query, user_id=7), _context(services)))

    assert repo.activation_saves[-1]["status"] == "failed"
    assert repo.activation_saves[-1]["activation_result"]["error"] == "RuntimeError"
    assert "secret stack trace" not in query.edits[-1]
    assert "Status: failed" in query.edits[-1]


def test_missing_table_is_reported_without_runtime_crash_or_details() -> None:
    repo = FakeSuggestionsRepository(
        error=SupabaseRequestError(404, 'Could not find the table "docs_candidate_suggestions" in the schema cache')
    )
    services = _services(repo, owner_ids=(7,))
    message = FakeMessage("/docs_suggestions")

    asyncio.run(docs_suggestions_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "migration has not been applied" in reply
    assert "SUPABASE" not in reply
    assert "service_role" not in reply
    assert "http" not in reply.lower()


def test_docs_suggestions_text_fallback_does_not_call_rag() -> None:
    repo = FakeSuggestionsRepository((_suggestion(),))
    pipeline = FakeRagPipeline()
    services = _services(repo, owner_ids=(7,))
    services.rag_pipeline = pipeline
    message = FakeMessage("/docs_suggestions")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert pipeline.calls == []
    assert "Docs suggestions" in message.replies[-1]


def test_help_mentions_docs_suggestions() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message, user_id=7), _context(BotServices())))

    assert "/docs_suggestions" in message.replies[-1]


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


def _services(
    repo: FakeSuggestionsRepository,
    *,
    preview_service: FakePreviewService | None = None,
    activation_service: FakeActivationService | None = None,
    owner_ids: tuple[int, ...] = (),
    admin_ids: tuple[int, ...] = (),
) -> BotServices:
    return BotServices(
        docs_suggestions_repository=repo,
        docs_preview_service=preview_service,
        docs_activation_service=activation_service,
        owner_ids=owner_ids,
        admin_ids=admin_ids,
        default_workspace_id="workspace-1",
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)


def _callback_update(query: FakeCallbackQuery, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=None, effective_user=SimpleNamespace(id=user_id), callback_query=query)


def _suggestion(
    *,
    id: str = "suggestion-123456",
    status: str = "pending",
    preview_status: str = "not_run",
) -> DocsCandidateSuggestion:
    return DocsCandidateSuggestion(
        id=id,
        workspace_id="workspace-1",
        service_id="demo_service",
        display_name="Demo Service",
        aliases=("demo service",),
        official_url="https://docs.example.com/start",
        allowed_domain="docs.example.com",
        source_query="how to connect demo",
        discovery_reason="user asked about demo service",
        confidence=0.82,
        risk_level="low",
        status=status,
        preview_status=preview_status,
        metadata={
            "source": "docs_source_candidates.yaml",
            "docs_source": "demo_docs",
            "allow_patterns": [r"^https://docs\.example\.com/"],
            "deny_patterns": ["/login"],
            "max_pages": 10,
            "crawl_depth": 1,
            "secret_internal_value": "do-not-show",
        },
    )


def _replace_suggestion(suggestion: DocsCandidateSuggestion, **changes: Any) -> DocsCandidateSuggestion:
    data = suggestion.__dict__.copy()
    data.update(changes)
    return DocsCandidateSuggestion(**data)


def _preview_result() -> DocsCandidatePreviewResult:
    return DocsCandidatePreviewResult(
        service_id="demo_service",
        display_name="Demo Service",
        docs_source="demo_docs",
        allowed_domains=("docs.example.com",),
        start_urls=("https://docs.example.com/start",),
        pages_checked=5,
        pages_found=2,
        sample_titles=("Overview", "API reference"),
        sample_urls=("https://docs.example.com/start",),
        status="ok",
        warnings=(),
        risk_level="low",
        notes="test",
    )


def _activation_result() -> DocsActivationResult:
    return DocsActivationResult(
        plan=DocsActivationPlan(
            service_id="demo_service",
            display_name="Demo Service",
            docs_source="demo_docs",
            allowed_domains=("docs.example.com",),
            start_urls=("https://docs.example.com/start",),
            max_pages=10,
            crawl_depth=1,
            risk_level="review",
            confirm_command="docs_suggest:confirm_add:suggestion-123456",
        ),
        fetched_pages=1,
        indexed_new=1,
        skipped_unchanged=0,
        archived_old=0,
        failed=0,
        chunks_total=3,
        errors=(),
        quality_gate=DocsActivationQualityGate(passed=True),
    )
