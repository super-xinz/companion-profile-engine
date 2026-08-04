from __future__ import annotations

from datetime import date
from typing import Any

from .rule_bank import RuleFragment, load_domain_proportions


ALGORITHM_VERSION = "birth-groups-digital-root-v1"
DOMAIN_LABELS = {
    "personality": "性格画像",
    "behavior": "行为画像",
    "work": "做事工作画像",
    "relationship": "关系情感画像",
}
FIELD_KEYS = (
    "head_number", "constraint_number", "main_strengths", "main_weaknesses",
    "main_emotion", "inner_code", "subconscious", "outer_code",
    "value_combination", "talent_number", "missing_1", "missing_2",
    "missing_3", "missing_4", "missing_5", "missing_6", "missing_7",
    "career_position", "career_extension_1", "career_extension_2",
    "marriage_position", "marriage_extension_1", "marriage_extension_2",
    "seat_code", "external_personality", "internal_personality",
)


def _digital_root(value: int) -> int:
    # The supplied 1458-row bank uses only 1-9 in each position. Treat an all-zero
    # group (for example the final two digits of 2000) as 9 on that cyclic scale.
    return 1 + (value - 1) % 9


def calculate_digital_code(birth_date: str) -> tuple[str | None, list[str]]:
    parsed = date.fromisoformat(birth_date)
    if not 1900 <= parsed.year <= 2099:
        return None, ["数字密码规则库目前只覆盖 1900 至 2099 年出生日期。"]
    groups = (parsed.day, parsed.month, parsed.year // 100, parsed.year % 100)
    code = "".join(str(_digital_root(value)) for value in groups)
    return code, ["数字密码归约算法依据现有码表范围和已知样例实现，仍需专家最终确认。"]


def empty_digital_code_profile() -> dict[str, Any]:
    return {
        "status": "unassigned",
        "code": None,
        "algorithm_version": ALGORITHM_VERSION,
        "confidence": 0.0,
        "domains": {},
        "provenance": {},
        "maintenance_note": "数字密码画像是生日生成的低置信度解释层，不作为已确认人格事实。",
    }


def _summary(label: str, code: str, components: list[dict[str, Any]]) -> tuple[str, float]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(components, key=lambda value: (-value["weight"], value["source_column"])):
        normalized = " ".join(item["text"].split())
        if normalized in seen:
            continue
        seen.add(normalized)
        selected.append({**item, "text": normalized[:240]})
    coverage = round(sum(item["weight"] for item in selected), 4)
    tiers = {
        "主导": [],
        "支撑": [],
        "补充": [],
    }
    for item in selected:
        weight = item["weight"]
        content = f"{item['label']}（{round(weight * 100)}%）{item['text']}"
        if weight >= 0.08:
            tiers["主导"].append(content)
        elif weight >= 0.03:
            tiers["支撑"].append(content)
        else:
            tiers["补充"].append(content)
    details = []
    for tier_name in ("主导", "支撑", "补充"):
        if tiers[tier_name]:
            details.append(f"{tier_name}项：" + "；".join(tiers[tier_name]))
    return f"数字密码 {code} 的{label}综合画像：" + "；".join(details), coverage


def build_digital_code_profile(
    code: str | None,
    fragments: tuple[RuleFragment, ...],
    workbook_sha256: str | None = None,
) -> dict[str, Any]:
    if not code or not fragments:
        return empty_digital_code_profile()
    source_file = "数字学画像2.xlsx"
    source_path = None
    try:
        from .config import get_settings
        from pathlib import Path

        source_dir = get_settings().rule_source_dir
        if not source_dir.is_absolute():
            source_dir = (Path.cwd() / source_dir).resolve()
        source_path = str(source_dir.parent / source_file)
    except Exception:
        source_path = None
    proportions = load_domain_proportions(source_path) if source_path else {}
    domains: dict[str, Any] = {}
    for domain, label in DOMAIN_LABELS.items():
        components = []
        domain_proportions = proportions.get(domain, ())
        for fragment in fragments:
            if fragment.domain != domain:
                continue
            field_index = fragment.source_column // 2 - 1
            field_key = FIELD_KEYS[field_index] if 0 <= field_index < len(FIELD_KEYS) else fragment.source_field
            sheet_ratio = domain_proportions[field_index] if 0 <= field_index < len(domain_proportions) else fragment.normalized_weight
            components.append({
                "field": field_key,
                "label": fragment.source_field,
                "text": fragment.text,
                "weight": round(fragment.normalized_weight, 6),
                "sheet_ratio": round(float(sheet_ratio), 6),
                "source_column": fragment.source_column,
            })
        summary, coverage = _summary(label, code, components)
        domains[domain] = {
            "label": label,
            "summary": summary,
            "summary_coverage_weight": coverage,
            "components": sorted(components, key=lambda item: item["source_column"]),
            "proportion_source": {
                "sheet": "比例",
                "sheet_sha256": workbook_sha256,
                "source_file": source_file,
            },
        }
    return {
        "status": "derived",
        "code": code,
        "algorithm_version": ALGORITHM_VERSION,
        "confidence": 0.35,
        "domains": domains,
        "provenance": {
            "source_file": "数字学画像2.xlsx",
            "source_sha256": workbook_sha256,
            "source_row_key": code,
            "summary_generation": "deterministic_weighted_synthesis_v2",
            "proportion_sheet": "比例",
        },
        "maintenance_note": "四类画像保留全部加权成分；摘要按比例表做完整综合，用户事实和后续证据优先。",
    }


def aggregate_trait_priors(signals: list[dict], cold_rules: dict) -> dict[str, float]:
    aggregation = cold_rules.get("dimension_aggregation", {})
    lower, upper = aggregation.get("cold_start_default_clip", [0.15, 0.85])
    per_fragment_cap = float(aggregation.get("single_fragment_max_absolute_effect", 0.06))
    same_text_cap = float(aggregation.get("same_text_total_effect_cap", 0.10))
    grouped: dict[tuple[str, str], float] = {}
    for signal in signals:
        key = (signal["target"], signal["source_fragment"]["text_sha256"])
        effect = signal["direction"] * signal["strength"] * signal["normalized_weight"]
        effect = max(-per_fragment_cap, min(per_fragment_cap, effect))
        grouped[key] = max(-same_text_cap, min(same_text_cap, grouped.get(key, 0.0) + effect))
    totals: dict[str, float] = {}
    for (trait, _), effect in grouped.items():
        totals[trait] = totals.get(trait, 0.0) + effect
    return {trait: round(max(lower, min(upper, 0.5 + effect)), 4) for trait, effect in totals.items()}
