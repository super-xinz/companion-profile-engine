import httpx
from fastapi.testclient import TestClient

import profile_engine.demo as demo_module
from profile_engine.api import app
from profile_engine.demo import demo_auth
from profile_engine.extractor import SemanticExtractorError
from profile_engine.model_gateway import ModelEndpoint


def _available_endpoint(provider="deepseek"):
    return ModelEndpoint(
        provider=provider,
        label="DeepSeek V3.2",
        route_label="OpenRouter",
        api_key="test-openrouter-key",  # pragma: allowlist secret
        base_url="https://openrouter.example/v1",
        model="deepseek/deepseek-v3.2",
        timeout=30,
        extra_headers={},
    )


def test_demo_page_and_conversation_flow(monkeypatch):
    monkeypatch.setattr("profile_engine.demo.get_model_endpoint", _available_endpoint)
    monkeypatch.setattr(
        "profile_engine.demo.chat_completion",
        lambda *_args, **_kwargs: ("明白了，我会先听你说完。", "deepseek/deepseek-v3.2"),
    )
    app.dependency_overrides[demo_auth] = lambda: "demo-test-tenant"
    try:
        with TestClient(app) as client:
            page = client.get("/demo")
            assert page.status_code == 200
            assert "对话与画像工作台" in page.text
            assert 'class="messages scroll-surface"' in page.text
            assert "完整画像" in page.text
            assert "规则管理" in page.text
            assert 'id="modelProviderSelect"' in page.text

            script = client.get("/assets/demo.js")
            assert script.status_code == 200
            assert '${esc(item.label)} · ${esc(item.model)}' in script.text

            started = client.post("/demo/api/start", json={
                "display_name": "演示用户",
                "birth_date": "1989-10-15",
            })
            assert started.status_code == 200, started.text
            session = started.json()
            assert session["profile_version"] == 1
            assert session["conversation_id"]

            turn = client.post("/demo/api/chat", json={
                "user_id": session["user_id"],
                "conversation_id": session["conversation_id"],
                "message_id": "demo-message-1",
                "expected_profile_version": 1,
                "text": "以后回答短一点，先听我把话说完。",
                "history": [{"role": "user", "content": "以后回答短一点，先听我把话说完。"}],
            })
            assert turn.status_code == 200, turn.text
            body = turn.json()
            assert body["assistant_reply"]
            assert body["engine"]["profile_version"] == 2
            assert body["engine"]["reply_hints"]["max_sentences"] == 3
            assert body["engine"]["reply_hints"]["empathy_first"] is True
    finally:
        app.dependency_overrides.pop(demo_auth, None)


def test_demo_chat_falls_back_when_selected_model_semantic_extraction_fails(monkeypatch):
    class FailingExtractor:
        version = "deepseek-unavailable-test"

        def analyze(self, *args, **kwargs):
            raise SemanticExtractorError("DeepSeek V3.2 语义抽取失败: HTTP 429")

    monkeypatch.setattr("profile_engine.service.get_semantic_extractor", lambda *_: FailingExtractor())
    monkeypatch.setattr("profile_engine.demo.get_model_endpoint", _available_endpoint)
    monkeypatch.setattr(
        "profile_engine.demo.chat_completion",
        lambda *_args, **_kwargs: ("你继续说，我会认真听。", "deepseek/deepseek-v3.2"),
    )
    app.dependency_overrides[demo_auth] = lambda: "demo-fallback-test-tenant"
    try:
        with TestClient(app) as client:
            session = client.post("/demo/api/start", json={"display_name": "降级测试用户"}).json()
            turn = client.post("/demo/api/chat", json={
                "user_id": session["user_id"],
                "conversation_id": session["conversation_id"],
                "message_id": "fallback-message-1",
                "expected_profile_version": 1,
                "text": "我想赚钱",
                "history": [],
            })
            assert turn.status_code == 200, turn.text
            body = turn.json()
            assert body["assistant_reply"]
            assert body["engine"]["semantic_extractor_version"] == "deterministic-zh-v1"
            assert body["engine"]["strategy_trace"]["semantic_fallback"] == "deepseek_unavailable"
    finally:
        app.dependency_overrides.pop(demo_auth, None)


def test_demo_chat_returns_real_model_no_response_without_fallback(monkeypatch):
    monkeypatch.setattr("profile_engine.demo.get_model_endpoint", _available_endpoint)
    real_ingest = demo_module.ingest_message
    calls = {"ingest": 0, "completion": 0}

    def counted_ingest(*args, **kwargs):
        calls["ingest"] += 1
        return real_ingest(*args, **kwargs)

    def fail_with_403(*_args, **_kwargs):
        calls["completion"] += 1
        if calls["completion"] > 1:
            return "第二次只重试模型调用，不重复更新画像。", "deepseek/deepseek-v3.2"
        request = httpx.Request("POST", "https://openrouter.example/v1/chat/completions")
        response = httpx.Response(403, request=request, json={
            "error": {"message": "Access denied from this region"},
        })
        raise httpx.HTTPStatusError("403 Forbidden", request=request, response=response)

    monkeypatch.setattr("profile_engine.demo.ingest_message", counted_ingest)
    monkeypatch.setattr("profile_engine.demo.chat_completion", fail_with_403)
    app.dependency_overrides[demo_auth] = lambda: "demo-no-response-test-tenant"
    try:
        with TestClient(app) as client:
            session = client.post("/demo/api/start", json={"display_name": "无返回测试用户"}).json()
            turn = client.post("/demo/api/chat", json={
                "user_id": session["user_id"],
                "conversation_id": session["conversation_id"],
                "message_id": "no-response-message-1",
                "expected_profile_version": 1,
                "text": "测试网络失败",
                "history": [],
            })
            assert turn.status_code == 502, turn.text
            body = turn.json()
            assert body["code"] == "model_no_response"
            assert "模型无返回：OpenRouter HTTP 403" in body["message"]
            assert "fallback" not in body["message"].lower()
            assert body["details"]["provider"] == "deepseek"
            assert body["details"]["model"] == "deepseek/deepseek-v3.2"
            assert body["details"]["http_status"] == 403
            assert body["details"]["upstream_message"] == "Access denied from this region"
            assert body["details"]["engine"]["strategy_trace"]["chat_responder"] == "no-response"

            retried = client.post("/demo/api/chat", json={
                "user_id": session["user_id"],
                "conversation_id": session["conversation_id"],
                "message_id": "no-response-message-1",
                "expected_profile_version": body["details"]["profile_version"],
                "text": "测试网络失败",
                "history": [],
            })
            assert retried.status_code == 200, retried.text
            assert retried.json()["assistant_reply"] == "第二次只重试模型调用，不重复更新画像。"
            cached = client.post("/demo/api/chat", json={
                "user_id": session["user_id"],
                "conversation_id": session["conversation_id"],
                "message_id": "no-response-message-1",
                "expected_profile_version": body["details"]["profile_version"],
                "text": "测试网络失败",
                "history": [],
            })
            assert cached.status_code == 200
            assert cached.json()["assistant_reply"] == retried.json()["assistant_reply"]
            assert calls == {"ingest": 2, "completion": 2}
    finally:
        app.dependency_overrides.pop(demo_auth, None)
