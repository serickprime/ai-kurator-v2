import logging

from app.config import Settings
from app.logging_config import configure_logging


def test_settings_use_first_run_env_names() -> None:
    settings = Settings(
        _env_file=None,
        OPENROUTER_DEFAULT_MODEL="openrouter/default",
        OPENROUTER_VISION_MODEL="openrouter/vision",
        RAG_PIPELINE_VERSION="v2",
    )

    assert settings.openrouter_default_model == "openrouter/default"
    assert settings.openrouter_model == "openrouter/default"
    assert settings.openrouter_vision_model == "openrouter/vision"
    assert settings.vision_model == "openrouter/vision"
    assert settings.rag_pipeline_version == "v2"
    assert settings.schema_version == "v2"


def test_configure_logging_creates_app_and_error_logs(tmp_path) -> None:
    configure_logging("INFO", tmp_path)

    logging.getLogger("test").info("hello")
    logging.getLogger("test").error("boom")

    app_log = tmp_path / "app.log"
    error_log = tmp_path / "errors.log"

    assert app_log.exists()
    assert error_log.exists()
    assert "hello" in app_log.read_text(encoding="utf-8")
    assert "boom" in error_log.read_text(encoding="utf-8")
