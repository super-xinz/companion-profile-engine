import uuid
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import select

from profile_engine.api import app
from profile_engine.db import SessionLocal
from profile_engine.demo import demo_auth
from profile_engine.models import User


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
            assert detail.json()["profile"]["enneagram_profile"]["identity"]["code"] == "SX/SO｜7w6"
            trait = detail.json()["profile"]["core_traits"]["energy_mode"]["extroversion"]["value"]
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
    finally:
        app.dependency_overrides.pop(demo_auth, None)
