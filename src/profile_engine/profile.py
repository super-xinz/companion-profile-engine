from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
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

PUBLIC_PROFILE_SCHEMA_VERSION = "public-profile-v2"


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
            if old.get("origin") == "user_supplied_complete_profile":
                item.update({
                    "feature": old.get("feature") or item["feature"],
                    "explanation": old.get("explanation") or item["explanation"],
                    "origin": "user_supplied_complete_profile",
                    "source_label": old.get("source_label"),
                    "parameter_text": old.get("parameter_text"),
                    "generation_rule_id": old.get("generation_rule_id") or item["generation_rule_id"],
                    "confidence": max(item["confidence"], float(old.get("confidence", 0))),
                })
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
    source_language = profile.get("source_language")
    if isinstance(source_language, dict) and source_language.get("speaking_style"):
        result = deepcopy(source_language)
    observed = [
        entry for entry in previous.get("speaking_style", [])
        if entry.get("origin") == "observed"
    ]
    if observed:
        result["speaking_style"] = [*observed[-6:], *result["speaking_style"]]
    if previous.get("observation_state"):
        result["observation_state"] = deepcopy(previous["observation_state"])
    return result


def derive_portrait(profile: dict) -> dict:
    source_portrait = profile.get("source_portrait")
    if isinstance(source_portrait, dict) and source_portrait:
        return {
            key: {
                "content": item.get("content", ""),
                "parameter_refs": list(item.get("parameter_refs", [])),
                "confidence": float(item.get("confidence", 0.45)),
                "origin": "user_supplied_complete_profile",
            }
            for key, item in source_portrait.items()
        }
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


def _evidence_grade(summary: dict[str, Any]) -> str:
    if summary.get("confirmed", 0):
        return "confirmed"
    if summary.get("repeated", 0) or summary.get("independent_sessions", 0) >= 3 or summary.get("explicit", 0) >= 2:
        return "well_supported"
    if summary.get("explicit", 0) or summary.get("observed", 0):
        return "emerging"
    return "unverified"


DIMENSION_POLES = {
    "extroversion": ("独处恢复", "互动恢复"),
    "social_warmth": ("关系表达克制", "主动传递温度"),
    "assertiveness": ("先协调再表态", "主动推动与拍板"),
    "impulsivity": ("充分过滤后行动", "快速响应与行动"),
    "openness": ("事实与经验优先", "可能性与概念优先"),
    "creativity": ("沿用成熟路径", "探索新路径"),
    "depth_of_thought": ("快速形成判断", "深入拆解"),
    "thinking_ratio": ("感受与价值优先", "逻辑与一致性优先"),
    "empathy": ("问题与边界优先", "情绪与处境优先"),
    "risk_tolerance": ("控制不确定性", "接受试错"),
    "structure_pref": ("弹性与即兴", "计划与秩序"),
    "discipline": ("兴趣与环境驱动", "自我约束驱动"),
    "adaptability": ("维持稳定路径", "快速调整路径"),
    "persistence": ("及时切换方向", "持续投入目标"),
    "confidence": ("谨慎校验自我", "相信自身判断"),
    "optimism": ("优先预判风险", "优先看见机会"),
    "romantic_orientation": ("保留个人边界", "主动投入关系"),
}

DIMENSION_TAGS = {
    "extroversion": ("独处充电", "互动驱动"),
    "social_warmth": ("关系克制", "温暖主动"),
    "assertiveness": ("审慎表态", "果断推动"),
    "impulsivity": ("行动克制", "快速行动"),
    "openness": ("经验导向", "可能性导向"),
    "creativity": ("成熟路径", "创意探索"),
    "depth_of_thought": ("快速判断", "深度思考"),
    "thinking_ratio": ("价值导向", "逻辑导向"),
    "empathy": ("边界清晰", "高共情"),
    "risk_tolerance": ("稳健试探", "敢于试错"),
    "structure_pref": ("弹性即兴", "结构清晰"),
    "discipline": ("环境驱动", "高度自律"),
    "adaptability": ("路径稳定", "灵活应变"),
    "persistence": ("灵活切换", "持续投入"),
    "confidence": ("谨慎自省", "自信坚定"),
    "optimism": ("风险敏感", "积极乐观"),
    "romantic_orientation": ("关系有边界", "重视关系"),
}

