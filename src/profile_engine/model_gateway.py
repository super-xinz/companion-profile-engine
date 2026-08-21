from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import get_settings
from .model_catalog import (MODEL_PROVIDERS, MODEL_SPECS_BY_PROVIDER,
                            ModelProvider)


class ModelConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelEndpoint:
    provider: ModelProvider
    label: str
    route_label: str
    api_key: str | None
    base_url: str
    model: str
    timeout: float
    extra_headers: dict[str, str]

    @property
    def available(self) -> bool:
        return bool(self.api_key)


def get_model_endpoint(provider: ModelProvider | str | None = None) -> ModelEndpoint:
    settings = get_settings()
    selected = provider or settings.default_model_provider
    openrouter_headers = {}
    if settings.openrouter_site_url:
        openrouter_headers["HTTP-Referer"] = settings.openrouter_site_url
    if settings.openrouter_app_name:
        openrouter_headers["X-Title"] = settings.openrouter_app_name

    spec = MODEL_SPECS_BY_PROVIDER.get(selected)
    if spec is None:
        raise ModelConfigurationError(f"不支持的模型供应商: {selected}")
    return ModelEndpoint(
        provider=spec.provider,
        label=spec.label,
        route_label="OpenRouter",
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url.rstrip("/"),
        model=getattr(settings, spec.setting_name),
        timeout=settings.model_timeout_seconds,
        extra_headers=openrouter_headers,
    )


def public_model_options() -> dict:
    settings = get_settings()
    options = []
    for provider in MODEL_PROVIDERS:
        endpoint = get_model_endpoint(provider)
        options.append({
            "provider": endpoint.provider,
            "label": endpoint.label,
            "route": endpoint.route_label,
            "model": endpoint.model,
            "available": endpoint.available,
        })
    return {"default_provider": settings.default_model_provider, "options": options}


def chat_completion(
    endpoint: ModelEndpoint,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    json_response: bool = False,
) -> tuple[str, str]:
    if not endpoint.api_key:
        raise ModelConfigurationError(
            f"{endpoint.label} 未配置 API Key；请在服务器环境变量中完成配置"
        )
    payload: dict = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    spec = MODEL_SPECS_BY_PROVIDER[endpoint.provider]
    if json_response and spec.supports_json_object:
        payload["response_format"] = {"type": "json_object"}
    if spec.disable_reasoning:
        payload["reasoning"] = {"enabled": False}
    response = httpx.post(
        f"{endpoint.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {endpoint.api_key}",
            "Content-Type": "application/json",
            **endpoint.extra_headers,
        },
        json=payload,
        timeout=endpoint.timeout,
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("模型返回了空内容")
    return content.strip(), str(body.get("model") or endpoint.model)
