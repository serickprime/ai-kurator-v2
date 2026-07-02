"""Telegram command, menu, and message handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.bot.access import UserAccessPolicy
from app.bot.base_status import BaseStatus, format_base_status
from app.bot.features.docs_registry import DocsPreviewReader, send_docs_dashboard, send_docs_preview
from app.bot.formatting import format_for_telegram, format_status
from app.bot.intake_buffer import MessageIntakeBuffer, UserIntake
from app.bot.keyboards import (
    BTN_CANCEL,
    BTN_DONE,
    BTN_NEW_TOPIC,
    BTN_SETTINGS,
    BTN_UPLOAD_MATERIAL,
    CALLBACK_DEBUG_OFF,
    CALLBACK_DEBUG_ON,
    CALLBACK_MODE_CHEAP,
    CALLBACK_MODE_FREE,
    CALLBACK_MODE_QUALITY,
    CALLBACK_SETTINGS_BACK,
    CALLBACK_VISION_AUTO,
    CALLBACK_VISION_OFF,
    main_menu_keyboard,
    settings_inline_keyboard,
    upload_menu_keyboard,
)
from app.bot.materials import (
    ExternalDocsArchiveError,
    MaterialAmbiguousError,
    MaterialCard,
    MaterialNotFoundError,
    format_material_archived,
    format_material_card,
    format_materials_list,
)
from app.bot.source_last import (
    find_last_answer_source,
    format_last_answer_sources,
    format_source_archived,
    last_answer_sources_from_debug,
    source_refs_to_debug_payload,
)
from app.bot.user_state import InMemoryBotUserStateStore, InMemoryUserSettingsRepository
from app.db.repositories import UserSettings
from app.rag.source_labels import SourceLabelBuilder
from app.service_registry.types import ServiceDocsStatus


class RagPipeline(Protocol):
    """Minimal RAG pipeline interface used by Telegram."""

    async def answer(
        self,
        question: str,
        *,
        workspace_id: str = "",
        course: str | None = None,
        dialog_context: object | None = None,
    ) -> Any:
        """Return a RAG answer result."""


class IntakeIngestionService(Protocol):
    """Optional ingestion service for upload mode."""

    async def ingest_path(
        self,
        path: Path,
        *,
        workspace: str = "team",
        course: str | None = None,
        module: str | None = None,
        lesson: str | None = None,
    ) -> list[Any]:
        """Ingest one file or directory."""


class ServiceDocsStatusReader(Protocol):
    """Read-only service docs status provider."""

    async def list_statuses(
        self,
        *,
        scan_corpus: bool = False,
        service: str | None = None,
    ) -> tuple[ServiceDocsStatus, ...]:
        """Return service/docs status rows."""


class BaseStatusReader(Protocol):
    """Read-only knowledge base status provider."""

    async def get_status(self) -> BaseStatus:
        """Return compact knowledge base status."""


class MaterialsReader(Protocol):
    """Read/update uploaded materials provider."""

    async def list_recent_materials(self, workspace_id: str, limit: int = 10) -> tuple[MaterialCard, ...]:
        """Return recent uploaded materials."""

    async def get_material(self, workspace_id: str, material_id_or_prefix: str) -> MaterialCard:
        """Return one uploaded material."""

    async def archive_material(self, workspace_id: str, material_id_or_prefix: str) -> MaterialCard:
        """Archive one uploaded material."""


@dataclass
class BotServices:
    """Telegram handler dependencies."""

    state_store: InMemoryBotUserStateStore = field(default_factory=InMemoryBotUserStateStore)
    settings_repo: Any = field(default_factory=InMemoryUserSettingsRepository)
    intake_buffer: MessageIntakeBuffer = field(default_factory=MessageIntakeBuffer)
    rag_pipeline: RagPipeline | None = None
    rag_runtime: Any | None = None
    rag_disabled_reason: str = ""
    rag_missing_config: tuple[str, ...] = ()
    ingestion_service: IntakeIngestionService | None = None
    ingestion_runtime: Any | None = None
    ingestion_disabled_reason: str = ""
    ingestion_missing_config: tuple[str, ...] = ()
    service_docs_status_provider: ServiceDocsStatusReader | None = None
    docs_preview_service: DocsPreviewReader | None = None
    base_status_provider: BaseStatusReader | None = None
    materials_provider: MaterialsReader | None = None
    conversation_repo: Any | None = None
    vision_textifier: Any | None = None
    download_dir: Path = Path("data/uploads/telegram")
    owner_ids: tuple[int, ...] = ()
    admin_ids: tuple[int, ...] = ()
    default_workspace_id: str = ""
    default_workspace_name: str = "team"
    embedding_model: str = "BAAI/bge-m3"
    reranker_mode: str = "identity"
    schema_version: str = "v2"
    model_lists: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def access_policy(self) -> UserAccessPolicy:
        """Return the current access policy."""
        return UserAccessPolicy(owner_ids=self.owner_ids, fallback_admin_ids=self.admin_ids)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/start`."""
    services = _services(context)
    user_id = _user_id(update)
    if user_id is not None:
        await services.settings_repo.get(user_id)
    if update.message is None:
        return

    await update.message.reply_text(
        (
            "Задавайте вопрос текстом, изображением или текстом вместе с изображением. "
            "Для новой темы нажмите «Новая тема». "
            "Для добавления базы знаний нажмите «Загрузить материал». "
            "Команды смотрите в /help."
        ),
        reply_markup=main_menu_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/help`."""
    if update.message is None:
        return

    await update.message.reply_text(
        "\n".join(
            [
                "Как работать:",
                "- задайте вопрос обычным сообщением;",
                "- /upload или «Загрузить материал» — загрузить материал в базу;",
                "- /base_status — статус базы знаний;",
                "- /docs — панель документации сервисов;",
                "- /docs_preview <id> — предпросмотр кандидата документации;",
                "- /materials — список загруженных материалов;",
                "- /material <id> — карточка материала;",
                "- /archive_material <id> — архивировать материал;",
                "- /source_last — источники последнего ответа;",
                "- /archive_source <id> — архивировать источник последнего ответа;",
                "- /services — найденные сервисы и документация;",
                "- /status — настройки и runtime-статус;",
                "- /new — новая тема;",
                "- /debug_last — последний debug, если нужен.",
            ]
        ),
        reply_markup=main_menu_keyboard(),
    )


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/new`."""
    await _start_new_topic(update, context)


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/upload`."""
    await _enter_upload_mode(update, context)


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/done`."""
    await _finish_upload_mode(update, context)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/status`."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    settings = await services.settings_repo.get(user_id)
    models = services.model_lists.get(settings.answer_mode, ())
    role = await services.access_policy.role_for(user_id)
    await update.message.reply_text(
        format_status(
            workspace=services.default_workspace_id or services.default_workspace_name,
            role=role,
            settings=settings,
            embedding_model=services.embedding_model,
            reranker_mode=services.reranker_mode,
            answer_models=models,
            supabase_connected=bool(services.default_workspace_id),
            schema_version=services.schema_version,
        ),
        reply_markup=main_menu_keyboard(),
    )


async def materials_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/materials`."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    if not _can_manage_materials(services, user_id):
        await update.message.reply_text("Просмотр материалов доступен владельцу бота.", reply_markup=main_menu_keyboard())
        return
    if services.materials_provider is None or not services.default_workspace_id:
        await update.message.reply_text(
            "Список материалов пока недоступен: не подключено чтение Supabase.",
            reply_markup=main_menu_keyboard(),
        )
        return
    try:
        materials = await services.materials_provider.list_recent_materials(services.default_workspace_id, limit=10)
    except Exception as exc:  # noqa: BLE001 - command must fail gracefully
        await update.message.reply_text(
            "Не получилось получить список материалов: " + _safe_error(exc),
            reply_markup=main_menu_keyboard(),
        )
        return
    await update.message.reply_text(format_materials_list(materials), reply_markup=main_menu_keyboard())


async def material_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/material <id>`."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    if not _can_manage_materials(services, user_id):
        await update.message.reply_text("Просмотр материалов доступен владельцу бота.", reply_markup=main_menu_keyboard())
        return
    material_id = _first_command_arg(update, context)
    if not material_id:
        await update.message.reply_text("Укажите id материала: /material <id>", reply_markup=main_menu_keyboard())
        return
    if services.materials_provider is None or not services.default_workspace_id:
        await update.message.reply_text(
            "Карточка материала пока недоступна: не подключено чтение Supabase.",
            reply_markup=main_menu_keyboard(),
        )
        return
    try:
        material = await services.materials_provider.get_material(services.default_workspace_id, material_id)
    except MaterialAmbiguousError:
        await update.message.reply_text(
            "Нашёл несколько материалов с таким id. Уточните id.",
            reply_markup=main_menu_keyboard(),
        )
        return
    except MaterialNotFoundError:
        await update.message.reply_text("Материал не найден.", reply_markup=main_menu_keyboard())
        return
    except Exception as exc:  # noqa: BLE001 - command must fail gracefully
        await update.message.reply_text(
            "Не получилось получить материал: " + _safe_error(exc),
            reply_markup=main_menu_keyboard(),
        )
        return
    await update.message.reply_text(format_material_card(material), reply_markup=main_menu_keyboard())


async def archive_material_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/archive_material <id>`."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    if not _can_archive_materials(services, user_id):
        await update.message.reply_text(
            "Архивирование доступно владельцу бота.",
            reply_markup=main_menu_keyboard(),
        )
        return
    material_id = _first_command_arg(update, context)
    if not material_id:
        await update.message.reply_text(
            "Укажите id материала: /archive_material <id>",
            reply_markup=main_menu_keyboard(),
        )
        return
    if services.materials_provider is None or not services.default_workspace_id:
        await update.message.reply_text(
            "Архивирование пока недоступно: не подключено чтение Supabase.",
            reply_markup=main_menu_keyboard(),
        )
        return
    try:
        material = await services.materials_provider.archive_material(services.default_workspace_id, material_id)
    except ExternalDocsArchiveError:
        await update.message.reply_text(
            "Официальную документацию нельзя архивировать через Telegram.",
            reply_markup=main_menu_keyboard(),
        )
        return
    except MaterialAmbiguousError:
        await update.message.reply_text(
            "Нашёл несколько материалов с таким id. Уточните id.",
            reply_markup=main_menu_keyboard(),
        )
        return
    except MaterialNotFoundError:
        await update.message.reply_text("Материал не найден.", reply_markup=main_menu_keyboard())
        return
    except Exception as exc:  # noqa: BLE001 - command must fail gracefully
        await update.message.reply_text(
            "Не получилось архивировать материал: " + _safe_error(exc),
            reply_markup=main_menu_keyboard(),
        )
        return
    await update.message.reply_text(format_material_archived(material), reply_markup=main_menu_keyboard())


async def source_last_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/source_last`."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    state = services.state_store.get(user_id)
    sources = last_answer_sources_from_debug(state.last_debug)
    await update.message.reply_text(format_last_answer_sources(sources), reply_markup=main_menu_keyboard())


async def archive_source_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/archive_source <id>`."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    if not _can_archive_materials(services, user_id):
        await update.message.reply_text(
            "Архивирование доступно владельцу бота.",
            reply_markup=main_menu_keyboard(),
        )
        return
    source_id = _first_command_arg(update, context)
    if not source_id:
        await update.message.reply_text(
            "Укажите id источника: /archive_source <id>",
            reply_markup=main_menu_keyboard(),
        )
        return
    sources = last_answer_sources_from_debug(services.state_store.get(user_id).last_debug)
    source = find_last_answer_source(sources, source_id)
    if source is None:
        await update.message.reply_text(
            "Такого источника нет в последнем ответе.",
            reply_markup=main_menu_keyboard(),
        )
        return
    if source.is_external:
        await update.message.reply_text(
            "Официальную документацию нельзя архивировать через Telegram.",
            reply_markup=main_menu_keyboard(),
        )
        return
    if services.materials_provider is None or not services.default_workspace_id:
        await update.message.reply_text(
            "Архивирование пока недоступно: не подключено чтение Supabase.",
            reply_markup=main_menu_keyboard(),
        )
        return
    try:
        material = await services.materials_provider.archive_material(services.default_workspace_id, source.document_id)
    except ExternalDocsArchiveError:
        await update.message.reply_text(
            "Официальную документацию нельзя архивировать через Telegram.",
            reply_markup=main_menu_keyboard(),
        )
        return
    except MaterialAmbiguousError:
        await update.message.reply_text(
            "Нашёл несколько материалов с таким id. Уточните id.",
            reply_markup=main_menu_keyboard(),
        )
        return
    except MaterialNotFoundError:
        await update.message.reply_text(
            "Материал уже не найден среди активных материалов.",
            reply_markup=main_menu_keyboard(),
        )
        return
    except Exception as exc:  # noqa: BLE001 - command must fail gracefully
        await update.message.reply_text(
            "Не получилось архивировать источник: " + _safe_error(exc),
            reply_markup=main_menu_keyboard(),
        )
        return
    await update.message.reply_text(format_source_archived(material.title or source.title), reply_markup=main_menu_keyboard())


async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/services`."""
    services = _services(context)
    if update.message is None:
        return
    if services.service_docs_status_provider is None:
        await update.message.reply_text(
            "Список сервисов пока недоступен: не подключено чтение Supabase или registry.",
            reply_markup=main_menu_keyboard(),
        )
        return
    try:
        statuses = await services.service_docs_status_provider.list_statuses(scan_corpus=True)
    except Exception as exc:  # noqa: BLE001 - command must fail gracefully
        await update.message.reply_text(
            "Не получилось получить список сервисов: " + _safe_error(exc),
            reply_markup=main_menu_keyboard(),
        )
        return
    await update.message.reply_text(_format_services_status(statuses), reply_markup=main_menu_keyboard())


async def docs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/docs`."""
    services = _services(context)
    user_id = _user_id(update)
    await send_docs_dashboard(
        update,
        status_provider=services.service_docs_status_provider,
        is_allowed=user_id is not None and _can_use_docs_dashboard(services, user_id),
        reply_markup=main_menu_keyboard(),
        safe_error=_safe_error,
    )


async def docs_preview_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/docs_preview`."""
    services = _services(context)
    user_id = _user_id(update)
    await send_docs_preview(
        update,
        service_id_or_alias=_first_command_arg(update, context),
        is_allowed=user_id is not None and _can_use_docs_dashboard(services, user_id),
        preview_service=services.docs_preview_service,
        reply_markup=main_menu_keyboard(),
        safe_error=_safe_error,
    )


async def base_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/base_status`."""
    services = _services(context)
    if update.message is None:
        return
    if services.base_status_provider is None:
        await update.message.reply_text(
            "Статус базы пока недоступен: не подключено чтение Supabase.",
            reply_markup=main_menu_keyboard(),
        )
        return
    try:
        status = await services.base_status_provider.get_status()
    except Exception as exc:  # noqa: BLE001 - command must fail gracefully
        await update.message.reply_text(
            "Не получилось получить статус базы: " + _safe_error(exc),
            reply_markup=main_menu_keyboard(),
        )
        return
    await update.message.reply_text(format_base_status(status), reply_markup=main_menu_keyboard())


async def debug_last_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/debug_last`."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    state = services.state_store.get(user_id)
    if not state.last_debug:
        await update.message.reply_text(
            "Пока нет debug-данных по последнему ответу.",
            reply_markup=main_menu_keyboard(),
        )
        return
    await update.message.reply_text(_format_debug_summary(state.last_debug)[:3500], reply_markup=main_menu_keyboard())


CommandFallbackHandler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

_TEXT_COMMAND_HANDLERS: dict[str, CommandFallbackHandler] = {
    "start": start,
    "help": help_command,
    "new": new_command,
    "upload": upload_command,
    "done": done_command,
    "status": status_command,
    "materials": materials_command,
    "material": material_command,
    "archive_material": archive_material_command,
    "source_last": source_last_command,
    "archive_source": archive_source_command,
    "services": services_command,
    "docs": docs_command,
    "docs_preview": docs_preview_command,
    "base_status": base_status_command,
    "debug_last": debug_last_command,
}


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages and reply keyboard buttons."""
    if update.message is None or update.message.text is None:
        return

    text = update.message.text.strip()
    if text.startswith("/"):
        await _handle_text_command_fallback(update, context, text)
        return

    if text == BTN_NEW_TOPIC:
        await _start_new_topic(update, context)
        return
    if text == BTN_UPLOAD_MATERIAL:
        await _enter_upload_mode(update, context)
        return
    if text == BTN_SETTINGS:
        await _show_settings(update, context)
        return
    if text == BTN_DONE:
        await _finish_upload_mode(update, context)
        return
    if text == BTN_CANCEL:
        await _cancel_upload_mode(update, context)
        return

    services = _services(context)
    user_id = _user_id(update)
    if user_id is None:
        return
    state = services.state_store.get(user_id)
    if state.mode == "upload_material":
        await update.message.reply_text(
            "Сейчас включен режим загрузки материалов. Отправьте файл или нажмите «Готово».",
            reply_markup=upload_menu_keyboard(),
        )
        return

    services.intake_buffer.add_text(user_id, text, message_id=update.message.message_id)
    settings = await services.settings_repo.get(user_id)
    intake = services.intake_buffer.build_intake(
        user_id,
        conversation_id=state.active_conversation_id,
        user_settings=_settings_dict(settings),
    )
    await _answer_intake(update, services, intake)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle images as material uploads or question context."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    state = services.state_store.get(user_id)
    settings = await services.settings_repo.get(user_id)
    image_path = await _download_photo(update, services)

    if state.mode == "upload_material":
        await _ingest_or_count_upload(update, services, image_path)
        return

    vision_text = ""
    vision_error = ""
    if settings.vision_mode == "auto" and services.vision_textifier is not None:
        try:
            vision_text = await services.vision_textifier.describe_image(image_path, answer_mode=settings.answer_mode)
        except Exception as exc:  # noqa: BLE001
            vision_error = str(exc)

    draft = services.intake_buffer.add_image(
        user_id,
        image_path,
        caption=update.message.caption or "",
        vision_text=vision_text,
        vision_error=vision_error,
        message_id=update.message.message_id,
        media_group_id=update.message.media_group_id,
    )
    if not draft.text_parts:
        await update.message.reply_text("Добавьте вопрос к изображению, чтобы я понял, что нужно разобрать.")
        return

    intake = services.intake_buffer.build_intake(
        user_id,
        conversation_id=state.active_conversation_id,
        user_settings=_settings_dict(settings),
    )
    await _answer_intake(update, services, intake)


async def _handle_text_command_fallback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    """Route slash commands that reached the text handler before RAG."""
    if update.message is None:
        return
    command = _extract_text_command(text)
    handler = _TEXT_COMMAND_HANDLERS.get(command)
    if handler is None:
        await update.message.reply_text(
            "Неизвестная команда. Список команд — /help.",
            reply_markup=main_menu_keyboard(),
        )
        return
    await handler(update, context)


def _extract_text_command(text: str) -> str:
    token = text.strip().split(maxsplit=1)[0]
    command = token.removeprefix("/")
    command = command.split("@", 1)[0]
    return command.casefold()


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle documents in upload mode."""
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    state = services.state_store.get(user_id)
    if state.mode != "upload_material":
        await update.message.reply_text(
            "Это материал для базы или файл к вопросу? Для MVP нажмите «Загрузить материал» и отправьте файл ещё раз.",
            reply_markup=main_menu_keyboard(),
        )
        return
    file_path = await _download_document(update, services)
    await _ingest_or_count_upload(update, services, file_path)


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline settings callbacks."""
    query = update.callback_query
    user_id = _user_id(update)
    if query is None or user_id is None:
        return
    services = _services(context)
    data = query.data or ""
    if data == CALLBACK_SETTINGS_BACK:
        await query.answer()
        await query.edit_message_text("Настройки закрыты.")
        return

    if data in {CALLBACK_MODE_FREE, CALLBACK_MODE_CHEAP, CALLBACK_MODE_QUALITY}:
        mode = data.rsplit(":", 1)[-1]
        settings = await services.settings_repo.set_answer_mode(user_id, mode)
    elif data in {CALLBACK_VISION_AUTO, CALLBACK_VISION_OFF}:
        mode = data.rsplit(":", 1)[-1]
        settings = await services.settings_repo.set_vision_mode(user_id, mode)
    elif data in {CALLBACK_DEBUG_ON, CALLBACK_DEBUG_OFF}:
        settings = await services.settings_repo.set_debug_mode(user_id, data.endswith(":on"))
    else:
        await query.answer("Неизвестная настройка")
        return

    await query.answer("Настройки обновлены")
    await query.edit_message_text(_settings_text(settings), reply_markup=settings_inline_keyboard(
        answer_mode=settings.answer_mode,
        vision_mode=settings.vision_mode,
        debug_mode=settings.debug_mode,
    ))


def register_handlers(application: Application, services: BotServices | None = None) -> None:
    """Register bot handlers."""
    application.bot_data["services"] = services or BotServices()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("new", new_command))
    application.add_handler(CommandHandler("upload", upload_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("materials", materials_command))
    application.add_handler(CommandHandler("material", material_command))
    application.add_handler(CommandHandler("archive_material", archive_material_command))
    application.add_handler(CommandHandler("source_last", source_last_command))
    application.add_handler(CommandHandler("archive_source", archive_source_command))
    application.add_handler(CommandHandler("services", services_command))
    application.add_handler(CommandHandler("docs", docs_command))
    application.add_handler(CommandHandler("docs_preview", docs_preview_command))
    application.add_handler(CommandHandler("base_status", base_status_command))
    application.add_handler(CommandHandler("debug_last", debug_last_command))
    application.add_handler(CallbackQueryHandler(handle_settings_callback, pattern=r"^settings:"))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))