CATEGORY_NAMES = {
    "energy_mode": "能量与社交",
    "cognition_mode": "认知与探索",
    "decision_mode": "决策与判断",
    "action_mode": "行动与执行",
    "self_perception": "自我感受",
    "relationship_mode": "关系投入",
    "self_system": "自我感受",
    "emotion_relation_mode": "情绪与关系",
}

SCENARIO_NAMES = {
    "task_received": "接到任务", "task_progress": "推进任务", "obstacle": "遇到阻碍",
    "decision": "做决定", "being_urged": "被催促", "after_error": "出错之后",
    "facing_change": "面对变化", "first_meeting": "初次见面",
    "familiar_relationship": "熟悉关系", "helping_others": "帮助他人",
    "being_needed": "被需要", "being_misunderstood": "被误解", "conflict": "发生冲突",
    "romantic_interaction": "亲密互动", "confidence_state": "自信状态",
    "optimism_state": "看待未来", "stress_response": "压力反应", "energy_source": "精力恢复",
}

SCENARIO_GROUP_NAMES = {
    "task_style": "工作与行动", "relationship_style": "关系与协作", "inner_state_style": "内在状态",
}

REFERENCE_TERMS = re.compile(
    r"MBTI|ENFP|ENTP|ISTJ|ESFJ|九型|数字密码|生命灵数|命理|星盘|星座|八字|日主|格局|大运|伤官|正官|七杀|正印|偏财|食神|官杀|"
    r"比肩|身强|身弱|子卯|丙辛|巳亥|甲木|壬水|戊子|己卯|[ESTFJPNI]型",
    re.IGNORECASE,
)


def _bounded_value(entry: dict[str, Any]) -> float:
    return max(0.0, min(1.0, float(entry.get("value", 0.5))))


def _dimension_tendency(trait_key: str, value: float) -> str:
    low, high = DIMENSION_POLES[trait_key]
    if value <= 0.15:
        return f"明显偏向{low}"
    if value < 0.40:
        return f"更偏向{low}"
    if value <= 0.60:
        return f"会在{low}与{high}之间切换"
    if value < 0.85:
        return f"更偏向{high}"
    return f"明显偏向{high}"


def _dimension_description(trait_key: str, value: float) -> str:
    low, high = DIMENSION_POLES[trait_key]
    if 0.40 <= value <= 0.60:
        return f"会根据任务、关系和当前状态，在“{low}”与“{high}”之间调整。"
    chosen = low if value < 0.5 else high
    return f"日常更常采用“{chosen}”的方式，具体表现仍会随场景变化。"


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\\u201c", "“").replace("\\u201d", "”")
    return re.sub(r"(?<![A-Za-z])ta(?![A-Za-z])", "这个人", text).strip()


