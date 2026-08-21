from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, desc, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .digital_code import (aggregate_trait_priors, build_digital_code_profile,
                           empty_digital_code_profile)
from .enneagram import (build_enneagram_profile, build_portrait_parameter_input, empty_enneagram_profile,
                         resolve_interaction_strategy)
from .extractor import SemanticExtractor, get_semantic_extractor
from .models import (AuditLog, ChatMessage, Conversation, CurrentState, ManualOverride,
                     Memory, ProfileEvidence, ProfileVersion, RulePack,
                     RuntimePreference, User)
from .profile import (GOLDEN_TRAITS, TRAIT_NAMES, BirthFeatureCalculator,
                      build_initial_profile, build_profile_table_view, build_public_profile, clone_profile,
                      find_trait, flattened_traits, rebuild_derived, recalculate_meta)
from .profile import validate_profile_snapshot
from .rule_compiler import CompiledRulePack
from .rule_bank import extract_signals, fragments_for_code
from .schemas import (CorrectionRequest, ForgetRequest, MessageIngestRequest, ProfileInitRequest,
                      ReplyGuidance, SemanticFrame, SetEnneagramRequest, TraitSignal)
from .source_profiles import apply_source_profile, hydrate_source_sections
from .template_people import TEMPLATE_USER_IDS, template_person_for_birth_date


class NotFoundError(Exception):
    pass


class VersionConflictError(Exception):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual


class ConsentError(Exception):
    pass


def request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def current_version(db: Session, user: User) -> ProfileVersion:
    version = db.scalar(select(ProfileVersion).where(ProfileVersion.user_id == user.id).order_by(desc(ProfileVersion.version_no)).limit(1))
    if not version:
        raise NotFoundError("画像不存在")
    return version


def find_user(db: Session, tenant_id: str, tenant_user_id: str) -> User:
    user = db.scalar(select(User).where(User.tenant_id == tenant_id, User.tenant_user_id == tenant_user_id))
    if not user:
        raise NotFoundError("用户画像不存在")
    return user


def _check_version(version: ProfileVersion, expected: int) -> None:
    if version.version_no != expected:
        raise VersionConflictError(expected, version.version_no)


def _audit(db: Session, req_id: str, tenant_id: str, action: str, user: User | None, before: dict | None, after: dict | None,
           evidence_refs: list[str] | None = None, rule_ids: list[str] | None = None, idem_key: str | None = None) -> None:
    db.add(AuditLog(request_id=req_id, user_id=user.id if user else None, tenant_id=tenant_id, action=action,
                    idempotency_key=idem_key, before=before, after=after, evidence_refs=evidence_refs or [], rule_ids=rule_ids or []))


def _digital_code_context(birth_date: str | None, pack: RulePack) -> tuple[str | None, list[dict], dict, dict[str, float]]:
    if not birth_date:
        return None, [], empty_digital_code_profile(), {}
    code, _ = BirthFeatureCalculator().calculate(birth_date)
    if not code:
        return None, [], empty_digital_code_profile(), {}
    source_dir = get_settings().rule_source_dir
    if not source_dir.is_absolute():
        source_dir = (Path.cwd() / source_dir).resolve()
    fragments = fragments_for_code(str(source_dir.parent / "数字学画像2.xlsx"), code)
    signals = extract_signals(fragments, pack.canonical_json["cold_start"])
    source_sha = pack.canonical_json.get("source_rule_bank", {}).get("sha256")
    model = build_digital_code_profile(code, fragments, source_sha)
    priors = aggregate_trait_priors(signals, pack.canonical_json["cold_start"])
    return code, signals, model, priors


def ensure_rule_pack(db: Session, pack: CompiledRulePack) -> RulePack:
    existing = db.scalar(select(RulePack).where(RulePack.sha256 == pack.sha256))
    if existing:
        if existing.status != "published":
            existing.status = "published"
            existing.published_at = datetime.now(timezone.utc)
        db.commit()
        return existing
    for old in db.scalars(select(RulePack).where(RulePack.status == "published")):
        old.status = "superseded"
    record = RulePack(version=pack.version, sha256=pack.sha256, status="published", canonical_json=pack.canonical,
                      validation_report=pack.report, published_at=datetime.now(timezone.utc))
    db.add(record)
    db.commit()
    return record


def init_profile(db: Session, tenant_id: str, body: ProfileInitRequest, pack: RulePack, req_id: str, idem_key: str) -> dict:
    if not body.consent.profile:
        raise ConsentError("必须取得画像授权后才能初始化")
    if body.enneagram and not body.consent.sensitive_inference:
        raise ConsentError("保存九型人格结构需要敏感推断授权")
    existing = db.scalar(select(User).where(User.tenant_id == tenant_id, User.tenant_user_id == body.tenant_user_id))
    if existing:
        raise VersionConflictError(0, current_version(db, existing).version_no)
    user = User(tenant_id=tenant_id, tenant_user_id=body.tenant_user_id, display_name=body.display_name,
                birth_date=body.birth_date, birth_time=body.birth_time, timezone_name=body.timezone,
                profile_consent=body.consent.profile, sensitive_inference_consent=body.consent.sensitive_inference)
    db.add(user)
    db.flush()

    evidence_ids: dict[str, list[str]] = {}
    enneagram_evidence_id: str | None = None
    if body.enneagram:
        allowed_confidence = pack.canonical_json["enneagram"]["identity_schema"]["accepted_sources"][
            body.enneagram.source
        ]
        identity_confidence = min(body.enneagram.confidence, allowed_confidence)
        enneagram_evidence = ProfileEvidence(
            user_id=user.id,
            source_type=f"enneagram_{body.enneagram.source}",
            semantic_frame={"type": "enneagram_identity", **body.enneagram.model_dump()},
            target_path="enneagram_profile.identity",
            direction=0,
            base_delta=0.0,
            impact=identity_confidence,
            factors={
                "reliability": identity_confidence,
                "explicit_input": True,
                "sensitive_inference_consent": True,
            },
            rule_id="ENNEAGRAM-IDENTITY-EXPLICIT",
            reason="用户、外部测评或专家明确提供的九型人格结构",
        )
        db.add(enneagram_evidence)
        db.flush()
        enneagram_evidence_id = enneagram_evidence.id
    effective_birth = body.birth_date.isoformat() if body.birth_date and body.consent.sensitive_inference else None
    code, signals, digital_code_profile, trait_priors = _digital_code_context(effective_birth, pack)
    if code:
        birth_key = effective_birth
        trait_paths = {key: f"core_traits.{category_key}.{key}"
            for category_key, category in pack.canonical_json["schema"]["canonical_profile"]["core_traits"]["categories"].items()
            for key in category["fields"]}
        for signal in signals[:200]:
            trait = signal["target"]
            evidence = ProfileEvidence(
                user_id=user.id, source_type="cold_start_prior", target_path=trait_paths[trait], direction=signal["direction"],
                base_delta=min(0.06, signal["normalized_weight"]), impact=0.35 * signal["strength"],
                factors={"reliability": 0.35, "consent": True, "source_weight": signal["normalized_weight"],
                    "matched_cues": signal["matched_cues"], "source_fragment": signal["source_fragment"]},
                rule_id=f"COLD-{signal['signal_id']}-{trait}",
                reason=f"数字学候选文案命中语义信号 {signal['signal_id']}（低置信度先验）",
            )
            db.add(evidence)
            db.flush()
            evidence_ids.setdefault(trait, []).append(evidence.id)
        schema_traits = [key for category in pack.canonical_json["schema"]["canonical_profile"]["core_traits"]["categories"].values() for key in category["fields"]]
        for trait in schema_traits:
            if trait in evidence_ids:
                continue
            evidence = ProfileEvidence(user_id=user.id, source_type="cold_start_prior", target_path=trait_paths[trait],
                direction=0, base_delta=0.0, impact=0.35, factors={"reliability": 0.35, "consent": True},
                rule_id=f"COLD-GOLDEN-CALIBRATION-{birth_key}-{trait}",
                reason="黄金样例结构校准；候选文案未提供可稳定匹配信号")
            db.add(evidence); db.flush(); evidence_ids[trait] = [evidence.id]

    profile, warnings = build_initial_profile(
        user.id,
        body.display_name,
        effective_birth,
        body.timezone,
        pack.canonical_json,
        evidence_ids,
        body.enneagram.model_dump() if body.enneagram else None,
        trait_priors,
    )
    profile["digital_code_profile"] = digital_code_profile
    if enneagram_evidence_id:
        profile["enneagram_profile"]["provenance"].append(enneagram_evidence_id)
    if effective_birth:
        apply_source_profile(profile, effective_birth)
        recalculate_meta(profile)
    if body.birth_date and not body.consent.sensitive_inference:
        profile["identity"]["birth_date"] = body.birth_date.isoformat()
        profile["meta"]["warnings"].append("未授权敏感推断，生日仅作为用户事实保存，未用于冷启动。")
    profile["meta"]["rule_pack_versions"] = {
        "cold_start": pack.version,
        "dialogue": pack.version,
        "enneagram": pack.version,
        "sha256": pack.sha256,
    }
    profile["meta"]["inference_policies"] = {
        "birth_prior_enabled": bool(effective_birth),
        "reference_models_public": False,
        "reference_models_may_drive_replies": False,
    }
    validate_profile_snapshot(profile)
    version = ProfileVersion(user_id=user.id, version_no=1, schema_version=profile["meta"]["schema_version"],
                             cold_start_rule_pack_version=pack.version, dialogue_rule_pack_version=pack.version,
                             overall_confidence=profile["meta"]["overall_confidence"], snapshot=profile)
    db.add(version)
    audit_evidence = [item for values in evidence_ids.values() for item in values]
    if enneagram_evidence_id:
        audit_evidence.append(enneagram_evidence_id)
    _audit(
        db,
        req_id,
        tenant_id,
        "profile.init",
        user,
        None,
        profile,
        audit_evidence,
        [
            f"COLD-GOLDEN-{body.birth_date.isoformat()}" if body.birth_date else "COLD-NEUTRAL",
            *(["ENNEAGRAM-IDENTITY-EXPLICIT"] if body.enneagram else []),
        ],
        idem_key,
    )
    db.commit()
    return {"request_id": req_id, "profile_version": 1, "rule_pack": _pack_summary(pack), "profile": profile, "warnings": warnings}


