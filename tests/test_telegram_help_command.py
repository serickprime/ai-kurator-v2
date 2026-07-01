import asyncio
from types import SimpleNamespace

from app.bot.handlers import BotServices, help_command, start


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.replies.append(text)


def test_help_command_mentions_base_status_and_services() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message), _context(BotServices())))

    reply = message.replies[-1]
    assert "/base_status" in reply
    assert "/services" in reply
    assert "/status" in reply
    assert "/new" in reply
    assert "/debug_last" in reply


def test_help_command_mentions_material_upload() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message), _context(BotServices())))

    reply = message.replies[-1]
    assert "/upload" in reply
    assert "Загрузить материал" in reply
    assert "загрузить материал" in reply


def test_help_command_does_not_show_json_or_dict_repr() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message), _context(BotServices())))

    reply = message.replies[-1]
    assert "{" not in reply
    assert "BotServices" not in reply
    assert "ServiceDocsStatus" not in reply


def test_start_command_still_replies_and_points_to_help() -> None:
    message = FakeMessage()

    asyncio.run(start(_update(message), _context(BotServices())))

    reply = message.replies[-1]
    assert "Задавайте вопрос" in reply
    assert "/help" in reply


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=7), callback_query=None)