def _neutralize_public_phrase(value: Any) -> str:
    text = _clean_text(value)
    replacements = {
        "表面接受、内心已经在想怎么颠覆": "接受任务后会迅速设想不同的实现路径",
        "看似规矩实则跳跃": "外部交付有秩序，内部推进路径较跳跃",
        "快速判断但会给自己一个“理性理由”": "通常先形成快速判断，再补充完整逻辑链",
        "拥抱——尤其是“意外”": "对意外变化接受度较高",
        "气场强、有魅力、但你不确定这个人在想什么": "初次接触时表达有力量，同时保留一定距离",
        "有温度但不黏腻、忠诚但有边界": "关系中愿意投入，同时保留边界",
        "直接给方案——但这个方案可能不走寻常路": "帮助他人时更常提供新角度和替代方案",
        "有担当但有底线": "愿意承担责任，同时会说明边界",
        "有标准、不将就、但要精神契合": "重视关系质量、价值观与精神交流",
        "中性偏下": "对风险保持敏感，较少盲目乐观",
        "外表冷静、内心翻腾": "压力下外在保持克制，内部思考速度加快",
        "表面配合、内心冷笑": "表面保持配合，内在可能产生抵触",
        "表面答应、内心焦虑、加速但不降质量": "会先回应任务，同时压力感可能上升，并倾向在维持质量的前提下加快推进",
        "偏低，需要外部肯定确认自己": "自我确认相对谨慎，也会参考外部反馈",
        "外表看不出，内心高速运转": "压力下外在表现较克制，同时会加快信息处理与自我校验",
        "独处充电，社交耗电": "独处更有助于恢复精力，持续社交会增加消耗",
        "重视关系但不主动": "重视关系，主动表达相对克制",
        "习惯性自我归因": "出现问题时容易先检查自身责任",
        "先想能不能绕过规则": "先寻找规则允许范围内的替代路径",
        "懒得解释——除非你在职场或重要关系里": "通常不主动解释；在工作或重要关系受影响时会主动说清楚",
        "正面刚、逻辑压、不留情": "冲突中倾向直接回应，并用逻辑推进",
        "极高但不盲目": "自我判断较坚定",
        "挑战+自由度+被认可": "挑战、行动自由与成果认可",
        "说服力极强": "说服力较强",
        "偶尔冷到极点的幽默": "偶尔使用冷幽默",
        "强者的语言习惯": "较少主动示弱",
        "有礼貌但观点一个都不退让": "保持礼貌，同时坚持核心观点",
        "逻辑清晰、但埋伏着挑衅": "逻辑清晰，偶尔使用挑战性反问",
        "用完美的逻辑包裹着的反问": "用逻辑充分的反问推动讨论",
        "说完没人能反驳": "能够清楚表达并维护自己的立场",
        "善于让人相信“你的想法其实就是这个人的想法”": "善于通过提问引导对方自行形成结论",
        "（引导对方得出这个人想要的结论）": "（通过提问帮助对方梳理结论）",
        "逻辑碾压型辩手": "逻辑清晰的讨论者",
        "创意策反者": "创意破局者",
        "那个“别人说不可能但这个人做成了”的人": "善于把高难度构想推进成结果的人",
    }
    for original, replacement in replacements.items():
        text = text.replace(original, replacement)
    return (text.replace("从不", "通常不会").replace("永远", "往往")
            .replace("极端", "明显").replace("极致", "高度")
            .replace("极高", "较高").replace("极低", "较低")
            .replace("极强", "较强").replace("爆表", "突出").replace("碾压", "强"))


def _clean_reference_segments(content: Any) -> list[str]:
    segments = [part.strip(" 。") for part in re.split(r"[、，,；;]", _clean_text(content))]
    replacements = {
        "感染力极强": "感染力较强",
        "创意无限": "创意丰富",
        "思维极度敏捷": "思维敏捷",
        "创造力爆表": "创造力突出",
        "逻辑碾压级": "逻辑分析能力强",
        "情商极高": "较善于理解关系",
        "共情无出其右": "共情能力突出",
        "自律可靠到骨子里": "自律而可靠",
        "极度可靠": "稳定可靠",
        "不会背叛的副手": "值得信赖的协作伙伴",
    }
    cleaned = []
    for part in segments:
        if not part or REFERENCE_TERMS.search(part):
            continue
        for original, replacement in replacements.items():
            part = part.replace(original, replacement)
        cleaned.append(_neutralize_public_phrase(part))
    return cleaned


def _clean_scenario_pattern(content: Any, scenario_key: str) -> str:
    text = _clean_text(content)
    if not REFERENCE_TERMS.search(text):
        return _neutralize_public_phrase(text)
    candidates = [part.strip(" 。") for part in re.split(r"——|—|：|:", text)]
    clean = next((part for part in candidates if part and not REFERENCE_TERMS.search(part)), "")
    return _neutralize_public_phrase(
        clean or f"在“{SCENARIO_NAMES.get(scenario_key, '当前场景')}”中会采用与自身节奏一致的应对方式"
    )


def _portrait_segments(profile: dict, key: str) -> list[str]:
    item = profile.get("source_portrait", {}).get(key, {})
    segments = _clean_reference_segments(item.get("content"))
    if key != "weaknesses":
        return segments
    softer = {
        "缺乏纪律和持久力": "长期执行更依赖明确节奏",
        "缺乏纪律": "重复执行更依赖外部节点",
        "厌恶routine": "对重复流程的耐受度较低",
        "容易分心": "多任务时注意力容易切换",
        "做决定靠感觉不靠逻辑": "决策时更依赖感受与价值",
        "自信依赖外部反馈": "自我确认容易受外部反馈影响",
        "对细节没耐心": "长时间处理细节时容易消耗",
        "不擅长安慰人": "安慰他人时更习惯先给方案",
        "共情偏弱": "高压沟通中容易先处理问题",
        "不擅长安抚": "安抚情绪时需要更明确的方法",
        "过度克制可能让自己太辛苦": "习惯克制时可能忽略自身消耗",
        "害怕冲突可能妥协太多": "面对冲突时可能过早让步",
        "不太会拒绝人": "拒绝他人时需要更清晰的边界",
        "不自信": "容易低估自己的完成度",
        "过度自省": "自我校验偏多",
        "不敢争取": "主动争取前会反复权衡",
        "做决定犹豫": "重要决定需要更长确认时间",
    }
    return [softer.get(segment, segment) for segment in segments]


