import uuid
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import select

from profile_engine.api import app
from profile_engine.db import SessionLocal
from profile_engine.demo import demo_auth
from profile_engine.models import ProfileVersion, User
from profile_engine.schemas import ReplyGuidance, SemanticAnalysis
from profile_engine.workspace import _actor


def test_caller_controlled_actor_header_cannot_impersonate_workspace_members():
    assert _actor("任意伪造管理员") == "系统管理员"


def test_rule_comparison_runs_semantic_analysis_once(monkeypatch):
    tenant = f"rules-model-{uuid.uuid4().hex}"
    calls = {"factory": 0, "analyze": 0, "provider": None}

    class CountingExtractor:
        version = "counting-extractor-v1"

        def analyze(self, *_args, **_kwargs):
            calls["analyze"] += 1
            return SemanticAnalysis(reply_guidance=ReplyGuidance())

    def factory(provider=None):
        calls["factory"] += 1
        calls["provider"] = provider
        return CountingExtractor()

    monkeypatch.setattr("profile_engine.workspace.get_semantic_extractor", factory)
    app.dependency_overrides[demo_auth] = lambda: tenant
    try:
        with TestClient(app) as client:
            response = client.post("/demo/api/rules/test", json={
                "text": "我通常会先自己梳理，再和团队讨论。",
                "model_provider": "deepseek",
            })
            assert response.status_code == 200, response.text
            assert response.json()["production_profile_unchanged"] is True
            assert response.json()["production"]["extractor_version"] == "counting-extractor-v1"
            assert response.json()["candidate"]["extractor_version"] == "counting-extractor-v1"
            assert calls == {"factory": 1, "analyze": 1, "provider": "deepseek"}
    finally:
        app.dependency_overrides.pop(demo_auth, None)


