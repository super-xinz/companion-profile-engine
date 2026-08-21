from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .model_catalog import MODEL_PROVIDERS


LEGACY_MODEL_ALIASES = {
    "~anthropic/claude-sonnet-latest": "anthropic/claude-sonnet-5",
    "~openai/gpt-latest": "openai/gpt-5.6-sol",
    "~google/gemini-pro-latest": "google/gemini-3.1-pro-preview",
    "~moonshotai/kimi-latest": "moonshotai/kimi-k3",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROFILE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/profile_engine.db"
    api_key: str = "local-development-key"
    tenant_id: str | None = None
    tenant_api_keys: dict[str, str] = Field(default_factory=dict)
    rule_source_dir: Path = Path("./rules")
    environment: str = "development"
    data_encryption_key: str | None = None
    state_ttl_hours: int = 24
    semantic_extractor: str = "deterministic"
    default_model_provider: str = "deepseek"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    deepseek_model: str = "deepseek/deepseek-v3.2"
    claude_model: str = "anthropic/claude-sonnet-5"
    gpt_model: str = "openai/gpt-5.6-sol"
    glm_model: str = "z-ai/glm-5.2"
    gemini_model: str = "google/gemini-3.1-pro-preview"
    kimi_model: str = "moonshotai/kimi-k3"
    openrouter_site_url: str | None = None
    openrouter_app_name: str = "Companion Profile Engine"
    model_timeout_seconds: float = 30.0
    allow_external_semantic_processing: bool = False
    demo_access_code: str | None = None
    demo_tenant_id: str = "demo-tenant"
    demo_features_enabled: bool | None = None
    rule_workbench_enabled: bool | None = None
    api_docs_enabled: bool | None = None
    allow_profile_reset: bool | None = None
    rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    demo_rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    demo_model_rate_limit_per_minute: int = Field(default=30, ge=1, le=1_000)
    auth_failure_rate_limit_per_minute: int = Field(default=30, ge=1, le=1_000)
    max_request_body_bytes: int = Field(default=2_500_000, ge=1_024, le=20_000_000)
    idempotency_ttl_hours: int = Field(default=24, ge=1, le=168)
    port: int = 8000

    @field_validator("database_url")
    @classmethod
    def use_psycopg3_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value

    @field_validator("environment", "semantic_extractor", "default_model_provider")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("claude_model", "gpt_model", "gemini_model", "kimi_model")
    @classmethod
    def use_explicit_model_id(cls, value: str) -> str:
        normalized = value.strip()
        return LEGACY_MODEL_ALIASES.get(normalized, normalized)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def demo_features_active(self) -> bool:
        if self.demo_features_enabled is not None:
            return self.demo_features_enabled
        return not self.is_production

    @property
    def api_docs_active(self) -> bool:
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return not self.is_production

    @property
    def rule_workbench_active(self) -> bool:
        if self.rule_workbench_enabled is not None:
            return self.rule_workbench_enabled
        return not self.is_production

    @property
    def profile_reset_active(self) -> bool:
        if self.allow_profile_reset is not None:
            return self.allow_profile_reset
        return not self.is_production

    def validate_runtime_configuration(self) -> None:
        errors: list[str] = []
        if self.environment not in {"development", "test", "production"}:
            errors.append("PROFILE_ENVIRONMENT 必须是 development、test 或 production")
        if self.default_model_provider not in MODEL_PROVIDERS:
            errors.append(
                "PROFILE_DEFAULT_MODEL_PROVIDER 必须是 " + "、".join(MODEL_PROVIDERS)
            )
        if self.semantic_extractor not in {"deterministic", "model"}:
            errors.append("PROFILE_SEMANTIC_EXTRACTOR 必须是 deterministic 或 model")
        if self.semantic_extractor == "model":
            if self.is_production and not self.openrouter_api_key:
                errors.append("启用 model 时必须配置 PROFILE_OPENROUTER_API_KEY")
            if not self.allow_external_semantic_processing:
                errors.append("启用 model 时必须明确设置 PROFILE_ALLOW_EXTERNAL_SEMANTIC_PROCESSING=true")
        if self.is_production and not self.openrouter_base_url.startswith("https://"):
            errors.append("生产环境 PROFILE_OPENROUTER_BASE_URL 必须使用 HTTPS")
        if self.is_production:
            if not self.database_url.startswith("postgresql+psycopg://"):
                errors.append("生产环境必须使用 PostgreSQL，禁止使用容器内 SQLite")
            single_tenant_configured = bool(self.tenant_id and len(self.api_key) >= 24)
            if not self.tenant_api_keys and not single_tenant_configured:
                errors.append(
                    "生产环境必须配置 PROFILE_TENANT_API_KEYS，或同时配置 "
                    "PROFILE_TENANT_ID 与至少 24 字符的 PROFILE_API_KEY"
                )
            weak_tenants = sorted(
                tenant for tenant, key in self.tenant_api_keys.items()
                if not tenant.strip() or len(key) < 24
            )
            if weak_tenants:
                errors.append(f"以下租户 API Key 必须至少 24 个字符: {weak_tenants}")
            if self.demo_features_active and not self.demo_access_code:
                errors.append("生产环境启用 Demo/规则工作台时必须设置 PROFILE_DEMO_ACCESS_CODE")
        if errors:
            raise RuntimeError("生产配置检查失败: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
