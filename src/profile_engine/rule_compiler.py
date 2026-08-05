import hashlib
import json
import re
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

ENNEAGRAM_DOCUMENT_COUNT = 8
INSTINCT_SUBTYPE_FIELDS = {
    "Attention Adjustment": "attention_adjustment",
    "Resource Focus": "resource_focus",
    "Relationship Adjustment": "relationship_adjustment",
    "Social Strategy": "social_strategy",
    "Blind Spot": "blind_spot",
    "Interaction Adjustment": "interaction_adjustment",
}
WING_FIELDS = {
    "Expression Adjustment": "expression",
    "Attention Adjustment": "attention",
    "Decision Adjustment": "decision",
    "Relationship Adjustment": "relationship",
    "Interaction Adjustment": "interaction",
}


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
        loader = UniqueKeyLoader(path.read_text(encoding="utf-8"))
        try:
            value = loader.get_single_data()
        finally:
            loader.dispose()
    except (OSError, yaml.YAMLError) as exc:
        raise RuleValidationError([f"{path.name}: YAML 无法解析: {exc}"]) from exc
    if not isinstance(value, dict):
        raise RuleValidationError([f"{path.name}: 顶层必须是对象"])
    return value


def _clean_markdown_block(value: str) -> list[str]:
    items: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line == "---" or line.startswith("```"):
            continue
        line = re.sub(r"^[>\-*+\s]+", "", line).strip()
        if not line or re.match(r"^(instinct_stack|core_type):", line):
            continue
        if line not in items:
            items.append(line)
    return items


def _parse_instinct_subtypes(document: str) -> dict[str, dict[str, Any]]:
    """Compile the 54 doc-05 subtype assets; raw prose never enters every prompt."""
    subtype_heading = re.compile(r"^# (SP/SX|SP/SO|SX/SP|SX/SO|SO/SP|SO/SX)｜([1-9])号\s*$", re.M)
    matches = list(subtype_heading.finditer(document))
    result: dict[str, dict[str, Any]] = {}
    for match in matches:
        next_heading = re.search(r"^# (?!#).+$", document[match.end():], re.M)
        end = match.end() + next_heading.start() if next_heading else len(document)
        block = document[match.end():end]
        sections = list(re.finditer(r"^## (.+?)\s*$", block, re.M))
        if not sections:
            continue
        entry: dict[str, Any] = {
            "instinct_stack": match.group(1),
            "core_type": int(match.group(2)),
            "name": sections[0].group(1).replace("\\", ""),
        }
        for index, section in enumerate(sections[1:], start=1):
            field = INSTINCT_SUBTYPE_FIELDS.get(section.group(1).replace("\\", ""))
            if not field:
                continue
            section_end = sections[index + 1].start() if index + 1 < len(sections) else len(block)
            entry[field] = _clean_markdown_block(block[section.end():section_end])
        result[f"{match.group(1)}|{match.group(2)}"] = entry
    return result


def _parse_wing_assets(document: str) -> dict[str, dict[str, Any]]:
    heading = re.compile(r"^# \d+\\\.\d+ ([1-9]w[1-9])｜(.+?)\s*$", re.M)
    result: dict[str, dict[str, Any]] = {}
    for match in heading.finditer(document):
        next_heading = re.search(r"^# (?!#).+$", document[match.end():], re.M)
        end = match.end() + next_heading.start() if next_heading else len(document)
        block = document[match.end():end]
        sections = list(re.finditer(r"^## (.+?)\s*$", block, re.M))
        entry: dict[str, Any] = {"name": match.group(2).replace("\\", "")}
        for index, section in enumerate(sections):
            field = WING_FIELDS.get(section.group(1).replace("\\", ""))
            if not field:
                continue
            section_end = sections[index + 1].start() if index + 1 < len(sections) else len(block)
            entry[field] = _clean_markdown_block(block[section.end():section_end])
        result[match.group(1)] = entry
    return result


