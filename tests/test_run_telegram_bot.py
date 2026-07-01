from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import run_telegram_bot
from scripts.run_telegram_bot import (
    PollingLock,
    RunnerDependencies,
    _pid_is_running,
    _posix_pid_is_running,
    _windows_pid_is_running,
    run_bot,
)
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


def test_windows_pid_helper_does_not_use_os_kill(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    kernel32 = FakeKernel32(handle=123)
    monkeypatch.setattr(run_telegram_bot.ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)

    def fail_kill(pid: int, signal: int) -> None:
        del pid, signal
        raise AssertionError("Windows PID helper must not use os.kill")

    monkeypatch.setattr(run_telegram_bot.os, "kill", fail_kill)

    assert _windows_pid_is_running(456) is True
    assert kernel32.open_calls == [(0x1000, False, 456)]
    assert kernel32.close_calls == [123]


def test_windows_pid_helper_returns_true_when_open_process_returns_handle(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    kernel32 = FakeKernel32(handle=99)
    monkeypatch.setattr(run_telegram_bot.ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)

    assert _windows_pid_is_running(123) is True
    assert kernel32.close_calls == [99]


def test_windows_pid_helper_returns_false_when_open_process_returns_zero(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    kernel32 = FakeKernel32(handle=0)
    monkeypatch.setattr(run_telegram_bot.ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)

    assert _windows_pid_is_running(123) is False
    assert kernel32.close_calls == []


def test_pid_dispatch_uses_windows_helper_on_windows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(run_telegram_bot.os, "name", "nt", raising=False)
    monkeypatch.setattr(run_telegram_bot, "_windows_pid_is_running", lambda pid: pid == 77)
    monkeypatch.setattr(
        run_telegram_bot,
        "_posix_pid_is_running",
        lambda pid: (_ for _ in ()).throw(AssertionError("POSIX helper should not run")),
    )

    assert _pid_is_running(77) is True
    assert _pid_is_running(78) is False


def test_posix_pid_helper_uses_os_kill(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, signal: int) -> None:
        calls.append((pid, signal))

    monkeypatch.setattr(run_telegram_bot.os, "kill", fake_kill)

    assert _posix_pid_is_running(321) is True
    assert calls == [(321, 0)]


def test_posix_pid_helper_returns_false_on_os_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_kill(pid: int, signal: int) -> None:
        del pid, signal
        raise OSError

    monkeypatch.setattr(run_telegram_bot.os, "kill", fake_kill)

    assert _posix_pid_is_running(321) is False


class FakeKernel32:
    def __init__(self, *, handle: int) -> None:
        self.handle = handle
        self.open_calls: list[tuple[int, bool, int]] = []
        self.close_calls: list[int] = []

    def OpenProcess(self, access: int, inherit_handle: bool, pid: int) -> int:  # noqa: N802 - Windows API name
        self.open_calls.append((access, inherit_handle, pid))
        return self.handle

    def CloseHandle(self, handle: int) -> bool:  # noqa: N802 - Windows API name
        self.close_calls.append(handle)
        return True


async def _report(status: str) -> HealthReport:
    return HealthReport(lines=(HealthLine("Config", status),))


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(log_dir=str(tmp_path))
