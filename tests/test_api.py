import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from profile_engine.api import SlidingWindowRateLimiter, app
from profile_engine.config import Settings


HEADERS = {"X-API-Key": "local-development-key", "X-Tenant-ID": "test-tenant"}


def idem(value: str) -> dict:
    return {**HEADERS, "Idempotency-Key": value}


def test_b2b_capabilities_security_headers_and_api_key_challenge():
    with TestClient(app) as client:
        response = client.get("/v1/capabilities", headers=HEADERS)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["api_version"] == "v1"
        assert body["service_version"] == "0.4.2"
        assert body["limits"]["requests_per_minute"] >= 1
        assert response.headers["x-api-version"] == "1"
        assert response.headers["x-ratelimit-limit"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"

        unauthorized = client.get("/v1/capabilities", headers={
            "X-API-Key": "wrong", "X-Tenant-ID": "test-tenant",
        })
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "ApiKey"


def test_production_configuration_fails_closed_and_disables_demo_defaults():
    unsafe = Settings(
        _env_file=None,
        environment="production",
        database_url="sqlite:///./unsafe.db",
        tenant_api_keys={},
        semantic_extractor="deterministic",
    )
    with pytest.raises(RuntimeError, match="生产配置检查失败"):
        unsafe.validate_runtime_configuration()

    production = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql://profile:secret@database/profile",
        tenant_api_keys={"customer-a": "x" * 32},
        semantic_extractor="deterministic",
    )
    production.validate_runtime_configuration()
    assert production.demo_features_active is False
    assert production.api_docs_active is False
    assert production.profile_reset_active is False

    missing_model_key = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql://profile:secret@database/profile",
        tenant_api_keys={"customer-a": "x" * 32},
        semantic_extractor="model",
        allow_external_semantic_processing=True,
        openrouter_api_key=None,
    )
    with pytest.raises(RuntimeError, match="PROFILE_OPENROUTER_API_KEY"):
        missing_model_key.validate_runtime_configuration()

    configured_model = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql://profile:secret@database/profile",
        tenant_api_keys={"customer-a": "x" * 32},
        semantic_extractor="model",
        default_model_provider="claude",
        allow_external_semantic_processing=True,
        openrouter_api_key="sk-or-test",
    )
    configured_model.validate_runtime_configuration()


def test_rate_limiter_is_tenant_scoped_and_returns_retry_window():
    limiter = SlidingWindowRateLimiter()
    assert limiter.check("tenant-a", 2, now=100.0) == (True, 1, 0)
    assert limiter.check("tenant-a", 2, now=101.0) == (True, 0, 0)
    allowed, remaining, retry_after = limiter.check("tenant-a", 2, now=102.0)
    assert allowed is False and remaining == 0 and retry_after == 58
    assert limiter.check("tenant-b", 2, now=102.0) == (True, 1, 0)


def test_golden_profiles_are_complete():
    with TestClient(app) as client:
        cases = (
            ("1988-08-09", "ENFP", 1.0),
            ("1989-10-15", "ENTP", .83),
            ("1989-11-28", "ENTP", 1.0),
            ("1996-03-28", "ESFJ", .65),
            ("1998-12-06", "ISTJ", .45),
        )
        for birth_date, expected_type, expected_extroversion in cases:
            user = f"golden-{birth_date}-{uuid.uuid4().hex[:8]}"
            response = client.post("/v1/profiles:init", headers=idem(f"init-{user}"), json={
                "tenant_user_id": user, "birth_date": birth_date, "timezone": "Asia/Shanghai",
                "consent": {"profile": True, "sensitive_inference": True},
            })
            assert response.status_code == 200, response.text
            profile = response.json()["profile"]
            assert profile["mbti_dimensions"]["type_label"] == expected_type
            assert profile["core_traits"]["energy_mode"]["extroversion"]["value"] == expected_extroversion
            assert sum(len(x) for x in profile["core_traits"].values()) == 17
            assert sum(len(x) for x in profile["behavior_style"].values()) == 18
            assert len(profile["language_style"]["typical_utterances"]) == 9
            assert len(profile["portrait"]) == 5
            assert profile["source_profile_document"]["birth_date"] == birth_date
            assert profile["identity"]["template_person_id"] == f"person-{birth_date}"
            assert set(profile["source_portrait"]) == {
                "essence", "strengths", "weaknesses", "core_tension", "suitable_roles",
            }
            assert profile["meta"]["overall_confidence"] <= 0.45


