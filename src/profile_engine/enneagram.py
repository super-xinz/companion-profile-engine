from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


def _trait(profile: dict[str, Any], key: str) -> dict[str, float]:
    for category in profile.get("core_traits", {}).values():
        if key in category:
            value = category[key]
            return {
                "value": float(value.get("value", 0.5)),
                "confidence": float(value.get("confidence", 0.0)),
            }
    return {"value": 0.5, "confidence": 0.0}


def _signal(profile: dict[str, Any], *traits: str, invert: bool = False) -> dict[str, Any]:
    values = [_trait(profile, key) for key in traits]
    score = sum(item["value"] for item in values) / len(values)
    if invert:
        score = 1 - score
    confidence = sum(item["confidence"] for item in values) / len(values)
    return {
        "value": round(score, 4),
        "confidence": round(confidence, 4),
        "source_traits": list(traits),
    }


def build_portrait_parameter_input(profile: dict[str, Any]) -> dict[str, Any]:
    """Document-02 bridge: map portrait traits to parameters, never to a type."""
    return {
        "identity": {
            "status": profile.get("enneagram_profile", {}).get("status", "unassigned"),
            "core_type": profile.get("enneagram_profile", {}).get("identity", {}).get("core_type"),
            "mapping_policy": "explicit_identity_only",
        },
        "motivation_signal": {
            "autonomy_need": _signal(profile, "assertiveness", "confidence"),
            "achievement_need": _signal(profile, "persistence", "discipline", "structure_pref"),
            "security_need": _signal(profile, "risk_tolerance", "confidence", invert=True),
            "connection_need": _signal(profile, "social_warmth", "empathy", "extroversion"),
            "identity_need": _signal(profile, "openness", "creativity", "depth_of_thought"),
        },
        "attention_signal": {
            "novelty_orientation": _signal(profile, "openness", "creativity", "adaptability"),
            "stability_orientation": _signal(profile, "structure_pref", "discipline", "persistence"),
            "abstract_orientation": _signal(profile, "openness", "depth_of_thought", "creativity"),
            "detail_orientation": _signal(profile, "structure_pref", "discipline", "thinking_ratio"),
            "social_orientation": _signal(profile, "extroversion", "social_warmth", "empathy"),
        },
        "expression_signal": {
            "communication_directness": _signal(profile, "assertiveness", "thinking_ratio"),
            "emotional_expression": _signal(profile, "empathy", "social_warmth", "extroversion"),
            "logic_orientation": _signal(profile, "thinking_ratio"),
            "warmth_level": _signal(profile, "social_warmth", "empathy"),
        },
        "state_signal": {
            "resilience": _signal(profile, "confidence", "optimism", "adaptability", "persistence"),
            "pressure_sensitivity": _signal(profile, "confidence", "optimism", "adaptability", invert=True),
            "recovery_capacity": _signal(profile, "optimism", "adaptability", "persistence"),
        },
        "provenance": "document_02_multi_parameter_bridge",
        "note": "这些是画像信号，不用于自动确认九型身份。",
    }


def empty_enneagram_profile() -> dict[str, Any]:
    return {
        "status": "unassigned",
        "identity": {
            "code": None,
            "core_type": None,
            "core_type_name": None,
            "wing": None,
            "primary_instinct": None,
            "secondary_instinct": None,
            "instinct_stack": None,
        },
        "confidence": 0.0,
        "source": None,
        "layers": {
            "motivation": {},
            "attention": {},
            "expression": {},
            "state": {},
        },
        "interaction_strategy": {},
        "provenance": [],
        "updated_at": None,
        "maintenance_note": "九型身份不会从MBTI、生日、单一行为或普通对话自动推断。",
    }


def identity_code(identity: dict[str, Any]) -> str:
    core_type = int(identity["core_type"])
    wing = identity.get("wing")
    type_code = f"{core_type}w{int(wing)}" if wing is not None else str(core_type)
    return f"{identity['primary_instinct']}/{identity['secondary_instinct']}｜{type_code}"


