from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


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
    wing_spec = deepcopy(rules["wings"].get(f"{core_type}w{wing}", {})) if wing is not None else {}
    source = identity.get("source", "user_supplied")
    source_confidence = rules.get("identity_schema", {}).get("accepted_sources", {}).get(source, 0.8)
    confidence = min(float(identity.get("confidence", source_confidence)), float(source_confidence))
    layer_cap = rules.get("maintenance", {}).get("confidence_policy", {}).get("derived_layers_cap", 0.85)
    derived_confidence = round(min(confidence, float(layer_cap)), 4)
    strategy = deepcopy(core["interaction_strategy"])
    strategy["wing_adjustment"] = {
        "expression": wing_spec.get("expression", []),
        "decision": wing_spec.get("decision"),
        "relationship": wing_spec.get("relationship"),
        "interaction": wing_spec.get("interaction"),
    } if wing_spec else {}
    strategy["instinct_adjustment"] = {
        "attention_focus": stack["attention_focus"],
        "relationship_style": stack["relationship_style"],
        "interaction": stack["interaction_adjustment"],
        "trust_path": stack["trust_path"],
        "blind_spot": stack["blind_spot"],
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
                "wing_decision_adjustment": wing_spec.get("decision"),
                "confidence": derived_confidence,
            },
            "expression": {
                **core["expression"],
                "wing_expression_adjustment": wing_spec.get("expression", []),
                "instinct_relationship_adjustment": stack["relationship_style"],
                "confidence": derived_confidence,
            },
            "state": {**core["state"], "confidence": derived_confidence},
        },
        "interaction_strategy": strategy,
        "provenance": [
            f"enneagram.core_types.{core_type}",
            *([f"enneagram.wings.{core_type}w{wing}"] if wing is not None else []),
            f"enneagram.instinct_stacks.{stack_id}",
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "maintenance_note": "九型身份来自明确输入；动机、状态和策略均为派生解释，不作为独立事实。",
    }


def _match_scene(topic: str | None, rules: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    if not topic:
        return None, None
    normalized = topic.strip().lower()
    for key, spec in rules.get("scene_adaptation", {}).items():
        if any(str(item).lower() in normalized or normalized in str(item).lower() for item in spec.get("topics", [])):
            return key, deepcopy(spec)
    return None, None


def resolve_interaction_strategy(
    profile: dict[str, Any],
    rules: dict[str, Any],
    topic: str | None = None,
) -> dict[str, Any] | None:
    enneagram_profile = profile.get("enneagram_profile") or empty_enneagram_profile()
    if enneagram_profile.get("status") != "confirmed":
        return None
    strategy = deepcopy(enneagram_profile["interaction_strategy"])
    scene_key, scene = _match_scene(topic, rules)
    states = profile.get("runtime", {}).get("current_state", {})
    hints = deepcopy(strategy.get("hints", {}))
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
    strategy["scene"] = scene_key
    strategy["topic"] = topic
    return strategy