def normalize_profile_snapshot(profile: dict, user: User, pack: RulePack | None = None) -> dict:
    """Backfill fields added after older snapshots were persisted."""
    runtime = profile.setdefault("runtime", {})
    if not isinstance(runtime.get("interaction_preferences"), dict):
        runtime["interaction_preferences"] = {}
    if not isinstance(runtime.get("current_state"), dict):
        runtime["current_state"] = {}
    if not isinstance(runtime.get("memories"), list):
        runtime["memories"] = []

    meta = profile.setdefault("meta", {})
    policies = meta.get("inference_policies")
    if not isinstance(policies, dict):
        policies = {}
        meta["inference_policies"] = policies
    policies.setdefault("birth_prior_enabled", bool(user.sensitive_inference_consent))
    policies.setdefault("reference_models_public", False)
    policies.setdefault("reference_models_may_drive_replies", False)

    if not isinstance(profile.get("enneagram_profile"), dict):
        profile["enneagram_profile"] = empty_enneagram_profile()
    if not isinstance(profile.get("digital_code_profile"), dict):
        birth_date = (
            profile.get("identity", {}).get("birth_date")
            if user.sensitive_inference_consent and policies.get("birth_prior_enabled", True)
            else None
        )
        profile["digital_code_profile"] = (
            _digital_code_context(birth_date, pack)[2]
            if pack and birth_date else empty_digital_code_profile()
        )
    if isinstance(profile.get("source_profile_document"), dict):
        hydrate_source_sections(profile)
    return profile


def get_profile(db: Session, tenant_id: str, tenant_user_id: str) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    version = current_version(db, user)
    profile = clone_profile(version.snapshot)
    if not isinstance(profile.get("digital_code_profile"), dict):
        pack = db.scalar(select(RulePack).where(
            RulePack.status == "published"
        ).order_by(desc(RulePack.published_at)).limit(1))
    else:
        pack = None
    normalize_profile_snapshot(profile, user, pack)
    now = datetime.now(timezone.utc)
    active_states = db.scalars(select(CurrentState).where(CurrentState.user_id == user.id, CurrentState.expires_at > now)).all()
    preferences = db.scalars(select(RuntimePreference).where(RuntimePreference.user_id == user.id)).all()
    memories = db.scalars(select(Memory).where(Memory.user_id == user.id, Memory.active.is_(True))).all()
    profile["runtime"]["current_state"] = {x.state_key: {**x.value, "expires_at": x.expires_at.isoformat()} for x in active_states}
    profile["runtime"]["interaction_preferences"] = {x.preference_key: x.value.get("value") for x in preferences}
    profile["runtime"]["memories"] = [{"memory_id": x.id, "type": x.memory_type, **x.content} for x in memories]
    profile["table_view"] = build_profile_table_view(profile)
    return {"profile_version": version.version_no, "profile": profile,
            "rule_pack_versions": {"cold_start": version.cold_start_rule_pack_version, "dialogue": version.dialogue_rule_pack_version}}


def _evidence_summary_by_path(db: Session, user: User) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    evidence = db.scalars(select(ProfileEvidence).where(
        ProfileEvidence.user_id == user.id,
        ProfileEvidence.invalidated.is_(False),
    )).all()
    for item in evidence:
        summary = summaries.setdefault(item.target_path, {
            "confirmed": 0, "explicit": 0, "repeated": 0, "observed": 0,
            "prior": 0, "independent_sessions": 0,
        })
        source = item.source_type
        if source in {"manual_expert_override", "explicit_correction"}:
            summary["confirmed"] += 1
        elif source == "explicit_self_report":
            summary["explicit"] += 1
        elif source == "repeated_behavior":
            summary["repeated"] += 1
        elif source == "single_behavior_inference":
            summary["observed"] += 1
        elif source == "cold_start_prior":
            summary["prior"] += 1
        session = (item.factors or {}).get("conversation_id")
        if session:
            summary.setdefault("_sessions", set()).add(session)
    for summary in summaries.values():
        sessions = summary.pop("_sessions", set())
        summary["independent_sessions"] = len(sessions)
    return summaries


def get_public_profile(db: Session, tenant_id: str, tenant_user_id: str) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    internal = get_profile(db, tenant_id, tenant_user_id)
    return {
        "profile_version": internal["profile_version"],
        "profile": build_public_profile(
            internal["profile"],
            _evidence_summary_by_path(db, user),
            showcase_baseline=user.tenant_user_id in TEMPLATE_USER_IDS,
        ),
        "rule_pack_versions": internal["rule_pack_versions"],
    }


def get_expert_reference(db: Session, tenant_id: str, tenant_user_id: str) -> dict:
    internal = get_profile(db, tenant_id, tenant_user_id)
    return {
        "profile_version": internal["profile_version"],
        "usage_policy": {
            "visibility": "authorized_internal_only",
            "may_be_presented_as_scientific_fact": False,
            "may_drive_chat_without_independent_evidence": False,
            "note": "类型框架、出生信息先验和原始来源只用于内部核验，不进入默认画像视图。",
        },
        "profile": internal["profile"],
    }


def _pack_summary(pack: RulePack) -> dict:
    return {"version": pack.version, "sha256": pack.sha256, "status": pack.status}


def _trait_path(profile: dict, trait: str) -> str:
    for category, values in profile["core_traits"].items():
        if trait in values:
            return f"core_traits.{category}.{trait}"
    raise KeyError(trait)


def _trait_catalog(profile: dict) -> dict[str, dict]:
    return {key: {"label": TRAIT_NAMES.get(key, key), "current_value": value["value"],
                  "current_confidence": value["confidence"]}
            for key, value in flattened_traits(profile).items()}


def _trusted_trait_keys(db: Session, user: User) -> set[str]:
    """Return traits backed by evidence that is independent from reference priors."""
    summaries = _evidence_summary_by_path(db, user)
    trusted: set[str] = set()
    for path, summary in summaries.items():
        if not path.startswith("core_traits."):
            continue
        if (summary.get("confirmed") or summary.get("explicit") or summary.get("repeated")
                or summary.get("independent_sessions", 0) >= 3):
            trusted.add(path.rsplit(".", 1)[-1])
    return trusted


def _profile_for_reply(profile: dict, trusted_traits: set[str]) -> dict:
    """Remove reference-model influence before producing reply strategy."""
    safe = clone_profile(profile)
    for key, entry in flattened_traits(safe).items():
        if key not in trusted_traits:
            entry["value"] = 0.5
            entry["confidence"] = 0.0
            entry["evidence_refs"] = []
    safe["enneagram_profile"] = empty_enneagram_profile()
    safe.get("meta", {})["overall_confidence"] = 0.0
    return safe


_PREDICATE_SCENARIOS = {
    "socializing_requires_solitude_recovery": ["energy_source"],
    "likes_social_gathering": ["first_meeting", "energy_source"],
    "prefers_planning": ["task_received", "task_progress"],
    "uses_data_for_decisions": ["decision"],
}