def validate_identity(identity: dict[str, Any], rules: dict[str, Any]) -> None:
    core_type = int(identity["core_type"])
    wing = identity.get("wing")
    primary = identity["primary_instinct"]
    secondary = identity["secondary_instinct"]
    if str(core_type) not in rules.get("core_types", {}):
        raise ValueError("九型主型必须为 1-9")
    if primary == secondary:
        raise ValueError("第一本能和第二本能不能相同")
    stack_id = f"{primary}/{secondary}"
    if stack_id not in rules.get("instinct_stacks", {}):
        raise ValueError("本能叠层必须是 SP、SX、SO 中两个不同本能的有序组合")
    if wing is not None:
        wing_id = f"{core_type}w{int(wing)}"
        if wing_id not in rules.get("wings", {}):
            raise ValueError(f"{wing_id} 不是有效的相邻侧翼")


def build_enneagram_profile(identity: dict[str, Any] | None, rules: dict[str, Any]) -> dict[str, Any]:
    if not identity:
        return empty_enneagram_profile()
    validate_identity(identity, rules)
    core_type = int(identity["core_type"])
    wing = int(identity["wing"]) if identity.get("wing") is not None else None
    stack_id = f"{identity['primary_instinct']}/{identity['secondary_instinct']}"
    core = deepcopy(rules["core_types"][str(core_type)])
    stack = deepcopy(rules["instinct_stacks"][stack_id])
    subtype_id = f"{stack_id}|{core_type}"
    subtype = deepcopy(rules.get("instinct_subtypes", {}).get(subtype_id, {}))
    wing_spec = deepcopy(rules["wings"].get(f"{core_type}w{wing}", {})) if wing is not None else {}
    wing_asset = deepcopy(rules.get("wing_document_assets", {}).get(f"{core_type}w{wing}", {})) if wing is not None else {}
    source = identity.get("source", "user_supplied")
    source_confidence = rules.get("identity_schema", {}).get("accepted_sources", {}).get(source, 0.8)
    confidence = min(float(identity.get("confidence", source_confidence)), float(source_confidence))
    layer_cap = rules.get("maintenance", {}).get("confidence_policy", {}).get("derived_layers_cap", 0.85)
    derived_confidence = round(min(confidence, float(layer_cap)), 4)
    strategy = deepcopy(core["interaction_strategy"])
    strategy["wing_adjustment"] = {
        "expression": wing_spec.get("expression", []),
        "attention": wing_asset.get("attention", []),
        "decision": wing_spec.get("decision"),
        "relationship": wing_spec.get("relationship"),
        "interaction": wing_spec.get("interaction"),
        "document_adjustments": wing_asset,
    } if wing_spec else {}
    strategy["instinct_adjustment"] = {
        "attention_focus": stack["attention_focus"],
        "relationship_style": stack["relationship_style"],
        "interaction": stack["interaction_adjustment"],
        "trust_path": stack["trust_path"],
        "blind_spot": stack["blind_spot"],
    }
    strategy["instinct_subtype_adjustment"] = subtype
    strategy["parameter_fusion"] = {
        "mode": "nonlinear_precedence_merge",
        "weights": {
            key: float(rules.get("weights", {}).get(key, 0))
            for key in ("core_type", "primary_instinct", "secondary_instinct", "wing", "dynamic_state")
        },
        "static_profile_weight": 0.90,
        "dynamic_state_weight": 0.10,
        "conflict_precedence": ["core_type", "primary_instinct", "wing", "secondary_instinct"],
        "resolution_rule": "保留核心动机，以第一本能确定资源优先级、侧翼修正表达、第二本能提供辅助策略。",
    }
    strategy["confidence"] = derived_confidence
    return {
        "status": "confirmed",
        "identity": {
            "code": identity_code(identity),
            "core_type": core_type,
            "core_type_name": core["name"],
            "wing": wing,
            "primary_instinct": identity["primary_instinct"],
            "secondary_instinct": identity["secondary_instinct"],
            "instinct_stack": stack_id,
        },
        "confidence": round(confidence, 4),
        "source": source,
        "layers": {
            "motivation": {**core["motivation"], "confidence": derived_confidence},
            "attention": {
                **core["attention"],
                "instinct_focus": stack["attention_focus"],
                "instinct_subtype_adjustment": subtype.get("attention_adjustment", []),
                "resource_focus": subtype.get("resource_focus", []),
                "wing_decision_adjustment": wing_spec.get("decision"),
                "wing_attention_adjustment": wing_asset.get("attention", []),
                "confidence": derived_confidence,
            },
            "expression": {
                **core["expression"],
                "wing_expression_adjustment": wing_spec.get("expression", []),
                "wing_document_expression_adjustment": wing_asset.get("expression", []),
                "instinct_relationship_adjustment": stack["relationship_style"],
                "instinct_subtype_relationship": subtype.get("relationship_adjustment", []),
                "instinct_subtype_social_strategy": subtype.get("social_strategy", []),
                "confidence": derived_confidence,
            },
            "state": {
                **core["state"],
                "instinct_blind_spot": subtype.get("blind_spot", []),
                "confidence": derived_confidence,
            },
        },
        "interaction_strategy": strategy,
        "provenance": [
            f"enneagram.core_types.{core_type}",
            *([f"enneagram.wings.{core_type}w{wing}"] if wing is not None else []),
            f"enneagram.instinct_stacks.{stack_id}",
            f"enneagram.instinct_subtypes.{subtype_id}",
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "maintenance_note": "九型身份来自明确输入；动机、状态和策略均为派生解释，不作为独立事实。",
    }


def _match_scene(context_text: str | None, rules: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    if not context_text:
        return None, None
    normalized = context_text.strip().lower()
    for key, spec in rules.get("scene_adaptation", {}).items():
        if any(str(item).lower() in normalized for item in spec.get("topics", [])):
            return key, deepcopy(spec)
    return None, None


def _scene_context(
    topic: str | None,
    current_message: str | None,
    semantic_frames: list[dict[str, Any]] | None,
    reply_guidance: dict[str, Any] | None,
) -> str:
    parts = [topic or "", current_message or ""]
    for frame in semantic_frames or []:
        parts.extend(str(frame.get(key, "")) for key in ("predicate", "object", "context", "semantic_domain"))
    if reply_guidance:
        parts.extend(str(reply_guidance.get(key, "")) for key in ("intent", "focus"))
    return " ".join(part for part in parts if part)


def _split_interaction_adjustment(lines: list[str]) -> tuple[list[str], list[str]]:
    recommended: list[str] = []
    avoid: list[str] = []
    target = recommended
    for line in lines:
        if line.startswith("推荐"):
            target = recommended
            continue
        if line.startswith("避免"):
            target = avoid
            continue
        if line.endswith("：") or line == "AI策略：":
            continue
        target.append(line.rstrip("；。"))
    return recommended, avoid


def _bridge_hints(parameter_input: dict[str, Any]) -> dict[str, Any]:
    expression = parameter_input["expression_signal"]
    attention = parameter_input["attention_signal"]
    hints: dict[str, Any] = {}
    directness = expression["communication_directness"]
    if directness["confidence"] >= 0.30 and directness["value"] >= 0.67:
        hints["answer_first"] = True
    detail = attention["detail_orientation"]
    if detail["confidence"] >= 0.30 and detail["value"] >= 0.67:
        hints["structure_level"] = "steps"
    return hints


def _behavior_directives(strategy: dict[str, Any], scene: dict[str, Any] | None, rules: dict[str, Any]) -> dict[str, Any]:
    policy = rules.get("output_adapters", {}).get("robot", {})
    hints = strategy.get("hints", {})
    return {
        "advisory_only": True,
        "safety_gate_required": bool(policy.get("safety_gate_required", True)),
        "voice": {
            "tone": "calm_warm" if hints.get("empathy_first") else "natural_warm",
            "pace": "slow" if hints.get("empathy_first") else "normal",
            "intensity": "low" if hints.get("empathy_first") else "medium",
        },
        "expression": "gentle_attentive" if hints.get("empathy_first") else "warm_neutral",
        "posture": "open_still" if scene else "open_natural",
        "device_actions": [],
    }


def _turn_plan(strategy: dict[str, Any], scene_key: str | None, scene: dict[str, Any] | None,
               states: dict[str, Any]) -> dict[str, Any]:
    stress = states.get("stress_level", {}).get("value", 0) >= 0.7
    if stress or scene_key == "emotional_support":
        active_modules = ["conflict", "trust", "communication"]
    elif scene_key in {"career_decision", "learning_growth", "entrepreneurship", "health_management"}:
        active_modules = ["motivation", "communication", "companionship"]
    else:
        active_modules = ["communication", "trust", "companionship"]
    subtype = strategy.get("instinct_subtype_adjustment", {})
    recommended, subtype_avoid = _split_interaction_adjustment(subtype.get("interaction_adjustment", []))
    return {
        "active_modules": active_modules,
        "communication": deepcopy(strategy.get("communication", {})),
        "motivation": deepcopy(strategy.get("motivation", {})),
        "conflict": deepcopy(strategy.get("conflict", {})),
        "trust": deepcopy(strategy.get("trust", {})),
        "companionship": deepcopy(strategy.get("companionship", {})),
        "subtype_guidance": recommended,
        "scene_goal": scene.get("priorities", []) if scene else [],
        "avoid": list(dict.fromkeys([*(scene.get("avoid", []) if scene else []), *subtype_avoid])),
        "rule": "场景决定本轮模块优先级；五类策略均保留，不作为固定话术。",
    }


def resolve_interaction_strategy(
    profile: dict[str, Any],
    rules: dict[str, Any],
    topic: str | None = None,
    current_message: str | None = None,
    semantic_frames: list[dict[str, Any]] | None = None,
    reply_guidance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enneagram_profile = profile.get("enneagram_profile") or empty_enneagram_profile()
    parameter_input = build_portrait_parameter_input(profile)
    strategy = deepcopy(enneagram_profile.get("interaction_strategy", {}))
    strategy["identity_status"] = enneagram_profile.get("status", "unassigned")
    strategy["profile_parameter_input"] = parameter_input
    context_text = _scene_context(topic, current_message, semantic_frames, reply_guidance)
    scene_key, scene = _match_scene(context_text, rules)
    states = profile.get("runtime", {}).get("current_state", {})
    hints = deepcopy(strategy.get("hints", {}))
    for key, value in _bridge_hints(parameter_input).items():
        hints.setdefault(key, value)
    if scene:
        strategy["scene_adaptation"] = {
            "scene": scene_key,
            "role": scene["role"],
            "priorities": scene["priorities"],
            "avoid": scene["avoid"],
        }
    if states.get("stress_level", {}).get("value", 0) >= 0.7:
        hints.update(empathy_first=True)
        strategy["dynamic_state_adjustment"] = ["先恢复安全感", "降低信息和行动压力", "避免幽默化处理"]
    if states.get("energy_level", {}).get("value", 1) <= 0.3:
        hints.update(structure_level="simple", max_sentences=3)
        strategy.setdefault("dynamic_state_adjustment", []).extend(["一次只给一个行动", "允许稍后继续"])
    scene_cap = rules.get("maintenance", {}).get("confidence_policy", {}).get("scene_strategy_cap", 0.75)
    strategy["confidence"] = round(min(float(strategy.get("confidence", 0)), float(scene_cap) if scene else 1.0), 4)
    strategy["hints"] = hints
    strategy["precedence"] = rules.get("weights", {}).get("precedence", [])
    strategy["turn_plan"] = _turn_plan(strategy, scene_key, scene, states)
    strategy["behavior_directives"] = _behavior_directives(strategy, scene, rules)
    strategy["strategy_sources"] = [
        "document_01_architecture",
        "document_02_profile_bridge",
        *(["document_03_core", "document_04_wing", "document_05_instinct_subtype", "document_06_fusion", "document_07_strategy"]
          if enneagram_profile.get("status") == "confirmed" else []),
        *(["document_08_scene_validation"] if scene else []),
    ]
    strategy["scene"] = scene_key
    strategy["topic"] = topic
    return strategy
