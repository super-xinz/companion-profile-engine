from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .config import get_settings
from .digital_code import (aggregate_trait_priors, build_digital_code_profile,
                           empty_digital_code_profile)
from .enneagram import (build_enneagram_profile, empty_enneagram_profile,
                         resolve_interaction_strategy)
from .extractor import SemanticExtractor, get_semantic_extractor
from .models import (AuditLog, CurrentState, ManualOverride, Memory, ProfileEvidence,
                     ProfileVersion, RulePack, RuntimePreference, User)
from .profile import (GOLDEN_TRAITS, TRAIT_NAMES, BirthFeatureCalculator,
                      build_initial_profile, clone_profile, find_trait, flattened_traits,
                      rebuild_derived, recalculate_meta)
from .rule_compiler import CompiledRulePack
from .rule_bank import extract_signals, fragments_for_code
from .schemas import (CorrectionRequest, ForgetRequest, MessageIngestRequest, ProfileInitRequest,
                      ReplyGuidance, SemanticFrame, SetEnneagramRequest, TraitSignal)
from .source_profiles import apply_source_profile
from .template_people import template_person_for_birth_date


class NotFoundError(Exception):
    pass


class VersionConflictError(Exception):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual


class ConsentError(Exception):
    pass


TRAIT_ROUTES = {
    "socializing_requires_solitude_recovery": [("extroversion", -1, "用户描述社交后需要独处恢复")],
    "likes_social_gathering": [("extroversion", 1, "用户表达对社交活动的稳定偏好")],
    "prefers_planning": [("structure_pref", 1, "用户表达计划偏好")],
    "uses_data_for_decisions": [("thinking_ratio", 1, "用户描述以数据辅助决策")],
}

FREQUENCY_FACTORS = {"once": 0.25, "sometimes": 0.45, "often": 0.70, "usually": 0.85, "always": 1.0, "never": 1.0, "unknown": 0.40}


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


def get_profile(db: Session, tenant_id: str, tenant_user_id: str) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    version = current_version(db, user)
    profile = clone_profile(version.snapshot)
    profile.setdefault("enneagram_profile", empty_enneagram_profile())
    if "digital_code_profile" not in profile:
        pack = db.scalar(select(RulePack).where(
            RulePack.status == "published"
        ).order_by(desc(RulePack.published_at)).limit(1))
        birth_date = profile.get("identity", {}).get("birth_date") if user.sensitive_inference_consent else None
        profile["digital_code_profile"] = (
            _digital_code_context(birth_date, pack)[2]
            if pack else empty_digital_code_profile()
        )
    now = datetime.now(timezone.utc)
    active_states = db.scalars(select(CurrentState).where(CurrentState.user_id == user.id, CurrentState.expires_at > now)).all()
    preferences = db.scalars(select(RuntimePreference).where(RuntimePreference.user_id == user.id)).all()
    memories = db.scalars(select(Memory).where(Memory.user_id == user.id, Memory.active.is_(True))).all()
    profile["runtime"]["current_state"] = {x.state_key: {**x.value, "expires_at": x.expires_at.isoformat()} for x in active_states}
    profile["runtime"]["interaction_preferences"] = {x.preference_key: x.value.get("value") for x in preferences}
    profile["runtime"]["memories"] = [{"memory_id": x.id, "type": x.memory_type, **x.content} for x in memories]
    return {"profile_version": version.version_no, "profile": profile,
            "rule_pack_versions": {"cold_start": version.cold_start_rule_pack_version, "dialogue": version.dialogue_rule_pack_version}}


def _pack_summary(pack: RulePack) -> dict:
    return {"version": pack.version, "sha256": pack.sha256, "status": pack.status}


def _trait_path(profile: dict, trait: str) -> str:
    for category, values in profile["core_traits"].items():
        if trait in values:
            return f"core_traits.{category}.{trait}"
    raise KeyError(trait)


def _evidence_type(frame: SemanticFrame) -> tuple[str, float, float]:
    if frame.temporal_scope == "habitual" or frame.frequency in {"often", "usually", "always", "never"}:
        return "explicit_self_report", 0.90, 0.06
    return "single_behavior_inference", 0.35, 0.02


