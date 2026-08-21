import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

import profile_engine.demo as demo_module
from profile_engine.api import app
from profile_engine.db import SessionLocal
from profile_engine.model_gateway import ModelEndpoint
from profile_engine.models import (CurrentState, ManualOverride, Memory,
                                   ProfileEvidence, ProfileVersion,
                                   RuntimePreference, User)
from profile_engine.profile import TRAIT_NAMES
from profile_engine.schemas import ReplyGuidance, SemanticAnalysis, SemanticFrame, TraitSignal


HEADERS = {"X-API-Key": "local-development-key", "X-Tenant-ID": "presentation-test"}


def idem(value: str) -> dict:
    return {**HEADERS, "Idempotency-Key": value}


def test_public_profile_hides_reference_models_and_turn_summary_explains_changes():
    user = f"public-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        initialized = client.post("/v1/profiles:init", headers=idem(f"init-{user}"), json={
            "tenant_user_id": user,
            "birth_date": "1998-12-06",
            "enneagram": {
                "core_type": 7, "wing": 6,
                "primary_instinct": "SX", "secondary_instinct": "SO",
                "source": "user_supplied", "confidence": .8,
            },
            "consent": {"profile": True, "sensitive_inference": True},
        })
        assert initialized.status_code == 200, initialized.text

        public = client.get(f"/v1/public-profiles/{user}", headers=HEADERS)
        assert public.status_code == 200, public.text
        profile = public.json()["profile"]
        for hidden in (
            "birth_analysis", "digital_code_profile", "mbti_dimensions",
            "enneagram_profile", "source_profile_document", "source_portrait",
        ):
            assert hidden not in profile
        assert "birth_date" not in profile["identity"]
        trait = profile["stable_tendencies"]["action_mode"]["structure_pref"]
        assert "value" not in trait and "confidence" not in trait
        assert trait["evidence_grade"] == "unverified"
        assert trait["direction"] == "unknown"
        assert trait["tendency"] == "证据待积累"

        update = client.post(
            f"/v1/profiles/{user}/messages:ingest",
            headers=idem(f"turn-{user}"),
            json={
                "conversation_id": "planning", "message_id": "planning-1",
                "expected_profile_version": 1,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "text": "我通常喜欢计划。",
            },
        )
        assert update.status_code == 200, update.text
        summary = update.json()["update_summary"]
        assert summary["status"] == "updated"
        assert summary["items"][0]["label"] == "结构化偏好"
        assert summary["items"][0]["evidence_quote"] == "我通常喜欢计划。"
        assert summary["items"][0]["why"]
        assert summary["items"][0]["how"]
        assert "场景表现依据" in summary["maintenance"]

        public = client.get(f"/v1/public-profiles/{user}", headers=HEADERS).json()["profile"]
        trait = public["stable_tendencies"]["action_mode"]["structure_pref"]
        assert trait["evidence_grade"] == "emerging"
        assert trait["basis"]["self_report"] == 1


def test_reply_prompt_excludes_reference_models(monkeypatch):
    captured = {}

    monkeypatch.setattr(demo_module, "get_model_endpoint", lambda _provider: ModelEndpoint(
        provider="deepseek", label="测试模型", route_label="测试路由",
        api_key="test-key", base_url="https://example.invalid/v1",
        model="test/model", timeout=30, extra_headers={},
    ))

    def completion(_endpoint, messages, **_kwargs):
        captured["system"] = messages[0]["content"]
        return "收到。", "test/model"

    monkeypatch.setattr(demo_module, "chat_completion", completion)
    profile = {
        "portrait": {"essence": {"content": "REFERENCE_PORTRAIT_MARKER"}},
        "digital_code_profile": {
            "status": "derived", "code": "REFERENCE_DIGITAL_MARKER",
            "domains": {"personality": {"summary": "REFERENCE_SUMMARY_MARKER"}},
        },
        "runtime": {
            "current_state": {},
            "interaction_preferences": {"response_length": "short"},
            "memories": [],
        },
        "meta": {"overall_confidence": .99},
    }
    engine = {
        "reply_hints": {"max_sentences": 3, "question_count": 0},
        "strategy_trace": {
            "scene": None, "trusted_trait_inputs": [],
            "reference_models_excluded": ["digital_code", "mbti", "enneagram", "birth_analysis"],
        },
    }
    reply, _ = demo_module._generate_reply("你好", [], profile, engine, "deepseek")
    assert reply == "收到。"
    system = captured["system"]
    assert "REFERENCE_DIGITAL_MARKER" not in system
    assert "REFERENCE_SUMMARY_MARKER" not in system
    assert "REFERENCE_PORTRAIT_MARKER" not in system
    assert '"response_length": "short"' in system


