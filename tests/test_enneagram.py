import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from profile_engine.api import app
from profile_engine.enneagram import build_enneagram_profile
from profile_engine.rule_compiler import compile_rule_pack


def headers(tenant: str, key: str | None = None) -> dict[str, str]:
    value = {"X-API-Key": "local-development-key", "X-Tenant-ID": tenant}
    if key:
        value["Idempotency-Key"] = key
    return value


def identity(core_type=7, wing=6, primary="SX", secondary="SO", source="expert_confirmed"):
    return {
        "core_type": core_type,
        "wing": wing,
        "primary_instinct": primary,
        "secondary_instinct": secondary,
        "source": source,
        "confidence": 0.95 if source == "expert_confirmed" else 0.8,
    }


def test_rule_pack_resolves_all_enneagram_combinations():
    pack = compile_rule_pack(Path(__file__).parents[1] / "rules")
    assert pack.report["enneagram_core_type_count"] == 9
    assert pack.report["enneagram_wing_count"] == 18
    assert pack.report["enneagram_wing_document_asset_count"] == 18
    assert pack.report["enneagram_instinct_stack_count"] == 6
    assert pack.report["enneagram_resolved_combination_count"] == 54
    assert pack.report["enneagram_source_document_count"] == 8
    assert pack.report["enneagram_instinct_subtype_count"] == 54
    assert pack.report["enneagram_scene_count"] == 10
    rules = pack.canonical["enneagram"]
    for core_type in range(1, 10):
        for primary, secondary in (
            ("SP", "SX"), ("SP", "SO"), ("SX", "SP"),
            ("SX", "SO"), ("SO", "SP"), ("SO", "SX"),
        ):
            profile = build_enneagram_profile(
                identity(core_type=core_type, wing=None, primary=primary, secondary=secondary),
                rules,
            )
            assert profile["status"] == "confirmed"
            assert profile["identity"]["core_type"] == core_type
            assert profile["identity"]["instinct_stack"] == f"{primary}/{secondary}"
            assert profile["layers"]["attention"]["instinct_subtype_adjustment"]
            assert profile["interaction_strategy"]["parameter_fusion"]["static_profile_weight"] == 0.9


def test_explicit_enneagram_identity_scene_strategy_update_and_forget():
    tenant = f"enneagram-{uuid.uuid4().hex}"
    user = f"user-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        initialized = client.post(
            "/v1/profiles:init",
            headers=headers(tenant, f"init-{user}"),
            json={
                "tenant_user_id": user,
                "display_name": "九型验证用户",
                "enneagram": identity(),
                "consent": {"profile": True, "sensitive_inference": True},
            },
        )
        assert initialized.status_code == 200, initialized.text
        enneagram = initialized.json()["profile"]["enneagram_profile"]
        assert enneagram["identity"]["code"] == "SX/SO｜7w6"
        assert enneagram["layers"]["motivation"]["core_drive"]
        assert enneagram["interaction_strategy"]["wing_adjustment"]["decision"] == "寻找有趣且可靠的选择"
        assert enneagram["interaction_strategy"]["wing_adjustment"]["attention"]

        turn = client.post(
            f"/v1/profiles/{user}/messages:ingest",
            headers=headers(tenant, f"turn-{user}"),
            json={
                "conversation_id": "career",
                "message_id": "career-1",
                "expected_profile_version": 1,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "text": "我正在考虑要不要换一个职业方向。",
                "context": {"topic": "career"},
            },
        )
        assert turn.status_code == 200, turn.text
        body = turn.json()
        assert body["strategy_trace"]["enneagram_identity"] == "SX/SO｜7w6"
        assert body["strategy_trace"]["scene"] == "career_decision"
        assert body["reply_hints"]["enneagram_strategy"]["scene_adaptation"]["role"] == "方向澄清与小实验设计者"
        assert set(body["reply_hints"]["turn_plan"]) >= {
            "communication", "motivation", "conflict", "trust", "companionship",
        }
        assert "document_05_instinct_subtype" in body["strategy_trace"]["strategy_sources"]
        assert body["behavior_directives"]["safety_gate_required"] is True
        assert body["behavior_directives"]["device_actions"] == []
        assert "structure_level" not in body["reply_hints"]["rule_locked_fields"]
        assert body["reply_hints"]["structure_level"] == "simple"

        updated = client.post(
            f"/v1/profiles/{user}:set-enneagram",
            headers=headers(tenant, f"set-{user}"),
            json={
                "expected_profile_version": body["profile_version"],
                "enneagram": identity(8, 9, "SP", "SX"),
                "reason": "专家复核测评结果",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["enneagram_profile"]["identity"]["code"] == "SP/SX｜8w9"

        forgotten = client.post(
            f"/v1/profiles/{user}:forget",
            headers=headers(tenant, f"forget-{user}"),
            json={
                "expected_profile_version": updated.json()["profile_version"],
                "scope": "enneagram",
                "reason": "用户撤回九型人格数据",
            },
        )
        assert forgotten.status_code == 200, forgotten.text
        current = client.get(f"/v1/profiles/{user}", headers=headers(tenant)).json()
        assert current["profile"]["enneagram_profile"]["status"] == "unassigned"
        assert current["profile"]["core_traits"]


def test_enneagram_requires_consent_and_rejects_invalid_wing():
    tenant = f"enneagram-consent-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        denied = client.post(
            "/v1/profiles:init",
            headers=headers(tenant, f"denied-{uuid.uuid4().hex}"),
            json={
                "tenant_user_id": f"user-{uuid.uuid4().hex}",
                "enneagram": identity(),
                "consent": {"profile": True, "sensitive_inference": False},
            },
        )
        assert denied.status_code == 403
        invalid = client.post(
            "/v1/profiles:init",
            headers=headers(tenant, f"invalid-{uuid.uuid4().hex}"),
            json={
                "tenant_user_id": f"user-{uuid.uuid4().hex}",
                "enneagram": identity(core_type=7, wing=2),
                "consent": {"profile": True, "sensitive_inference": True},
            },
        )
        assert invalid.status_code == 422


def test_portrait_bridge_and_scene_strategy_do_not_auto_assign_enneagram():
    tenant = f"enneagram-bridge-{uuid.uuid4().hex}"
    user = f"user-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        initialized = client.post(
            "/v1/profiles:init",
            headers=headers(tenant, f"init-{user}"),
            json={
                "tenant_user_id": user,
                "display_name": "桥接验证用户",
                "consent": {"profile": True, "sensitive_inference": True},
            },
        )
        assert initialized.status_code == 200, initialized.text
        parameter_input = initialized.json()["profile"]["enneagram_profile"]["parameter_input"]
        assert parameter_input["identity"]["core_type"] is None
        assert parameter_input["identity"]["mapping_policy"] == "explicit_identity_only"

        turn = client.post(
            f"/v1/profiles/{user}/messages:ingest",
            headers=headers(tenant, f"turn-{user}"),
            json={
                "conversation_id": "auto-scene",
                "message_id": "auto-scene-1",
                "expected_profile_version": 1,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "text": "我最近在考虑换工作，但还不知道该选哪个方向。",
            },
        )
        assert turn.status_code == 200, turn.text
        body = turn.json()
        assert body["strategy_trace"]["enneagram_identity"] is None
        assert body["strategy_trace"]["scene"] == "career_decision"
        assert "enneagram_strategy" not in body["reply_hints"]
        assert body["reply_hints"]["profile_parameter_input"]["identity"]["core_type"] is None