def _apply_trait_frame(db: Session, user: User, profile: dict, frame: SemanticFrame, message_id: str, conversation_id: str) -> tuple[list[dict], list[str]]:
    patches, evidence_ids = [], []
    if frame.subject != "user" or frame.modality in {"hypothetical", "quoted"}:
        return patches, evidence_ids
    frequency = FREQUENCY_FACTORS[frame.frequency]
    for trait, base_direction, reason in TRAIT_ROUTES.get(frame.predicate, []):
        direction = -base_direction if frame.negated else base_direction
        existing = db.scalars(select(ProfileEvidence).where(ProfileEvidence.user_id == user.id,
            ProfileEvidence.target_path.like(f"%{trait}"), ProfileEvidence.invalidated.is_(False))).all()
        source_type, reliability, max_delta = _evidence_type(frame)
        prior_sessions = {x.factors.get("conversation_id") for x in existing if x.rule_id == f"DIALOGUE-{frame.predicate}-{trait}" and x.factors.get("conversation_id")}
        if source_type == "single_behavior_inference" and conversation_id not in prior_sessions and len(prior_sessions) >= 2:
            source_type, reliability, max_delta = "repeated_behavior", 0.75, 0.04
        independence = 0.5 if conversation_id in prior_sessions else 1.0
        factors = {"reliability": reliability, "explicitness": frame.explicitness, "frequency": frequency,
                   "context_relevance": 1.0, "freshness": 1.0, "independence": independence, "rule_weight": 1.0,
                   "conversation_id": conversation_id}
        impact = 1.0
        for key in ("reliability", "explicitness", "frequency", "context_relevance", "freshness", "independence", "rule_weight"):
            impact *= factors[key]
        entry = find_trait(profile, trait)
        before, conf_before = entry["value"], entry["confidence"]
        delta = min(max_delta, max_delta * impact)
        after = min(1.0, max(0.0, before + direction * delta))
        new_conf = 1 - (1 - conf_before) * (1 - impact)
        opposite = sum(abs(x.impact) for x in existing if x.direction and x.direction != direction)
        same = sum(abs(x.impact) for x in existing if x.direction == direction)
        conflict_ratio = min(opposite, same + impact) / max(opposite + same + impact, 1e-9)
        new_conf *= 1 - conflict_ratio * 0.5
        evidence = ProfileEvidence(user_id=user.id, source_type=source_type, source_message_id=message_id,
            semantic_frame=frame.model_dump(), target_path=_trait_path(profile, trait), direction=direction,
            base_delta=max_delta, impact=impact, factors=factors, rule_id=f"DIALOGUE-{frame.predicate}-{trait}", reason=reason)
        db.add(evidence)
        db.flush()
        entry.update(value=round(after, 4), confidence=round(new_conf, 4), updated_at=datetime.now(timezone.utc).isoformat())
        entry["evidence_refs"] = [*entry.get("evidence_refs", []), evidence.id]
        patches.append({"field": _trait_path(profile, trait), "before": before, "after": round(after, 4),
                        "confidence_before": conf_before, "confidence_after": round(new_conf, 4)})
        evidence_ids.append(evidence.id)
    return patches, evidence_ids


def _trait_catalog(profile: dict) -> dict[str, dict]:
    return {key: {"label": TRAIT_NAMES.get(key, key), "current_value": value["value"],
                  "current_confidence": value["confidence"]}
            for key, value in flattened_traits(profile).items()}