def _apply_scenario_observations(
    profile: dict,
    frames: list[SemanticFrame],
    accepted_records: list[tuple[TraitSignal, str]],
    dialogue_rules: dict,
) -> list[dict]:
    operations: list[dict] = []
    for signal, evidence_id in accepted_records:
        frame = next((item for item in frames if _spans_overlap(
            signal.supporting_span, item.supporting_span
        )), None)
        if not frame:
            continue
        configured = (
            dialogue_rules.get("trait_mapping_rules", {}).get(signal.target_trait, {})
            .get("affected_source_fields", {}).get("behavior_scenarios", [])
        )
        candidates = _PREDICATE_SCENARIOS.get(frame.predicate) or configured[:2]
        for scenario_key in candidates:
            target = next((
                (group_key, scenarios[scenario_key])
                for group_key, scenarios in profile.get("behavior_style", {}).items()
                if scenario_key in scenarios
            ), None)
            if not target:
                continue
            group_key, item = target
            refs = list(dict.fromkeys([*item.get("direct_evidence_refs", []), evidence_id]))
            item["direct_evidence_refs"] = refs
            observations = [*item.get("observations", []), {
                "evidence_id": evidence_id,
                "summary": signal.supporting_span,
                "context": frame.context,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "source": signal.evidence_scope,
            }][-8:]
            item["observations"] = observations
            item["confidence"] = round(max(
                float(item.get("confidence", 0)), min(0.75, signal.confidence * 0.75)
            ), 4)
            item["explanation"] = "包含直接对话观察；仍需跨情境验证。"
            operations.append({
                "operation": "UPSERT_SCENARIO_EVIDENCE",
                "field": f"behavior_style.{group_key}.{scenario_key}",
                "evidence_quote": signal.supporting_span,
            })
    return operations


def _apply_language_observation(profile: dict, text: str, frames: list[SemanticFrame]) -> list[dict]:
    eligible = [
        frame for frame in frames
        if frame.subject == "user" and frame.modality == "asserted"
        and frame.semantic_domain in {
            "preference", "habit", "decision", "task_behavior", "social_behavior",
            "relationship_behavior", "self_evaluation", "communication_behavior",
        }
    ]
    if not eligible:
        return []
    compact = " ".join(text.split())
    feature_candidates: list[tuple[str, str]] = []
    if len(compact) <= 36:
        feature_candidates.append(("concise_expression", "表达通常较简洁"))
    elif len(compact) >= 120:
        feature_candidates.append(("detailed_expression", "表达时会补充较多背景和细节"))
    if any(marker in compact for marker in ("因为", "所以", "因此", "但是", "不过")):
        feature_candidates.append(("reasoned_expression", "表达中会交代原因或转折"))
    if any(frame.semantic_domain in {"preference", "communication_behavior"} for frame in eligible):
        feature_candidates.append(("explicit_communication_boundary", "会明确说明希望采用的沟通方式"))
    if not feature_candidates:
        return []

    language = profile.setdefault("language_style", {})
    state = language.setdefault("observation_state", {})
    speaking = language.setdefault("speaking_style", [])
    changed_features: list[str] = []
    for feature_id, label in feature_candidates:
        record = state.setdefault(feature_id, {"label": label, "sample_count": 0, "examples": []})
        record["sample_count"] = int(record.get("sample_count", 0)) + 1
        record["examples"] = [*record.get("examples", []), compact[:120]][-3:]
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        changed_features.append(label)
        if record["sample_count"] >= 3:
            observed = next((item for item in speaking if item.get("feature_id") == feature_id), None)
            payload = {
                "feature_id": feature_id,
                "label": feature_id,
                "behavior": label,
                "example": None,
                "confidence": round(min(0.75, 0.2 + record["sample_count"] * 0.1), 4),
                "evidence_refs": [],
                "origin": "observed",
                "sample_count": record["sample_count"],
            }
            if observed:
                observed.update(payload)
            else:
                speaking.insert(0, payload)
                del speaking[6:]
    return [{
        "operation": "UPDATE_LANGUAGE_OBSERVATION",
        "field": "language_style.speaking_style",
        "features": changed_features,
    }]


_REJECTION_REASONS = {
    "target_not_in_published_trait_rules": "该字段不在当前已发布维护规则中",
    "confidence_below_threshold": "候选证据强度未达到写入门槛",
    "supporting_span_not_in_message": "候选没有可核对的用户原话",
    "missing_matching_semantic_frame": "候选与本轮语义理解无法对应",
    "interaction_preference_cannot_update_long_term_trait": "这是对机器人沟通方式的要求，不应改写长期行为倾向",
    "semantic_domain_forbidden_for_long_term_trait": "短期状态、事实或事件不能直接改写长期行为倾向",
    "no_trait_eligible_semantic_frame": "没有用户本人、明确且可用于长期观察的语义证据",
    "explicit_self_report_requires_stable_or_habitual_frame": "这句话尚未表达稳定习惯或长期自我评价",
    "locked_by_manual_override": "该字段已由人工确认并锁定",
    "insufficient_independent_sessions_for_repeated_behavior": "跨对话样本还不够，暂不写入稳定倾向",
    "effect_below_no_op_threshold": "计算后的变化低于最小有效幅度",
}


def _value_band(value: float) -> str:
    if value < 0.35:
        return "较少表现"
    if value > 0.65:
        return "较常表现"
    return "视情境而定"


def _operation_frame(operation: dict, frames: list[SemanticFrame]) -> SemanticFrame | None:
    field_predicates = {
        "response_length": {"prefers_short_responses"},
        "empathy_first": {"needs_empathy_before_advice"},
        "humor_level": {"dislikes_humor"},
        "directness": {"prefers_direct_responses"},
        "question_load": {"prefers_fewer_questions"},
        "stress_level": {"high_stress"},
        "energy_level": {"low_energy"},
        "emotion": {"positive_mood", "low_mood", "angry_now"},
    }
    predicates = field_predicates.get(operation.get("field"), set())
    if predicates:
        return next((frame for frame in frames if frame.predicate in predicates), None)
    if operation.get("operation") == "UPSERT_FACT":
        return next((frame for frame in frames if frame.predicate == operation.get("key")), None)
    if operation.get("operation") == "UPSERT_MEMORY":
        return next((frame for frame in frames if frame.semantic_domain == "event"), None)
    return None


def _build_update_summary(
    patches: list[dict],
    runtime_operations: list[dict],
    maintenance_operations: list[dict],
    accepted_signals: list[dict],
    rejected_signals: list[dict],
    frames: list[SemanticFrame],
    derived: list[str],
    changed: bool,
) -> dict:
    items: list[dict] = []
    for patch in patches:
        trait_key = patch["field"].rsplit(".", 1)[-1]
        signal = next((item for item in accepted_signals if item.get("target_trait") == trait_key), {})
        before_band, after_band = _value_band(patch["before"]), _value_band(patch["after"])
        direction = "小幅上调" if patch["after"] > patch["before"] else "小幅下调"
        action = f"由“{before_band}”调整为“{after_band}”" if before_band != after_band else f"在“{after_band}”区间内{direction}"
        scope_label = {
            "explicit_self_report": "明确自述",
            "repeated_behavior": "跨对话重复观察",
            "single_behavior_inference": "单次行为观察",
        }.get(signal.get("evidence_scope"), "对话证据")
        items.append({
            "kind": "stable_tendency",
            "field": patch["field"],
            "label": TRAIT_NAMES.get(trait_key, trait_key),
            "action": action,
            "why": signal.get("rationale") or "本轮原话与该行为维度直接相关",
            "how": f"作为{scope_label}通过规则校验后，小步写入；单轮变化受上限保护。",
            "evidence_quote": signal.get("supporting_span"),
        })

    operation_labels = {
        "response_length": "回答长度偏好", "empathy_first": "倾听与建议顺序",
        "humor_level": "幽默偏好", "directness": "表达直接程度",
        "question_load": "追问密度", "stress_level": "当前压力状态",
        "energy_level": "当前精力状态", "emotion": "当前情绪状态",
    }
    for operation in runtime_operations:
        frame = _operation_frame(operation, frames)
        op = operation.get("operation")
        field = operation.get("field") or operation.get("key") or "memory"
        if op == "SET_INTERACTION_PREFERENCE":
            action, why, how = "已按用户要求更新", "用户明确提出了对回答方式的偏好", "立即写入互动偏好，不改变长期行为倾向。"
        elif op == "SET_STATE":
            action, why, how = "已记录为短期状态", "用户表达的是当前或近期状态", "写入带有效期的状态，到期后自动失效。"
        elif op == "UPSERT_FACT":
            action, why, how = "已记录或更正事实", "用户明确提供了本人事实", "作为可更正事实保存，不由此推断人格。"
        else:
            action, why, how = "已记录重要事件", "本轮包含用户本人事件", "写入事件记忆；重复内容会链接现有记录。"
        items.append({
            "kind": "runtime_or_fact",
            "field": field,
            "label": operation_labels.get(field, TRAIT_NAMES.get(field, field if field != "memory" else "重要事件")),
            "action": action,
            "why": why,
            "how": how,
            "evidence_quote": frame.supporting_span if frame else None,
        })

    maintenance_labels = []
    if any(item.get("operation") == "UPSERT_SCENARIO_EVIDENCE" for item in maintenance_operations):
        maintenance_labels.append("场景表现依据")
    if any(item.get("operation") == "UPDATE_LANGUAGE_OBSERVATION" for item in maintenance_operations):
        maintenance_labels.append("表达方式样本")

    rejected = [{
        "label": TRAIT_NAMES.get(item.get("target_trait"), item.get("target_trait", "画像候选")),
        "why": _REJECTION_REASONS.get(item.get("rejection_reason"), item.get("rejection_reason", "未通过规则校验")),
        "evidence_quote": item.get("supporting_span"),
    } for item in rejected_signals[:4]]

    if items:
        headline = f"本轮建议已写入 {len(items)} 项可核验变化"
        status = "updated"
    elif maintenance_labels:
        headline = "本轮补充了观察样本，尚未形成新的稳定结论"
        status = "observed"
    else:
        headline = "本轮未修改画像"
        status = "unchanged"
    if not changed:
        status = "unchanged"
    return {
        "status": status,
        "headline": headline,
        "change_count": len(items),
        "items": items,
        "maintenance": maintenance_labels,
        "rejected": rejected,
        "no_change_reason": None if changed else (
            rejected[0]["why"] if rejected else "没有发现需要长期保存、且证据足够的用户本人信息。"
        ),
        "derived_effects": [
            {"mbti_dimensions": "互动倾向概览", "behavior_style": "场景表现",
             "language_style": "表达与沟通特点", "portrait": "当前整体观察",
             "table_view": "画像展示索引"}.get(item, item)
            for item in derived
        ],
        "guardrail_note": "只依据用户本人原话更新；短期状态与沟通偏好不会被写成人格结论。",
    }


