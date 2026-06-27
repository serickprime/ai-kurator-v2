"""Telegram command and message handlers."""

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/start`."""
    del context
    if update.message is None:
        return

    await update.message.reply_text(
        "Привет. Это каркас новой версии AI Kurator с evidence-first RAG."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle `/help`."""
    del context
    if update.message is None:
        return

    await update.message.reply_text(
        "Пока готова основа проекта. Дальше здесь появятся загрузка материалов, поиск evidence и ответы по источникам."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular user messages until the RAG pipeline is implemented."""
    del context
    if update.message is None:
        return

    await update.message.reply_text(
        "Я пока не отвечаю по базе: RAG-пайплайн еще не подключен."
    )


def register_handlers(application: Application) -> None:
    """Register bot handlers."""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
