"""Telegram keyboard builders."""

from telegram import ReplyKeyboardRemove


def remove_keyboard() -> ReplyKeyboardRemove:
    """Return a keyboard removal marker."""
    return ReplyKeyboardRemove()