def _tag_for_trait(trait_key: str, value: float) -> str:
    low, high = DIMENSION_TAGS[trait_key]
    return low if value < 0.5 else high


def _headline_for_traits(traits: dict[str, dict], visible_keys: set[str]) -> tuple[str, list[str]]:
    ranked = sorted(
        ((key, _bounded_value(entry)) for key, entry in traits.items() if key in visible_keys),
        key=lambda pair: abs(pair[1] - 0.5), reverse=True,
    )
    tags = [_tag_for_trait(key, value) for key, value in ranked[:4]]
    if not tags:
        return "从真实表达与互动记录中整理出的个体画像。", []
    if len(tags) == 1:
        return f"一个以{tags[0]}为鲜明特征的人。", tags
    return f"一个{('、'.join(tags[:3]))}的人。", tags


def _operating_model(traits: dict[str, dict], visible_keys: set[str], source_complete: bool) -> list[dict[str, Any]]:
    def value(key: str) -> float:
        return _bounded_value(traits.get(key, {}))

    def include(keys: tuple[str, ...]) -> bool:
        return source_complete or bool(set(keys) & visible_keys)

    extroversion, warmth = value("extroversion"), value("social_warmth")
    openness, creativity, depth = value("openness"), value("creativity"), value("depth_of_thought")
    thinking, empathy, assertiveness = value("thinking_ratio"), value("empathy"), value("assertiveness")
    structure, discipline = value("structure_pref"), value("discipline")
    adaptability, persistence = value("adaptability"), value("persistence")
    relationship = value("romantic_orientation")

    cards = []
    if include(("extroversion", "social_warmth")):
        energy = ("互动和观点交换会明显提升精力" if extroversion > .65 else
                  "独处和安静空间更有助于恢复精力" if extroversion < .35 else
                  "会在独处恢复与互动激活之间切换")
        relation_entry = ("，进入关系时通常会主动释放友好信号。" if warmth > .65 else
                          "，关系表达相对克制，更看重合适的距离。" if warmth < .35 else
                          "，社交热度会随熟悉程度逐步变化。")
        cards.append({"key": "energy", "title": "能量来源", "summary": energy + relation_entry,
                      "drivers": ["外向性", "社交温度"]})
    if include(("openness", "creativity", "depth_of_thought")):
        cognition = ("先看可能性、关联和新的解法" if openness > .65 else
                     "先看事实、经验和已经验证的路径" if openness < .35 else
                     "会同时考虑事实基础与未来可能")
        creation = ("，擅长主动改写问题和探索新路径。" if creativity > .65 else
                    "，更善于在成熟框架中优化细节。" if creativity < .35 else
                    "，会在创新与可执行性之间寻找平衡。")
        if depth > .7:
            creation = creation[:-1] + "，并倾向把问题继续向深处拆解。"
        cards.append({"key": "cognition", "title": "理解世界", "summary": cognition + creation,
                      "drivers": ["开放性", "创造力", "思考深度"]})
    if include(("thinking_ratio", "empathy", "assertiveness")):
        decision = ("逻辑一致性和可论证性通常排在前面" if thinking > .65 else
                    "价值感、关系影响和人的感受通常排在前面" if thinking < .35 else
                    "会综合逻辑、现实影响与人的感受")
        pace = ("，形成判断后会比较主动地推动结果。" if assertiveness > .65 else
                "，做决定前更愿意先听取他人意见。" if assertiveness < .35 else
                "，会根据责任边界决定是推动还是协调。")
        if empathy > .75:
            pace = pace[:-1] + "，也会敏锐留意对方是否被照顾到。"
        cards.append({"key": "decision", "title": "判断与决策", "summary": decision + pace,
                      "drivers": ["理性决策", "共情能力", "果断性"]})
    if include(("structure_pref", "discipline", "adaptability", "persistence")):
        execution = ("喜欢先明确步骤、标准与节奏" if structure > .65 else
                     "更适合先明确目标，再保留路径上的自由度" if structure < .35 else
                     "既需要基本边界，也希望保留调整空间")
        follow = ("，并能依靠自我约束稳定推进。" if discipline > .7 else
                  "，推进效果更依赖兴趣、环境或外部节点。" if discipline < .4 else
                  "，通常能维持基本节奏。")
        if adaptability > .7:
            follow = follow[:-1] + "，遇到变化时切换方案较快。"
        elif persistence > .7:
            follow = follow[:-1] + "，认定目标后会持续投入。"
        cards.append({"key": "execution", "title": "行动与执行", "summary": execution + follow,
                      "drivers": ["结构偏好", "自律性", "适应性", "坚持度"]})
    if include(("social_warmth", "empathy", "romantic_orientation")):
        closeness = ("会主动经营重要关系，并通过行动表达投入" if relationship > .7 else
                     "重视关系，同时也会保留清晰的个人边界" if relationship < .4 else
                     "会根据关系的重要程度决定投入深度")
        care = ("，也很容易捕捉到他人的情绪和处境。" if empathy > .7 else
                "，更习惯通过解决问题或明确边界来表达关心。" if empathy < .4 else
                "，能够在理解感受与处理问题之间切换。")
        cards.append({"key": "relationship", "title": "关系方式", "summary": closeness + care,
                      "drivers": ["社交温度", "共情能力", "关系投入"]})
    return cards


