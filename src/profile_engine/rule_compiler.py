import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


RULE_FILES = (
    "01_profile_schema.yaml",
    "02_cold_start_rule_system.yaml",
    "03_dialogue_profile_maintenance.yaml",
    "04_enneagram_interaction_model.yaml",
)


class RuleValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark,
                f"duplicate key: {key}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


@dataclass(frozen=True)
class CompiledRulePack:
    version: str
    sha256: str
    canonical: dict[str, Any]
    report: dict[str, Any]


def _load_yaml(path: Path) -> dict:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise RuleValidationError([f"{path.name}: YAML 无法解析: {exc}"]) from exc
    if not isinstance(value, dict):
        raise RuleValidationError([f"{path.name}: 顶层必须是对象"])
    return value


def trait_keys(schema: dict) -> list[str]:
    categories = schema["canonical_profile"]["core_traits"]["categories"]
    return [key for category in categories.values() for key in category["fields"]]


def scenario_keys(schema: dict) -> list[str]:
    groups = schema["canonical_profile"]["behavior_style"]["groups"]
    return [key for group in groups.values() for key in group["scenarios"]]


def compile_rule_pack(source_dir: Path) -> CompiledRulePack:
    missing = [name for name in RULE_FILES if not (source_dir / name).exists()]
    if missing:
        raise RuleValidationError([f"缺少规则文件: {name}" for name in missing])

    schema, cold, dialogue, enneagram = (_load_yaml(source_dir / name) for name in RULE_FILES)
    errors: list[str] = []
    traits = trait_keys(schema)
    scenarios = scenario_keys(schema)
    coverage = schema.get("coverage_manifest", {}).get("source_fields", {})
    golden_cases = cold.get("golden_cases", [])
    schema_source_files = set(schema.get("purpose", {}).get("source_files", []))
    golden_source_files = {case.get("target_profile_source") for case in golden_cases}
    if len(traits) != 17 or len(set(traits)) != 17:
        errors.append(f"核心维度应为17个，实际为{len(set(traits))}个")
    if len(scenarios) != 18 or len(set(scenarios)) != 18:
        errors.append(f"行为场景应为18个，实际为{len(set(scenarios))}个")
    if coverage.get("mbti_dimension_count") != 4:
        errors.append("MBTI 连续维度覆盖声明必须为4")
    if len(golden_cases) != 5:
        errors.append(f"完整画像模板应为5个，实际为{len(golden_cases)}个")
    if len({case.get("birth_date") for case in golden_cases}) != len(golden_cases):
        errors.append("完整画像模板生日不能重复")
    if schema_source_files != golden_source_files:
        errors.append("画像结构来源文件与冷启动完整画像模板不一致")
    source_profile_dir = source_dir.parent / "source_profiles"
    missing_source_profiles = sorted(
        filename for filename in schema_source_files
        if not (source_profile_dir / filename).exists()
    )
    if missing_source_profiles:
        errors.append(f"缺少完整画像资料: {missing_source_profiles}")

    target_schema = f"01_profile_schema.yaml@{schema.get('schema_version')}"
    for name, rules in (("cold", cold), ("dialogue", dialogue), ("enneagram", enneagram)):
        if rules.get("target_schema") != target_schema:
            errors.append(f"{name} target_schema 不兼容: {rules.get('target_schema')}")

    core_types = enneagram.get("core_types", {})
    wings = enneagram.get("wings", {})
    instinct_stacks = enneagram.get("instinct_stacks", {})
    if set(core_types) != {str(value) for value in range(1, 10)}:
        errors.append("九型主型参数必须完整覆盖 1-9")
    expected_wings = {
        "1w9", "1w2", "2w1", "2w3", "3w2", "3w4", "4w3", "4w5", "5w4",
        "5w6", "6w5", "6w7", "7w6", "7w8", "8w7", "8w9", "9w8", "9w1",
    }
    if set(wings) != expected_wings:
        errors.append(f"九型侧翼参数覆盖不完整，缺少: {sorted(expected_wings - set(wings))}")
    expected_stacks = {"SP/SX", "SP/SO", "SX/SP", "SX/SO", "SO/SP", "SO/SX"}
    if set(instinct_stacks) != expected_stacks:
        errors.append(f"本能叠层覆盖不完整，缺少: {sorted(expected_stacks - set(instinct_stacks))}")
    adjacency = enneagram.get("identity_schema", {}).get("wing", {}).get("adjacency", {})
    for wing_id, spec in wings.items():
        base, adjacent = int(spec.get("base_type", 0)), int(spec.get("adjacent_type", 0))
        allowed = adjacency.get(base, adjacency.get(str(base), []))
        if adjacent not in allowed or wing_id != f"{base}w{adjacent}":
            errors.append(f"侧翼 {wing_id} 不满足相邻类型规则")
    weights = enneagram.get("weights", {})
    weight_keys = ("core_type", "primary_instinct", "secondary_instinct", "wing", "dynamic_state")
    if abs(sum(float(weights.get(key, 0)) for key in weight_keys) - 1.0) > 1e-9:
        errors.append("九型静态与动态权重总和必须为 1")
    accepted_sources = enneagram.get("identity_schema", {}).get("accepted_sources", {})
    if set(accepted_sources) != {"user_supplied", "external_assessment", "expert_confirmed"}:
        errors.append("九型身份来源必须覆盖用户声明、外部测评和专家确认")
    resolved_combination_count = len(core_types) * len(instinct_stacks)
    if resolved_combination_count != 54:
        errors.append(f"九型主型×本能叠层应解析为54种组合，实际为{resolved_combination_count}")

    for signal_id, signal in cold.get("semantic_signal_extraction", {}).get("generalized_signal_dictionary", {}).items():
        for target, direction in signal.get("effects", {}).items():
            if target not in traits:
                errors.append(f"冷启动信号 {signal_id} 引用了未知维度 {target}")
            if direction not in (-1, 0, 1):
                errors.append(f"冷启动信号 {signal_id}.{target} 方向越界")

    mapping = dialogue.get("trait_mapping_rules", {})
    unknown_mappings = sorted(set(mapping) - set(traits))
    if unknown_mappings:
        errors.append(f"对话规则含未知维度: {unknown_mappings}")
    missing_mappings = sorted(set(traits) - set(mapping))
    if missing_mappings:
        errors.append(f"对话规则未覆盖维度: {missing_mappings}")

    dialogue_scenarios = set(dialogue.get("behavior_scenario_maintenance", {}).get("scenarios", {}))
    if dialogue_scenarios != set(scenarios):
        errors.append(f"对话场景覆盖不完整，缺少: {sorted(set(scenarios) - dialogue_scenarios)}")
    schema_contexts = {x["key"] for x in schema["canonical_profile"]["language_style"]["groups"]["typical_utterances"]["fixed_contexts"]}
    dialogue_contexts = set(dialogue.get("language_style_maintenance", {}).get("typical_utterances", {}).get("contexts", {}))
    if schema_contexts != dialogue_contexts:
        errors.append(f"典型话术语境覆盖不完整，缺少: {sorted(schema_contexts - dialogue_contexts)}")

    bounded_keys = {"base_reliability", "max_trait_delta", "max_preference_delta", "max_state_delta",
                    "no_op_threshold", "same_message_same_target_cap", "maximum_total_trait_change_per_turn"}
    def check_bounds(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if key in bounded_keys and isinstance(child, (int, float)) and not 0 <= child <= 1:
                    errors.append(f"数值越界 {child_path}={child}")
                check_bounds(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_bounds(child, f"{path}[{index}]")
    check_bounds(cold, "cold_start")
    check_bounds(dialogue, "dialogue")

    raw_weights = cold.get("source_rule_bank", {}).get("raw_weights", {})
    for domain, spec in raw_weights.items():
        values = spec.get("values", {})
        if sum(values.values()) != spec.get("total"):
            errors.append(f"{domain} 原始权重总和与 total 不一致")
        if any(v < 0 for v in values.values()):
            errors.append(f"{domain} 含负权重")

    rule_bank_meta = {"available": False}
    workbook = source_dir.parent / "数字学画像2.xlsx"
    if workbook.exists():
        from .rule_bank import load_rule_index, workbook_sha256
        index = load_rule_index(str(workbook.resolve()))
        rule_bank_meta = {"available": True, "filename": workbook.name, "sha256": workbook_sha256(workbook),
                          "code_count": len(index), "fragment_count": sum(len(items) for items in index.values())}
    serialized = json.dumps(
        {
            "schema": schema,
            "cold_start": cold,
            "dialogue": dialogue,
            "enneagram": enneagram,
            "source_rule_bank": rule_bank_meta,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if '"42"' in serialized:
        # 占位符可以存在于清洗声明中，但不能作为实际 cue 或生成内容。
        cues = json.dumps(cold.get("semantic_signal_extraction", {}), ensure_ascii=False)
        if '"42"' in cues:
            errors.append("占位符42进入了运行语义规则")
    if errors:
        raise RuleValidationError(errors)

    canonical = json.loads(serialized)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    report = {
        "valid": True,
        "schema_version": schema["schema_version"],
        "trait_count": len(traits),
        "scenario_count": len(scenarios),
        "typical_utterance_context_count": len(
            schema["canonical_profile"]["language_style"]["groups"]["typical_utterances"]["fixed_contexts"]
        ),
        "portrait_field_count": len(schema["canonical_profile"]["portrait"]["fields"]),
        "golden_case_count": len(golden_cases),
        "enneagram_core_type_count": len(core_types),
        "enneagram_wing_count": len(wings),
        "enneagram_instinct_stack_count": len(instinct_stacks),
        "enneagram_resolved_combination_count": resolved_combination_count,
        "enneagram_scene_count": len(enneagram.get("scene_adaptation", {})),
        "source_rule_bank": rule_bank_meta,
        "warnings": [cold.get("status"), dialogue.get("status"), enneagram.get("status")],
    }
    version = (
        f"{schema['schema_version']}+{cold['rule_system_version']}+"
        f"{dialogue['rule_system_version']}+enneagram-{enneagram['rule_system_version']}"
    )
    return CompiledRulePack(version=version, sha256=digest, canonical=canonical, report=report)