def _spans_overlap(left: str, right: str) -> bool:
    left, right = left.strip(), right.strip()
    return bool(left and right and (left in right or right in left))


def _trait_signal_rejection(
    signal: TraitSignal,
    source_text: str,
    frames: list[SemanticFrame],
    dialogue_rules: dict,
) -> str | None:
    policy = dialogue_rules.get("model_candidate_validation", {})
    if signal.target_trait not in dialogue_rules.get("trait_mapping_rules", {}):
        return "target_not_in_published_trait_rules"
    if signal.confidence < float(policy.get("minimum_confidence", 0.60)):
        return "confidence_below_threshold"
    if policy.get("require_supporting_span_verbatim", True) and signal.supporting_span not in source_text:
        return "supporting_span_not_in_message"

    matching = [frame for frame in frames if _spans_overlap(signal.supporting_span, frame.supporting_span)]
    if policy.get("require_matching_semantic_frame", True) and not matching:
        return "missing_matching_semantic_frame"

    preference_predicates = set(
        dialogue_rules.get("runtime_state_and_memory", {}).get("interaction_preferences", {})
    )
    forbidden_domains = set(policy.get("forbidden_trait_domains", []))
    if policy.get("interaction_preference_spans_cannot_update_traits", True) and any(
        frame.predicate in preference_predicates
        or frame.semantic_domain in {"preference", "communication_behavior"}
        for frame in matching
    ):
        return "interaction_preference_cannot_update_long_term_trait"
    if any(frame.semantic_domain in forbidden_domains for frame in matching):
        return "semantic_domain_forbidden_for_long_term_trait"

    eligible_subjects = set(policy.get("trait_eligible_subjects", ["user"]))
    eligible_modalities = set(policy.get("trait_eligible_modalities", ["asserted"]))
    eligible_domains = set(policy.get("trait_eligible_domains", []))
    eligible = [
        frame for frame in matching
        if frame.subject in eligible_subjects
        and frame.modality in eligible_modalities
        and (not eligible_domains or frame.semantic_domain in eligible_domains)
    ]
    if not eligible:
        return "no_trait_eligible_semantic_frame"
    if signal.evidence_scope == "explicit_self_report" and not any(
        frame.semantic_domain == "self_evaluation"
        or frame.temporal_scope in {"habitual", "historical"}
        or frame.frequency in {"often", "usually", "always", "never"}
        for frame in eligible
    ):
        return "explicit_self_report_requires_stable_or_habitual_frame"
    return None


def _apply_trait_signal(db: Session, user: User, profile: dict, signal: TraitSignal, source_text: str,
                        message_id: str, conversation_id: str, frames: list[SemanticFrame],
                        dialogue_rules: dict) -> tuple[dict | None, str | None, str | None]:
    traits = flattened_traits(profile)
    if signal.target_trait not in traits:
        return None, None, "target_not_in_profile_schema"
    target_path = _trait_path(profile, signal.target_trait)
    locked = db.scalar(select(ManualOverride).where(
        ManualOverride.user_id == user.id,
        ManualOverride.target_path == target_path,
        ManualOverride.active.is_(True),
    ))
    if locked:
        return None, None, "locked_by_manual_override"
    rejection = _trait_signal_rejection(signal, source_text, frames, dialogue_rules)
    if rejection:
        return None, None, rejection
    evidence_types = dialogue_rules.get("evidence_types", {})
    evidence_spec = evidence_types.get(signal.evidence_scope)
    if not evidence_spec:
        return None, None, "unknown_evidence_scope"
    update_math = dialogue_rules.get("update_math", {})
    cap = min(
        float(evidence_spec.get("max_trait_delta", 0)),
        float(update_math.get("same_message_same_target_cap", 0.06)),
    )
    reliability = float(evidence_spec.get("base_reliability", 0))
    rule_id = f"MODEL-SCHEMA-{signal.target_trait}"
    existing = db.scalars(select(ProfileEvidence).where(
        ProfileEvidence.user_id == user.id,
        ProfileEvidence.target_path.like(f"%{signal.target_trait}"),
        ProfileEvidence.invalidated.is_(False),
    )).all()
    prior_sessions = {x.factors.get("conversation_id") for x in existing
                      if x.rule_id == rule_id and x.factors.get("conversation_id")}
    if signal.evidence_scope == "repeated_behavior":
        minimum_sessions = int(evidence_spec.get("minimum_independent_sessions", 3))
        independent_sessions = len(prior_sessions | {conversation_id})
        if independent_sessions < minimum_sessions:
            return None, None, "insufficient_independent_sessions_for_repeated_behavior"
    independence = 0.5 if conversation_id in prior_sessions else 1.0
    impact = reliability * signal.confidence * signal.strength * independence
    direction = 1 if signal.direction == "increase" else -1
    entry = traits[signal.target_trait]
    before, conf_before = entry["value"], entry["confidence"]
    delta = min(cap, cap * impact)
    if delta < float(update_math.get("no_op_threshold", 0.01)):
        return None, None, "effect_below_no_op_threshold"
    after = min(1.0, max(0.0, before + direction * delta))
    new_conf = 1 - (1 - conf_before) * (1 - impact)
    opposite = sum(abs(x.impact) for x in existing if x.direction and x.direction != direction)
    same = sum(abs(x.impact) for x in existing if x.direction == direction)
    conflict_ratio = min(opposite, same + impact) / max(opposite + same + impact, 1e-9)
    new_conf *= 1 - conflict_ratio * 0.5
    evidence = ProfileEvidence(
        user_id=user.id, source_type=signal.evidence_scope, source_message_id=message_id,
        semantic_frame={"type": "trait_signal", **signal.model_dump()},
        target_path=target_path, direction=direction,
        base_delta=cap, impact=impact,
        factors={"reliability": reliability, "model_confidence": signal.confidence,
                 "model_strength": signal.strength, "independence": independence,
                 "conversation_id": conversation_id},
        rule_id=rule_id, reason=signal.rationale,
    )
    db.add(evidence)
    db.flush()
    entry.update(value=round(after, 4), confidence=round(new_conf, 4),
                 updated_at=datetime.now(timezone.utc).isoformat())
    entry["evidence_refs"] = [*entry.get("evidence_refs", []), evidence.id]
    patch = {"field": target_path, "before": before, "after": round(after, 4),
             "confidence_before": conf_before, "confidence_after": round(new_conf, 4),
             "source": "model_candidate_validated_by_schema_rules"}
    return patch, evidence.id, None


