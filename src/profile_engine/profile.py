from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .rule_compiler import scenario_keys, trait_keys


GOLDEN_CODES = {
    "1998-12-06": ("6318", "ISTJ"),
    "1989-10-15": ("6118", "ENTP"),
    "1988-08-09": ("9817", "ENFP"),
}

# Exact 17-dimension values transcribed from the three supplied complete-profile workbooks.
# They remain low-confidence display fixtures until experts confirm them as golden truth.
GOLDEN_TRAITS = {
    "1998-12-06": {"extroversion": .45, "social_warmth": .69, "assertiveness": .30, "impulsivity": .26,
        "openness": .31, "creativity": .49, "depth_of_thought": .58, "thinking_ratio": .56, "empathy": .64,
        "risk_tolerance": .41, "structure_pref": .68, "discipline": .84, "adaptability": .55, "persistence": .45,
        "confidence": .31, "optimism": .66, "romantic_orientation": .83},
    "1989-10-15": {"extroversion": .83, "social_warmth": .54, "assertiveness": .83, "impulsivity": .66,
        "openness": 1.0, "creativity": .66, "depth_of_thought": .57, "thinking_ratio": .51, "empathy": .51,
        "risk_tolerance": .66, "structure_pref": .10, "discipline": .39, "adaptability": .70, "persistence": .80,
        "confidence": .84, "optimism": .49, "romantic_orientation": .71},
    "1988-08-09": {"extroversion": 1.0, "social_warmth": 1.0, "assertiveness": .40, "impulsivity": .60,
        "openness": 1.0, "creativity": .52, "depth_of_thought": .50, "thinking_ratio": .31, "empathy": .70,
        "risk_tolerance": .84, "structure_pref": .18, "discipline": .60, "adaptability": .87, "persistence": .36,
        "confidence": .47, "optimism": .75, "romantic_orientation": 1.0},
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
    """Extension point. The source material only authorizes three exact mappings."""

    algorithm_version = "golden-mappings-only-v1"

    def calculate(self, birth_date: str) -> tuple[str | None, list[str]]:
        if birth_date in GOLDEN_CODES:
            return GOLDEN_CODES[birth_date][0], []
        return None, ["专家尚未提供任意生日到四位数字学编码的通用公式；已生成中性低置信度画像。"]


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


def build_initial_profile(user_id: str, display_name: str | None, birth_date: str | None, timezone_name: str | None, rules: dict, evidence_ids: dict[str, list[str]]) -> tuple[dict, list[str]]:
    schema = rules["schema"]
    calculator = BirthFeatureCalculator()
    code, warnings = calculator.calculate(birth_date) if birth_date else (None, ["未提供生日，已生成中性画像。"])
    overrides = _golden_trait_overrides(birth_date)
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
        "runtime": {"interaction_preferences": {}, "current_state": {}, "memories": []},
        "meta": {"profile_version": 1, "schema_version": schema["schema_version"], "rule_pack_versions": {},
                 "overall_confidence": 0.0, "created_at": now, "updated_at": now, "warnings": warnings},
    }
    profile["mbti_dimensions"] = derive_mbti(profile)
    if birth_date in GOLDEN_CODES:
        profile["mbti_dimensions"]["type_label"] = GOLDEN_CODES[birth_date][1]
    profile["behavior_style"] = derive_behavior(profile, schema)
    profile["language_style"] = derive_language(profile, schema)
    profile["portrait"] = derive_portrait(profile)
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
    recalculate_meta(profile)
    return ["mbti_dimensions", "behavior_style", "language_style", "portrait"]


def clone_profile(profile: dict) -> dict:
    return deepcopy(profile)