async def _start_new_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    user_id = _user_id(update)
    if user_id is not None:
        state = services.state_store.clear_context(user_id)
        services.intake_buffer.clear(user_id)
        if services.conversation_repo is not None and services.default_workspace_id:
            try:
                await services.conversation_repo.close_active_conversations(user_id, services.default_workspace_id)
                conversation = await services.conversation_repo.create_conversation(
                    telegram_user_id=user_id,
                    workspace_id=services.default_workspace_id,
                    title="Telegram topic",
                )
                state.active_conversation_id = str(conversation.get("id") or "")
            except Exception:  # noqa: BLE001 - Telegram UX should keep working if persistence is unavailable
                state.active_conversation_id = None
    if update.message is not None:
        await update.message.reply_text(
            "Новая тема начата. Можете отправить вопрос текстом, изображением или текстом вместе с изображением.",
            reply_markup=main_menu_keyboard(),
        )


async def _enter_upload_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    user_id = _user_id(update)
    if user_id is not None and not _can_upload(services, user_id):
        if update.message is not None:
            await update.message.reply_text(
                "Загрузка материалов доступна владельцу бота. Вопросы можно задавать в обычном режиме.",
                reply_markup=main_menu_keyboard(),
            )
        return
    if user_id is not None:
        services.state_store.set_mode(user_id, "upload_material")
    if update.message is not None:
        await update.message.reply_text(
            (
                "Отправьте файл с материалом. Можно загрузить PDF, TXT, MD, JSON или изображение. "
                "Когда закончите, нажмите Готово."
            ),
            reply_markup=upload_menu_keyboard(),
        )


