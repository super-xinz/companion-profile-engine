import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from profile_engine.api import app
from profile_engine.db import SessionLocal
from profile_engine.demo import demo_auth
from profile_engine.models import ChatMessage, Conversation, User
from profile_engine.public_demo import (PUBLIC_TEMPLATE_IDENTITIES,
                                        public_conversation_id,
                                        public_dynamic_summary,
                                        public_preferences,
                                        sanitize_public_text)


FORBIDDEN_PUBLIC_MARKERS = (
    "mbti", "enneagram", "numerology", "九型", "数字密码", "数字学", "八字",
    "bazi", "person-1988", "person-1989", "person-1996", "person-1998",
    "1988-08-09", "1989-10-15", "1989-11-28", "1996-03-28", "1998-12-06",
    ".xlsx", "birth_date", "source_profile", "type_label", "engine_trace",
    "rule_pack", "model_config", "permissions",
    "四柱", "日主", "身强", "身弱", "偏财格", "七杀格", "伤官格", "正官格",
    "完美型", "助人型", "成就型", "观察型", "忠诚型", "探索型", "挑战型", "和平型",
    "1号", "1型", "内心码", "制约数", "天赋数", "坐镇码", "缺1", "6318",
    "戊辰 庚申 丙申", "己巳乙亥壬辰", "deepseek", "openai", "gpt",
    "claude", "anthropic", "gemini", "glm", "kimi", "moonshot", "openrouter",
)


def _assert_public_payload(payload) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for marker in FORBIDDEN_PUBLIC_MARKERS:
        assert marker not in text


def test_public_text_sanitizer_covers_method_codes_birth_data_and_source_files():
    raw = (
        "MBTI ENTP；九型人格 7w8，SX/SO；Numerology 数字密码；"
        "生辰八字 Bazi，四柱、日主、身强、身弱、偏财格、七杀格、伤官格、正官格；"
        "完美型、助人型、成就型、观察型、忠诚型、探索型、挑战型、和平型、1号、1型；"
        "内心码、制约数、天赋数、坐镇码、缺1、6318；戊辰 庚申 丙申、己巳乙亥壬辰；"
        "生日：1998-12-06；person-1998-12-06；"
        "1998年12月6日_机器人性格设定.xlsx；整体可信度 45%。"
        "M B T I、E N N E A G R A M、N U M E R O L O G Y、B A Z I；"
        "E-N-T-P、S X / S O、九 型 人 格、数 字 密 码、八 字。"
        "DeepSeek、D e e p S e e k、OpenAI、G P T-5、Claude、Gemini、GLM、Kimi、OpenRouter。"
        "ＭＢＴＩ、ＥＮＴＰ、甲 子 乙 丑。"
    )
    cleaned = sanitize_public_text(raw)
    _assert_public_payload(cleaned)
    assert "45%" in cleaned


