from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .digital_code import calculate_digital_code, empty_digital_code_profile
from .enneagram import build_enneagram_profile, build_portrait_parameter_input
from .rule_compiler import scenario_keys, trait_keys
from .template_people import TEMPLATE_BY_BIRTH_DATE, template_person_for_birth_date


# Exact 17-dimension values transcribed from the supplied complete-profile workbooks.
# They remain low-confidence display fixtures until experts confirm them as golden truth.
GOLDEN_TRAITS = {
    birth_date: dict(person.trait_values)
    for birth_date, person in TEMPLATE_BY_BIRTH_DATE.items()
}

TRAIT_NAMES = {
    "extroversion": "外向性", "social_warmth": "社交温度", "assertiveness": "果断性",
    "impulsivity": "冲动性", "openness": "开放性", "creativity": "创造力",
    "depth_of_thought": "思考深度", "thinking_ratio": "理性决策", "empathy": "共情能力",
    "risk_tolerance": "风险容忍度", "structure_pref": "结构化偏好", "discipline": "自律性",
    "adaptability": "适应性", "persistence": "坚持度", "confidence": "自信度",
    "optimism": "乐观度", "romantic_orientation": "关系投入",
}


class BirthFeatureCalculator:
    """Birthday feature adapter kept for compatibility with the profile builder."""

    algorithm_version = "birth-groups-digital-root-v1"

    def calculate(self, birth_date: str) -> tuple[str | None, list[str]]:
        return calculate_digital_code(birth_date)