async def _finish_upload_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    user_id = _user_id(update)
    if user_id is not None:
        services.state_store.set_mode(user_id, "normal")
    if update.message is not None:
        await update.message.reply_text(
            "Готово. Материалы добавлены в базу. Теперь можно задавать вопросы.",
            reply_markup=main_menu_keyboard(),
        )


async def _cancel_upload_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    user_id = _user_id(update)
    if user_id is not None:
        services.state_store.set_mode(user_id, "normal")
    if update.message is not None:
        await update.message.reply_text("Загрузка отменена.", reply_markup=main_menu_keyboard())


async def _show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    settings = await services.settings_repo.get(user_id)
    await update.message.reply_text(
        _settings_text(settings),
        reply_markup=settings_inline_keyboard(
            answer_mode=settings.answer_mode,
            vision_mode=settings.vision_mode,
            debug_mode=settings.debug_mode,
        ),
    )


async def _ingest_or_count_upload(update: Update, services: BotServices, file_path: Path) -> None:
    user_id = _user_id(update)
    if update.message is None or user_id is None:
        return
    state = services.state_store.get(user_id)
    if services.ingestion_service is None:
        reason = services.ingestion_disabled_reason or (
            "Загрузка материалов не подключена: не хватает настроек окружения. Проверьте .env."
        )
        await update.message.reply_text(reason, reply_markup=upload_menu_keyboard())
        return
    try:
        await update.message.reply_text(
            "Файл получен. Обрабатываю и добавляю в базу...",
            reply_markup=upload_menu_keyboard(),
        )
        results = await services.ingestion_service.ingest_path(
            file_path,
            workspace=services.default_workspace_name,
        )
        service_statuses = await _upload_service_statuses(services)
        state.uploaded_materials += 1
        await update.message.reply_text(
            _format_upload_results(results, service_statuses=service_statuses),
            reply_markup=upload_menu_keyboard(),
        )
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(
            "Не получилось загрузить материал: " + _safe_error(exc),
            reply_markup=upload_menu_keyboard(),
        )


