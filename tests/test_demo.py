from fastapi.testclient import TestClient

from profile_engine.api import app
from profile_engine.demo import _fallback_reply, demo_auth
from profile_engine.extractor import DeterministicSemanticExtractor, SemanticExtractorError


def test_casual_chat_does_not_force_a_closing_question():
    guidance = DeterministicSemanticExtractor().analyze("我最近有点想去看看闺蜜").reply_guidance
    assert guidance.question_count == 0
    for hints in ({}, {"empathy_first": True}, {"allow_resume_later": True}):
        reply = _fallback_reply("我最近有点想去看看闺蜜", hints)
        assert not reply.endswith(("?", "？"))


def test_demo_page_and_conversation_flow():
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
