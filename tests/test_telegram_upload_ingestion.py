import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.bot.handlers import BotServices, handle_document


class FakeIngestionService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    async def ingest_path(self, path: Path, **kwargs: object) -> list[object]:
        self.calls.append({"path": path, **kwargs})
        if self.fail:
            raise RuntimeError("Unsupported file type: .exe")
        return [
            SimpleNamespace(
                document_id="doc-1",
                document_key=path.name,
                skipped=False,
                sections_count=2,
                chunks_count=5,
                term_statistics_status="updated",
            )
        ]


class FakeTelegramFile:
    async def download_to_drive(self, custom_path: Path) -> None:
        Path(custom_path).parent.mkdir(parents=True, exist_ok=True)
        Path(custom_path).write_text("# Lesson\n\nBody", encoding="utf-8")


class FakeDocument:
    file_name = "lesson.txt"

    async def get_file(self) -> FakeTelegramFile:
        return FakeTelegramFile()


class ExplodingDocument:
    file_name = "lesson.txt"

    async def get_file(self) -> FakeTelegramFile:
        raise AssertionError("file must not be downloaded outside upload mode")


class FakeMessage:
    def __init__(self, document: object) -> None:
        self.document = document
        self.message_id = 77
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.replies.append(text)


def test_file_in_upload_mode_calls_ingestion_service(tmp_path: Path) -> None:
    ingestion = FakeIngestionService()
    services = BotServices(ingestion_service=ingestion, download_dir=tmp_path)
    services.state_store.set_mode(7, "upload_material")
    message = FakeMessage(FakeDocument())

    asyncio.run(handle_document(_update(7, message), _context(services)))

    assert ingestion.calls
    assert ingestion.calls[0]["workspace"] == "team"
    assert "Материал загружен" in message.replies[-1]
    assert "Разделов: 2" in message.replies[-1]
    assert "Чанков: 5" in message.replies[-1]
    assert "Term statistics: updated" in message.replies[-1]


def test_file_outside_upload_mode_does_not_call_ingestion_service(tmp_path: Path) -> None:
    ingestion = FakeIngestionService()
    services = BotServices(ingestion_service=ingestion, download_dir=tmp_path)
    message = FakeMessage(ExplodingDocument())

    asyncio.run(handle_document(_update(7, message), _context(services)))

    assert ingestion.calls == []
    assert "Загрузить материал" in message.replies[-1]


def test_upload_without_ingestion_service_explains_configuration(tmp_path: Path) -> None:
    services = BotServices(download_dir=tmp_path)
    services.state_store.set_mode(7, "upload_material")
    message = FakeMessage(FakeDocument())

    asyncio.run(handle_document(_update(7, message), _context(services)))

    assert "Загрузка материалов не подключена" in message.replies[-1]


def test_ingestion_error_returns_readable_message(tmp_path: Path) -> None:
    services = BotServices(ingestion_service=FakeIngestionService(fail=True), download_dir=tmp_path)
    services.state_store.set_mode(7, "upload_material")
    message = FakeMessage(FakeDocument())

    asyncio.run(handle_document(_update(7, message), _context(services)))

    assert "Не получилось загрузить материал" in message.replies[-1]
    assert "Unsupported file type" in message.replies[-1]
    assert "Traceback" not in message.replies[-1]


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(user_id: int, message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)
