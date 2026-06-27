"""Environment-driven application settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_embedding_model: str = Field(default="BAAI/bge-m3", alias="OLLAMA_EMBEDDING_MODEL")
    embedding_provider: str = Field(default="local", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=1024, alias="EMBEDDING_DIM")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="openai/gpt-4.1-mini", alias="OPENROUTER_MODEL")
    openrouter_free_text_models: str = Field(default="", alias="OPENROUTER_FREE_TEXT_MODELS")
    openrouter_free_vision_models: str = Field(default="", alias="OPENROUTER_FREE_VISION_MODELS")
    openrouter_cheap_text_models: str = Field(default="", alias="OPENROUTER_CHEAP_TEXT_MODELS")
    openrouter_cheap_vision_models: str = Field(default="", alias="OPENROUTER_CHEAP_VISION_MODELS")
    openrouter_quality_text_models: str = Field(default="", alias="OPENROUTER_QUALITY_TEXT_MODELS")
    openrouter_quality_vision_models: str = Field(default="", alias="OPENROUTER_QUALITY_VISION_MODELS")
    allow_quality_to_cheap_fallback: bool = Field(default=False, alias="ALLOW_QUALITY_TO_CHEAP_FALLBACK")
    vision_enabled: bool = Field(default=False, alias="VISION_ENABLED")
    vision_model: str = Field(default="openai/gpt-4.1-mini", alias="VISION_MODEL")
    owner_ids: str = Field(default="", alias="OWNER_IDS")
    default_workspace_id: str = Field(default="", alias="DEFAULT_WORKSPACE_ID")
    default_workspace_name: str = Field(default="team", alias="DEFAULT_WORKSPACE_NAME")
    schema_version: str = Field(default="v2", alias="SCHEMA_VERSION")
    reranker_mode: str = Field(default="identity", alias="RERANKER_MODE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for the running process."""
    return Settings()