def test_multi_person_profile_and_rule_workspaces():
    tenant = f"workspace-{uuid.uuid4().hex}"
    app.dependency_overrides[demo_auth] = lambda: tenant
    headers = {"X-Demo-Code": "ignored", "X-Actor-Name": quote("系统管理员")}
    try:
        with TestClient(app) as client:
            boot = client.post("/demo/api/workspace/bootstrap", headers=headers)
            assert boot.status_code == 200, boot.text
            template_ids = {
                "person-1988-08-09", "person-1989-10-15", "person-1989-11-28",
                "person-1996-03-28", "person-1998-12-06",
            }
            assert template_ids <= {person["user_id"] for person in boot.json()["people"]}

            duplicate_yaml = client.post("/demo/api/rules/documents/parse", headers=headers, json={
                "asset": "schema",
                "document_text": "schema_version: v1\nschema_version: v2\n",
            })
            assert duplicate_yaml.status_code == 422

            for user_id, expected_code in (("person-1989-11-28", "SX/SO｜7w8"), ("person-1996-03-28", "SO/SX｜2w1")):
                detail = client.get(f"/demo/api/people/{user_id}", headers=headers)
                assert detail.status_code == 200, detail.text
                assert "enneagram_profile" not in detail.json()["profile"]
                audit = client.get(f"/demo/api/people/{user_id}/profile-explain", headers=headers)
                assert audit.status_code == 200, audit.text
                assert audit.json()["hidden_reference_evidence_count"] > 0
                assert all(
                    item["source_type"] != "cold_start_prior"
                    for key in ("supporting_evidence", "counter_evidence", "invalidated_evidence")
                    for item in audit.json()[key]
                )
                expert = client.get(f"/demo/api/people/{user_id}/expert-reference", headers=headers)
                assert expert.status_code == 200, expert.text
                assert expert.json()["profile"]["enneagram_profile"]["identity"]["code"] == expected_code

            with SessionLocal() as db:
                target = db.scalar(select(ProfileVersion).where(
                    ProfileVersion.user_id == db.scalar(select(User).where(
                        User.tenant_id == tenant,
                        User.tenant_user_id == "person-1996-03-28",
                    )).id
                ).order_by(ProfileVersion.version_no.desc()).limit(1))
                assert target is not None
                snapshot = target.snapshot
                snapshot["enneagram_profile"] = {"status": "unassigned", "identity": {}}
                target.snapshot = snapshot
                db.commit()

            refreshed = client.post("/demo/api/workspace/bootstrap", headers=headers)
            assert refreshed.status_code == 200, refreshed.text
            template_people = [
                person for person in refreshed.json()["people"] if person["user_id"] in template_ids
            ]
            assert template_people
            assert all(person["conversation_count"] == 1 for person in template_people)
            restored = client.get("/demo/api/people/person-1996-03-28", headers=headers)
            assert restored.status_code == 200, restored.text
            assert "enneagram_profile" not in restored.json()["profile"]
            restored_expert = client.get("/demo/api/people/person-1996-03-28/expert-reference", headers=headers)
            assert restored_expert.json()["profile"]["enneagram_profile"]["identity"]["code"] == "SO/SX｜2w1"

            disabled_template_id = "person-1988-08-09"
            with SessionLocal() as db:
                disabled = db.scalar(select(User).where(
                    User.tenant_id == tenant,
                    User.tenant_user_id == disabled_template_id,
                ))
                assert disabled is not None
                disabled.inference_enabled = False
                db.commit()
            refreshed = client.post("/demo/api/workspace/bootstrap", headers=headers)
            assert refreshed.status_code == 200, refreshed.text
            assert disabled_template_id not in {
                person["user_id"] for person in refreshed.json()["people"]
            }
            searched = client.get("/demo/api/people", headers=headers)
            assert searched.status_code == 200, searched.text
            assert disabled_template_id not in {
                person["user_id"] for person in searched.json()["people"]
            }

            minimal = client.post("/demo/api/people", headers=headers, json={
                "display_name": "无生日测试人物", "birth_date": None, "notes": "推断开关回归测试",
            })
            assert minimal.status_code == 200, minimal.text
            with SessionLocal() as db:
                minimal_user = db.scalar(select(User).where(
                    User.tenant_id == tenant,
                    User.tenant_user_id == minimal.json()["person"]["user_id"],
                ))
                assert minimal_user is not None
                assert minimal_user.profile_consent is True
                assert minimal_user.inference_enabled is True

            created = client.post("/demo/api/people", headers=headers, json={
                "display_name": "测试人物", "birth_date": None, "notes": "隔离测试",
                "enneagram": {
                    "core_type": 7, "wing": 6,
                    "primary_instinct": "SX", "secondary_instinct": "SO",
                    "source": "expert_confirmed", "confidence": .95,
                },
            })
            assert created.status_code == 200, created.text
            person = created.json()["person"]
            conversation = created.json()["conversation"]

            detail = client.get(f"/demo/api/people/{person['user_id']}", headers=headers)
            assert detail.status_code == 200, detail.text
            assert detail.json()["person"]["conversation_count"] == 1
            assert "digital_code_profile" not in detail.json()["profile"]
            assert "enneagram_profile" not in detail.json()["profile"]
            expert = client.get(f"/demo/api/people/{person['user_id']}/expert-reference", headers=headers).json()
            assert expert["profile"]["enneagram_profile"]["identity"]["code"] == "SX/SO｜7w6"
            trait = expert["profile"]["core_traits"]["energy_mode"]["extroversion"]["value"]
            edited = client.post(f"/demo/api/people/{person['user_id']}/manual-edit", headers=headers, json={
                "expected_profile_version": detail.json()["profile_version"],
                "target_path": "core_traits.energy_mode.extroversion",
                "value": min(1, trait + .2),
                "reason": "专家根据线下访谈确认",
            })
            assert edited.status_code == 200, edited.text
            assert edited.json()["locked"] is True
            enneagram = client.post(f"/demo/api/people/{person['user_id']}/enneagram", headers=headers, json={
                "expected_profile_version": edited.json()["profile_version"],
                "enneagram": {
                    "core_type": 8, "wing": 9,
                    "primary_instinct": "SP", "secondary_instinct": "SX",
                    "source": "expert_confirmed", "confidence": .95,
                },
                "reason": "专家复核九型测评结果",
            })
            assert enneagram.status_code == 200, enneagram.text
            assert enneagram.json()["enneagram_profile"]["identity"]["code"] == "SP/SX｜8w9"

            rejected_path = client.post(
                f"/demo/api/people/{person['user_id']}/manual-edit",
                headers=headers,
                json={
                    "expected_profile_version": enneagram.json()["profile_version"],
                    "target_path": "runtime.memories",
                    "value": [],
                    "reason": "不应允许通过人工编辑覆盖运行时内部结构",
                },
            )
            assert rejected_path.status_code == 422

            messages = client.get(
                f"/demo/api/people/{person['user_id']}/conversations/{conversation['conversation_id']}/messages",
                headers=headers,
            )
            assert messages.status_code == 200
            assert messages.json()["messages"] == []

            rules = client.get("/demo/api/rules/workspace", headers=headers)
            assert rules.status_code == 200, rules.text
            current = rules.json()["current"]
            draft = client.post("/demo/api/rules/drafts", headers=headers, json={
                "title": "自动化测试草稿", "base_revision_id": current["id"],
            })
            assert draft.status_code == 200, draft.text
            revision = draft.json()["revision"]
            content = revision["canonical_json"]
            content["cold_start"]["status"] = "专家协作测试"
            saved = client.put(f"/demo/api/rules/drafts/{revision['id']}", headers=headers, json={
                "canonical_json": content, "change_summary": "更新规则状态说明",
            })
            assert saved.status_code == 200, saved.text
            assert saved.json()["revision"]["validation_report"]["valid"] is True

            submitted = client.post(f"/demo/api/rules/revisions/{revision['id']}/submit", headers=headers, json={"note": "提交"})
            assert submitted.status_code == 200, submitted.text
            approved = client.post(f"/demo/api/rules/revisions/{revision['id']}/approve", headers=headers, json={"note": "通过"})
            assert approved.status_code == 200, approved.text
            published = client.post(f"/demo/api/rules/revisions/{revision['id']}/publish", headers=headers, json={"note": "发布"})
            assert published.status_code == 200, published.text
            assert published.json()["rule_pack"]["status"] == "published"

            isolated = client.post("/demo/api/rules/test", headers=headers, json={
                "revision_id": revision["id"],
                "text": "聚会后我通常需要一个人待一会儿才能恢复。",
                "user_id": person["user_id"],
            })
            assert isolated.status_code == 200, isolated.text
            assert isolated.json()["isolated"] is True
            assert isolated.json()["production_profile_unchanged"] is True

            malformed = client.post("/demo/api/rules/drafts", headers=headers, json={
                "title": "异常结构验证", "base_revision_id": revision["id"],
            })
            malformed_id = malformed.json()["revision"]["id"]
            malformed_save = client.put(
                f"/demo/api/rules/drafts/{malformed_id}",
                headers=headers,
                json={"canonical_json": {"schema": []}, "change_summary": "结构错误测试"},
            )
            assert malformed_save.status_code == 200, malformed_save.text
            report = malformed_save.json()["revision"]["validation_report"]
            assert report["valid"] is False
            assert "字段类型不正确" in report["errors"][0]
    finally:
        app.dependency_overrides.pop(demo_auth, None)
