from pathlib import Path

from profile_engine.rule_compiler import compile_rule_pack, scenario_keys, trait_keys
from profile_engine.rule_bank import fragments_for_code


def test_rule_pack_has_required_coverage():
    source = Path(__file__).parents[2] / "陪伴机器人画像引擎开发包_v0.2_AI可读版"
    pack = compile_rule_pack(source)
    schema = pack.canonical["schema"]
    assert pack.report["valid"] is True
    assert len(trait_keys(schema)) == 17
    assert len(scenario_keys(schema)) == 18
    assert pack.report["typical_utterance_context_count"] == 9
    assert pack.report["portrait_field_count"] == 5
    assert pack.report["source_rule_bank"]["code_count"] == 1458
    assert pack.report["source_rule_bank"]["fragment_count"] == 123234


def test_source_rule_bank_is_cleaned_and_indexed():
    workbook = Path(__file__).parents[2] / "数字学画像2.xlsx"
    fragments = fragments_for_code(str(workbook), "6318")
    assert {item.domain for item in fragments} == {"personality", "behavior", "work", "relationship"}
    assert all(item.text.strip() and item.text.strip() != "42" and item.normalized_weight > 0 for item in fragments)
