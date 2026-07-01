from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.run_telegram_bot import PollingLock, RunnerDependencies, run_bot
from scripts.runtime_healthcheck import HealthLine, HealthReport


def test_fail_healthcheck_does_not_start_bot(tmp_path: Path) -> None:
    calls: list[str] = []
    output: list[str] = []

    def bot_runner() -> None:
        calls.append("started")

    exit_code = run_bot(
        _settings(tmp_path),
        dependencies=RunnerDependencies(
            bot_runner=bot_runner,
            healthcheck_runner=lambda settings: _report("fail"),
            output=output.append,
        ),
        lock_path=tmp_path / "telegram_bot.pid",
    )

    assert exit_code == 1
    assert calls == []
    assert any("not started" in line for line in output)


def test_ok_healthcheck_starts_runner_and_cleans_lock(tmp_path: Path) -> None:
    calls: list[str] = []

    exit_code = run_bot(
        _settings(tmp_path),
        dependencies=RunnerDependencies(
            bot_runner=lambda: calls.append("started"),
            healthcheck_runner=lambda settings: _report("ok"),
            output=lambda text: None,
        ),
        lock_path=tmp_path / "telegram_bot.pid",
    )

    assert exit_code == 0
    assert calls == ["started"]
    assert not (tmp_path / "telegram_bot.pid").exists()


def test_existing_running_lock_blocks_start(tmp_path: Path) -> None:
    lock_path = tmp_path / "telegram_bot.pid"
    lock_path.write_text("12345", encoding="utf-8")
    calls: list[str] = []
    output: list[str] = []

    exit_code = run_bot(
        _settings(tmp_path),
        dependencies=RunnerDependencies(
            bot_runner=lambda: calls.append("started"),
            healthcheck_runner=lambda settings: _report("ok"),
            output=output.append,
            pid_checker=lambda pid: pid == 12345,
        ),
        lock_path=lock_path,
    )

    assert exit_code == 3
    assert calls == []
    assert any("already appears to be running" in line for line in output)


def test_keyboard_interrupt_is_reported_cleanly(tmp_path: Path) -> None:
    output: list[str] = []

    def bot_runner() -> None:
        raise KeyboardInterrupt

    exit_code = run_bot(
        _settings(tmp_path),
        dependencies=RunnerDependencies(
            bot_runner=bot_runner,
            healthcheck_runner=lambda settings: _report("ok"),
            output=output.append,
        ),
        lock_path=tmp_path / "telegram_bot.pid",
    )

    assert exit_code == 0
    assert output[-1] == "Telegram bot stopped by Ctrl+C."


def test_runner_output_redacts_secrets(tmp_path: Path) -> None:
    output: list[str] = []
    secret = "sk-" + "or-v1-" + "fakeRunnerSecret123456789"

    def bot_runner() -> None:
        raise RuntimeError(f"OpenRouter failed: {secret}")

    exit_code = run_bot(
        _settings(tmp_path),
        dependencies=RunnerDependencies(
            bot_runner=bot_runner,
            healthcheck_runner=lambda settings: _report("ok"),
            output=output.append,
        ),
        lock_path=tmp_path / "telegram_bot.pid",
    )

    combined = "\n".join(output)
    assert exit_code == 1
    assert secret not in combined
    assert "sk-or-v1-<redacted>" in combined


def test_polling_lock_replaces_stale_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "telegram_bot.pid"
    lock_path.write_text("98765", encoding="utf-8")

    with PollingLock(lock_path, pid=111, pid_checker=lambda pid: False):
        assert lock_path.read_text(encoding="utf-8") == "111"

    assert not lock_path.exists()


async def _report(status: str) -> HealthReport:
    return HealthReport(lines=(HealthLine("Config", status),))


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(log_dir=str(tmp_path))