def test_public_workspace_uses_aliases_and_whitelisted_dtos_only():
    tenant = f"public-workspace-{uuid.uuid4().hex}"
    app.dependency_overrides[demo_auth] = lambda: tenant
    try:
        with TestClient(app) as client:
            boot = client.post("/demo/api/workspace/bootstrap")
            assert boot.status_code == 200, boot.text
            assert set(boot.json()) == {"people"}
            people = boot.json()["people"]
            expected_aliases = {
                identity.public_id for identity in PUBLIC_TEMPLATE_IDENTITIES.values()
            }
            assert len(people) == 5
            assert expected_aliases == {item["public_id"] for item in people}
            assert {f"互动样本 {letter}" for letter in "ABCDE"} <= {
                item["display_name"] for item in people
            }
            for item in people:
                assert set(item) == {
                    "public_id", "display_name", "tagline", "profile_version",
                    "confidence", "confidence_explanation", "conversation_count",
                    "updated_at",
                }
                assert 0 <= item["confidence"] <= 1
                assert "不是对一个人的判断准确率" in item["confidence_explanation"]
            _assert_public_payload(boot.json())

            summaries = {}
            for identity in PUBLIC_TEMPLATE_IDENTITIES.values():
                example = client.get(f"/demo/api/people/{identity.public_id}")
                assert example.status_code == 200, example.text
                payload = example.json()
                summaries[identity.public_id] = payload["dynamic_summary"]
                assert 3 <= len(payload["communication_preferences"]) <= 5
                assert all(
                    set(preference) == {"name", "value"}
                    for preference in payload["communication_preferences"]
                )
                _assert_public_payload(payload)
            assert len(set(summaries.values())) == 5

            alias = "profile-sky"
            detail = client.get(f"/demo/api/people/{alias}")
            assert detail.status_code == 200, detail.text
            body = detail.json()
            assert set(body) == {
                "person", "metrics", "dynamic_summary", "communication_preferences",
                "confidence", "confidence_explanation", "profile_version", "conversations",
            }
            assert body["person"]["public_id"] == alias
            assert len(body["metrics"]) == 17
            assert all(set(metric) == {"name", "value", "confidence"} for metric in body["metrics"])
            assert all(0 <= metric["value"] <= 1 for metric in body["metrics"])
            assert all(0 <= metric["confidence"] <= 1 for metric in body["metrics"])
            assert isinstance(body["dynamic_summary"], str)
            assert isinstance(body["communication_preferences"], list)
            _assert_public_payload(body)

            internal = client.get("/demo/api/people/person-1998-12-06")
            assert internal.status_code == 404

            listed = client.get("/demo/api/people", params={"q": "互动样本 E"})
            assert listed.status_code == 200
            assert [item["public_id"] for item in listed.json()["people"]] == [alias]

            created = client.post(
                f"/demo/api/people/{alias}/conversations",
                json={"title": "ENTP 与九型人格 7w8 的 1998-12-06.xlsx"},
            )
            assert created.status_code == 200, created.text
            conversation = created.json()["conversation"]
            assert conversation["conversation_id"].startswith("conversation-")
            _assert_public_payload(conversation)

            with SessionLocal() as db:
                user = db.scalar(select(User).where(
                    User.tenant_id == tenant,
                    User.tenant_user_id == "person-1998-12-06",
                ))
                stored = db.scalars(select(Conversation).where(
                    Conversation.user_id == user.id,
                )).all()
                item = next(
                    row for row in stored
                    if public_conversation_id(user, row.external_id)
                    == conversation["conversation_id"]
                )
                db.add(ChatMessage(
                    conversation_id=item.id,
                    external_id="person-1998-12-06",
                    role="assistant",
                    content="你是 ENTP，九型人格 7w8，来源在 secret.xlsx，可信度 45%。",
                    profile_version=body["profile_version"],
                    engine_trace={"strategy_trace": {"source": "secret.xlsx"}},
                ))
                db.commit()

            messages = client.get(
                f"/demo/api/people/{alias}/conversations/"
                f"{conversation['conversation_id']}/messages"
            )
            assert messages.status_code == 200, messages.text
            item = messages.json()["messages"][0]
            assert set(item) == {"role", "content", "profile_version", "created_at"}
            assert "45%" in item["content"]
            _assert_public_payload(messages.json())
    finally:
        app.dependency_overrides.pop(demo_auth, None)


def test_public_template_defaults_are_overridden_by_valid_runtime_preferences():
    profile = {
        "identity": {"template_person_id": "person-1988-08-09"},
        "runtime": {
            "interaction_preferences": {
                "response_length": "long",
                "directness": 0.91,
                "empathy_first": False,
                "unknown_internal_option": "secret",
            },
            "current_state": {"engagement": {"value": 0.8}},
            "memories": [],
        },
    }

    preferences = public_preferences(profile)
    values = {item["name"]: item["value"] for item in preferences}
    assert len(preferences) == 5
    assert values["回复篇幅"] == "充分"
    assert values["表达直接度"] == 0.91
    assert values["优先回应感受"] is False
    assert values["追问频率"] == 0.45
    assert values["幽默程度"] == 0.7
    assert "已结合 1 项近期互动状态" in public_dynamic_summary(profile)
    assert "已确认 3 项沟通偏好" in public_dynamic_summary(profile)
    _assert_public_payload({
        "summary": public_dynamic_summary(profile),
        "preferences": preferences,
    })


def test_non_public_workspace_and_rule_routes_are_not_reachable():
    tenant = f"blocked-workspace-{uuid.uuid4().hex}"
    app.dependency_overrides[demo_auth] = lambda: tenant
    blocked = (
        ("post", "/demo/api/start", {"display_name": "测试"}),
        ("post", "/demo/api/people", {"display_name": "测试"}),
        ("post", "/demo/api/people/profile-sky/manual-edit", {}),
        ("post", "/demo/api/people/profile-sky/enneagram", {}),
        ("get", "/demo/api/people/profile-sky/profile-explain", None),
        ("get", "/demo/api/rules/workspace", None),
        ("post", "/demo/api/rules/test", {"text": "测试"}),
        ("post", "/demo/api/rules/drafts", {}),
        ("post", "/demo/api/members", {}),
    )
    try:
        with TestClient(app) as client:
            for method, path, payload in blocked:
                response = client.request(method, path, json=payload)
                assert response.status_code == 404, (method, path, response.text)
                assert response.json() == {"detail": "Not Found"}
            assert client.get("/rules").status_code == 404
            assert client.get("/assets/rules.js").status_code == 404
            assert client.get("/assets/rules.html").status_code == 404
            assert client.get("/assets/demo.html").status_code == 404
            for variant in (
                "/demo/api//rules/workspace",
                "/demo/api/RULES/workspace",
                "/demo/api/people/profile-sky%2Fprofile-explain",
                "/assets/%72ules.js",
                "/assets/rules.js/extra",
            ):
                assert client.get(variant, follow_redirects=False).status_code == 404
    finally:
        app.dependency_overrides.pop(demo_auth, None)