def test_dialogue_state_machine_idempotency_and_isolation():
    user = f"flow-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        init = client.post("/v1/profiles:init", headers=idem(f"init-{user}"), json={
            "tenant_user_id": user, "birth_date": "1989-10-15", "consent": {"profile": True, "sensitive_inference": True},
        })
        assert init.status_code == 200
        before = init.json()["profile"]["core_traits"]["energy_mode"]["extroversion"]["value"]

        payload = {"conversation_id": "c1", "message_id": "m1", "expected_profile_version": 1,
            "occurred_at": datetime.now(timezone.utc).isoformat(), "text": "其实聚会后我一般要自己待一会儿才能恢复。"}
        update = client.post(f"/v1/profiles/{user}/messages:ingest", headers=idem(f"msg-{user}-1"), json=payload)
        assert update.status_code == 200, update.text
        assert update.json()["profile_version"] == 2
        assert update.json()["profile_patch"][0]["after"] < before
        retry = client.post(f"/v1/profiles/{user}/messages:ingest", headers=idem(f"msg-{user}-1"), json=payload)
        assert retry.json() == update.json()

        preference = client.post(f"/v1/profiles/{user}/messages:ingest", headers=idem(f"msg-{user}-2"), json={
            "conversation_id": "c1", "message_id": "m2", "expected_profile_version": 2,
            "occurred_at": datetime.now(timezone.utc).isoformat(), "text": "以后回答短一点，先听我把话说完。"})
        assert preference.status_code == 200, preference.text
        assert preference.json()["profile_patch"] == []
        assert preference.json()["reply_hints"]["max_sentences"] == 3
        assert preference.json()["reply_hints"]["empathy_first"] is True

        state = client.post(f"/v1/profiles/{user}/messages:ingest", headers=idem(f"msg-{user}-3"), json={
            "conversation_id": "c1", "message_id": "m3", "expected_profile_version": 3,
            "occurred_at": datetime.now(timezone.utc).isoformat(), "text": "我今天很累，没精力。"})
        assert state.status_code == 200, state.text
        assert state.json()["profile_patch"] == []
        assert any(x["operation"] == "SET_STATE" for x in state.json()["runtime_operations"])

        other = client.post(f"/v1/profiles/{user}/messages:ingest", headers=idem(f"msg-{user}-4"), json={
            "conversation_id": "c1", "message_id": "m4", "expected_profile_version": 4,
            "occurred_at": datetime.now(timezone.utc).isoformat(), "text": "我朋友很喜欢聚会。"})
        assert other.status_code == 200
        assert other.json()["no_profile_change"] is True
        assert other.json()["profile_version"] == 4

        conflict = client.post(f"/v1/profiles/{user}/messages:ingest", headers=idem(f"msg-{user}-5"), json={
            "conversation_id": "c1", "message_id": "m5", "expected_profile_version": 1,
            "occurred_at": datetime.now(timezone.utc).isoformat(), "text": "我通常喜欢计划。"})
        assert conflict.status_code == 409


def test_consent_and_forget_birth_inference():
    user = f"forget-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        denied = client.post("/v1/profiles:init", headers=idem(f"denied-{user}"), json={
            "tenant_user_id": f"denied-{user}", "consent": {"profile": False, "sensitive_inference": False}})
        assert denied.status_code == 403
        client.post("/v1/profiles:init", headers=idem(f"init-{user}"), json={
            "tenant_user_id": user, "birth_date": "1998-12-06", "consent": {"profile": True, "sensitive_inference": True}})
        forgotten = client.post(f"/v1/profiles/{user}:forget", headers=idem(f"forget-{user}"), json={
            "expected_profile_version": 1, "scope": "birth_inference", "reason": "用户撤回生日推断授权"})
        assert forgotten.status_code == 200, forgotten.text
        profile = client.get(f"/v1/profiles/{user}", headers=HEADERS).json()["profile"]
        assert profile["birth_analysis"]["numerology_code"] is None
        assert profile["digital_code_profile"]["status"] == "unassigned"
        assert profile["core_traits"]["energy_mode"]["extroversion"]["value"] == 0.5


def test_reset_profile_is_confirmed_idempotent_and_recreates_blank_profile():
    user = f"reset-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        initialized = client.post("/v1/profiles:init", headers=idem(f"init-{user}"), json={
            "tenant_user_id": user,
            "consent": {"profile": True, "sensitive_inference": False},
        })
        assert initialized.status_code == 200
        changed = client.post(f"/v1/profiles/{user}:correct", headers=idem(f"correct-{user}"), json={
            "expected_profile_version": 1,
            "target_path": "core_traits.energy_mode.extroversion",
            "value": 1.0,
            "reason": "测试重置前的画像变化",
        })
        assert changed.status_code == 200

        rejected = client.post(
            f"/v1/profiles/{user}:reset",
            headers=idem(f"reset-invalid-{user}"),
            json={"confirm": False},
        )
        assert rejected.status_code == 422

        headers = idem(f"reset-{user}")
        reset = client.post(
            f"/v1/profiles/{user}:reset",
            headers=headers,
            json={"confirm": True, "display_name": "重置用户"},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["reset"] is True
        assert reset.json()["profile_version"] == 1
        assert reset.json()["profile"]["core_traits"]["energy_mode"]["extroversion"]["value"] == 0.5

        retry = client.post(
            f"/v1/profiles/{user}:reset",
            headers=headers,
            json={"confirm": True, "display_name": "重置用户"},
        )
        assert retry.json() == reset.json()


def test_correction_explanation_and_evidence_reversal():
    user = f"correct-{uuid.uuid4().hex}"
    field = "core_traits.energy_mode.extroversion"
    with TestClient(app) as client:
        initialized = client.post("/v1/profiles:init", headers=idem(f"init-{user}"), json={
            "tenant_user_id": user, "consent": {"profile": True, "sensitive_inference": False}}).json()
        corrected = client.post(f"/v1/profiles/{user}:correct", headers=idem(f"correct-{user}"), json={
            "expected_profile_version": 1, "target_path": field, "value": 1.0, "reason": "用户明确更正"})
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["after"] == 0.6  # explicit correction is capped at one 0.10 step
        explained = client.get(f"/v1/profiles/{user}/explain", headers=HEADERS, params={"field": field}).json()
        evidence_id = explained["supporting_evidence"][-1]["evidence_id"]
        forgotten = client.post(f"/v1/profiles/{user}:forget", headers=idem(f"forget-evidence-{user}"), json={
            "expected_profile_version": 2, "scope": "evidence", "target_id": evidence_id, "reason": "撤销这条证据"})
        assert forgotten.status_code == 200, forgotten.text
        profile = client.get(f"/v1/profiles/{user}", headers=HEADERS).json()["profile"]
        assert profile["core_traits"]["energy_mode"]["extroversion"]["value"] == 0.5