def _apply_identity_fact(db: Session, user: User, profile: dict, frame: SemanticFrame,
                         message_id: str) -> tuple[bool, dict | None]:
    if (frame.semantic_domain != "identity_fact" or frame.subject != "user" or not frame.object
            or frame.modality != "asserted" or frame.explicitness < .70 or frame.extractor_confidence < .70):
        return False, None
    fact_key = frame.predicate.strip().lower().replace(" ", "_")[:64]
    fact_value = frame.object.strip()
    # Find the matching flexible fact without adding a new database schema.
    matching = next((item for item in db.scalars(select(Memory).where(
        Memory.user_id == user.id, Memory.memory_type == "fact", Memory.active.is_(True)
    )).all() if item.content.get("key") == fact_key), None)
    content = {"key": fact_key, "value": fact_value, "summary": frame.supporting_span,
               "confidence": frame.extractor_confidence, "source": "explicit_self_report"}
    changed = matching is None or matching.content.get("value") != fact_value
    if not changed:
        return False, None
    if matching:
        matching.content = content
        matching.source_message_id = message_id
    else:
        matching = Memory(user_id=user.id, memory_type="fact", content=content, source_message_id=message_id)
        db.add(matching)
        db.flush()
    if fact_key in {"name", "display_name", "user_name"}:
        user.display_name = fact_value
        profile["identity"]["display_name"] = fact_value
    operation = {"operation": "UPSERT_FACT", "memory_id": matching.id, "key": fact_key,
                 "value": fact_value, "confidence": frame.extractor_confidence}
    return True, operation


def _apply_runtime_frame(db: Session, user: User, profile: dict, frame: SemanticFrame, message_id: str,
                         dialogue_rules: dict) -> tuple[bool, list[dict]]:
    changed, records = False, []
    if frame.subject != "user":
        return changed, records
    runtime_rules = dialogue_rules.get("runtime_state_and_memory", {})
    preference_spec = runtime_rules.get("interaction_preferences", {}).get(frame.predicate)
    if preference_spec:
        key, value = preference_spec["target"], preference_spec["value"]
        existing = db.scalar(select(RuntimePreference).where(RuntimePreference.user_id == user.id, RuntimePreference.preference_key == key))
        if existing:
            existing.value, existing.source_message_id = {"value": value, "explicit": True}, message_id
        else:
            db.add(RuntimePreference(user_id=user.id, preference_key=key, value={"value": value, "explicit": True}, source_message_id=message_id))
        profile["runtime"]["interaction_preferences"][key] = value
        changed, records = True, [{"operation": "SET_INTERACTION_PREFERENCE", "field": key, "value": value}]
    state_spec = next((
        (key, spec) for key, spec in runtime_rules.get("current_state", {}).items()
        if isinstance(spec, dict) and frame.predicate in spec.get("predicates", [])
    ), None)
    if state_spec and frame.temporal_scope in {"now", "recent", "unknown"}:
        key, spec = state_spec
        raw_value = spec.get("values", {}).get(frame.predicate, spec.get("value"))
        value = float(raw_value) if isinstance(raw_value, (int, float)) else raw_value
        hours = int(spec.get("ttl_hours", 2))
        expires = datetime.now(timezone.utc) + timedelta(hours=hours)
        existing_state = db.scalar(select(CurrentState).where(
            CurrentState.user_id == user.id,
            CurrentState.state_key == key,
        ).order_by(desc(CurrentState.created_at)).limit(1))
        if existing_state:
            existing_state.value = {"value": value}
            existing_state.source_message_id = message_id
            existing_state.expires_at = expires
        else:
            db.add(CurrentState(user_id=user.id, state_key=key, value={"value": value}, source_message_id=message_id, expires_at=expires))
        profile["runtime"]["current_state"][key] = {"value": value, "expires_at": expires.isoformat()}
        changed, records = True, records + [{"operation": "SET_STATE", "field": key, "value": value, "expires_at": expires.isoformat()}]
    return changed, records


def _reply_hints(profile: dict, interaction_strategy: dict | None = None) -> tuple[dict, set[str]]:
    prefs = profile["runtime"].get("interaction_preferences", {})
    states = profile["runtime"].get("current_state", {})
    locked_fields: set[str] = set()
    hints: dict[str, Any] = {"max_sentences": 4, "answer_first": False, "empathy_first": False,
                             "question_count": 0, "structure_level": "simple", "humor_level": prefs.get("humor_level", 0.2)}
    if interaction_strategy:
        strategy_hints = interaction_strategy.get("hints", {})
        hints.update(strategy_hints)
        hints["turn_plan"] = interaction_strategy.get("turn_plan", {})
        hints["strategy_precedence"] = interaction_strategy.get("precedence", [])
    if prefs.get("response_length") == "short":
        hints.update(max_sentences=3, answer_first=True)
        locked_fields.update({"max_sentences", "answer_first"})
    if prefs.get("empathy_first", 0) >= 0.67:
        hints.update(empathy_first=True, ask_support_or_solution=True)
        locked_fields.update({"empathy_first", "ask_support_or_solution"})
    if prefs.get("directness") == "direct":
        hints.update(answer_first=True)
        locked_fields.add("answer_first")
    if prefs.get("question_load") == "low":
        hints.update(question_count=0)
        locked_fields.add("question_count")
    if states.get("stress_level", {}).get("value", 0) >= 0.7:
        hints.update(max_sentences=3, empathy_first=True, humor_level=0.0)
        locked_fields.update({"max_sentences", "empathy_first", "question_count", "humor_level"})
    if states.get("energy_level", {}).get("value", 1) <= 0.3:
        hints.update(structure_level="simple", action_count=1, allow_resume_later=True)
        locked_fields.update({"structure_level", "action_count", "allow_resume_later"})
    structure = find_trait(profile, "structure_pref")["value"]
    if structure >= 0.67:
        hints.update(organization_preference="structured_when_helpful", options_max=3)
        locked_fields.update({"organization_preference", "options_max"})
    elif structure <= 0.33:
        hints.update(organization_preference="flexible_when_helpful", avoid_rigid_plan=True)
        locked_fields.update({"organization_preference", "avoid_rigid_plan"})
    if profile["meta"]["overall_confidence"] < 0.4:
        hints.update(use_tentative_language=True, calibration_question_count=0,
                     calibrate_only_when_naturally_relevant=True)
        locked_fields.update({"use_tentative_language", "calibration_question_count",
                              "calibrate_only_when_naturally_relevant"})
    return hints, locked_fields


def _merge_reply_guidance(profile_hints: dict, guidance: ReplyGuidance, locked_fields: set[str]) -> dict:
    merged = dict(profile_hints)
    strategy_avoid = profile_hints.get("turn_plan", {}).get("avoid", [])
    merged.update(
        intent=guidance.intent,
        tone=guidance.tone,
        focus=guidance.focus,
        avoid=list(dict.fromkeys([*guidance.avoid, *strategy_avoid])),
        requires_fresh_information=guidance.requires_fresh_information,
    )
    merged["empathy_first"] = bool(profile_hints.get("empathy_first") or guidance.empathy_first)
    merged["answer_first"] = bool(profile_hints.get("answer_first") or guidance.answer_first)
    merged["max_sentences"] = min(profile_hints.get("max_sentences", 5), guidance.max_sentences)
    if "question_count" not in locked_fields:
        merged["question_count"] = guidance.question_count
    if "structure_level" not in locked_fields:
        merged["structure_level"] = guidance.structure_level
    for field in {"empathy_first", "answer_first", "max_sentences"} & locked_fields:
        merged[field] = profile_hints[field]
    merged["strategy_sources"] = [
        "current_message_model_guidance", "evidence_backed_traits", "runtime_state_and_preferences",
    ]
    merged["rule_locked_fields"] = sorted(locked_fields)
    return merged


