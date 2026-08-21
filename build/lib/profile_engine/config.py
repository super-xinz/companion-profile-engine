from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROFILE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/profile_engine.db"
    api_key: str = "local-development-key"
    tenant_api_keys: dict[str, str] = Field(default_factory=dict)
    rule_source_dir: Path = Path("./rules")
    environment: str = "development"
    data_encryption_key: str | None = None
    state_ttl_hours: int = 24
    semantic_extractor: str = "deterministic"
    qwen_api_key: str | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen3.7-plus"
    qwen_timeout_seconds: float = 30.0
    allow_external_semantic_processing: bool = False
    demo_access_code: str | None = None
    demo_tenant_id: str = "demo-tenant"
    port: int = 8000

    @field_validator("database_url")
    @classmethod
    def use_psycopg3_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
