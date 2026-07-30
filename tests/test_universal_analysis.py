import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from profile_engine.api import app
from profile_engine.schemas import ReplyGuidance, SemanticAnalysis, SemanticFrame, TraitSignal


class UniversalFakeExtractor:
    version = "universal-fake-v1"

    def analyze(self, text, trait_catalog=None, recent_turns=None):
        return SemanticAnalysis(
            frames=[
                SemanticFrame(frame_id="frm-name", subject="user", predicate="name", object="张鑫",
                    semantic_domain="identity_fact", explicitness=.99, extractor_confidence=.99,
                    supporting_span="我是张鑫"),
                SemanticFrame(frame_id="frm-school", subject="user", predicate="education_institution",
                    object="南京大学", semantic_domain="identity_fact", explicitness=.99,
                    extractor_confidence=.98, supporting_span="我在南京大学读书"),
            ],
            trait_signals=[TraitSignal(target_trait="risk_tolerance", direction="increase", strength=.8,
                confidence=.9, evidence_scope="explicit_self_report", supporting_span="我愿意承担创业风险",
                rationale="用户明确表达愿意承担创业风险")],
            reply_guidance=ReplyGuidance(intent="identity_and_goal_disclosure", tone="natural",
                answer_first=True, max_sentences=3, question_count=1,
                focus="确认身份并询问创业目标", avoid=["学校刻板印象"]),
        )

    def extract(self, text):
        return self.analyze(text).frames


def test_model_candidates_are_constrained_by_profile_schema_and_facts_use_existing_memory(monkeypatch):
    monkeypatch.setattr("profile_engine.service.get_semantic_extractor", lambda: UniversalFakeExtractor())
    user = f"universal-{uuid.uuid4().hex}"
    headers = {"X-API-Key": "local-development-key", "X-Tenant-ID": "universal-test"}
    with TestClient(app) as client:
        init = client.post("/v1/profiles:init", headers={**headers, "Idempotency-Key": f"init-{user}"}, json={
            "tenant_user_id": user, "consent": {"profile": True, "sensitive_inference": True},
        })
        assert init.status_code == 200, init.text
        turn = client.post(f"/v1/profiles/{user}/messages:ingest",
            headers={**headers, "Idempotency-Key": f"msg-{user}"}, json={
                "conversation_id": "c1", "message_id": "m1", "expected_profile_version": 1,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "text": "我是张鑫，我在南京大学读书，我愿意承担创业风险。",
                "context": {"previous_turn_count": 1, "recent_turns": [
                    {"role": "user", "content": "有什么创业机会？"}
                ]},
            })
        assert turn.status_code == 200, turn.text
        body = turn.json()
        assert body["profile_version"] == 2
        assert body["accepted_trait_signals"][0]["target_trait"] == "risk_tolerance"
        assert body["profile_patch"][0]["field"].endswith("risk_tolerance")
        facts = [op for op in body["runtime_operations"] if op["operation"] == "UPSERT_FACT"]
        assert {(fact["key"], fact["value"]) for fact in facts} == {
            ("name", "张鑫"), ("education_institution", "南京大学")
        }
        assert body["reply_hints"]["focus"] == "确认身份并询问创业目标"
        profile = client.get(f"/v1/profiles/{user}", headers=headers).json()["profile"]
        assert profile["identity"]["display_name"] == "张鑫"
        assert any(memory.get("key") == "education_institution" for memory in profile["runtime"]["memories"])