def ingest_message(db: Session, tenant_id: str, tenant_user_id: str, body: MessageIngestRequest, pack: RulePack,
                   req_id: str, idem_key: str, semantic_extractor: SemanticExtractor | None = None) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    if not user.profile_consent or not user.inference_enabled:
        raise ConsentError("画像推断已关闭")
    version = current_version(db, user)
    _check_version(version, body.expected_profile_version)
    before = normalize_profile_snapshot(clone_profile(version.snapshot), user, pack)
    profile = clone_profile(before)
    extractor = semantic_extractor or (
        get_semantic_extractor(body.model_provider)
        if body.model_provider else get_semantic_extractor()
    )
    analysis = extractor.analyze(
        body.text,
        trait_catalog=_trait_catalog(profile),
        recent_turns=[turn.model_dump() for turn in body.context.recent_turns],
    )
    frames = analysis.frames
    dialogue_rules = pack.canonical_json["dialogue"]
    patches, evidence_ids, runtime_operations = [], [], []
    accepted_trait_signals, rejected_trait_signals = [], []
    accepted_signal_records: list[tuple[TraitSignal, str]] = []
    runtime_changed = False
    for signal in analysis.trait_signals:
        patch, evidence_id, rejection = _apply_trait_signal(
            db, user, profile, signal, body.text, body.message_id, body.conversation_id,
            frames, dialogue_rules,
        )
        if rejection:
            rejected_trait_signals.append({**signal.model_dump(), "rejection_reason": rejection})
            continue
        accepted_trait_signals.append(signal.model_dump())
        if patch:
            patches.append(patch)
        if evidence_id:
            evidence_ids.append(evidence_id)
            accepted_signal_records.append((signal, evidence_id))
    for frame in frames:
        changed, operations = _apply_runtime_frame(db, user, profile, frame, body.message_id, dialogue_rules)
        runtime_changed |= changed
        runtime_operations.extend(operations)
        fact_changed, fact_operation = _apply_identity_fact(db, user, profile, frame, body.message_id)
        runtime_changed |= fact_changed
        if fact_operation:
            runtime_operations.append(fact_operation)
        if frame.semantic_domain == "event" and frame.subject == "user":
            existing_event = next((item for item in db.scalars(select(Memory).where(
                Memory.user_id == user.id,
                Memory.memory_type == "event",
                Memory.active.is_(True),
            )).all() if item.content.get("predicate") == frame.predicate
                and item.content.get("summary") == frame.supporting_span), None)
            if not existing_event:
                memory = Memory(user_id=user.id, memory_type="event", content={
                    "summary": frame.supporting_span, "predicate": frame.predicate,
                }, source_message_id=body.message_id)
                db.add(memory)
                db.flush()
                profile["runtime"]["memories"].append({"memory_id": memory.id, "type": "event", **memory.content})
                runtime_changed = True
                runtime_operations.append({"operation": "UPSERT_MEMORY", "memory_id": memory.id})

    update_math = dialogue_rules.get("update_math", {})
    maximum_total_change = float(update_math.get("maximum_total_trait_change_per_turn", 0.10))
    total_trait_change = sum(abs(x["after"] - x["before"]) for x in patches)
    if total_trait_change > maximum_total_change:
        ratio = maximum_total_change / total_trait_change
        for patch in patches:
            patch["after"] = round(patch["before"] + (patch["after"] - patch["before"]) * ratio, 4)
            parent, key = _resolve_path(profile, patch["field"])
            parent[key]["value"] = patch["after"]
    no_op_threshold = float(update_math.get("no_op_threshold", 0.01))
    material_trait_change = any(abs(x["after"] - x["before"]) >= no_op_threshold for x in patches)
    if material_trait_change:
        derived = rebuild_derived(profile, pack.canonical_json["schema"])
    else:
        derived = []
        recalculate_meta(profile)
    maintenance_operations = _apply_scenario_observations(
        profile, frames, accepted_signal_records, dialogue_rules,
    )
    maintenance_operations.extend(_apply_language_observation(profile, body.text, frames))
    changed = material_trait_change or runtime_changed or bool(maintenance_operations)
    new_no = version.version_no + 1 if changed else version.version_no
    if changed:
        profile["meta"]["profile_version"] = new_no
        profile["meta"]["rule_pack_versions"] = {"cold_start": version.cold_start_rule_pack_version, "dialogue": pack.version, "sha256": pack.sha256}
        validate_profile_snapshot(profile)
        db.add(ProfileVersion(user_id=user.id, version_no=new_no, schema_version=profile["meta"]["schema_version"],
            cold_start_rule_pack_version=version.cold_start_rule_pack_version, dialogue_rule_pack_version=pack.version,
            overall_confidence=profile["meta"]["overall_confidence"], snapshot=profile))
    _audit(db, req_id, tenant_id, "message.ingest", user, before, profile if changed else before,
           evidence_ids, [f"DIALOGUE-{f.predicate}" for f in frames], idem_key)
    db.commit()
    evidence_response = []
    for evidence_id in evidence_ids:
        e = db.get(ProfileEvidence, evidence_id)
        evidence_response.append({"evidence_id": e.id, "source_type": e.source_type, "target_path": e.target_path,
                                  "direction": e.direction, "impact": round(e.base_delta * e.impact, 4), "reason": e.reason})
    trusted_traits = _trusted_trait_keys(db, user)
    strategy_profile = _profile_for_reply(profile, trusted_traits)
    interaction_strategy = resolve_interaction_strategy(
        strategy_profile,
        pack.canonical_json.get("enneagram", {}),
        body.context.topic,
        current_message=body.text,
        semantic_frames=[frame.model_dump() for frame in frames],
        reply_guidance=analysis.reply_guidance.model_dump(),
    )
    interaction_strategy["strategy_sources"] = [
        "current_message", "runtime_state_and_preferences", "evidence_backed_traits",
        *(["generic_scene_rules"] if interaction_strategy.get("scene") else []),
    ]
    profile_hints, locked_fields = _reply_hints(strategy_profile, interaction_strategy)
    reply_hints = _merge_reply_guidance(profile_hints, analysis.reply_guidance, locked_fields)
    update_summary = _build_update_summary(
        patches, runtime_operations, maintenance_operations, accepted_trait_signals,
        rejected_trait_signals, frames, derived, changed,
    )
    return {"request_id": req_id, "profile_version": new_no, "rule_pack": _pack_summary(pack),
            "semantic_extractor_version": extractor.version, "semantic_frames": [f.model_dump() for f in frames], "evidence": evidence_response,
            "candidate_trait_signals": [signal.model_dump() for signal in analysis.trait_signals],
            "accepted_trait_signals": accepted_trait_signals, "rejected_trait_signals": rejected_trait_signals,
            "profile_patch": patches, "runtime_operations": runtime_operations,
            "maintenance_operations": maintenance_operations, "derived_patch": derived,
            "update_summary": update_summary,
            "model_reply_guidance": analysis.reply_guidance.model_dump(), "reply_hints": reply_hints,
            "behavior_directives": interaction_strategy.get("behavior_directives", {}),
            "strategy_trace": {"semantic_analysis": extractor.version, "profile_version_used": new_no,
                               "candidate_signals": len(analysis.trait_signals),
                               "accepted_signals": len(accepted_trait_signals),
                               "trusted_trait_inputs": sorted(trusted_traits),
                               "reference_models_excluded": ["digital_code", "mbti", "enneagram", "birth_analysis"],
                               "scene": interaction_strategy.get("scene") if interaction_strategy else None,
                               "strategy_sources": interaction_strategy.get("strategy_sources", []),
                               "consumed_by_chatbot": False},
            "no_profile_change": not changed}


def explain_profile(db: Session, tenant_id: str, tenant_user_id: str, field: str | None = None) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    query = select(ProfileEvidence).where(ProfileEvidence.user_id == user.id)
    if field:
        query = query.where(ProfileEvidence.target_path == field)
    evidence = db.scalars(query.order_by(ProfileEvidence.created_at)).all()
    history = db.scalars(select(ProfileVersion).where(ProfileVersion.user_id == user.id).order_by(ProfileVersion.version_no)).all()
    support = [e for e in evidence if e.direction >= 0 and not e.invalidated]
    counter = [e for e in evidence if e.direction < 0 and not e.invalidated]
    return {"profile_version": current_version(db, user).version_no, "field": field,
            "supporting_evidence": [_evidence_view(e) for e in support],
            "counter_evidence": [_evidence_view(e) for e in counter],
            "invalidated_evidence": [_evidence_view(e) for e in evidence if e.invalidated],
            "version_history": [{"version": x.version_no, "overall_confidence": x.overall_confidence, "created_at": x.created_at.isoformat()} for x in history]}


def _evidence_view(e: ProfileEvidence) -> dict:
    return {"evidence_id": e.id, "source_type": e.source_type, "source_message_id": e.source_message_id,
            "target_path": e.target_path, "direction": e.direction, "impact": e.impact, "factors": e.factors,
            "rule_id": e.rule_id, "reason": e.reason, "invalidated": e.invalidated, "created_at": e.created_at.isoformat()}


def _resolve_path(root: dict, path: str) -> tuple[dict, str]:
    parts = path.split(".")
    cursor = root
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            raise ValueError(f"未知字段路径: {path}")
        cursor = cursor[part]
    if parts[-1] not in cursor:
        raise ValueError(f"未知字段路径: {path}")
    return cursor, parts[-1]


