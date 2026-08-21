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

PUBLIC_PROFILE_SCHEMA_VERSION = "public-profile-v1"
EVIDENCE_GRADE_LABELS = {
    "confirmed": "本人或专家确认",
    "well_supported": "较充分",
    "emerging": "初步观察",
    "unverified": "待观察",
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
    previous = profile.get("behavior_style", {})
    derived = {
        group_key: {
            scenario_key: _scenario_item(scenario_key, spec, traits)
            for scenario_key, spec in group["scenarios"].items()
        }
        for group_key, group in schema["canonical_profile"]["behavior_style"]["groups"].items()
    }
    # Direct dialogue observations are independent evidence. Rebuilding the
    # trait-derived layer must not silently erase them.
    for group_key, scenarios in derived.items():
        for scenario_key, item in scenarios.items():
            old = previous.get(group_key, {}).get(scenario_key, {})
            direct_refs = list(dict.fromkeys(old.get("direct_evidence_refs", [])))
            observations = old.get("observations", [])[-8:]
            if direct_refs:
                item["direct_evidence_refs"] = direct_refs
                item["observations"] = observations
                item["confidence"] = max(item["confidence"], float(old.get("confidence", 0)))
                item["explanation"] = "包含直接对话观察；底层维度只作为辅助归纳。"
    return derived


def derive_language(profile: dict, schema: dict) -> dict:
    traits = flattened_traits(profile)
    previous = profile.get("language_style", {})
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
    result = {
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
    observed = [
        entry for entry in previous.get("speaking_style", [])
        if entry.get("origin") == "observed"
    ]
    if observed:
        result["speaking_style"] = [*observed[-6:], *result["speaking_style"]][:6]
    if previous.get("observation_state"):
        result["observation_state"] = deepcopy(previous["observation_state"])
    return result


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


def validate_profile_snapshot(profile: dict) -> None:
    """Validate invariants that the JSON database column cannot enforce."""
    required = {
        "identity", "birth_analysis", "digital_code_profile", "mbti_dimensions",
        "core_traits", "enneagram_profile", "behavior_style", "language_style",
        "portrait", "runtime", "meta",
    }
    missing = sorted(required - set(profile))
    if missing:
        raise ValueError(f"画像快照缺少顶层字段: {missing}")
    traits = flattened_traits(profile)
    missing_traits = sorted(set(TRAIT_NAMES) - set(traits))
    unknown_traits = sorted(set(traits) - set(TRAIT_NAMES))
    if missing_traits or unknown_traits:
        raise ValueError(f"画像17维结构不完整，缺少={missing_traits}，未知={unknown_traits}")
    for key, entry in traits.items():
        if not isinstance(entry, dict):
            raise ValueError(f"画像维度 {key} 必须是对象")
        for number_key in ("value", "confidence"):
            value = entry.get(number_key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"画像维度 {key}.{number_key} 必须在0到1之间")
        if not isinstance(entry.get("evidence_refs", []), list):
            raise ValueError(f"画像维度 {key}.evidence_refs 必须是数组")
    runtime = profile["runtime"]
    for key, default_type in (
        ("interaction_preferences", dict), ("current_state", dict), ("memories", list),
    ):
        if not isinstance(runtime.get(key), default_type):
            raise ValueError(f"runtime.{key} 类型不正确")


def _trait_direction(value: float) -> tuple[str, str, int]:
    if value < 0.35:
        return "lower", "较少表现", 24
    if value > 0.65:
        return "higher", "较常表现", 76
    return "balanced", "视情境而定", 50


def _evidence_grade(summary: dict[str, Any]) -> str:
    if summary.get("confirmed", 0):
        return "confirmed"
    if summary.get("repeated", 0) or summary.get("independent_sessions", 0) >= 3 or summary.get("explicit", 0) >= 2:
        return "well_supported"
    if summary.get("explicit", 0) or summary.get("observed", 0):
        return "emerging"
    return "unverified"


def build_public_profile(profile: dict, evidence_by_path: dict[str, dict[str, Any]] | None = None) -> dict:
    """Project an internal snapshot into the evidence-oriented presentation contract.

    Reference systems, raw source files, birthdays and internal confidence numbers
    are deliberately absent. Callers needing those fields must use an explicitly
    authorized expert endpoint.
    """
    evidence_by_path = evidence_by_path or {}
    public_traits: dict[str, dict[str, dict[str, Any]]] = {}
    trusted_traits: dict[str, tuple[str, dict[str, Any], float]] = {}
    for category_key, category in profile.get("core_traits", {}).items():
        public_traits[category_key] = {}
        for trait_key, entry in category.items():
            path = f"core_traits.{category_key}.{trait_key}"
            summary = evidence_by_path.get(path, {})
            grade = _evidence_grade(summary)
            if grade == "unverified":
                direction, tendency, position = "unknown", "证据待积累", 50
            else:
                direction, tendency, position = _trait_direction(float(entry.get("value", 0.5)))
            non_prior_count = int(summary.get("confirmed", 0) + summary.get("explicit", 0)
                                  + summary.get("repeated", 0) + summary.get("observed", 0))
            public_item = {
                "label": TRAIT_NAMES.get(trait_key, trait_key),
                "direction": direction,
                "tendency": tendency,
                "position": position,
                "evidence_grade": grade,
                "evidence_grade_label": EVIDENCE_GRADE_LABELS[grade],
                "evidence_count": non_prior_count,
                "basis": {
                    "confirmed": int(summary.get("confirmed", 0)),
                    "self_report": int(summary.get("explicit", 0)),
                    "repeated_observation": int(summary.get("repeated", 0)),
                    "single_observation": int(summary.get("observed", 0)),
                    "independent_sessions": int(summary.get("independent_sessions", 0)),
                },
                "updated_at": entry.get("updated_at"),
                "editable_path": path,
            }
            public_traits[category_key][trait_key] = public_item
            if grade != "unverified":
                trusted_traits[trait_key] = (grade, public_item, abs(float(entry.get("value", 0.5)) - 0.5))

    rank = {"confirmed": 3, "well_supported": 2, "emerging": 1, "unverified": 0}
    stable = sorted(
        trusted_traits.values(), key=lambda item: (rank[item[0]], item[2]), reverse=True,
    )
    if stable:
        descriptions = [f"{item[1]['label']}方面{item[1]['tendency']}" for item in stable[:3]]
        overall_observation = "已有证据显示，" + "；".join(descriptions) + "。这些描述会随新对话继续校准。"
    else:
        overall_observation = "对话证据仍在积累，目前不适合形成稳定的人物结论。"

    public_behavior: dict[str, dict[str, Any]] = {}
    for group_key, scenarios in profile.get("behavior_style", {}).items():
        visible: dict[str, Any] = {}
        for scenario_key, item in scenarios.items():
            direct_refs = item.get("direct_evidence_refs", [])
            driver_keys = [ref.get("field") for ref in item.get("parameter_refs", []) if isinstance(ref, dict)]
            driver_grades = [trusted_traits[key][0] for key in driver_keys if key in trusted_traits]
            if not direct_refs and not driver_grades:
                continue
            grade = "well_supported" if len(direct_refs) >= 3 else (
                "emerging" if direct_refs else max(driver_grades, key=rank.get)
            )
            latest = (item.get("observations") or [{}])[-1]
            visible[scenario_key] = {
                "observation": latest.get("summary") or item.get("feature"),
                "basis": "直接对话观察" if direct_refs else "由已有行为证据归纳",
                "evidence_grade": grade,
                "evidence_grade_label": EVIDENCE_GRADE_LABELS[grade],
                "evidence_count": len(direct_refs),
            }
        if visible:
            public_behavior[group_key] = visible

    language_items = [
        {
            "label": item.get("behavior") or item.get("label"),
            "sample_count": int(item.get("sample_count", 0)),
            "evidence_grade": "well_supported" if int(item.get("sample_count", 0)) >= 5 else "emerging",
            "evidence_grade_label": EVIDENCE_GRADE_LABELS[
                "well_supported" if int(item.get("sample_count", 0)) >= 5 else "emerging"
            ],
        }
        for item in profile.get("language_style", {}).get("speaking_style", [])
        if item.get("origin") == "observed" and int(item.get("sample_count", 0)) >= 3
    ]

    coverage = len(trusted_traits)
    if coverage >= 12 and sum(rank[item[0]] >= 2 for item in stable) >= 5:
        evidence_level = "较充分"
    elif coverage >= 5:
        evidence_level = "形成中"
    elif coverage:
        evidence_level = "初步"
    else:
        evidence_level = "待积累"

    runtime = profile.get("runtime", {})
    identity = profile.get("identity", {})
    return {
        "schema_version": PUBLIC_PROFILE_SCHEMA_VERSION,
        "display_mode": "evidence_oriented",
        "summary": {
            "overall_observation": overall_observation,
            "evidence_level": evidence_level,
            "observed_dimensions": coverage,
            "total_dimensions": len(TRAIT_NAMES),
        },
        "identity": {
            "display_name": identity.get("display_name"),
            "timezone": identity.get("timezone"),
        },
        "interaction": {
            "preferences": deepcopy(runtime.get("interaction_preferences", {})),
            "current_state": deepcopy(runtime.get("current_state", {})),
        },
        "stable_tendencies": public_traits,
        "scenario_observations": public_behavior,
        "communication_observations": language_items,
        "facts_and_memories": deepcopy(runtime.get("memories", [])),
        "meta": {
            "profile_version": profile.get("meta", {}).get("profile_version"),
            "updated_at": profile.get("meta", {}).get("updated_at"),
            "evidence_level": evidence_level,
            "observed_dimensions": coverage,
            "total_dimensions": len(TRAIT_NAMES),
        },
        "visibility": {
            "internal_reference_available": True,
            "hidden_from_default_view": [
                "birth_analysis", "digital_code_profile", "mbti_dimensions",
                "enneagram_profile", "source_profile_document", "source_portrait",
                "internal_confidence", "internal_weights",
            ],
        },
    }


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
