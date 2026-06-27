import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.bot.handlers import BotServices, _answer_intake, _start_new_topic, handle_text
from app.bot.intake_buffer import UserIntake
from app.bot.keyboards import (
    BTN_CANCEL,
    BTN_DONE,
    BTN_NEW_TOPIC,
    BTN_SETTINGS,
    BTN_UPLOAD_MATERIAL,
    main_menu_keyboard,
    upload_menu_keyboard,
)


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


class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="ok", status="answered", sources=())


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(user_id: int, message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)


def test_main_keyboard_has_only_primary_buttons() -> None:
    keyboard = main_menu_keyboard().to_dict()["keyboard"]

    labels = [button["text"] for row in keyboard for button in row]

    assert labels == [BTN_NEW_TOPIC, BTN_UPLOAD_MATERIAL, BTN_SETTINGS]


def test_upload_keyboard_has_done_and_cancel() -> None:
    keyboard = upload_menu_keyboard().to_dict()["keyboard"]

    labels = [button["text"] for row in keyboard for button in row]

    assert labels == [BTN_DONE, BTN_CANCEL]


def test_upload_mode_text_does_not_call_rag() -> None:
    pipeline = FakePipeline()
    services = BotServices(rag_pipeline=pipeline)
    services.state_store.set_mode(7, "upload_material")
    message = FakeMessage("как установить n8n?")

    asyncio.run(handle_text(_update(7, message), _context(services)))

    assert pipeline.calls == []
    assert services.state_store.get(7).mode == "upload_material"
    assert "режим загрузки материалов" in message.replies[-1]


def test_new_topic_clears_state_and_intake_buffer() -> None:
    services = BotServices()
    services.state_store.set_mode(7, "upload_material")
    services.state_store.get(7).active_conversation_id = "old"
    services.intake_buffer.add_text(7, "pending")
    message = FakeMessage()

    asyncio.run(_start_new_topic(_update(7, message), _context(services)))

    assert services.state_store.get(7).mode == "normal"
    assert services.state_store.get(7).active_conversation_id is None
    assert not services.intake_buffer.has_pending(7)
    assert "Новая тема начата" in message.replies[-1]


def test_image_context_without_text_does_not_call_rag() -> None:
    pipeline = FakePipeline()
    services = BotServices(rag_pipeline=pipeline)
    intake = UserIntake(images=(Path("screen.png"),), vision_text="visible settings screen")
    message = FakeMessage()

    asyncio.run(_answer_intake(_update(7, message), services, intake))

    assert pipeline.calls == []
    assert "Пришлите вопрос" in message.replies[-1]