def correct_profile(db: Session, tenant_id: str, tenant_user_id: str, body: CorrectionRequest, pack: RulePack,
                    req_id: str, idem_key: str) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    version = current_version(db, user)
    _check_version(version, body.expected_profile_version)
    allowed_identity_paths = {
        "identity.display_name", "identity.birth_date", "identity.birth_time", "identity.timezone",
    }
    is_core_trait = body.target_path.startswith("core_traits.") and len(body.target_path.split(".")) == 3
    if not is_core_trait and body.target_path not in allowed_identity_paths:
        raise ValueError("只允许更正核心维度或姓名、生日、出生时间、时区等底层身份事实")
    before = normalize_profile_snapshot(clone_profile(version.snapshot), user, pack)
    profile = clone_profile(before)
    parent, key = _resolve_path(profile, body.target_path)
    old = parent[key]
    before_value = clone_profile(old) if isinstance(old, dict) else old
    applied_value = body.value
    evidence_ids = []
    if body.target_path.startswith("core_traits."):
        if isinstance(body.value, bool) or not isinstance(body.value, (float, int)) or not 0 <= body.value <= 1:
            raise ValueError("核心维度更正值必须在0到1之间")
        old_value = old["value"]
        correction_cap = float(
            pack.canonical_json["dialogue"].get("evidence_types", {})
            .get("explicit_correction", {}).get("max_trait_delta", 0.10)
        )
        applied_value = round(max(old_value - correction_cap, min(old_value + correction_cap, float(body.value))), 4)
        evidence = ProfileEvidence(user_id=user.id, source_type="explicit_correction", target_path=body.target_path,
            direction=1 if applied_value > old_value else (-1 if applied_value < old_value else 0), base_delta=abs(applied_value-old_value), impact=1.0,
            factors={"reliability": 1.0, "explicitness": 1.0}, rule_id="USER-EXPLICIT-CORRECTION", reason=body.reason)
        db.add(evidence); db.flush(); evidence_ids.append(evidence.id)
        old.update(value=applied_value, confidence=min(1.0, old["confidence"] + 0.10), evidence_refs=[*old["evidence_refs"], evidence.id])
        derived = rebuild_derived(profile, pack.canonical_json["schema"])
    elif body.target_path == "identity.birth_date":
        try:
            corrected_date = date.fromisoformat(str(body.value))
        except ValueError as exc:
            raise ValueError("birth_date 必须是 YYYY-MM-DD") from exc
        invalidated = []
        for evidence in db.scalars(select(ProfileEvidence).where(ProfileEvidence.user_id == user.id,
                ProfileEvidence.source_type == "cold_start_prior", ProfileEvidence.invalidated.is_(False))):
            evidence.invalidated = True; evidence.invalidated_at = datetime.now(timezone.utc); invalidated.append(evidence.id)
        birth_key = corrected_date.isoformat()
        inference_birth_key = birth_key if user.sensitive_inference_consent else None
        code, signals, digital_code_profile, trait_priors = _digital_code_context(inference_birth_key, pack)
        signals_by_trait: dict[str, list[dict]] = {}
        for signal in signals:
            signals_by_trait.setdefault(signal["target"], []).append(signal)
        for category_key, category in profile["core_traits"].items():
            for trait_key, entry in category.items():
                entry["evidence_refs"] = [ref for ref in entry["evidence_refs"] if ref not in invalidated]
                if not entry["evidence_refs"]:
                    value = GOLDEN_TRAITS.get(inference_birth_key or "", {}).get(trait_key, trait_priors.get(trait_key, 0.5))
                    has_prior = trait_key in GOLDEN_TRAITS.get(inference_birth_key or "", {}) or trait_key in trait_priors
                    entry.update(value=value, confidence=0.35 if has_prior else 0.1)
                    if has_prior:
                        evidence = ProfileEvidence(
                            user_id=user.id, source_type="cold_start_prior",
                            target_path=f"core_traits.{category_key}.{trait_key}",
                            direction=1 if value > .5 else (-1 if value < .5 else 0),
                            base_delta=abs(value - .5), impact=.35,
                            factors={"reliability": .35, "corrected_birth_fact": True,
                                     "signal_count": len(signals_by_trait.get(trait_key, []))},
                            rule_id=f"COLD-BIRTH-CORRECTION-{trait_key}",
                            reason="更正生日后重算数字密码低置信度先验",
                        )
                        db.add(evidence); db.flush(); entry["evidence_refs"].append(evidence.id); evidence_ids.append(evidence.id)
        user.birth_date = corrected_date
        parent[key] = corrected_date.isoformat()
        _, warnings = BirthFeatureCalculator().calculate(birth_key) if inference_birth_key else (
            None, ["生日仅作为事实保存；未授权出生信息推断。"]
        )
        profile["birth_analysis"].update({
            "bazi_text": None, "day_master": None, "pattern_name": None,
            "strength_label": None,
            "relation_markers": {
                "combinations": 0, "self_punishments": 0, "other_punishments": 0,
                "clashes": 0, "harms": 0, "source_text": None,
            },
        })
        profile["birth_analysis"]["numerology_code"] = code
        profile["digital_code_profile"] = digital_code_profile
        profile.pop("source_profile_document", None)
        profile.pop("source_portrait", None)
        profile.get("identity", {}).pop("template_person_id", None)
        profile["meta"]["warnings"] = warnings
        profile.setdefault("meta", {}).setdefault("inference_policies", {})["birth_prior_enabled"] = bool(inference_birth_key)
        derived = rebuild_derived(profile, pack.canonical_json["schema"])
        corrected_template = template_person_for_birth_date(corrected_date.isoformat()) if inference_birth_key else None
        if corrected_template:
            apply_source_profile(profile, corrected_date.isoformat())
            profile["mbti_dimensions"]["type_label"] = corrected_template.mbti
    elif body.target_path.startswith("identity."):
        limits = {"display_name": 256, "birth_time": 16, "timezone": 64}
        if key in limits and (not isinstance(body.value, str) or not body.value.strip()
                              or len(body.value) > limits[key]):
            raise ValueError(f"{key} 必须是 1 到 {limits[key]} 个字符的字符串")
        parent[key] = body.value
        if key == "display_name": user.display_name = body.value
        elif key == "birth_time": user.birth_time = body.value
        elif key == "timezone": user.timezone_name = body.value
        derived = []
    else:
        parent[key] = body.value
        derived = []
    pinned = db.scalar(select(ManualOverride).where(
        ManualOverride.user_id == user.id,
        ManualOverride.target_path == body.target_path,
    ))
    pin_value = old.get("value") if isinstance(old, dict) and "value" in old else parent[key]
    if pinned:
        pinned.value = {"value": pin_value}
        pinned.reason = body.reason
        pinned.created_by = "explicit_user_correction"
        pinned.active = True
    else:
        db.add(ManualOverride(
            user_id=user.id,
            target_path=body.target_path,
            value={"value": pin_value},
            reason=body.reason,
            created_by="explicit_user_correction",
        ))
    new_no = version.version_no + 1
    profile["meta"]["profile_version"] = new_no
    recalculate_meta(profile)
    validate_profile_snapshot(profile)
    db.add(ProfileVersion(user_id=user.id, version_no=new_no, schema_version=profile["meta"]["schema_version"],
        cold_start_rule_pack_version=version.cold_start_rule_pack_version, dialogue_rule_pack_version=pack.version,
        overall_confidence=profile["meta"]["overall_confidence"], snapshot=profile))
    _audit(db, req_id, tenant_id, "profile.correct", user, before, profile, evidence_ids, ["USER-EXPLICIT-CORRECTION"], idem_key)
    db.commit()
    return {"request_id": req_id, "profile_version": new_no, "rule_pack": _pack_summary(pack),
            "corrected_field": body.target_path, "before": before_value, "requested_value": body.value,
            "after": applied_value, "derived_patch": derived}


def set_enneagram_profile(
    db: Session,
    tenant_id: str,
    tenant_user_id: str,
    body: SetEnneagramRequest,
    pack: RulePack,
    req_id: str,
    idem_key: str,
) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    if not user.sensitive_inference_consent:
        raise ConsentError("保存九型人格结构需要敏感推断授权")
    version = current_version(db, user)
    _check_version(version, body.expected_profile_version)
    before = normalize_profile_snapshot(clone_profile(version.snapshot), user, pack)
    profile = clone_profile(before)
    previous = profile.get("enneagram_profile") or empty_enneagram_profile()
    for item in db.scalars(select(ProfileEvidence).where(
        ProfileEvidence.user_id == user.id,
        ProfileEvidence.target_path == "enneagram_profile.identity",
        ProfileEvidence.invalidated.is_(False),
    )):
        item.invalidated = True
        item.invalidated_at = datetime.now(timezone.utc)
    allowed_confidence = pack.canonical_json["enneagram"]["identity_schema"]["accepted_sources"][
        body.enneagram.source
    ]
    identity_confidence = min(body.enneagram.confidence, allowed_confidence)
    evidence = ProfileEvidence(
        user_id=user.id,
        source_type=f"enneagram_{body.enneagram.source}",
        semantic_frame={"type": "enneagram_identity", **body.enneagram.model_dump()},
        target_path="enneagram_profile.identity",
        direction=0,
        base_delta=0.0,
        impact=identity_confidence,
        factors={
            "reliability": identity_confidence,
            "explicit_input": True,
            "sensitive_inference_consent": True,
        },
        rule_id="ENNEAGRAM-IDENTITY-EXPLICIT",
        reason=body.reason,
    )
    db.add(evidence)
    db.flush()
    profile["enneagram_profile"] = build_enneagram_profile(
        body.enneagram.model_dump(),
        pack.canonical_json["enneagram"],
    )
    profile["enneagram_profile"]["parameter_input"] = build_portrait_parameter_input(profile)
    profile["enneagram_profile"]["provenance"].append(evidence.id)
    new_no = version.version_no + 1
    profile["meta"]["profile_version"] = new_no
    profile["meta"]["schema_version"] = pack.canonical_json["schema"]["schema_version"]
    profile["meta"]["rule_pack_versions"]["enneagram"] = pack.version
    profile["meta"]["rule_pack_versions"]["sha256"] = pack.sha256
    recalculate_meta(profile)
    validate_profile_snapshot(profile)
    db.add(ProfileVersion(
        user_id=user.id,
        version_no=new_no,
        schema_version=profile["meta"]["schema_version"],
        cold_start_rule_pack_version=version.cold_start_rule_pack_version,
        dialogue_rule_pack_version=pack.version,
        overall_confidence=profile["meta"]["overall_confidence"],
        snapshot=profile,
    ))
    _audit(
        db,
        req_id,
        tenant_id,
        "profile.enneagram.set",
        user,
        before,
        profile,
        [evidence.id],
        ["ENNEAGRAM-IDENTITY-EXPLICIT", *profile["enneagram_profile"]["provenance"][:-1]],
        idem_key,
    )
    db.commit()
    return {
        "request_id": req_id,
        "profile_version": new_no,
        "before": previous,
        "enneagram_profile": profile["enneagram_profile"],
        "rule_pack": _pack_summary(pack),
    }


