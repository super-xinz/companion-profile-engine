import json
import uuid

import httpx
from fastapi.testclient import TestClient

from profile_engine.api import app
from profile_engine.demo import demo_auth
from profile_engine.extractor import DeterministicSemanticExtractor
from profile_engine.model_gateway import ModelEndpoint


def test_casual_chat_does_not_force_a_closing_question():
    guidance = DeterministicSemanticExtractor().analyze("我最近有点想去看看闺蜜").reply_guidance
    assert guidance.question_count == 0


def _available_endpoint(provider="deepseek"):
    return ModelEndpoint(
        provider=provider,
        label="Demo responder",
        route_label="model gateway",
        api_key="test-openrouter-key",  # pragma: allowlist secret
        base_url="https://openrouter.example/v1",
        model="demo/responder",
        timeout=30,
        extra_headers={},
    )


def _session(client: TestClient) -> tuple[str, str, int]:
    boot = client.post("/demo/api/workspace/bootstrap")
    assert boot.status_code == 200, boot.text
    person = next(item for item in boot.json()["people"] if item["public_id"] == "profile-sky")
    detail = client.get("/demo/api/people/profile-sky")
    assert detail.status_code == 200, detail.text
    conversation = detail.json()["conversations"][0]
    return person["public_id"], conversation["conversation_id"], detail.json()["profile_version"]


def _chat_payload(public_id: str, conversation_id: str, version: int, message_id: str) -> dict:
    return {
        "public_id": public_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "expected_profile_version": version,
        "text": "以后回答短一点，先听我把话说完。",
        "history": [{"role": "user", "content": "以后回答短一点，先听我把话说完。"}],
    }


def _assert_no_trace(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for marker in (
        "request_id", "model", "provider", "engine", "trace", "reply_hints",
        "profile_patch", "semantic_frames", "accepted_trait_signals", "digital_code",
        "mbti", "enneagram", "numerology", "九型", "数字密码", "八字", "7w8",
        "sx/so", "1998-12-06", "person-1998", ".xlsx", "deepseek", "openai",
        "gpt", "claude", "anthropic", "gemini", "glm", "kimi", "openrouter",
    ):
        assert marker not in text


def test_demo_chat_success_cache_and_model_context_are_public_safe(monkeypatch):
    tenant = f"public-chat-{uuid.uuid4().hex}"
    captured = {}

    def completion(_endpoint, messages, **_kwargs):
        captured["provider"] = _endpoint.provider
        captured["messages"] = messages
        return (
            "你是 ENTP，九型人格 7w8，SX/SO；生日 1998-12-06；"
            "参考 secret.xlsx；整体可信度 45%。",
            "demo/responder",
        )

    monkeypatch.setattr("profile_engine.demo.get_model_endpoint", _available_endpoint)
    monkeypatch.setattr("profile_engine.demo.chat_completion", completion)
    app.dependency_overrides[demo_auth] = lambda: tenant
    try:
        with TestClient(app) as client:
            public_id, conversation_id, version = _session(client)
            payload = _chat_payload(public_id, conversation_id, version, "public-chat-1")
            # Public callers cannot select or enumerate the server-side responder.
            payload["model_provider"] = "not-a-public-option"
            turn = client.post("/demo/api/chat", json=payload)
            assert turn.status_code == 200, turn.text
            assert set(turn.json()) == {"assistant_reply", "profile_version", "update_summary"}
            assert "45%" in turn.json()["assistant_reply"]
            assert "x-request-id" not in turn.headers
            assert "x-model-ratelimit-remaining" not in turn.headers
            assert captured["provider"] == "deepseek"
            _assert_no_trace(turn.json())

            system = captured["messages"][0]["content"].lower()
            for marker in (
                "digital_code", "strategy_trace", "reply_hints", "profile_patch",
                "semantic_frames", "accepted_trait_signals", "runtime_operations",
                "source_profile", "enneagram", "numerology", "mbti",
            ):
                assert marker not in system
            assert "interaction_summary" in system
            assert "communication_preferences" in system
            assert "safe_indicators" in system
            assert "turn_guidance" in system

            cached = client.post("/demo/api/chat", json=payload)
            assert cached.status_code == 200, cached.text
            assert cached.json() == turn.json()
            assert "x-request-id" not in cached.headers
            _assert_no_trace(cached.json())

            messages = client.get(
                f"/demo/api/people/{public_id}/conversations/{conversation_id}/messages"
            )
            assert messages.status_code == 200, messages.text
            assert all("engine_trace" not in item for item in messages.json()["messages"])
            _assert_no_trace(messages.json())
    finally:
        app.dependency_overrides.pop(demo_auth, None)


def test_demo_chat_502_hides_upstream_and_internal_execution(monkeypatch):
    tenant = f"public-chat-error-{uuid.uuid4().hex}"
    monkeypatch.setattr("profile_engine.demo.get_model_endpoint", _available_endpoint)

    def fail(*_args, **_kwargs):
        request = httpx.Request("POST", "https://openrouter.example/v1/chat/completions")
        response = httpx.Response(403, request=request, json={
            "error": {"message": "Access denied by secret provider"},
        })
        raise httpx.HTTPStatusError("403 Forbidden", request=request, response=response)

    monkeypatch.setattr("profile_engine.demo.chat_completion", fail)
    app.dependency_overrides[demo_auth] = lambda: tenant
    try:
        with TestClient(app) as client:
            public_id, conversation_id, version = _session(client)
            payload = _chat_payload(public_id, conversation_id, version, "public-chat-error-1")
            turn = client.post("/demo/api/chat", json=payload)
            assert turn.status_code == 502, turn.text
            assert set(turn.json()) == {
                "code", "message", "profile_version", "update_summary",
            }
            assert turn.json()["code"] == "assistant_temporarily_unavailable"
            assert "403" not in turn.text
            assert "Access denied" not in turn.text
            assert "x-request-id" not in turn.headers
            _assert_no_trace(turn.json())
    finally:
        app.dependency_overrides.pop(demo_auth, None)
