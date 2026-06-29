"""Telegram command, menu, and message handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Any, Protocol

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.bot.access import UserAccessPolicy
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
from app.bot.user_state import InMemoryBotUserStateStore, InMemoryUserSettingsRepository
from app.db.repositories import UserSettings


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
            "Для добавления базы знаний нажмите «Загрузить материал»."
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
                "- задайте вопрос текстом;",
                "- отправьте скрин с подписью или сначала скрин, потом уточнение;",
                "- для загрузки базы нажмите «Загрузить материал»;",
                "- для нового диалога нажмите «Новая тема»;",
                "- настройки открываются кнопкой «Настройки».",
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
    del context
    if update.message is None:
        return
    await update.message.reply_text(
        (
            "Просмотр материалов будет подключен к Supabase documents/document_cards. "
            "Сейчас используйте загрузку материалов."
        ),
        reply_markup=main_menu_keyboard(),
    )


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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages and reply keyboard buttons."""
    if update.message is None or update.message.text is None:
        return

    text = update.message.text.strip()
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
        results = await services.ingestion_service.ingest_path(
            file_path,
            workspace=services.default_workspace_name,
        )
        state.uploaded_materials += 1
        await update.message.reply_text(_format_upload_results(results), reply_markup=upload_menu_keyboard())
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
            "sources": [getattr(source, "document_title", "") for source in getattr(result, "sources", ())],
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


def _format_upload_results(results: list[Any]) -> str:
    if not results:
        return "Материал не был загружен: ingestion не вернул результат."
    if len(results) == 1:
        return _format_upload_result(results[0])
    total_sections = sum(int(getattr(result, "sections_count", 0) or 0) for result in results)
    total_chunks = sum(int(getattr(result, "chunks_count", 0) or 0) for result in results)
    statuses = _dedupe_strings(str(getattr(result, "term_statistics_status", "skipped")) for result in results)
    return "\n".join(
        [
            "Материалы загружены.",
            f"Файлов: {len(results)}",
            f"Разделов: {total_sections}",
            f"Чанков: {total_chunks}",
            "Embeddings: ok",
            "Term statistics: " + ", ".join(statuses),
        ]
    )


def _format_upload_result(result: Any) -> str:
    document_label = str(getattr(result, "document_key", "") or getattr(result, "document_id", "") or "unknown")
    headline = (
        "Материал уже был загружен без изменений."
        if bool(getattr(result, "skipped", False))
        else "Материал загружен."
    )
    return "\n".join(
        [
            headline,
            f"Документ: {document_label}",
            f"Разделов: {int(getattr(result, 'sections_count', 0) or 0)}",
            f"Чанков: {int(getattr(result, 'chunks_count', 0) or 0)}",
            "Embeddings: ok",
            f"Term statistics: {getattr(result, 'term_statistics_status', 'skipped')}",
        ]
    )


def _safe_error(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip() or exc.__class__.__name__
    text = re.sub(r"bot[0-9]{6,}(?::|%3[Aa])[A-Za-z0-9_-]+", "bot<redacted>", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", text)
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
