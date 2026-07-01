from pathlib import Path

from scripts.check_tracked_secrets import find_secret_hits


def test_realistic_secret_without_safe_marker_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("token = '" + _supabase_prefix() + "realisticValue123'\n", encoding="utf-8")

    hits = find_secret_hits([path])

    assert len(hits) == 1
    assert hits[0].kind == "supabase secret key"
    assert str(path) in hits[0].format()


def test_redaction_regex_is_allowed(tmp_path: Path) -> None:
    path = tmp_path / "logging_config.py"
    path.write_text(
        "_SUPABASE_SECRET_RE = re.compile(r'\\b" + _supabase_prefix() + "[A-Za-z0-9_-]+')\n",
        encoding="utf-8",
    )

    assert find_secret_hits([path]) == []


def test_fake_value_in_tests_is_allowed(tmp_path: Path) -> None:
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    path = test_dir / "test_redaction.py"
    path.write_text("value = '" + _openrouter_prefix() + "fakeOpenRouterKey123'\n", encoding="utf-8")

    assert find_secret_hits([path]) == []


def test_redacted_value_is_allowed(tmp_path: Path) -> None:
    path = tmp_path / "redaction.py"
    path.write_text("safe = 'Bearer <redacted>'\n", encoding="utf-8")

    assert find_secret_hits([path]) == []


def test_bearer_and_telegram_tokens_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "settings.py"
    telegram_token = "123456789:" + "RealisticTelegramToken123"
    path.write_text(
        "headers = {'Authorization': 'Bearer " + "realistic.access.token'}\n"
        f"telegram = '{telegram_token}'\n",
        encoding="utf-8",
    )

    hits = find_secret_hits([path])

    assert [hit.kind for hit in hits] == ["bearer token", "telegram token"]


def test_split_secret_prefix_in_sanitizer_code_is_allowed(tmp_path: Path) -> None:
    path = tmp_path / "sanitizer.py"
    path.write_text("prefix = 'sb_' + 'secret_'\n", encoding="utf-8")

    assert find_secret_hits([path]) == []


def _supabase_prefix() -> str:
    return "sb_" + "secret_"


def _openrouter_prefix() -> str:
    return "sk-" + "or-v1-"
