import asyncio
from types import SimpleNamespace

from app.service_registry.types import ServiceDocsStatus
from scripts.runtime_healthcheck import format_report, run_healthcheck


def test_missing_env_is_reported_without_raw_json() -> None:
    report = asyncio.run(
        run_healthcheck(
            _settings(
                telegram_bot_token="",
                supabase_url="",
                supabase_service_role_key="",
                default_workspace_id="",
                openrouter_api_key="",
                owner_ids="",
            ),
            supabase_factory=_raising_supabase_factory,
        )
    )

    output = format_report(report)

    assert "Runtime healthcheck: FAIL" in output
    assert "Config: TELEGRAM_BOT_TOKEN - missing" in output
    assert "Config: SUPABASE_URL - missing" in output
    assert "Supabase read-only check - missing Supabase settings" in output
    assert "{" not in output
    assert "}" not in output


def test_successful_config_prints_ok_status() -> None:
    report = asyncio.run(
        run_healthcheck(
            _settings(),
            supabase_factory=lambda settings: FakeSupabaseClient(),
            service_status_provider_factory=lambda client: FakeServiceStatusProvider(),
        )
    )

    output = format_report(report)

    assert report.overall == "ok"
    assert "Runtime healthcheck: OK" in output
    assert "Config: TELEGRAM_BOT_TOKEN - present" in output
    assert "Supabase read-only check - workspace found: team" in output
    assert "Service/docs status - n8n: indexed, PASS" in output


def test_secrets_are_not_printed() -> None:
    settings = _settings(
        telegram_bot_token="123456789:" + "FakeTelegramToken123456789",
        supabase_service_role_key="sb_" + "secret_" + "fakeRuntimeKey123456789",
        openrouter_api_key="sk-" + "or-v1-" + "fakeRuntimeKey123456789",
    )
    report = asyncio.run(
        run_healthcheck(
            settings,
            supabase_factory=lambda settings: FailingSupabaseClient(settings.supabase_service_role_key),
            service_status_provider_factory=lambda client: FakeServiceStatusProvider(),
        )
    )

    output = format_report(report)

    assert settings.telegram_bot_token not in output
    assert settings.supabase_service_role_key not in output
    assert settings.openrouter_api_key not in output
    assert "Bearer <redacted>" in output


def test_service_docs_warning_is_visible_but_not_json() -> None:
    report = asyncio.run(
        run_healthcheck(
            _settings(),
            supabase_factory=lambda settings: FakeSupabaseClient(),
            service_status_provider_factory=lambda client: WarningServiceStatusProvider(),
        )
    )

    output = format_report(report)

    assert report.overall == "warn"
    assert "Service/docs status - Supabase: needs_review, FAIL" in output
    assert "{" not in output
    assert "}" not in output


class FakeSupabaseClient:
    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, str]]:
        assert table == "workspaces"
        return [{"id": "00000000-0000-0000-0000-000000000001", "name": "team"}]

    async def close(self) -> None:
        return None


class FailingSupabaseClient:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, str]]:
        raise RuntimeError(f"Authorization Bearer {self.secret}")

    async def close(self) -> None:
        return None


class FakeServiceStatusProvider:
    async def list_statuses(self, *, scan_corpus: bool = False) -> tuple[ServiceDocsStatus, ...]:
        assert scan_corpus is False
        return (
            ServiceDocsStatus(
                service_id="n8n",
                display_name="n8n",
                aliases=("n8n",),
                docs_source="n8n_docs",
                configured_status="enabled",
                docs_status="indexed",
                quality_status="PASS",
            ),
        )


class WarningServiceStatusProvider:
    async def list_statuses(self, *, scan_corpus: bool = False) -> tuple[ServiceDocsStatus, ...]:
        assert scan_corpus is False
        return (
            ServiceDocsStatus(
                service_id="supabase",
                display_name="Supabase",
                aliases=("supabase",),
                docs_source="supabase_docs",
                configured_status="enabled",
                docs_status="needs_review",
                quality_status="FAIL",
            ),
        )


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "telegram_bot_token": "123456789:" + "FakeTelegramToken123456789",
        "supabase_url": "https://example.supabase.co",
        "supabase_service_role_key": "service-role-test",
        "default_workspace_id": "00000000-0000-0000-0000-000000000001",
        "openrouter_api_key": "openrouter-test",
        "openrouter_default_model": "openai/gpt-4.1-mini",
        "openrouter_vision_model": "openai/gpt-4.1-mini",
        "embedding_provider": "local",
        "embedding_model": "BAAI/bge-m3",
        "embedding_dim": 1024,
        "ollama_base_url": "http://localhost:11434",
        "rag_pipeline_version": "v2",
        "owner_ids": "123456789",
        "log_dir": "logs",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _raising_supabase_factory(settings: object) -> object:
    raise AssertionError("Supabase should not be called when config is missing")
