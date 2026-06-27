import asyncio
from pathlib import Path

from app.bot.intake_buffer import MessageIntakeBuffer
from app.bot.user_state import InMemoryUserSettingsRepository


def test_text_and_image_share_one_intake_context() -> None:
    buffer = MessageIntakeBuffer()
    buffer.add_image(10, Path("screen.png"), vision_text="Docker compose error is visible", message_id=1)
    buffer.add_text(10, "Как исправить запуск n8n?", message_id=2)

    intake = buffer.build_intake(10, user_settings={"answer_mode": "cheap"})
    combined = intake.combined_question()

    assert "Как исправить запуск n8n?" in combined
    assert "Docker compose error is visible" in combined
    assert intake.telegram_message_ids == (1, 2)
    assert not buffer.has_pending(10)


def test_vision_off_keeps_image_marker_without_vision_text() -> None:
    buffer = MessageIntakeBuffer()
    buffer.add_image(10, Path("screen.png"), caption="Что здесь не так?", message_id=1)

    intake = buffer.build_intake(10, user_settings={"vision_mode": "off"})
    combined = intake.combined_question()

    assert "Что здесь не так?" in combined
    assert "vision context недоступен" in combined


def test_user_settings_repo_updates_modes() -> None:
    async def run() -> tuple[str, str, bool]:
        repo = InMemoryUserSettingsRepository()
        await repo.set_answer_mode(10, "quality")
        await repo.set_vision_mode(10, "off")
        settings = await repo.set_debug_mode(10, True)
        return settings.answer_mode, settings.vision_mode, settings.debug_mode

    assert asyncio.run(run()) == ("quality", "off", True)