def _core_tensions(traits: dict[str, dict], visible_keys: set[str], source_complete: bool) -> list[dict[str, str]]:
    def value(key: str) -> float:
        return _bounded_value(traits.get(key, {}))

    def eligible(keys: tuple[str, ...]) -> bool:
        return source_complete or set(keys).issubset(visible_keys)

    candidates: list[tuple[float, str, str]] = []
    if eligible(("social_warmth", "persistence")) and value("social_warmth") >= .7 and value("persistence") <= .5:
        candidates.append((value("social_warmth") - value("persistence"), "快速投入与持续兑现",
                           "愿意迅速回应关系与他人需要，但长期承诺更需要明确节奏和边界来支撑。"))
    if eligible(("creativity", "discipline")) and value("creativity") >= .6 and value("discipline") <= .5:
        candidates.append((value("creativity") - value("discipline"), "构想速度与执行节奏",
                           "新想法出现得很快，重复执行和流程推进则更依赖兴趣、节点或外部约束。"))
    if eligible(("openness", "structure_pref")) and value("openness") >= .7 and value("structure_pref") <= .4:
        candidates.append((value("openness") - value("structure_pref"), "探索自由与稳定结构",
                           "对新可能性反应很快，但固定流程容易带来束缚感；清晰目标加弹性路径更能发挥优势。"))
    if eligible(("empathy", "impulsivity")) and value("empathy") >= .75 and value("impulsivity") <= .3:
        candidates.append((value("empathy") - value("impulsivity"), "照顾他人与表达自己",
                           "很容易感知他人的情绪，却可能把自己的即时感受压后处理，需要为自身需求保留位置。"))
    if eligible(("discipline", "confidence")) and value("discipline") >= .7 and value("confidence") <= .4:
        candidates.append((value("discipline") - value("confidence"), "高标准与自我确认",
                           "执行标准很高，但对自己的完成度往往比别人更严格，容易忽略已经做好的部分。"))
    if eligible(("confidence", "empathy")) and value("confidence") >= .8 and value("empathy") <= .5:
        candidates.append((value("confidence") - value("empathy"), "强判断与情绪同步",
                           "判断和推动能力很强；在高压沟通中，放慢半拍确认对方感受会让影响力更完整。"))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if candidates:
        return [{"title": title, "description": description} for _, title, description in candidates[:2]]

    ranked = sorted(
        ((key, _bounded_value(entry)) for key, entry in traits.items() if key in visible_keys),
        key=lambda pair: abs(pair[1] - .5), reverse=True,
    )
    if len(ranked) >= 2:
        first, second = ranked[:2]
        return [{
            "title": f"{TRAIT_NAMES[first[0]]}与{TRAIT_NAMES[second[0]]}",
            "description": f"{_dimension_tendency(first[0], first[1])}，同时{_dimension_tendency(second[0], second[1])}；两者会共同影响具体选择。",
        }]
    return []


