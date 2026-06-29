import asyncio

from app.config import Settings
from app.rag.pipeline import EvidenceFirstRagPipeline
from app.rag.runtime import build_rag_runtime_from_settings, validate_runtime_config
from app.rag.evidence_pack import build_sources
from app.rag.types import EvidencePack, EvidenceSpan, SourceRef


def test_validate_runtime_config_reports_missing_values() -> None:
    validation = validate_runtime_config(Settings(_env_file=None, telegram_bot_token="123456:test"))

    assert not validation.ready
    assert "SUPABASE_URL" in validation.missing
    assert "SUPABASE_SERVICE_ROLE_KEY" in validation.missing
    assert "OPENROUTER_API_KEY" in validation.missing


def test_build_rag_runtime_from_complete_settings() -> None:
    runtime = build_rag_runtime_from_settings(_complete_settings())

    assert runtime is not None
    assert isinstance(runtime.pipeline, EvidenceFirstRagPipeline)
    assert runtime.validation.ready

    asyncio.run(runtime.close())


def test_sources_still_come_only_from_evidence_pack() -> None:
    pack = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-used",
                document_id="doc-used",
                document_title="Used document",
                text="Supported text.",
            ),
        ),
        source_matches=(
            SourceRef(
                document_id="doc-used",
                document_title="Used document",
                locator="p. 1",
                evidence_id="ev-used",
            ),
        ),
    )

    assert build_sources(pack) == ["Used document, p. 1"]


def _complete_settings() -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="123456:test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-test",
        default_workspace_id="00000000-0000-0000-0000-000000000001",
        openrouter_api_key="openrouter-test",
        openrouter_default_model="openai/gpt-4.1-mini",
        embedding_provider="local",
        embedding_model="BAAI/bge-m3",
        embedding_dim=1024,
        rag_pipeline_version="v2",
    )
