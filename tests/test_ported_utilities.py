import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.bot.access import UserAccessPolicy, parse_telegram_ids
from app.bot.formatting import format_for_telegram
from app.bot.text_routing import classify_intake_note_text, should_store_intake_note
from app.ingestion.loaders import FileLoader, detect_file_type, is_image
from app.llm.openrouter_client import looks_like_bad_output


def test_format_for_telegram_escapes_html_and_keeps_code_blocks() -> None:
    text = "## Setup\nUse `docker compose up` <now>\n```bash\ndocker compose up -d\n```"

    formatted = format_for_telegram(text)

    assert "Setup" in formatted
    assert "&lt;now&gt;" in formatted
    assert "<pre><code>docker compose up -d</code></pre>" in formatted
    assert "**" not in formatted


def test_json_loader_marks_n8n_workflow_and_invalid_json(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"name": "Demo", "nodes": []}), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("{not valid json", encoding="utf-8")

    loaded_workflow = asyncio.run(FileLoader().load(workflow))
    loaded_broken = asyncio.run(FileLoader().load(broken))

    assert loaded_workflow.metadata["json_valid"] is True
    assert loaded_workflow.metadata["looks_like_n8n_workflow"] is True
    assert loaded_broken.metadata["json_valid"] is False
    assert "Raw JSON-like text" in loaded_broken.structured_text


def test_file_type_helpers_match_old_loader_behavior(tmp_path: Path) -> None:
    image = tmp_path / "screen.PNG"
    image.write_bytes(b"image")

    assert detect_file_type("lesson.md") == "md"
    assert is_image("screen.PNG")


def test_access_policy_owner_admin_and_open_access() -> None:
    policy = UserAccessPolicy(owner_ids=(1,), fallback_admin_ids=(2,))

    assert parse_telegram_ids("1, bad, 2, 1") == (1, 2)
    assert policy.is_owner(1)
    assert asyncio.run(policy.is_allowed(2))
    assert asyncio.run(policy.role_for(2)) == "admin"
    assert asyncio.run(UserAccessPolicy().is_allowed(999))


def test_text_routing_classifies_short_intake_notes() -> None:
    assert classify_intake_note_text("n8n") == "topic_hint"
    assert classify_intake_note_text("ответь по материалам") == "source_instruction"
    note_type = classify_intake_note_text("что разобрать в этом вопросе?")

    assert note_type == "context_dependent_instruction"
    assert should_store_intake_note(note_type, has_recent_dialog_messages=False)
    assert not should_store_intake_note(note_type, has_recent_dialog_messages=True)


def test_openrouter_bad_output_detection() -> None:
    assert looks_like_bad_output("User safety: safe")
    assert not looks_like_bad_output("Use Docker to start n8n locally.")


def test_telegram_answer_formatting_is_applied_at_output_boundary() -> None:
    from app.bot.handlers import BotServices, _answer_intake
    from app.bot.intake_buffer import UserIntake

    class FakeMessage:
        def __init__(self) -> None:
            self.replies: list[tuple[str, dict[str, object]]] = []

        async def reply_text(self, text: str, **kwargs: object) -> None:
            self.replies.append((text, kwargs))

    class FakePipeline:
        async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
            del question, kwargs
            return SimpleNamespace(answer="Run `docker ps` <now>", status="answered", sources=())

    message = FakeMessage()
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=7))
    services = BotServices(rag_pipeline=FakePipeline())

    asyncio.run(_answer_intake(update, services, UserIntake(text="Как проверить запуск?")))

    assert message.replies[-1][0] == "Run docker ps &lt;now&gt;"
    assert message.replies[-1][1]["parse_mode"] == "HTML"