def _scenario_matrix(profile: dict, visible_keys: set[str], source_complete: bool) -> list[dict[str, Any]]:
    baseline = profile.get("source_behavior", {})
    current = profile.get("behavior_style", {})
    groups: list[dict[str, Any]] = []
    for group_key in ("task_style", "relationship_style", "inner_state_style"):
        items = []
        scenario_keys = list((baseline.get(group_key) or current.get(group_key) or {}).keys())
        for scenario_key in scenario_keys:
            source_item = baseline.get(group_key, {}).get(scenario_key, {})
            current_item = current.get(group_key, {}).get(scenario_key, {})
            direct_refs = current_item.get("direct_evidence_refs", [])
            driver_keys = {
                ref.get("field") for ref in current_item.get("parameter_refs", []) if isinstance(ref, dict)
            }
            if not source_complete and not direct_refs and not (driver_keys & visible_keys):
                continue
            latest = (current_item.get("observations") or [{}])[-1]
            pattern = latest.get("summary") or source_item.get("feature") or current_item.get("feature")
            if not pattern:
                continue
            items.append({
                "key": scenario_key,
                "label": SCENARIO_NAMES.get(scenario_key, source_item.get("source_label") or scenario_key),
                "pattern": _clean_scenario_pattern(pattern, scenario_key),
            })
        if items:
            groups.append({"key": group_key, "label": SCENARIO_GROUP_NAMES[group_key], "items": items})
    return groups


def _interaction_tips(traits: dict[str, dict], visible_keys: set[str], source_complete: bool) -> list[str]:
    def value(key: str) -> float:
        return _bounded_value(traits.get(key, {}))

    tips = []
    if source_complete or "thinking_ratio" in visible_keys:
        tips.append("先给结论和关键逻辑，再展开讨论。" if value("thinking_ratio") > .65 else
                    "先确认感受和价值取向，再进入方案。" if value("thinking_ratio") < .35 else
                    "把结论、理由和对人的影响放在同一层沟通。")
    if source_complete or "structure_pref" in visible_keys:
        tips.append("提前说明步骤、时间和完成标准。" if value("structure_pref") > .65 else
                    "明确目标与边界，同时给执行路径留出空间。" if value("structure_pref") < .35 else
                    "给出基本框架，并允许根据现场情况调整。")
    if (source_complete or "empathy" in visible_keys) and value("empathy") > .7:
        tips.append("重要反馈先让这个人感到被理解，再讨论改进动作。")
    if (source_complete or "assertiveness" in visible_keys) and value("assertiveness") > .7:
        tips.append("沟通可以直接、具体，不必用过多铺垫弱化重点。")
    if (source_complete or "extroversion" in visible_keys) and value("extroversion") < .4:
        tips.append("给出独立思考时间，避免要求立即在多人场合表态。")
    elif (source_complete or "extroversion" in visible_keys) and value("extroversion") > .7:
        tips.append("允许通过讨论碰撞想法，边说边梳理往往更有效。")
    return list(dict.fromkeys(tips))[:4]


def _communication_style(profile: dict) -> list[dict[str, Any]]:
    source = profile.get("source_language") or profile.get("language_style", {})
    observed = [
        item for item in profile.get("language_style", {}).get("speaking_style", [])
        if item.get("origin") == "observed"
    ]
    baseline = source.get("speaking_style", []) if isinstance(source, dict) else []
    combined = [*observed, *baseline]
    result = []
    seen = set()
    for item in combined:
        label = _neutralize_public_phrase(item.get("label") or item.get("behavior"))
        behavior = _neutralize_public_phrase(item.get("behavior") or item.get("label"))
        if not label or label in seen or REFERENCE_TERMS.search(label + behavior):
            continue
        seen.add(label)
        result.append({
            "label": label,
            "description": behavior,
            "example": _neutralize_public_phrase(item.get("example")) or None,
        })
        if len(result) == 6:
            break
    return result