def _apply_trait_signal(db: Session, user: User, profile: dict, signal: TraitSignal, source_text: str,
                        message_id: str, conversation_id: str) -> tuple[dict | None, str | None, str | None]:
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
    if signal.confidence < 0.60:
        return None, None, "confidence_below_threshold"
    if signal.supporting_span not in source_text:
        return None, None, "supporting_span_not_in_message"
    caps = {"explicit_self_report": .06, "repeated_behavior": .04, "single_behavior_inference": .02}
    reliability = {"explicit_self_report": .90, "repeated_behavior": .75, "single_behavior_inference": .35}
    cap = caps[signal.evidence_scope]
    rule_id = f"MODEL-SCHEMA-{signal.target_trait}"
    existing = db.scalars(select(ProfileEvidence).where(
        ProfileEvidence.user_id == user.id,
        ProfileEvidence.target_path.like(f"%{signal.target_trait}"),
        ProfileEvidence.invalidated.is_(False),
    )).all()
    prior_sessions = {x.factors.get("conversation_id") for x in existing
                      if x.rule_id == rule_id and x.factors.get("conversation_id")}
    independence = 0.5 if conversation_id in prior_sessions else 1.0
    impact = reliability[signal.evidence_scope] * signal.confidence * signal.strength * independence
    direction = 1 if signal.direction == "increase" else -1
    entry = traits[signal.target_trait]
    before, conf_before = entry["value"], entry["confidence"]
    delta = min(cap, cap * impact)
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
        factors={"reliability": reliability[signal.evidence_scope], "model_confidence": signal.confidence,
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


def _apply_runtime_frame(db: Session, user: User, profile: dict, frame: SemanticFrame, message_id: str) -> tuple[bool, list[dict]]:
    changed, records = False, []
    if frame.subject != "user":
        return changed, records
    pref_map = {
        "prefers_short_responses": ("response_length", "short"),
        "needs_empathy_before_advice": ("empathy_first", 1.0),
        "dislikes_humor": ("humor_level", 0.0),
    }
    if frame.predicate in pref_map:
        key, value = pref_map[frame.predicate]
        existing = db.scalar(select(RuntimePreference).where(RuntimePreference.user_id == user.id, RuntimePreference.preference_key == key))
        if existing:
            existing.value, existing.source_message_id = {"value": value, "explicit": True}, message_id
        else:
            db.add(RuntimePreference(user_id=user.id, preference_key=key, value={"value": value, "explicit": True}, source_message_id=message_id))
        profile["runtime"]["interaction_preferences"][key] = value
        changed, records = True, [{"operation": "SET_INTERACTION_PREFERENCE", "field": key, "value": value}]
    state_map = {"low_energy": ("energy_level", 0.2, 12), "high_stress": ("stress_level", 0.85, 24)}
    if frame.predicate in state_map and frame.temporal_scope in {"now", "recent", "unknown"}:
        key, value, hours = state_map[frame.predicate]
        expires = datetime.now(timezone.utc) + timedelta(hours=hours)
        db.add(CurrentState(user_id=user.id, state_key=key, value={"value": value}, source_message_id=message_id, expires_at=expires))
        profile["runtime"]["current_state"][key] = {"value": value, "expires_at": expires.isoformat()}
        changed, records = True, records + [{"operation": "SET_STATE", "field": key, "value": value, "expires_at": expires.isoformat()}]
    return changed, records


def _reply_hints(profile: dict, interaction_strategy: dict | None = None) -> dict:
    prefs = profile["runtime"].get("interaction_preferences", {})
    states = profile["runtime"].get("current_state", {})
    hints: dict[str, Any] = {"max_sentences": 5, "answer_first": False, "empathy_first": False,
                             "question_count": 1, "structure_level": "simple", "humor_level": prefs.get("humor_level", 0.2)}
    if interaction_strategy:
        hints.update(interaction_strategy.get("hints", {}))
        hints["enneagram_strategy"] = {
            key: value for key, value in interaction_strategy.items()
            if key not in {"hints", "precedence"}
        }
        hints["strategy_precedence"] = interaction_strategy.get("precedence", [])
    if prefs.get("response_length") == "short":
        hints.update(max_sentences=3, answer_first=True)
    if prefs.get("empathy_first", 0) >= 0.67:
        hints.update(empathy_first=True, ask_support_or_solution=True)
    if states.get("stress_level", {}).get("value", 0) >= 0.7:
        hints.update(max_sentences=3, empathy_first=True, humor_level=0.0)
    if states.get("energy_level", {}).get("value", 1) <= 0.3:
        hints.update(structure_level="simple", action_count=1, allow_resume_later=True)
    structure = find_trait(profile, "structure_pref")["value"]
    if structure >= 0.67:
        hints.update(structure_level="steps", options_max=3)
    elif structure <= 0.33:
        hints.update(structure_level="flexible_options", avoid_rigid_plan=True)
    if profile["meta"]["overall_confidence"] < 0.4:
        hints.update(use_tentative_language=True, calibration_question_count=1)
    return hints


def _merge_reply_guidance(profile_hints: dict, guidance: ReplyGuidance) -> dict:
    merged = dict(profile_hints)
    merged.update(
        intent=guidance.intent,
        tone=guidance.tone,
        focus=guidance.focus,
        avoid=guidance.avoid,
        requires_fresh_information=guidance.requires_fresh_information,
        question_count=guidance.question_count,
    )
    merged["empathy_first"] = bool(profile_hints.get("empathy_first") or guidance.empathy_first)
    merged["answer_first"] = bool(profile_hints.get("answer_first") or guidance.answer_first)
    merged["max_sentences"] = min(profile_hints.get("max_sentences", 5), guidance.max_sentences)
    if profile_hints.get("structure_level") == "simple":
        merged["structure_level"] = guidance.structure_level
    merged["strategy_sources"] = ["current_message_model_guidance", "current_profile", "runtime_state_and_preferences"]
    return merged


def ingest_message(db: Session, tenant_id: str, tenant_user_id: str, body: MessageIngestRequest, pack: RulePack,
                   req_id: str, idem_key: str, semantic_extractor: SemanticExtractor | None = None) -> dict:
    user = find_user(db, tenant_id, tenant_user_id)
    if not user.profile_consent or not user.inference_enabled:
        raise ConsentError("画像推断已关闭")
    version = current_version(db, user)
    _check_version(version, body.expected_profile_version)
    before = clone_profile(version.snapshot)
    profile = clone_profile(version.snapshot)
    extractor = semantic_extractor or get_semantic_extractor()
    analysis = extractor.analyze(
        body.text,
        trait_catalog=_trait_catalog(profile),
        recent_turns=[turn.model_dump() for turn in body.context.recent_turns],
    )
    frames = analysis.frames
    patches, evidence_ids, runtime_operations = [], [], []
    accepted_trait_signals, rejected_trait_signals = [], []
    runtime_changed = False
    for signal in analysis.trait_signals:
        patch, evidence_id, rejection = _apply_trait_signal(
            db, user, profile, signal, body.text, body.message_id, body.conversation_id,
        )
        if rejection:
            rejected_trait_signals.append({**signal.model_dump(), "rejection_reason": rejection})
            continue
        accepted_trait_signals.append(signal.model_dump())
        if patch:
            patches.append(patch)
        if evidence_id:
            evidence_ids.append(evidence_id)
    for frame in frames:
        changed, operations = _apply_runtime_frame(db, user, profile, frame, body.message_id)
        runtime_changed |= changed
        runtime_operations.extend(operations)
        fact_changed, fact_operation = _apply_identity_fact(db, user, profile, frame, body.message_id)
        runtime_changed |= fact_changed
        if fact_operation:
            runtime_operations.append(fact_operation)
        if frame.semantic_domain == "event" and frame.subject == "user":
            memory = Memory(user_id=user.id, memory_type="event", content={"summary": frame.supporting_span, "predicate": frame.predicate}, source_message_id=body.message_id)
            db.add(memory)
            db.flush()
            profile["runtime"]["memories"].append({"memory_id": memory.id, "type": "event", **memory.content})
            runtime_changed = True
            runtime_operations.append({"operation": "UPSERT_MEMORY", "memory_id": memory.id})

    total_trait_change = sum(abs(x["after"] - x["before"]) for x in patches)
    if total_trait_change > 0.10:
        ratio = 0.10 / total_trait_change
        for patch in patches:
            patch["after"] = round(patch["before"] + (patch["after"] - patch["before"]) * ratio, 4)
            parent, key = _resolve_path(profile, patch["field"])
            parent[key]["value"] = patch["after"]
    material_trait_change = any(abs(x["after"] - x["before"]) >= 0.01 for x in patches)
    if material_trait_change:
        derived = rebuild_derived(profile, pack.canonical_json["schema"])
    else:
        derived = []
        recalculate_meta(profile)
    changed = material_trait_change or runtime_changed
    new_no = version.version_no + 1 if changed else version.version_no
    if changed:
        profile["meta"]["profile_version"] = new_no
        profile["meta"]["rule_pack_versions"] = {"cold_start": version.cold_start_rule_pack_version, "dialogue": pack.version, "sha256": pack.sha256}
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
    interaction_strategy = resolve_interaction_strategy(
        profile,
        pack.canonical_json.get("enneagram", {}),
        body.context.topic,
    )
    reply_hints = _merge_reply_guidance(_reply_hints(profile, interaction_strategy), analysis.reply_guidance)
    return {"request_id": req_id, "profile_version": new_no, "rule_pack": _pack_summary(pack),
            "semantic_extractor_version": extractor.version, "semantic_frames": [f.model_dump() for f in frames], "evidence": evidence_response,
            "candidate_trait_signals": [signal.model_dump() for signal in analysis.trait_signals],
            "accepted_trait_signals": accepted_trait_signals, "rejected_trait_signals": rejected_trait_signals,
            "profile_patch": patches, "runtime_operations": runtime_operations, "derived_patch": derived,
            "model_reply_guidance": analysis.reply_guidance.model_dump(), "reply_hints": reply_hints,
            "strategy_trace": {"semantic_analysis": extractor.version, "profile_version_used": new_no,
                               "candidate_signals": len(analysis.trait_signals),
                               "accepted_signals": len(accepted_trait_signals),
                               "enneagram_identity": profile.get("enneagram_profile", {}).get("identity", {}).get("code"),
                               "scene": interaction_strategy.get("scene") if interaction_strategy else None,
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
    if body.target_path.startswith(
        ("mbti_dimensions", "behavior_style", "language_style", "portrait", "digital_code_profile",
         "enneagram_profile", "meta")
    ):
        raise ValueError("派生字段不可直接更正；请更正底层事实或核心维度")
    before = clone_profile(version.snapshot)
    profile = clone_profile(version.snapshot)
    parent, key = _resolve_path(profile, body.target_path)
    old = parent[key]
    before_value = clone_profile(old) if isinstance(old, dict) else old
    applied_value = body.value
    evidence_ids = []
    if body.target_path.startswith("core_traits."):
        if not isinstance(body.value, (float, int)) or not 0 <= body.value <= 1:
            raise ValueError("核心维度更正值必须在0到1之间")
        old_value = old["value"]
        applied_value = round(max(old_value - 0.10, min(old_value + 0.10, float(body.value))), 4)
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
        code, signals, digital_code_profile, trait_priors = _digital_code_context(birth_key, pack)
        signals_by_trait: dict[str, list[dict]] = {}
        for signal in signals:
            signals_by_trait.setdefault(signal["target"], []).append(signal)
        for category_key, category in profile["core_traits"].items():
            for trait_key, entry in category.items():
                entry["evidence_refs"] = [ref for ref in entry["evidence_refs"] if ref not in invalidated]
                if not entry["evidence_refs"]:
                    value = GOLDEN_TRAITS.get(birth_key, {}).get(trait_key, trait_priors.get(trait_key, 0.5))
                    has_prior = trait_key in GOLDEN_TRAITS.get(birth_key, {}) or trait_key in trait_priors
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
        _, warnings = BirthFeatureCalculator().calculate(birth_key)
        profile["birth_analysis"]["numerology_code"] = code
        profile["digital_code_profile"] = digital_code_profile
        profile["meta"]["warnings"] = warnings
        derived = rebuild_derived(profile, pack.canonical_json["schema"])
        corrected_template = template_person_for_birth_date(corrected_date.isoformat())
        if corrected_template:
            apply_source_profile(profile, corrected_date.isoformat())
            profile["mbti_dimensions"]["type_label"] = corrected_template.mbti
    elif body.target_path.startswith("identity."):
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
    before = clone_profile(version.snapshot)
    profile = clone_profile(version.snapshot)
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
    profile["enneagram_profile"]["provenance"].append(evidence.id)
    new_no = version.version_no + 1
    profile["meta"]["profile_version"] = new_no
    profile["meta"]["schema_version"] = pack.canonical_json["schema"]["schema_version"]
    profile["meta"]["rule_pack_versions"]["enneagram"] = pack.version
    profile["meta"]["rule_pack_versions"]["sha256"] = pack.sha256
    recalculate_meta(profile)
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
    before = clone_profile(version.snapshot); profile = clone_profile(version.snapshot)
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
        for category in profile["core_traits"].values():
            for entry in category.values():
                if any(ref in affected for ref in entry["evidence_refs"]):
                    entry.update(value=0.5, confidence=0.1, evidence_refs=[r for r in entry["evidence_refs"] if r not in affected])
        profile["birth_analysis"]["numerology_code"] = None
        profile["digital_code_profile"] = empty_digital_code_profile()
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
    else:
        user.inference_enabled = False
        for item in db.scalars(select(ProfileEvidence).where(ProfileEvidence.user_id == user.id, ProfileEvidence.invalidated.is_(False))):
            item.invalidated = True; item.invalidated_at = datetime.now(timezone.utc); affected.append(item.id)
        for item in db.scalars(select(Memory).where(Memory.user_id == user.id, Memory.active.is_(True))):
            item.active = False; affected.append(item.id)
        profile["runtime"] = {"interaction_preferences": {}, "current_state": {}, "memories": []}
        profile["digital_code_profile"] = empty_digital_code_profile()
        profile["enneagram_profile"] = empty_enneagram_profile()
        profile["meta"]["warnings"] = ["画像推断已关闭，历史证据与记忆已失效。"]
    new_no = version.version_no + 1; profile["meta"]["profile_version"] = new_no; recalculate_meta(profile)
    db.add(ProfileVersion(user_id=user.id, version_no=new_no, schema_version=profile["meta"]["schema_version"],
        cold_start_rule_pack_version=version.cold_start_rule_pack_version, dialogue_rule_pack_version=pack.version,
        overall_confidence=profile["meta"]["overall_confidence"], snapshot=profile))
    _audit(db, req_id, tenant_id, f"profile.forget.{body.scope}", user, before, profile, affected, ["USER-FORGET"], idem_key)
    db.commit()
    return {"request_id": req_id, "profile_version": new_no, "scope": body.scope, "affected_ids": affected, "rule_pack": _pack_summary(pack)}
