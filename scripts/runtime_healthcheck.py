"""Read-only runtime healthcheck for local/server Telegram deployment."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.supabase_client import SupabaseClient  # noqa: E402
from app.logging_config import _redact_secrets  # noqa: E402
from app.service_registry.provider import ServiceDocsStatusProvider  # noqa: E402
from app.service_registry.types import ServiceDocsStatus  # noqa: E402

Status = str
SupabaseFactory = Callable[[Any], Any]
ServiceStatusProviderFactory = Callable[[Any], Any]


@dataclass(frozen=True)
class HealthLine:
    """One healthcheck line."""

    name: str
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class HealthReport:
    """Complete healthcheck report."""

    lines: tuple[HealthLine, ...]

    @property
    def overall(self) -> Status:
        """Return the overall healthcheck status."""
        statuses = {line.status for line in self.lines}
        if "fail" in statuses:
            return "fail"
        if "warn" in statuses:
            return "warn"
        return "ok"

    @property
    def exit_code(self) -> int:
        """Return a CLI exit code for automation."""
        if any(line.status == "fail" and line.name.startswith("Config:") for line in self.lines):
            return 2
        if self.overall == "fail":
            return 1
        return 0


async def run_healthcheck(
    settings: Any,
    *,
    supabase_factory: SupabaseFactory = SupabaseClient,
    service_status_provider_factory: ServiceStatusProviderFactory | None = None,
) -> HealthReport:
    """Run read-only runtime checks without starting Telegram polling."""
    lines: list[HealthLine] = []
    lines.extend(_config_lines(settings))
    lines.append(_polling_line())
    lines.append(_logs_line(settings))

    if _supabase_config_ready(settings):
        lines.append(await _supabase_line(settings, supabase_factory=supabase_factory))
        lines.append(
            await _service_docs_line(
                settings,
                supabase_factory=supabase_factory,
                service_status_provider_factory=service_status_provider_factory,
            )
        )
    else:
        lines.append(HealthLine("Supabase read-only check", "skip", "missing Supabase settings"))
        lines.append(HealthLine("Service/docs status", "skip", "missing Supabase settings"))

    return HealthReport(lines=tuple(lines))


def format_report(report: HealthReport) -> str:
    """Return a human-readable report that does not expose raw config or secrets."""
    rows = [f"Runtime healthcheck: {report.overall.upper()}", ""]
    for line in report.lines:
        detail = f" - {_safe_text(line.detail)}" if line.detail else ""
        rows.append(f"[{line.status.upper()}] {line.name}{detail}")
    return "\n".join(rows)


def _config_lines(settings: Any) -> list[HealthLine]:
    lines: list[HealthLine] = []
    for env_name, attr, label in _required_settings():
        value = getattr(settings, attr, "")
        if _is_missing(value):
            lines.append(HealthLine(f"Config: {env_name}", "fail", "missing"))
        else:
            lines.append(HealthLine(f"Config: {env_name}", "ok", label))

    provider = str(getattr(settings, "embedding_provider", "") or "").strip().lower()
    if provider in {"local", "ollama"}:
        if _is_missing(getattr(settings, "ollama_base_url", "")):
            lines.append(HealthLine("Config: OLLAMA_BASE_URL", "fail", "missing for local embeddings"))
        else:
            lines.append(HealthLine("Config: OLLAMA_BASE_URL", "ok", "present"))
    elif provider:
        lines.append(HealthLine("Config: EMBEDDING_PROVIDER", "fail", "expected local or ollama"))

    if getattr(settings, "embedding_dim", None) != 1024:
        lines.append(HealthLine("Config: EMBEDDING_DIM", "fail", "must be 1024 for schema vector(1024)"))

    if str(getattr(settings, "rag_pipeline_version", "") or "").strip() != "v2":
        lines.append(HealthLine("Config: RAG_PIPELINE_VERSION", "fail", "must be v2"))

    workspace_id = str(getattr(settings, "default_workspace_id", "") or "").strip()
    if workspace_id and not _is_uuid(workspace_id):
        lines.append(HealthLine("Config: DEFAULT_WORKSPACE_ID", "fail", "must be a UUID"))

    if _is_missing(getattr(settings, "owner_ids", "")):
        lines.append(HealthLine("Config: OWNER_IDS", "warn", "empty; review Telegram access policy"))

    return lines


def _required_settings() -> tuple[tuple[str, str, str], ...]:
    return (
        ("TELEGRAM_BOT_TOKEN", "telegram_bot_token", "present"),
        ("SUPABASE_URL", "supabase_url", "present"),
        ("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key", "present"),
        ("DEFAULT_WORKSPACE_ID", "default_workspace_id", "present"),
        ("OPENROUTER_API_KEY", "openrouter_api_key", "present"),
        ("OPENROUTER_DEFAULT_MODEL", "openrouter_default_model", "present"),
        ("OPENROUTER_VISION_MODEL", "openrouter_vision_model", "present"),
        ("EMBEDDING_PROVIDER", "embedding_provider", "present"),
        ("EMBEDDING_MODEL", "embedding_model", "present"),
        ("EMBEDDING_DIM", "embedding_dim", "present"),
        ("RAG_PIPELINE_VERSION", "rag_pipeline_version", "present"),
    )


def _polling_line() -> HealthLine:
    return HealthLine("Telegram polling", "ok", "not started by this healthcheck")


def _logs_line(settings: Any) -> HealthLine:
    log_dir = str(getattr(settings, "log_dir", "") or "logs").strip()
    return HealthLine("Logs", "ok", f"configured at {log_dir}/app.log and {log_dir}/errors.log")


async def _supabase_line(settings: Any, *, supabase_factory: SupabaseFactory) -> HealthLine:
    client = supabase_factory(settings)
    try:
        rows = await client.select(
            "workspaces",
            params={
                "select": "id,name",
                "id": f"eq.{getattr(settings, 'default_workspace_id', '')}",
                "limit": "1",
            },
        )
        if rows:
            workspace_name = str(rows[0].get("name") or rows[0].get("id") or "found")
            return HealthLine("Supabase read-only check", "ok", f"workspace found: {workspace_name}")
        return HealthLine("Supabase read-only check", "fail", "DEFAULT_WORKSPACE_ID not found in workspaces")
    except Exception as exc:  # noqa: BLE001 - healthcheck should report setup issues
        return HealthLine("Supabase read-only check", "fail", _safe_text(str(exc) or exc.__class__.__name__))
    finally:
        await _close_if_possible(client)


async def _service_docs_line(
    settings: Any,
    *,
    supabase_factory: SupabaseFactory,
    service_status_provider_factory: ServiceStatusProviderFactory | None,
) -> HealthLine:
    client = supabase_factory(settings)
    try:
        provider_factory = service_status_provider_factory or (lambda supabase: ServiceDocsStatusProvider(supabase))
        provider = provider_factory(client)
        statuses = tuple(await provider.list_statuses(scan_corpus=False))
        if not statuses:
            return HealthLine("Service/docs status", "warn", "registry returned no services")
        risky = [
            status.display_name
            for status in statuses
            if status.docs_status == "needs_review" or status.quality_status == "FAIL"
        ]
        detail = _service_status_summary(statuses)
        return HealthLine("Service/docs status", "warn" if risky else "ok", detail)
    except Exception as exc:  # noqa: BLE001 - healthcheck should stay readable
        return HealthLine("Service/docs status", "warn", _safe_text(str(exc) or exc.__class__.__name__))
    finally:
        await _close_if_possible(client)


def _service_status_summary(statuses: Iterable[ServiceDocsStatus]) -> str:
    parts: list[str] = []
    for status in list(statuses)[:6]:
        quality = f", {status.quality_status}" if status.quality_status not in {"", "none"} else ""
        parts.append(f"{status.display_name}: {status.docs_status}{quality}")
    return "; ".join(parts)


def _supabase_config_ready(settings: Any) -> bool:
    return not any(
        _is_missing(getattr(settings, attr, ""))
        for attr in ("supabase_url", "supabase_service_role_key", "default_workspace_id")
    )


def _is_missing(value: object) -> bool:
    text = "" if value is None else str(value).strip()
    return (
        not text
        or text.startswith("replace_with")
        or "your-project-ref" in text
        or text in {"0", "None"}
    )


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


async def _close_if_possible(client: Any) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result


def _safe_text(text: str) -> str:
    return _redact_secrets(text.replace("\n", " ").strip())[:500]


async def main_async() -> int:
    """CLI entry point for async checks."""
    report = await run_healthcheck(get_settings())
    print(format_report(report))
    return report.exit_code


def main() -> None:
    """Run the healthcheck."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