async def _answer_intake(update: Update, services: BotServices, intake: UserIntake) -> None:
    if update.message is None:
        return
    user_id = _user_id(update)
    question = intake.combined_question()
    if not question or (not intake.text.strip() and (intake.images or intake.vision_text)):
        await update.message.reply_text("Пришлите вопрос текстом или добавьте подпись к изображению.")
        return
    if services.rag_pipeline is None:
        reason = services.rag_disabled_reason or (
            "RAG v2 pipeline не подключён: не хватает настроек окружения. Проверьте .env."
        )
        await update.message.reply_text(
            reason,
            reply_markup=main_menu_keyboard(),
        )
        return
    result = await services.rag_pipeline.answer(
        question,
        workspace_id=str(intake.user_settings.get("selected_workspace_id") or services.default_workspace_id),
        dialog_context=_dialog_context(intake),
    )
    await update.message.reply_text(
        format_for_telegram(result.answer),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    if user_id is not None:
        state = services.state_store.get(user_id)
        state.last_debug = {
            "status": str(getattr(result, "status", "")),
            "sources": SourceLabelBuilder().build_many(getattr(result, "sources", ())),
            "source_refs": source_refs_to_debug_payload(getattr(result, "sources", ())),
            "vision_errors": list(intake.vision_errors),
            "rag": getattr(result, "debug", {}) or {},
        }
        if bool(intake.user_settings.get("debug_mode")):
            await update.message.reply_text(_format_debug_summary(state.last_debug)[:3500], reply_markup=main_menu_keyboard())


async def _download_photo(update: Update, services: BotServices) -> Path:
    if update.message is None or not update.message.photo:
        raise RuntimeError("Photo message is empty")
    user_id = _user_id(update) or 0
    photo = update.message.photo[-1]
    path = services.download_dir / str(user_id) / f"photo-{update.message.message_id}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    telegram_file = await photo.get_file()
    await telegram_file.download_to_drive(custom_path=path)
    return path


async def _download_document(update: Update, services: BotServices) -> Path:
    if update.message is None or update.message.document is None:
        raise RuntimeError("Document message is empty")
    user_id = _user_id(update) or 0
    document = update.message.document
    filename = _safe_filename(document.file_name or f"document-{update.message.message_id}")
    path = services.download_dir / str(user_id) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    telegram_file = await document.get_file()
    await telegram_file.download_to_drive(custom_path=path)
    return path


def _safe_filename(filename: str) -> str:
    clean = Path(filename).name.strip() or "telegram-file"
    clean = re.sub(r"[^A-Za-z0-9А-Яа-яЁё._ -]+", "_", clean)
    return clean[:160] or "telegram-file"


async def _upload_service_statuses(services: BotServices) -> dict[str, ServiceDocsStatus]:
    provider = services.service_docs_status_provider
    if provider is None:
        return {}
    try:
        statuses = await provider.list_statuses(scan_corpus=False)
    except Exception:  # noqa: BLE001 - service status is optional upload context
        return {}
    return {status.service_id: status for status in statuses}


def _format_upload_results(
    results: list[Any],
    *,
    service_statuses: dict[str, ServiceDocsStatus] | None = None,
) -> str:
    if not results:
        return "Материал не был загружен: ingestion не вернул результат."
    if len(results) == 1:
        return _format_upload_result(results[0], service_statuses=service_statuses)
    total_sections = sum(int(getattr(result, "sections_count", 0) or 0) for result in results)
    total_chunks = sum(int(getattr(result, "chunks_count", 0) or 0) for result in results)
    statuses = _dedupe_strings(str(getattr(result, "term_statistics_status", "skipped")) for result in results)
    service_lines = _format_upload_services(results, service_statuses=service_statuses)
    return "\n".join(
        [
            "Файлы обработаны и добавлены в базу.",
            f"Файлов: {len(results)}",
            f"Разделов: {total_sections}",
            f"Чанков: {total_chunks}",
            "",
            "Найдены сервисы:",
            *service_lines,
            "",
            "Теперь можно задавать вопросы по этим материалам.",
            "Embeddings: ok",
            "Term statistics: " + ", ".join(statuses),
        ]
    )


def _format_upload_result(
    result: Any,
    *,
    service_statuses: dict[str, ServiceDocsStatus] | None = None,
) -> str:
    document_label = str(getattr(result, "document_key", "") or getattr(result, "document_id", "") or "unknown")
    headline = (
        "Файл уже был обработан раньше, изменений нет."
        if bool(getattr(result, "skipped", False))
        else "Файл обработан и добавлен в базу."
    )
    return "\n".join(
        [
            headline,
            f"Документ: {document_label}",
            f"Разделов: {int(getattr(result, 'sections_count', 0) or 0)}",
            f"Чанков: {int(getattr(result, 'chunks_count', 0) or 0)}",
            "",
            "Найдены сервисы:",
            *_format_upload_services([result], service_statuses=service_statuses),
            "",
            "Теперь можно задавать вопросы по этому материалу.",
            "Embeddings: ok",
            f"Term statistics: {getattr(result, 'term_statistics_status', 'skipped')}",
        ]
    )


def _format_upload_services(
    results: list[Any],
    *,
    service_statuses: dict[str, ServiceDocsStatus] | None = None,
) -> list[str]:
    service_statuses = service_statuses or {}
    labels: dict[str, str] = {}
    for result in results:
        for mention in _upload_service_mentions(result):
            service_id = str(mention.get("service_id") or "").strip()
            if not service_id:
                continue
            display_name = str(mention.get("display_name") or service_id).strip() or service_id
            labels.setdefault(service_id, display_name)
        for service_id in _upload_service_ids(result):
            labels.setdefault(service_id, service_id)
    if not labels:
        return ["сервисы не найдены"]
    lines: list[str] = []
    for service_id, display_name in sorted(labels.items(), key=lambda item: item[1].casefold()):
        status = service_statuses.get(service_id)
        display_name = str(status.display_name or display_name) if status is not None else display_name
        suffix = f" — {_service_docs_phrase(status)}" if status is not None else ""
        lines.append(f"{display_name}{suffix}")
    return lines


def _upload_service_ids(result: Any) -> list[str]:
    values = getattr(result, "service_ids", ()) or ()
    if not isinstance(values, (list, tuple, set)):
        return []
    return _dedupe_strings(str(value).strip() for value in values if str(value).strip())


def _upload_service_mentions(result: Any) -> list[dict[str, object]]:
    values = getattr(result, "service_mentions", ()) or ()
    if not isinstance(values, (list, tuple)):
        return []
    mentions: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        service_id = str(value.get("service_id") or "").strip()
        if not service_id or service_id in seen:
            continue
        seen.add(service_id)
        mentions.append(value)
    return mentions


def _format_services_status(statuses: tuple[ServiceDocsStatus, ...]) -> str:
    if not statuses:
        return "Сервисы пока не настроены."
    lines = ["Сервисы:"]
    for status in statuses:
        found = _service_found_in_base(status)
        docs = _service_docs_phrase(status)
        quality = f", {status.quality_status}" if status.quality_status not in {"", "none"} else ""
        lines.append(f"{status.display_name} — {'найден в базе' if found else 'не найден в базе'}, {docs}{quality}")
    return "\n".join(lines)


def _service_found_in_base(status: ServiceDocsStatus) -> bool:
    return any(
        value > 0
        for value in (
            int(status.detected_documents_count or 0),
            int(status.detected_chunks_count or 0),
            int(status.mention_count or 0),
            int(status.active_docs_count or 0),
        )
    )


def _service_docs_phrase(status: ServiceDocsStatus) -> str:
    if status.docs_status == "indexed":
        return "документация подключена"
    if status.docs_status == "not_configured":
        return "документация не подключена"
    if status.docs_status == "configured_not_indexed":
        return "документация настроена, но не проиндексирована"
    if status.docs_status == "disabled":
        return "документация отключена"
    return "документация требует проверки"


def _safe_error(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip() or exc.__class__.__name__
    text = re.sub(r"bot[0-9]{6,}(?::|%3[Aa])[A-Za-z0-9_-]+", "bot<redacted>", text)
    text = re.sub(r"\b[0-9]{6,}(?::|%3[Aa])[A-Za-z0-9_-]{20,}\b", "<telegram-token-redacted>", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", text)
    text = re.sub(r"\bsk-or-v1-[A-Za-z0-9_-]+", "sk-or-v1-<redacted>", text)
    text = re.sub(r"sb_secret_[A-Za-z0-9_-]+", "sb_secret_<redacted>", text)
    return text[:700]


def _dedupe_strings(items: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = str(item).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _can_upload(services: BotServices, telegram_user_id: int) -> bool:
    policy = services.access_policy
    return (
        policy.open_access()
        or telegram_user_id in set(policy.owner_ids)
        or telegram_user_id in set(policy.fallback_admin_ids)
    )


def _can_manage_materials(services: BotServices, telegram_user_id: int) -> bool:
    return _can_archive_materials(services, telegram_user_id)


def _can_archive_materials(services: BotServices, telegram_user_id: int) -> bool:
    policy = services.access_policy
    return telegram_user_id in set(policy.owner_ids) or telegram_user_id in set(policy.fallback_admin_ids)


def _can_use_docs_dashboard(services: BotServices, telegram_user_id: int) -> bool:
    policy = services.access_policy
    return telegram_user_id in set(policy.owner_ids) or telegram_user_id in set(policy.fallback_admin_ids)


def _first_command_arg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    args = getattr(context, "args", None)
    if args:
        return str(args[0]).strip()
    if update.message is None or not update.message.text:
        return ""
    parts = update.message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip().split(maxsplit=1)[0] if parts[1].strip() else ""


def _services(context: ContextTypes.DEFAULT_TYPE) -> BotServices:
    return context.application.bot_data.setdefault("services", BotServices())


def _user_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def _settings_dict(settings: UserSettings) -> dict[str, Any]:
    return {
        "answer_mode": settings.answer_mode,
        "vision_mode": settings.vision_mode,
        "debug_mode": settings.debug_mode,
        "selected_workspace_id": settings.selected_workspace_id,
    }


def _dialog_context(intake: UserIntake) -> dict[str, object]:
    blocked = ("candidate", "discarded", "raw", "retrieval")
    clean_settings = {
        str(key): value
        for key, value in intake.user_settings.items()
        if not any(marker in str(key).lower() for marker in blocked)
    }
    return {
        "user_settings": clean_settings,
        "vision_errors": intake.vision_errors,
    }


def _format_debug_summary(debug: dict[str, Any]) -> str:
    status = str(debug.get("status") or "unknown").replace("AnswerStatus.", "")
    sources = [str(source) for source in debug.get("sources", []) if str(source).strip()]
    vision_errors = [str(error) for error in debug.get("vision_errors", []) if str(error).strip()]
    lines = [f"Debug: status={status}"]
    lines.append("sources=" + (", ".join(sources) if sources else "none"))
    if vision_errors:
        lines.append("vision_errors=" + "; ".join(vision_errors[:3]))
    rag = debug.get("rag")
    if isinstance(rag, dict) and rag:
        answer_mode = rag.get("answer_mode")
        if answer_mode:
            lines.append(f"answer_mode={answer_mode}")
        query_plan = rag.get("query_plan") if isinstance(rag.get("query_plan"), dict) else {}
        expected = rag.get("expected_content_types") or query_plan.get("expected_content_types", [])
        if expected:
            lines.append("expected_content_types=" + ", ".join(str(item) for item in expected[:5]))
        course_hint = rag.get("course_hint") or query_plan.get("course_hint")
        if course_hint:
            lines.append(f"course_hint={course_hint}")
        documents = rag.get("selected_documents")
        if isinstance(documents, list) and documents:
            lines.append("documents:")
            for document in documents[:5]:
                if not isinstance(document, dict):
                    continue
                title = document.get("title") or document.get("filename") or document.get("document_id")
                title = document.get("clean_label") or SourceLabelBuilder().build_document_label(document) or title
                score = document.get("score")
                penalties = document.get("penalties") or []
                penalty_text = f" penalties={','.join(str(item) for item in penalties[:3])}" if penalties else ""
                lines.append(f"- {title} score={score}{penalty_text}")
        decisions = rag.get("accepted_decisions")
        if isinstance(decisions, list) and decisions:
            lines.append("accepted_evidence:")
            for decision in decisions[:5]:
                if isinstance(decision, dict):
                    lines.append(
                        f"- {decision.get('evidence_id')} {decision.get('status')} "
                        f"reasons={','.join(str(item) for item in (decision.get('reasons') or [])[:3])}"
                    )
        discarded = rag.get("discarded_decisions") or rag.get("discarded_evidence")
        if isinstance(discarded, list) and discarded:
            lines.append("discarded:")
            for item in discarded[:5]:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('evidence_id') or item.get('chunk_id')} reason={item.get('reason') or item.get('reasons')}")
    return "\n".join(lines)


def _settings_text(settings: UserSettings) -> str:
    debug = "вкл" if settings.debug_mode else "выкл"
    return "\n".join(
        [
            "Настройки",
            f"Режим ответа: {settings.answer_mode}",
            f"Vision: {settings.vision_mode}",
            f"Debug: {debug}",
        ]
    )
