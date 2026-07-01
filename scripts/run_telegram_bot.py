"""Production-friendly Telegram bot runner with healthcheck and local lock."""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.logging_config import _redact_secrets  # noqa: E402
from app.main import main as start_telegram_bot  # noqa: E402
from scripts.runtime_healthcheck import HealthReport, format_report, run_healthcheck  # noqa: E402

Output = Callable[[str], None]
BotRunner = Callable[[], None]
HealthcheckRunner = Callable[[Any], Awaitable[HealthReport]]
PidChecker = Callable[[int], bool]


class AlreadyRunningError(RuntimeError):
    """Raised when a local Telegram polling lock is already active."""


@dataclass(frozen=True)
class RunnerDependencies:
    """Injectable dependencies for tests and CLI runtime."""

    bot_runner: BotRunner = start_telegram_bot
    healthcheck_runner: HealthcheckRunner = run_healthcheck
    output: Output = print
    pid_checker: PidChecker | None = None


def run_bot(
    settings: Any | None = None,
    *,
    dependencies: RunnerDependencies | None = None,
    lock_path: Path | None = None,
) -> int:
    """Run healthcheck, acquire local polling lock, and start Telegram polling."""
    settings = settings or get_settings()
    dependencies = dependencies or RunnerDependencies()
    output = dependencies.output

    report = asyncio.run(dependencies.healthcheck_runner(settings))
    output(format_report(report))

    if report.overall == "fail":
        output("Telegram bot was not started because runtime healthcheck failed.")
        return report.exit_code or 1
    if report.overall == "warn":
        output("Runtime healthcheck returned warnings. Starting bot anyway; review the warnings above.")

    path = lock_path or _default_lock_path(settings)
    try:
        with PollingLock(path, pid_checker=dependencies.pid_checker):
            output("Starting Telegram bot polling. Press Ctrl+C to stop.")
            dependencies.bot_runner()
            output("Telegram bot stopped.")
            return 0
    except AlreadyRunningError as exc:
        output(_safe_message(exc))
        output("Telegram bot was not started. Stop the existing bot process first.")
        return 3
    except KeyboardInterrupt:
        output("Telegram bot stopped by Ctrl+C.")
        return 0
    except Exception as exc:  # noqa: BLE001 - runner should explain startup/runtime failure
        output("Telegram bot stopped with error: " + _safe_message(exc))
        return 1


class PollingLock:
    """Local PID lock that prevents two runner-started polling processes."""

    def __init__(
        self,
        path: Path,
        *,
        pid: int | None = None,
        pid_checker: PidChecker | None = None,
    ) -> None:
        self._path = path
        self._pid = pid or os.getpid()
        self._pid_checker = pid_checker or _pid_is_running

    def __enter__(self) -> "PollingLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        existing_pid = _read_pid(self._path)
        if existing_pid and self._pid_checker(existing_pid):
            raise AlreadyRunningError(
                f"Telegram bot polling already appears to be running locally: pid={existing_pid}, lock={self._path}"
            )
        if self._path.exists():
            self._path.unlink()
        self._path.write_text(str(self._pid), encoding="utf-8")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        if _read_pid(self._path) == self._pid:
            self._path.unlink(missing_ok=True)


def _default_lock_path(settings: Any) -> Path:
    log_dir = str(getattr(settings, "log_dir", "") or "logs").strip() or "logs"
    return Path(log_dir) / "telegram_bot.pid"


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_running(pid)
    return _posix_pid_is_running(pid)


def _windows_pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    process_query_limited_information = 0x1000
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return False
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return False
    try:
        return True
    finally:
        kernel32.CloseHandle(handle)


def _posix_pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _safe_message(exc: BaseException) -> str:
    return _redact_secrets(str(exc).strip() or exc.__class__.__name__)[:700]


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(run_bot())


if __name__ == "__main__":
    main()
