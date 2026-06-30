import asyncio
from types import SimpleNamespace

from app.bot.handlers import BotServices, _answer_intake, _start_new_topic, handle_text
from app.bot.intake_buffer import UserIntake
from app.bot.telegram_bot import build_application
from app.config import Settings


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.caption = None
        self.message_id = 42
        self.media_group_id = None
        self.photo = []
        self.document = None
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.replies.append(text)


class RecordingPipeline:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append({"question": question, **kwargs})
        return SimpleNamespace(answer="ok", status="answered", sources=())


def test_build_application_wires_rag_pipeline_when_config_is_complete() -> None:
    application = build_application(_complete_settings())
    services = application.bot_data["services"]

    try:
        assert services.rag_pipeline is not None
        assert services.rag_runtime is not None
        assert services.conversation_repo is not None
        assert services.ingestion_service is not None
        assert services.ingestion_runtime is not None
        assert services.service_docs_status_provider is not None
    finally:
        asyncio.run(services.rag_runtime.close())
        asyncio.run(services.ingestion_runtime.close())


def test_build_application_keeps_running_when_rag_config_is_incomplete() -> None:
    application = build_application(Settings(_env_file=None, telegram_bot_token="123456:test"))
    services = application.bot_data["services"]

    assert services.rag_pipeline is None
    assert services.rag_missing_config
    assert "Проверьте .env" in services.rag_disabled_reason


def test_handler_shows_clear_message_when_rag_pipeline_is_disabled() -> None:
    services = BotServices(
        rag_pipeline=None,
        rag_disabled_reason="RAG v2 pipeline не подключён: не хватает настроек окружения. Проверьте .env.",
    )
    message = FakeMessage()

    asyncio.run(_answer_intake(_update(7, message), services, UserIntake(text="как установить n8n?")))

    assert "RAG v2 pipeline не подключён" in message.replies[-1]
    assert "Проверьте .env" in message.replies[-1]


def test_telegram_layer_does_not_pass_raw_candidates_to_pipeline() -> None:
    pipeline = RecordingPipeline()
    services = BotServices(rag_pipeline=pipeline, default_workspace_id="workspace-1")
    message = FakeMessage()
    intake = UserIntake(
        text="как установить n8n?",
        user_settings={
            "answer_mode": "cheap",
            "debug_mode": False,
            "raw_candidates": "must not pass",
            "discarded_candidates": "must not pass",
        },
        vision_errors=("vision failed",),
    )

    asyncio.run(_answer_intake(_update(7, message), services, intake))

    assert pipeline.calls
    call = pipeline.calls[0]
    assert call["workspace_id"] == "workspace-1"
    assert "raw_candidates" not in call
    assert "discarded_candidates" not in call
    assert "raw_candidates" not in call["dialog_context"]
    assert "discarded_candidates" not in call["dialog_context"]
    assert "raw_candidates" not in call["dialog_context"]["user_settings"]
    assert "discarded_candidates" not in call["dialog_context"]["user_settings"]


def test_new_topic_still_clears_state_with_runtime_services() -> None:
    services = BotServices(rag_pipeline=RecordingPipeline())
    services.state_store.set_mode(7, "upload_material")
    services.intake_buffer.add_text(7, "pending")
    message = FakeMessage()

    asyncio.run(_start_new_topic(_update(7, message), _context(services)))

    assert services.state_store.get(7).mode == "normal"
    assert not services.intake_buffer.has_pending(7)
    assert "Новая тема начата" in message.replies[-1]


def test_upload_mode_still_does_not_call_rag() -> None:
    pipeline = RecordingPipeline()
    services = BotServices(rag_pipeline=pipeline)
    services.state_store.set_mode(7, "upload_material")
    message = FakeMessage("как установить n8n?")

    asyncio.run(handle_text(_update(7, message), _context(services)))

    assert pipeline.calls == []
    assert "режим загрузки материалов" in message.replies[-1]


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(user_id: int, message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)


def _complete_settings() -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="123456:test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-test",
        default_workspace_id="00000000-0000-0000-0000-000000000001",
        openrouter_api_key="openrouter-test",
        openrouter_default_model="openai/gpt-4.1-mini",
        embedding_provider="local",
        embedding_model="BAAI/bge-m3",
        embedding_dim=1024,
        rag_pipeline_version="v2",
    )
