import asyncio

from app.config import Settings
from app.ingestion.indexing import IndexingService
from app.ingestion.runtime import build_ingestion_runtime_from_settings, validate_ingestion_config


def test_validate_ingestion_config_reports_missing_values() -> None:
    validation = validate_ingestion_config(Settings(_env_file=None))

    assert not validation.ready
    assert "SUPABASE_URL" in validation.missing
    assert "SUPABASE_SERVICE_ROLE_KEY" in validation.missing


def test_build_ingestion_runtime_from_complete_settings() -> None:
    runtime = build_ingestion_runtime_from_settings(_complete_settings())

    assert runtime is not None
    assert isinstance(runtime.service, IndexingService)
    assert runtime.validation.ready

    asyncio.run(runtime.close())


def _complete_settings() -> Settings:
    return Settings(
        _env_file=None,
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-test",
        embedding_provider="local",
        embedding_model="BAAI/bge-m3",
        embedding_dim=1024,
    )