def test_all_profile_forget_purges_profile_state_and_history():
    user_id = f"purge-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        initialized = client.post("/v1/profiles:init", headers=idem(f"init-{user_id}"), json={
            "tenant_user_id": user_id,
            "display_name": "待清除人物",
            "birth_date": "1998-12-06",
            "enneagram": {
                "core_type": 7, "wing": 6,
                "primary_instinct": "SX", "secondary_instinct": "SO",
                "source": "user_supplied", "confidence": .8,
            },
            "consent": {"profile": True, "sensitive_inference": True},
        })
        assert initialized.status_code == 200, initialized.text
        changed = client.post(
            f"/v1/profiles/{user_id}/messages:ingest",
            headers=idem(f"pref-{user_id}"),
            json={
                "conversation_id": "c1", "message_id": "m1", "expected_profile_version": 1,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "text": "以后回答短一点，我今天很累。",
            },
        )
        assert changed.status_code == 200, changed.text
        forgotten = client.post(f"/v1/profiles/{user_id}:forget", headers=idem(f"forget-{user_id}"), json={
            "expected_profile_version": changed.json()["profile_version"],
            "scope": "all_profile", "reason": "用户要求清除全部画像",
        })
        assert forgotten.status_code == 200, forgotten.text
        assert forgotten.json()["profile_version"] == 1

        internal = client.get(f"/v1/profiles/{user_id}", headers=HEADERS).json()["profile"]
        assert internal["identity"]["display_name"] is None
        assert internal["identity"]["birth_date"] is None
        assert internal["digital_code_profile"]["status"] == "unassigned"
        assert internal["enneagram_profile"]["status"] == "unassigned"
        assert all(
            entry["value"] == .5
            for category in internal["core_traits"].values()
            for entry in category.values()
        )
        assert internal["runtime"] == {
            "interaction_preferences": {}, "current_state": {}, "memories": [],
        }
        assert "source_profile_document" not in internal

        with SessionLocal() as db:
            user = db.scalar(select(User).where(
                User.tenant_id == HEADERS["X-Tenant-ID"], User.tenant_user_id == user_id,
            ))
            assert user is not None
            assert user.profile_consent is False
            assert user.sensitive_inference_consent is False
            assert user.inference_enabled is False
            for model in (ProfileEvidence, Memory, CurrentState, RuntimePreference, ManualOverride):
                assert db.scalar(select(func.count()).select_from(model).where(model.user_id == user.id)) == 0
            assert db.scalar(select(func.count()).select_from(ProfileVersion).where(
                ProfileVersion.user_id == user.id,
            )) == 1


class AllTraitsExtractor:
    version = "all-traits-contract-v1"

    def analyze(self, text, trait_catalog=None, recent_turns=None):
        trait = text.strip()
        span = trait
        return SemanticAnalysis(
            frames=[SemanticFrame(
                frame_id=f"frame-{trait}", subject="user", predicate=f"reports_{trait}",
                semantic_domain="self_evaluation", temporal_scope="habitual", frequency="usually",
                explicitness=.95, extractor_confidence=.95, supporting_span=span,
            )],
            trait_signals=[TraitSignal(
                target_trait=trait, direction="increase", strength=.8, confidence=.9,
                evidence_scope="explicit_self_report", supporting_span=span,
                rationale=f"用户明确自述 {trait}",
            )],
            reply_guidance=ReplyGuidance(question_count=0),
        )

    def extract(self, text):
        return self.analyze(text).frames


def test_all_17_core_traits_are_reachable_through_validated_dialogue_updates(monkeypatch):
    monkeypatch.setattr("profile_engine.service.get_semantic_extractor", lambda: AllTraitsExtractor())
    user = f"coverage-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        initialized = client.post("/v1/profiles:init", headers=idem(f"init-{user}"), json={
            "tenant_user_id": user,
            "consent": {"profile": True, "sensitive_inference": False},
        })
        assert initialized.status_code == 200, initialized.text
        version = 1
        for index, trait in enumerate(TRAIT_NAMES):
            response = client.post(
                f"/v1/profiles/{user}/messages:ingest",
                headers=idem(f"trait-{user}-{index}"),
                json={
                    "conversation_id": f"session-{index}", "message_id": f"message-{index}",
                    "expected_profile_version": version,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "text": trait,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["accepted_trait_signals"][0]["target_trait"] == trait
            assert body["profile_patch"][0]["field"].endswith(trait)
            version = body["profile_version"]
        assert version == 1 + len(TRAIT_NAMES)
