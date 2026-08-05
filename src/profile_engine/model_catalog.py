from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ModelProvider = Literal["deepseek", "claude", "gpt", "glm", "gemini", "kimi"]


@dataclass(frozen=True)
class ModelSpec:
    provider: ModelProvider
    label: str
    setting_name: str
    supports_json_object: bool = False
    disable_reasoning: bool = False


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        provider="deepseek",
        label="DeepSeek V3.2",
        setting_name="deepseek_model",
        supports_json_object=True,
        disable_reasoning=True,
    ),
    ModelSpec(provider="claude", label="Claude", setting_name="claude_model"),
    ModelSpec(provider="gpt", label="GPT", setting_name="gpt_model"),
    ModelSpec(provider="glm", label="GLM", setting_name="glm_model"),
    ModelSpec(provider="gemini", label="Gemini", setting_name="gemini_model"),
    ModelSpec(provider="kimi", label="Kimi", setting_name="kimi_model"),
)

MODEL_PROVIDERS: tuple[ModelProvider, ...] = tuple(spec.provider for spec in MODEL_SPECS)
MODEL_SPECS_BY_PROVIDER = {spec.provider: spec for spec in MODEL_SPECS}