def _life_context(profile: dict) -> dict[str, Any]:
    groups = {
        "facts": {"label": "稳定事实", "items": []},
        "goals": {"label": "目标与承诺", "items": []},
        "experiences": {"label": "经历与事件", "items": []},
        "relationships": {"label": "重要关系", "items": []},
    }
    type_group = {
        "fact": "facts", "preference": "facts", "commitment": "goals",
        "event": "experiences", "relationship": "relationships",
    }
    for item in profile.get("runtime", {}).get("memories", []):
        group = type_group.get(item.get("type"), "facts")
        value = item.get("value") or item.get("summary") or item.get("predicate")
        if not value:
            continue
        groups[group]["items"].append({
            "label": _clean_text(item.get("key") or item.get("predicate") or "重要信息"),
            "content": _clean_text(value),
        })
    roles = _portrait_segments(profile, "suitable_roles")
    return {
        "groups": [group for group in groups.values() if group["items"]],
        "collaboration_roles": roles,
    }


def build_public_profile(
    profile: dict,
    evidence_by_path: dict[str, dict[str, Any]] | None = None,
    *,
    showcase_baseline: bool = False,
) -> dict:
    """Project the internal snapshot into a narrative, scientifically phrased portrait.

    The five bundled showcase people may use their user-supplied complete workbooks
    as a display baseline. Reference-system labels and raw source material remain
    private; ordinary people only expose dimensions supported by conversation or
    explicit correction.
    """
    evidence_by_path = evidence_by_path or {}
    traits = flattened_traits(profile)
    identity = profile.get("identity", {})
    source_complete = bool(
        showcase_baseline and identity.get("template_person_id") and profile.get("source_profile_document")
    )
    visible_keys: set[str] = set()
    dimensions: list[dict[str, Any]] = []
    for category_key, category in profile.get("core_traits", {}).items():
        items = []
        for trait_key, entry in category.items():
            path = f"core_traits.{category_key}.{trait_key}"
            grade = _evidence_grade(evidence_by_path.get(path, {}))
            if not source_complete and grade == "unverified":
                continue
            visible_keys.add(trait_key)
            value = _bounded_value(entry)
            low, high = DIMENSION_POLES[trait_key]
            items.append({
                "key": trait_key,
                "label": TRAIT_NAMES[trait_key],
                "position": round(value * 100, 1),
                "low_label": low,
                "high_label": high,
                "tendency": _dimension_tendency(trait_key, value),
                "description": _dimension_description(trait_key, value),
                "editable_path": path,
                "updated_at": entry.get("updated_at"),
            })
        if items:
            dimensions.append({"key": category_key, "label": CATEGORY_NAMES.get(category_key, category_key), "items": items})

    headline, tags = _headline_for_traits(traits, visible_keys)
    strengths = _portrait_segments(profile, "strengths")
    potential_costs = _portrait_segments(profile, "weaknesses")
    tensions = _core_tensions(traits, visible_keys, source_complete)
    summary_parts = []
    if strengths:
        summary_parts.append("突出优势集中在" + "、".join(strengths[:4]) + "。")
    if tensions:
        summary_parts.append(tensions[0]["description"])
    portrait_summary = "".join(summary_parts) or headline
    runtime = profile.get("runtime", {})

    return {
        "schema_version": PUBLIC_PROFILE_SCHEMA_VERSION,
        "display_mode": "narrative_portrait",
        "identity": {
            "display_name": identity.get("display_name"),
            "timezone": identity.get("timezone"),
        },
        "portrait": {
            "headline": headline,
            "summary": portrait_summary,
            "tags": tags,
            "strengths": strengths,
            "potential_costs": potential_costs,
        },
        "operating_model": _operating_model(traits, visible_keys, source_complete),
        "core_tensions": tensions,
        "scenario_matrix": _scenario_matrix(profile, visible_keys, source_complete),
        "interaction_guide": {
            "tips": _interaction_tips(traits, visible_keys, source_complete),
            "communication_style": _communication_style(profile),
            "preferences": deepcopy(runtime.get("interaction_preferences", {})),
            "current_state": deepcopy(runtime.get("current_state", {})),
        },
        "life_context": _life_context(profile),
        "dimension_details": dimensions,
        "meta": {
            "profile_version": profile.get("meta", {}).get("profile_version"),
            "updated_at": profile.get("meta", {}).get("updated_at"),
            "complete_baseline": source_complete,
        },
        "visibility": {
            "internal_reference_available": True,
            "hidden_from_default_view": [
                "birth_analysis", "digital_code_profile", "mbti_dimensions",
                "enneagram_profile", "source_profile_document", "source_portrait",
                "source_behavior", "source_language", "internal_confidence", "internal_weights",
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
