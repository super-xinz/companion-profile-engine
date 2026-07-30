from fastapi.testclient import TestClient

from profile_engine.api import app
from profile_engine.demo import demo_auth


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