def _load_enneagram_documents(
    source_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    document_dir = source_dir.parent / "飞书文档"
    manifest: dict[str, dict[str, Any]] = {}
    contents: dict[int, str] = {}
    for number in range(1, ENNEAGRAM_DOCUMENT_COUNT + 1):
        matches = sorted(document_dir.glob(f"文档{number:02d}｜*.md"))
        if len(matches) != 1:
            raise RuleValidationError([f"九型技术文档 {number:02d} 应唯一存在，实际找到 {len(matches)} 份"])
        path = matches[0]
        content = path.read_text(encoding="utf-8")
        contents[number] = content
        manifest[f"document_{number:02d}"] = {
            "filename": path.name,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
    core_types = set(re.findall(r"^# Type ([1-9])｜", contents[3], re.M))
    wing_assets = _parse_wing_assets(contents[4])
    instinct_subtypes = _parse_instinct_subtypes(contents[5])
    manifest["document_03"]["asset_count"] = len(core_types)
    manifest["document_04"]["asset_count"] = len(wing_assets)
    manifest["document_05"]["asset_count"] = len(instinct_subtypes)
    return manifest, wing_assets, instinct_subtypes


def trait_keys(schema: dict) -> list[str]:
    categories = schema["canonical_profile"]["core_traits"]["categories"]
    return [key for category in categories.values() for key in category["fields"]]


def scenario_keys(schema: dict) -> list[str]:
    groups = schema["canonical_profile"]["behavior_style"]["groups"]
    return [key for group in groups.values() for key in group["scenarios"]]


def validate_rule_references(schema: dict, dialogue: dict) -> list[str]:
    """Validate every dialogue-rule target against the canonical profile schema."""
    errors: list[str] = []
    try:
        traits = trait_keys(schema)
        scenarios = scenario_keys(schema)
        canonical_profile = schema["canonical_profile"]
        language_sections = set(canonical_profile["language_style"]["groups"])
        mbti_dimensions = set(canonical_profile["mbti_dimensions"]["fields"]) - {"type_label"}
        identity_fields = set(canonical_profile["identity"]["fields"])
    except (KeyError, TypeError, AttributeError):
        return ["画像结构不完整，无法校验对话规则字段引用"]

    mapping = dialogue.get("trait_mapping_rules", {})
    if not isinstance(mapping, dict):
        return ["trait_mapping_rules 必须是对象"]
    unknown_mappings = sorted(set(mapping) - set(traits))
    if unknown_mappings:
        errors.append(f"对话规则含未知维度: {unknown_mappings}")
    missing_mappings = sorted(set(traits) - set(mapping))
    if missing_mappings:
        errors.append(f"对话规则未覆盖维度: {missing_mappings}")

    for trait, spec in mapping.items():
        if not isinstance(spec, dict):
            errors.append(f"对话维度 {trait} 的规则必须是对象")
            continue
        affected = spec.get("affected_source_fields", {})
        unknown_scenarios = sorted(set(affected.get("behavior_scenarios", [])) - set(scenarios))
        unknown_language = sorted(set(affected.get("language_sections", [])) - language_sections)
        mbti_dimension = affected.get("mbti_dimension")
        if unknown_scenarios:
            errors.append(f"对话维度 {trait} 引用了未知行为场景: {unknown_scenarios}")
        if unknown_language:
            errors.append(f"对话维度 {trait} 引用了未知语言板块: {unknown_language}")
        if mbti_dimension and mbti_dimension not in mbti_dimensions:
            errors.append(f"对话维度 {trait} 引用了未知 MBTI 维度: {mbti_dimension}")

    runtime_schema = schema.get("runtime_extensions", {})
    preference_fields = set(runtime_schema.get("interaction_preferences", {}).get("fields", {}))
    state_fields = set(runtime_schema.get("current_state", {}).get("fields", {}))
    runtime_rules = dialogue.get("runtime_state_and_memory", {})
    predicate_targets: dict[str, str] = {}
    for predicate, spec in runtime_rules.get("interaction_preferences", {}).items():
        target = spec.get("target") if isinstance(spec, dict) else None
        if target not in preference_fields:
            errors.append(f"交互偏好谓词 {predicate} 引用了未知字段: {target}")
        if predicate in predicate_targets:
            errors.append(f"运行时谓词 {predicate} 被重复路由")
        predicate_targets[predicate] = f"runtime.interaction_preferences.{target}"
    for state_key, spec in runtime_rules.get("current_state", {}).items():
        if not isinstance(spec, dict) or "predicates" not in spec:
            continue
        if state_key not in state_fields:
            errors.append(f"短期状态规则引用了未知字段: {state_key}")
        if not isinstance(spec.get("ttl_hours"), int) or spec.get("ttl_hours", 0) <= 0:
            errors.append(f"短期状态 {state_key} 的 ttl_hours 必须是正整数")
        if not isinstance(spec.get("value"), (int, float)) or not 0 <= spec.get("value", -1) <= 1:
            errors.append(f"短期状态 {state_key} 的 value 必须在 0..1")
        for predicate in spec.get("predicates", []):
            if predicate in predicate_targets:
                errors.append(f"运行时谓词 {predicate} 被重复路由")
            predicate_targets[predicate] = f"runtime.current_state.{state_key}"

    valid_operator_targets = {
        *(f"identity.{key}" for key in identity_fields),
        *(f"runtime.interaction_preferences.{key}" for key in preference_fields),
        *(f"runtime.current_state.{key}" for key in state_fields),
        "runtime.memories",
        *(f"language_style.{key}" for key in language_sections),
        "core_traits.*.*",
        "behavior_style.*.*",
        "mbti_dimensions.*",
        "language_style.*",
        "portrait.*",
    }
    for operator, spec in dialogue.get("update_operators", {}).items():
        targets = spec.get("targets", []) if isinstance(spec, dict) else []
        if not isinstance(targets, list) or not targets:
            errors.append(f"更新操作 {operator} 必须声明非空 targets 数组")
            continue
        unknown_targets = sorted(set(targets) - valid_operator_targets)
        if unknown_targets:
            errors.append(f"更新操作 {operator} 引用了未知画像字段: {unknown_targets}")

    candidate_rules = dialogue.get("model_candidate_validation", {})
    minimum_confidence = candidate_rules.get("minimum_confidence")
    if not isinstance(minimum_confidence, (int, float)) or not 0 <= minimum_confidence <= 1:
        errors.append("模型候选 minimum_confidence 必须在 0..1")
    eligible_domains = set(candidate_rules.get("trait_eligible_domains", []))
    forbidden_domains = set(candidate_rules.get("forbidden_trait_domains", []))
    overlap = sorted(eligible_domains & forbidden_domains)
    if overlap:
        errors.append(f"模型候选语义域同时允许又禁止: {overlap}")
    evidence_types = dialogue.get("evidence_types", {})
    required_scopes = {"explicit_self_report", "repeated_behavior", "single_behavior_inference"}
    missing_scopes = sorted(required_scopes - set(evidence_types))
    if missing_scopes:
        errors.append(f"缺少长期特质证据类型: {missing_scopes}")
    return errors


def compile_rule_pack(source_dir: Path) -> CompiledRulePack:
    missing = [name for name in RULE_FILES if not (source_dir / name).exists()]
    if missing:
        raise RuleValidationError([f"缺少规则文件: {name}" for name in missing])

    schema, cold, dialogue, enneagram = (_load_yaml(source_dir / name) for name in RULE_FILES)
    document_manifest, wing_document_assets, instinct_subtypes = _load_enneagram_documents(source_dir)
    enneagram["source_document_manifest"] = document_manifest
    enneagram["wing_document_assets"] = wing_document_assets
    enneagram["instinct_subtypes"] = instinct_subtypes
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
    if document_manifest["document_03"].get("asset_count") != 9:
        errors.append("文档03必须完整包含9种主型资产")
    expected_wings = {
        "1w9", "1w2", "2w1", "2w3", "3w2", "3w4", "4w3", "4w5", "5w4",
        "5w6", "6w5", "6w7", "7w6", "7w8", "8w7", "8w9", "9w8", "9w1",
    }
    if set(wings) != expected_wings:
        errors.append(f"九型侧翼参数覆盖不完整，缺少: {sorted(expected_wings - set(wings))}")
    if set(wing_document_assets) != expected_wings:
        errors.append(f"文档04的18侧翼资产覆盖不完整，缺少: {sorted(expected_wings - set(wing_document_assets))}")
    for wing_id, asset in wing_document_assets.items():
        missing_fields = sorted(set(WING_FIELDS.values()) - set(asset))
        if missing_fields:
            errors.append(f"文档04条目 {wing_id} 缺少字段: {missing_fields}")
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
    resolved_combination_count = len(instinct_subtypes)
    if resolved_combination_count != 54:
        errors.append(f"文档05的主型×本能叠层应解析为54种组合，实际为{resolved_combination_count}")
    expected_subtypes = {
        f"{stack}|{core_type}"
        for stack in expected_stacks
        for core_type in range(1, 10)
    }
    if set(instinct_subtypes) != expected_subtypes:
        errors.append(f"文档05的54组资产覆盖不完整，缺少: {sorted(expected_subtypes - set(instinct_subtypes))}")
    for subtype_id, subtype in instinct_subtypes.items():
        missing_fields = sorted(set(INSTINCT_SUBTYPE_FIELDS.values()) - set(subtype))
        if missing_fields:
            errors.append(f"文档05条目 {subtype_id} 缺少字段: {missing_fields}")

    for signal_id, signal in cold.get("semantic_signal_extraction", {}).get("generalized_signal_dictionary", {}).items():
        for target, direction in signal.get("effects", {}).items():
            if target not in traits:
                errors.append(f"冷启动信号 {signal_id} 引用了未知维度 {target}")
            if direction not in (-1, 0, 1):
                errors.append(f"冷启动信号 {signal_id}.{target} 方向越界")

    errors.extend(validate_rule_references(schema, dialogue))

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
        "enneagram_wing_document_asset_count": len(wing_document_assets),
        "enneagram_instinct_stack_count": len(instinct_stacks),
        "enneagram_resolved_combination_count": resolved_combination_count,
        "enneagram_source_document_count": len(document_manifest),
        "enneagram_instinct_subtype_count": len(instinct_subtypes),
        "enneagram_scene_count": len(enneagram.get("scene_adaptation", {})),
        "source_rule_bank": rule_bank_meta,
        "warnings": [cold.get("status"), dialogue.get("status"), enneagram.get("status")],
    }
    version = (
        f"{schema['schema_version']}+{cold['rule_system_version']}+"
        f"{dialogue['rule_system_version']}+enneagram-{enneagram['rule_system_version']}"
    )
    return CompiledRulePack(version=version, sha256=digest, canonical=canonical, report=report)
