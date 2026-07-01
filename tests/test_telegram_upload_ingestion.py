import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.bot.handlers import BotServices, _format_upload_result, handle_document
from app.service_registry.types import ServiceDocsStatus


class FakeIngestionService:
    def __init__(
        self,
        *,
        fail: bool = False,
        fail_message: str = "Unsupported file type: .exe",
        skipped: bool = False,
        service_ids: tuple[str, ...] = (),
        service_mentions: tuple[dict[str, object], ...] = (),
    ) -> None:
        self.fail = fail
        self.fail_message = fail_message
        self.skipped = skipped
        self.service_ids = service_ids
        self.service_mentions = service_mentions
        self.calls: list[dict[str, object]] = []

    async def ingest_path(self, path: Path, **kwargs: object) -> list[object]:
        self.calls.append({"path": path, **kwargs})
        if self.fail:
            raise RuntimeError(self.fail_message)
        return [
            SimpleNamespace(
                document_id="doc-1",
                document_key=path.name,
                skipped=self.skipped,
                sections_count=2,
                chunks_count=5,
                term_statistics_status="updated",
                service_ids=self.service_ids,
                service_mentions=self.service_mentions,
            )
        ]


class FakeStatusProvider:
    async def list_statuses(self, **kwargs: object) -> tuple[ServiceDocsStatus, ...]:
        assert kwargs == {"scan_corpus": False}
        return (
            ServiceDocsStatus(
                service_id="supabase",
                display_name="Supabase",
                aliases=("supabase",),
                docs_source="supabase_docs",
                configured_status="enabled",
                docs_status="indexed",
                quality_status="PASS",
                docs_source_configured=True,
            ),
            ServiceDocsStatus(
                service_id="flutterflow",
                display_name="FlutterFlow",
                aliases=("flutterflow",),
                docs_source=None,
                configured_status="not_configured",
                docs_status="not_configured",
            ),
        )


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
    ingestion = FakeIngestionService(
        service_ids=("flutterflow", "supabase"),
        service_mentions=(
            {"service_id": "flutterflow", "display_name": "FlutterFlow", "matched_alias": "FlutterFlow"},
            {"service_id": "supabase", "display_name": "Supabase", "matched_alias": "Supabase"},
        ),
    )
    services = BotServices(
        ingestion_service=ingestion,
        service_docs_status_provider=FakeStatusProvider(),
        download_dir=tmp_path,
    )
    services.state_store.set_mode(7, "upload_material")
    message = FakeMessage(FakeDocument())

    asyncio.run(handle_document(_update(7, message), _context(services)))

    assert ingestion.calls
    assert ingestion.calls[0]["workspace"] == "team"
    assert "Файл получен. Обрабатываю и добавляю в базу" in message.replies[0]
    assert "Файл обработан и добавлен в базу" in message.replies[-1]
    assert "Документ: lesson.txt" in message.replies[-1]
    assert "Разделов: 2" in message.replies[-1]
    assert "Чанков: 5" in message.replies[-1]
    assert "Найдены сервисы:" in message.replies[-1]
    assert "FlutterFlow — документация не подключена" in message.replies[-1]
    assert "Supabase — документация подключена" in message.replies[-1]
    assert "Теперь можно задавать вопросы по этому материалу" in message.replies[-1]
    assert "Term statistics: updated" in message.replies[-1]
    assert "{" not in message.replies[-1]
    assert "service_id" not in message.replies[-1]


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


def test_upload_result_says_services_not_found() -> None:
    text = _format_upload_result(
        SimpleNamespace(
            document_id="doc-1",
            document_key="lesson.txt",
            skipped=False,
            sections_count=1,
            chunks_count=2,
            term_statistics_status="updated",
            service_ids=(),
            service_mentions=(),
        )
    )

    assert "сервисы не найдены" in text
    assert "{" not in text


def test_skipped_upload_result_does_not_crash_and_shows_services() -> None:
    text = _format_upload_result(
        SimpleNamespace(
            document_id="doc-1",
            document_key="lesson.txt",
            skipped=True,
            sections_count=0,
            chunks_count=0,
            term_statistics_status="skipped",
            service_ids=("supabase",),
            service_mentions=({"service_id": "supabase", "display_name": "Supabase"},),
        ),
        service_statuses={
            "supabase": ServiceDocsStatus(
                service_id="supabase",
                display_name="Supabase",
                aliases=("supabase",),
                docs_source="supabase_docs",
                configured_status="enabled",
                docs_status="indexed",
                quality_status="PASS",
                docs_source_configured=True,
            )
        },
    )

    assert "Файл уже был обработан раньше" in text
    assert "Supabase — документация подключена" in text


def test_ingestion_error_redacts_secrets(tmp_path: Path) -> None:
    secret_error = (
        "Bearer fake.access.token failed with sk-or-v1-fakeOpenRouterKey "
        "and sb_secret_fakeSupabaseKey at https://api.telegram.org/file/bot123456789:ABCdef_1234567890_secret/doc"
    )
    services = BotServices(
        ingestion_service=FakeIngestionService(fail=True, fail_message=secret_error),
        download_dir=tmp_path,
    )
    services.state_store.set_mode(7, "upload_material")
    message = FakeMessage(FakeDocument())

    asyncio.run(handle_document(_update(7, message), _context(services)))

    reply = message.replies[-1]
    assert "Bearer <redacted>" in reply
    assert "sk-or-v1-<redacted>" in reply
    assert "sb_secret_<redacted>" in reply
    assert "bot<redacted>" in reply
    assert "fake.access.token" not in reply
    assert "sk-or-v1-fakeOpenRouterKey" not in reply
    assert "sb_secret_fakeSupabaseKey" not in reply
    assert "ABCdef_1234567890_secret" not in reply


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(user_id: int, message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)