def _entry(value: float = 0.5, confidence: float = 0.1, evidence_refs: list[str] | None = None) -> dict:
    if value < 0.35:
        label = "偏低"
    elif value > 0.65:
        label = "偏高"
    else:
        label = "中性/待确认"
    return {
        "value": round(value, 4), "confidence": round(confidence, 4), "tendency_label": label,
        "interpretation": "当前为低置信度候选，需要通过对话校准。",
        "evidence_refs": evidence_refs or [], "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _golden_trait_overrides(birth_date: str | None) -> dict[str, float]:
    return GOLDEN_TRAITS.get(birth_date or "", {})


def derive_mbti(profile: dict) -> dict:
    traits = flattened_traits(profile)
    values = {key: traits[key]["value"] for key in traits}
    dims = {
        "ei": values["extroversion"],
        "sn": 0.75 * values["openness"] + 0.25 * values["creativity"],
        "tf": values["thinking_ratio"],
        "jp": 0.75 * values["structure_pref"] + 0.25 * values["discipline"],
    }
    letters = (("I", "E"), ("S", "N"), ("F", "T"), ("P", "J"))
    label = "".join("X" if 0.45 <= value <= 0.55 else pair[value > 0.55] for value, pair in zip(dims.values(), letters))
    result = {}
    for key, value in dims.items():
        drivers = {
            "ei": ["extroversion"], "sn": ["openness", "creativity"],
            "tf": ["thinking_ratio"], "jp": ["structure_pref", "discipline"],
        }[key]
        result[key] = {
            "value": round(value, 4),
            "confidence": round(sum(traits[d]["confidence"] for d in drivers) / len(drivers), 4),
            "evidence_refs": sorted({ref for d in drivers for ref in traits[d]["evidence_refs"]}),
        }
    result["type_label"] = label
    return result


def flattened_traits(profile: dict) -> dict[str, dict]:
    return {key: value for category in profile["core_traits"].values() for key, value in category.items()}


def find_trait(profile: dict, trait_key: str) -> dict:
    for category in profile["core_traits"].values():
        if trait_key in category:
            return category[trait_key]
    raise KeyError(trait_key)


def _scenario_item(key: str, spec: dict, traits: dict) -> dict:
    drivers = spec["default_drivers"]
    refs = [{"field": d, "value": traits[d]["value"], "confidence": traits[d]["confidence"]} for d in drivers]
    strongest = max(refs, key=lambda x: abs(x["value"] - 0.5) * x["confidence"])
    direction = "偏明显" if abs(strongest["value"] - 0.5) >= 0.17 else "尚不明显"
    return {
        "feature": f"{spec['source_label']}：{TRAIT_NAMES[strongest['field']]}倾向{direction}",
        "parameter_refs": refs,
        "explanation": "由底层维度派生，不能视为独立事实。",
        "direct_evidence_refs": [], "generation_rule_id": f"DERIVED-SCENARIO-{key}",
        "confidence": round(max(x["confidence"] for x in refs), 4),
    }


def derive_behavior(profile: dict, schema: dict) -> dict:
    traits = flattened_traits(profile)
    return {
        group_key: {
            scenario_key: _scenario_item(scenario_key, spec, traits)
            for scenario_key, spec in group["scenarios"].items()
        }
        for group_key, group in schema["canonical_profile"]["behavior_style"]["groups"].items()
    }


def derive_language(profile: dict, schema: dict) -> dict:
    traits = flattened_traits(profile)
    ranked = sorted(traits.items(), key=lambda pair: abs(pair[1]["value"] - 0.5) * pair[1]["confidence"], reverse=True)

    def item(index: int, kind: str) -> dict:
        key, data = ranked[index % len(ranked)]
        return {
            "label": f"{kind}-{index + 1}", "behavior": f"可能体现{TRAIT_NAMES[key]}相关表达倾向",
            "example": None, "confidence": min(data["confidence"], 0.45),
            "evidence_refs": data["evidence_refs"], "origin": "derived",
        }

    groups = schema["canonical_profile"]["language_style"]["groups"]
    contexts = groups["typical_utterances"]["fixed_contexts"]
    return {
        "speaking_style": [item(i, "说话方式") for i in range(6)],
        "humor": [item(i + 6, "幽默") for i in range(3)],
        "emotion_expression": [item(i + 9, "情绪表达") for i in range(4)],
        "typical_utterances": {
            context["key"]: {
                "utterance_pattern": "待对话观察", "example": "暂无直接观察",
                "parameter_refs": [], "evidence_refs": [], "confidence": 0.1, "origin": "derived",
            }
            for context in contexts
        },
        "rare_utterances": [
            {"utterance_or_pattern": "待长期观察", "reason": "不能由缺失统计直接断言", "evidence_refs": [], "confidence": 0.05, "origin": "derived"}
            for _ in range(5)
        ],
    }


def derive_portrait(profile: dict) -> dict:
    ranked = sorted(
        flattened_traits(profile).items(),
        key=lambda pair: abs(pair[1]["value"] - 0.5) * pair[1]["confidence"], reverse=True,
    )
    top = ranked[:4]
    descriptors = [f"{TRAIT_NAMES[k]}{v['tendency_label']}" for k, v in top]
    refs = [k for k, _ in top]
    confidence = round(sum(v["confidence"] for _, v in top) / len(top), 4)
    return {
        "essence": {"content": "、".join(descriptors), "parameter_refs": refs, "confidence": confidence, "origin": "derived"},
        "strengths": {"content": "这些倾向在合适情境下可形成稳定优势。", "parameter_refs": refs, "confidence": confidence, "origin": "derived"},
        "weaknesses": {"content": "这些倾向在极端情境下也可能带来代价，需结合真实行为校准。", "parameter_refs": refs, "confidence": confidence, "origin": "derived"},
        "core_tension": {"content": "现有证据不足以可靠判断核心矛盾。", "parameter_refs": refs[:2], "confidence": min(confidence, 0.35), "origin": "derived"},
        "suitable_roles": {"content": "暂仅建议作为互动方式参考，不用于职业或人生决定。", "parameter_refs": refs, "confidence": confidence, "origin": "derived"},
    }


def build_profile_table_view(profile: dict) -> dict:
    return {
        "identity": profile.get("identity", {}),
        "birth_analysis": profile.get("birth_analysis", {}),
        "digital_code_profile": {
            "status": profile.get("digital_code_profile", {}).get("status"),
            "code": profile.get("digital_code_profile", {}).get("code"),
            "confidence": profile.get("digital_code_profile", {}).get("confidence"),
            "domains": {
                key: {
                    "label": value.get("label"),
                    "summary": value.get("summary"),
                    "summary_coverage_weight": value.get("summary_coverage_weight"),
                }
                for key, value in profile.get("digital_code_profile", {}).get("domains", {}).items()
            },
        },
        "enneagram_profile": {
            "status": profile.get("enneagram_profile", {}).get("status"),
            "identity": profile.get("enneagram_profile", {}).get("identity", {}),
            "confidence": profile.get("enneagram_profile", {}).get("confidence"),
            "interaction_strategy": profile.get("enneagram_profile", {}).get("interaction_strategy", {}),
        },
        "core_traits": {
            category_key: {
                trait_key: {
                    "value": trait.get("value"),
                    "confidence": trait.get("confidence"),
                }
                for trait_key, trait in category.items()
            }
            for category_key, category in profile.get("core_traits", {}).items()
        },
        "behavior_style": profile.get("behavior_style", {}),
        "language_style": profile.get("language_style", {}),
        "portrait": profile.get("portrait", {}),
        "runtime": profile.get("runtime", {}),
        "meta": profile.get("meta", {}),
    }


def build_initial_profile(
    user_id: str,
    display_name: str | None,
    birth_date: str | None,
    timezone_name: str | None,
    rules: dict,
    evidence_ids: dict[str, list[str]],
    enneagram_identity: dict[str, Any] | None = None,
    trait_priors: dict[str, float] | None = None,
) -> tuple[dict, list[str]]:
    schema = rules["schema"]
    calculator = BirthFeatureCalculator()
    code, warnings = calculator.calculate(birth_date) if birth_date else (None, ["未提供生日，已生成中性画像。"])
    overrides = {**(trait_priors or {}), **_golden_trait_overrides(birth_date)}
    categories = schema["canonical_profile"]["core_traits"]["categories"]
    core_traits = {}
    for category_key, category in categories.items():
        core_traits[category_key] = {}
        for key in category["fields"]:
            evidence = evidence_ids.get(key, [])
            core_traits[category_key][key] = _entry(overrides.get(key, 0.5), 0.35 if key in overrides else 0.12, evidence)

    now = datetime.now(timezone.utc).isoformat()
    profile = {
        "identity": {"user_id": user_id, "display_name": display_name, "birth_date": birth_date, "birth_time": None, "timezone": timezone_name},
        "birth_analysis": {"bazi_text": None, "day_master": None, "pattern_name": None, "strength_label": None,
                           "relation_markers": {"combinations": 0, "self_punishments": 0, "other_punishments": 0, "clashes": 0, "harms": 0, "source_text": None},
                           "numerology_code": code, "algorithm_version": calculator.algorithm_version},
        "core_traits": core_traits,
        "digital_code_profile": empty_digital_code_profile(),
        "enneagram_profile": build_enneagram_profile(enneagram_identity, rules["enneagram"]),
        "runtime": {"interaction_preferences": {}, "current_state": {}, "memories": []},
        "meta": {"profile_version": 1, "schema_version": schema["schema_version"], "rule_pack_versions": {},
                 "overall_confidence": 0.0, "created_at": now, "updated_at": now, "warnings": warnings},
    }
    profile["mbti_dimensions"] = derive_mbti(profile)
    template = template_person_for_birth_date(birth_date)
    if template:
        profile["mbti_dimensions"]["type_label"] = template.mbti
    profile["behavior_style"] = derive_behavior(profile, schema)
    profile["language_style"] = derive_language(profile, schema)
    profile["portrait"] = derive_portrait(profile)
    profile["enneagram_profile"]["parameter_input"] = build_portrait_parameter_input(profile)
    profile["table_view"] = build_profile_table_view(profile)
    recalculate_meta(profile)
    return profile, warnings


def recalculate_meta(profile: dict) -> None:
    traits = flattened_traits(profile)
    profile["meta"]["overall_confidence"] = round(sum(x["confidence"] for x in traits.values()) / len(traits), 4)
    profile["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()


def rebuild_derived(profile: dict, schema: dict) -> list[str]:
    profile["mbti_dimensions"] = derive_mbti(profile)
    profile["behavior_style"] = derive_behavior(profile, schema)
    profile["language_style"] = derive_language(profile, schema)
    profile["portrait"] = derive_portrait(profile)
    profile.setdefault("enneagram_profile", {})["parameter_input"] = build_portrait_parameter_input(profile)
    profile["table_view"] = build_profile_table_view(profile)
    recalculate_meta(profile)
    return ["mbti_dimensions", "behavior_style", "language_style", "portrait", "table_view"]


def clone_profile(profile: dict) -> dict:
    return deepcopy(profile)
