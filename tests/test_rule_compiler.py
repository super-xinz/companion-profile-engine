from copy import deepcopy
from pathlib import Path

from profile_engine.rule_compiler import (
    compile_rule_pack,
    scenario_keys,
    trait_keys,
    validate_rule_references,
)
from profile_engine.rule_bank import fragments_for_code


def test_rule_pack_has_required_coverage():
    source = Path(__file__).parents[1] / "rules"
    pack = compile_rule_pack(source)
    schema = pack.canonical["schema"]
    assert pack.report["valid"] is True
    assert len(trait_keys(schema)) == 17
    assert len(scenario_keys(schema)) == 18
    assert pack.report["typical_utterance_context_count"] == 9
    assert pack.report["portrait_field_count"] == 5
    assert pack.report["golden_case_count"] == 5
    assert pack.report["enneagram_resolved_combination_count"] == 54
    assert pack.report["source_rule_bank"]["code_count"] == 1458
    assert pack.report["source_rule_bank"]["fragment_count"] == 123234


def test_source_rule_bank_is_cleaned_and_indexed():
    workbook = Path(__file__).parents[1] / "数字学画像2.xlsx"
    fragments = fragments_for_code(str(workbook), "6318")
    assert {item.domain for item in fragments} == {"personality", "behavior", "work", "relationship"}
    assert all(item.text.strip() and item.text.strip() != "42" and item.normalized_weight > 0 for item in fragments)


def test_every_dialogue_rule_target_resolves_to_a_real_profile_field():
    source = Path(__file__).parents[1] / "rules"
    pack = compile_rule_pack(source)
    assert validate_rule_references(pack.canonical["schema"], pack.canonical["dialogue"]) == []


def test_dangling_or_conflicting_rule_targets_are_rejected():
    source = Path(__file__).parents[1] / "rules"
    pack = compile_rule_pack(source)
    schema = deepcopy(pack.canonical["schema"])
    dialogue = deepcopy(pack.canonical["dialogue"])
    dialogue["trait_mapping_rules"]["risk_tolerance"]["affected_source_fields"][
        "behavior_scenarios"
    ].append("not_a_real_scenario")
    dialogue["runtime_state_and_memory"]["interaction_preferences"][
        "prefers_short_responses"
    ]["target"] = "not_a_real_preference"
    dialogue["update_operators"]["ADD_TRAIT_EVIDENCE"]["targets"] = ["core_traits.ghost.field"]
    dialogue["model_candidate_validation"]["forbidden_trait_domains"].append("decision")

    errors = validate_rule_references(schema, dialogue)
    assert any("not_a_real_scenario" in error for error in errors)
    assert any("not_a_real_preference" in error for error in errors)
    assert any("core_traits.ghost.field" in error for error in errors)
    assert any("同时允许又禁止" in error for error in errors)