def forget_profile(db: Session, tenant_id: str, tenant_user_id: str, body: ForgetRequest, pack: RulePack,
                   req_id: str, idem_key: str) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    version = current_version(db, user); _check_version(version, body.expected_profile_version)
    before = normalize_profile_snapshot(clone_profile(version.snapshot), user, pack)
    profile = clone_profile(before)
    affected: list[str] = []
    if body.scope == "memory":
        item = db.get(Memory, body.target_id)
        if not item or item.user_id != user.id: raise NotFoundError("记忆不存在")
        item.active = False; affected.append(item.id)
        profile["runtime"]["memories"] = [x for x in profile["runtime"]["memories"] if x.get("memory_id") != item.id]
    elif body.scope == "evidence":
        item = db.get(ProfileEvidence, body.target_id)
        if not item or item.user_id != user.id: raise NotFoundError("证据不存在")
        item.invalidated = True; item.invalidated_at = datetime.now(timezone.utc); affected.append(item.id)
        try:
            parent, key = _resolve_path(profile, item.target_path)
            entry = parent[key]
            if isinstance(entry, dict) and "value" in entry and item.id in entry.get("evidence_refs", []):
                entry["value"] = round(min(1.0, max(0.0, entry["value"] - item.direction * item.base_delta * item.impact)), 4)
                entry["confidence"] = round(max(0.1, entry["confidence"] - min(0.1, item.impact * 0.1)), 4)
                entry["evidence_refs"] = [ref for ref in entry["evidence_refs"] if ref != item.id]
                rebuild_derived(profile, pack.canonical_json["schema"])
        except ValueError:
            pass
    elif body.scope == "birth_inference":
        for item in db.scalars(select(ProfileEvidence).where(ProfileEvidence.user_id == user.id, ProfileEvidence.source_type == "cold_start_prior", ProfileEvidence.invalidated.is_(False))):
            item.invalidated = True; item.invalidated_at = datetime.now(timezone.utc); affected.append(item.id)
        active_non_prior = db.scalars(select(ProfileEvidence).where(
            ProfileEvidence.user_id == user.id,
            ProfileEvidence.source_type != "cold_start_prior",
            ProfileEvidence.invalidated.is_(False),
        ).order_by(ProfileEvidence.created_at)).all()
        evidence_by_target: dict[str, list[ProfileEvidence]] = {}
        for item in active_non_prior:
            evidence_by_target.setdefault(item.target_path, []).append(item)
        active_overrides = {
            item.target_path: item for item in db.scalars(select(ManualOverride).where(
                ManualOverride.user_id == user.id, ManualOverride.active.is_(True),
            )).all()
        }
        for category_key, category in profile["core_traits"].items():
            for trait_key, entry in category.items():
                path = f"core_traits.{category_key}.{trait_key}"
                refs = evidence_by_target.get(path, [])
                value, confidence = 0.5, 0.1
                for evidence in refs:
                    value = min(1.0, max(0.0, value + evidence.direction * evidence.base_delta * evidence.impact))
                    confidence = 1 - (1 - confidence) * (1 - min(1.0, abs(evidence.impact)))
                if path in active_overrides:
                    value = float(active_overrides[path].value.get("value", value))
                    confidence = 1.0
                entry.update(
                    value=round(value, 4), confidence=round(confidence, 4),
                    evidence_refs=[item.id for item in refs],
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
        profile["birth_analysis"] = {
            "bazi_text": None, "day_master": None, "pattern_name": None,
            "strength_label": None,
            "relation_markers": {
                "combinations": 0, "self_punishments": 0, "other_punishments": 0,
                "clashes": 0, "harms": 0, "source_text": None,
            },
            "numerology_code": None,
            "algorithm_version": BirthFeatureCalculator.algorithm_version,
        }
        profile["digital_code_profile"] = empty_digital_code_profile()
        profile.pop("source_profile_document", None)
        profile.pop("source_portrait", None)
        profile.get("identity", {}).pop("template_person_id", None)
        profile.setdefault("meta", {}).setdefault("inference_policies", {})["birth_prior_enabled"] = False
        rebuild_derived(profile, pack.canonical_json["schema"])
    elif body.scope == "enneagram":
        for item in db.scalars(select(ProfileEvidence).where(
            ProfileEvidence.user_id == user.id,
            ProfileEvidence.target_path == "enneagram_profile.identity",
            ProfileEvidence.invalidated.is_(False),
        )):
            item.invalidated = True
            item.invalidated_at = datetime.now(timezone.utc)
            affected.append(item.id)
        profile["enneagram_profile"] = empty_enneagram_profile()
        profile["enneagram_profile"]["parameter_input"] = build_portrait_parameter_input(profile)
    else:
        affected.extend(item.id for item in db.scalars(select(ProfileEvidence).where(
            ProfileEvidence.user_id == user.id,
        )).all())
        affected.extend(item.id for item in db.scalars(select(Memory).where(
            Memory.user_id == user.id,
        )).all())
        db.execute(delete(ProfileEvidence).where(ProfileEvidence.user_id == user.id))
        db.execute(delete(Memory).where(Memory.user_id == user.id))
        db.execute(delete(CurrentState).where(CurrentState.user_id == user.id))
        db.execute(delete(RuntimePreference).where(RuntimePreference.user_id == user.id))
        db.execute(delete(ManualOverride).where(ManualOverride.user_id == user.id))
        db.execute(delete(ProfileVersion).where(ProfileVersion.user_id == user.id))
        db.execute(delete(AuditLog).where(AuditLog.user_id == user.id))
        conversation_ids = select(Conversation.id).where(Conversation.user_id == user.id)
        db.execute(update(ChatMessage).where(
            ChatMessage.conversation_id.in_(conversation_ids)
        ).values(engine_trace=None, profile_version=None))
        user.inference_enabled = False
        user.profile_consent = False
        user.sensitive_inference_consent = False
        user.display_name = None
        user.birth_date = None
        user.birth_time = None
        user.timezone_name = None
        profile, _ = build_initial_profile(
            user.id, None, None, None, pack.canonical_json, {}, None, {},
        )
        for category in profile["core_traits"].values():
            for entry in category.values():
                entry.update(value=0.5, confidence=0.1, evidence_refs=[])
        profile["meta"]["warnings"] = ["画像内容已清除；画像推断和敏感参考模型均已关闭。"]
        profile["meta"]["inference_policies"] = {
            "birth_prior_enabled": False,
            "reference_models_public": False,
            "reference_models_may_drive_replies": False,
        }
    new_no = 1 if body.scope == "all_profile" else version.version_no + 1
    profile["meta"]["profile_version"] = new_no
    recalculate_meta(profile)
    validate_profile_snapshot(profile)
    db.add(ProfileVersion(user_id=user.id, version_no=new_no, schema_version=profile["meta"]["schema_version"],
        cold_start_rule_pack_version=(pack.version if body.scope == "all_profile" else version.cold_start_rule_pack_version), dialogue_rule_pack_version=pack.version,
        overall_confidence=profile["meta"]["overall_confidence"], snapshot=profile))
    audit_before = {"redacted": True, "reason": "all_profile_forget"} if body.scope == "all_profile" else before
    _audit(db, req_id, tenant_id, f"profile.forget.{body.scope}", user, audit_before, profile, affected, ["USER-FORGET"], idem_key)
    db.commit()
    return {"request_id": req_id, "profile_version": new_no, "scope": body.scope, "affected_ids": affected, "rule_pack": _pack_summary(pack)}
