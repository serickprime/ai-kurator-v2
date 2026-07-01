import logging

from app.logging_config import RedactingFormatter, _redact_secrets


def test_bot_api_url_token_is_masked() -> None:
    url = "https://api.telegram.org/bot123456789:ABCdef_123/getUpdates"

    redacted = _redact_secrets(url)

    assert "123456789:ABCdef_123" not in redacted
    assert "bot<redacted>" in redacted


def test_file_download_url_token_is_masked() -> None:
    url = "https://api.telegram.org/file/bot123456789:ABCdef_123/documents/file.txt"

    redacted = _redact_secrets(url)

    assert "123456789:ABCdef_123" not in redacted
    assert "file/bot<redacted>/documents" in redacted


def test_url_encoded_token_is_masked() -> None:
    url = "https://api.telegram.org/bot123456789%3AABCdef_123/getMe"

    redacted = _redact_secrets(url)

    assert "123456789%3AABCdef_123" not in redacted
    assert "bot<redacted>" in redacted


def test_raw_telegram_token_is_masked() -> None:
    text = "token=123456789:ABCdef_1234567890_secret"

    redacted = _redact_secrets(text)

    assert "123456789:ABCdef_1234567890_secret" not in redacted
    assert "<telegram-token-redacted>" in redacted


def test_bearer_token_is_masked() -> None:
    text = "Authorization: Bearer fake.access.token_123"

    redacted = _redact_secrets(text)

    assert "fake.access.token_123" not in redacted
    assert "Bearer <redacted>" in redacted


def test_openrouter_key_is_masked() -> None:
    text = "OPENROUTER_API_KEY=sk-or-v1-fakeOpenRouterKey123"

    redacted = _redact_secrets(text)

    assert "sk-or-v1-fakeOpenRouterKey123" not in redacted
    assert "sk-or-v1-<redacted>" in redacted


def test_supabase_secret_key_is_masked() -> None:
    text = "SUPABASE_SERVICE_ROLE_KEY=sb_secret_fakeSupabaseKey123"

    redacted = _redact_secrets(text)

    assert "sb_secret_fakeSupabaseKey123" not in redacted
    assert "sb_secret_<redacted>" in redacted


def test_redacting_formatter_does_not_print_secret() -> None:
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="GET https://api.telegram.org/file/bot123456789:ABCdef_123/documents/file.txt",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)

    assert "ABCdef_123" not in formatted
    assert "bot<redacted>" in formatted
