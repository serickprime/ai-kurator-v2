"""Telegram keyboard builders."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_NEW_TOPIC = "Новая тема"
BTN_UPLOAD_MATERIAL = "Загрузить материал"
BTN_SETTINGS = "Настройки"
BTN_DONE = "Готово"
BTN_CANCEL = "Отмена"

CALLBACK_MODE_FREE = "settings:answer_mode:free"
CALLBACK_MODE_CHEAP = "settings:answer_mode:cheap"
CALLBACK_MODE_QUALITY = "settings:answer_mode:quality"
CALLBACK_VISION_AUTO = "settings:vision:auto"
CALLBACK_VISION_OFF = "settings:vision:off"
CALLBACK_DEBUG_ON = "settings:debug:on"
CALLBACK_DEBUG_OFF = "settings:debug:off"
CALLBACK_SETTINGS_BACK = "settings:back"
CALLBACK_DOCS_CONNECTED = "docs:connected"
CALLBACK_DOCS_CANDIDATES = "docs:candidates"
CALLBACK_DOCS_PREVIEW_HELP = "docs:preview_help"
CALLBACK_DOCS_HELP = "docs:help"
CALLBACK_DOCS_BACK = "docs:back"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Return the persistent main menu keyboard."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_NEW_TOPIC), KeyboardButton(BTN_UPLOAD_MATERIAL)],
            [KeyboardButton(BTN_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def upload_menu_keyboard() -> ReplyKeyboardMarkup:
    """Return the upload-material mode keyboard."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_DONE), KeyboardButton(BTN_CANCEL)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def settings_inline_keyboard(
    *,
    answer_mode: str = "cheap",
    vision_mode: str = "auto",
    debug_mode: bool = False,
) -> InlineKeyboardMarkup:
    """Return compact settings inline keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(_mark("Режим: Бесплатно", answer_mode == "free"), callback_data=CALLBACK_MODE_FREE),
                InlineKeyboardButton(_mark("Режим: Дешево", answer_mode == "cheap"), callback_data=CALLBACK_MODE_CHEAP),
            ],
            [
                InlineKeyboardButton(_mark("Режим: Качество", answer_mode == "quality"), callback_data=CALLBACK_MODE_QUALITY),
            ],
            [
                InlineKeyboardButton(_mark("Vision: Авто", vision_mode == "auto"), callback_data=CALLBACK_VISION_AUTO),
                InlineKeyboardButton(_mark("Vision: Выкл", vision_mode == "off"), callback_data=CALLBACK_VISION_OFF),
            ],
            [
                InlineKeyboardButton(_mark("Debug: Вкл", debug_mode), callback_data=CALLBACK_DEBUG_ON),
                InlineKeyboardButton(_mark("Debug: Выкл", not debug_mode), callback_data=CALLBACK_DEBUG_OFF),
            ],
            [InlineKeyboardButton("Назад", callback_data=CALLBACK_SETTINGS_BACK)],
        ]
    )


def docs_registry_inline_keyboard() -> InlineKeyboardMarkup:
    """Return the read-only docs dashboard inline wizard keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Подключённые", callback_data=CALLBACK_DOCS_CONNECTED),
                InlineKeyboardButton("Можно подключить", callback_data=CALLBACK_DOCS_CANDIDATES),
            ],
            [
                InlineKeyboardButton("Проверить сервис", callback_data=CALLBACK_DOCS_PREVIEW_HELP),
                InlineKeyboardButton("Помощь", callback_data=CALLBACK_DOCS_HELP),
            ],
        ]
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    """Return a keyboard removal marker."""
    return ReplyKeyboardRemove()


def _mark(label: str, selected: bool) -> str:
    return f"{label} ✓" if selected else label
