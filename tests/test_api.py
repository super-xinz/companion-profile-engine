import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from profile_engine.api import app


HEADERS = {"X-API-Key": "local-development-key", "X-Tenant-ID": "test-tenant"}


def idem(value: str) -> dict:
    return {**HEADERS, "Idempotency-Key": value}


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
        assert profile["core_traits"]["energy_mode"]["extroversion"]["value"] == 0.5


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
