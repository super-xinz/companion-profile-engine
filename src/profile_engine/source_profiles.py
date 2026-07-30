from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


SOURCE_FILES = {
    "1988-08-09": "1988年8月9日_机器人性格设定.xlsx",
    "1989-10-15": "1989年10月15日_机器人性格设定.xlsx",
    "1998-12-06": "1998年12月6日_机器人性格设定.xlsx",
}

SOURCE_META = {
    "1988-08-09": {
        "bazi_text": "戊辰 庚申 丙申",
        "day_master": "丙火",
        "pattern_name": "偏财格",
        "strength_label": "身弱",
        "mbti": "ENFP",
        "relation_markers": {
            "combinations": 0, "self_punishments": 0, "other_punishments": 0,
            "clashes": 0, "harms": 0, "source_text": "无刑冲合害",
        },
    },
    "1989-10-15": {
        "bazi_text": "己巳 甲戌 戊申",
        "day_master": "戊土",
        "pattern_name": "七杀格",
        "strength_label": "身强",
        "mbti": "ENTP",
        "relation_markers": {
            "combinations": 2, "self_punishments": 0, "other_punishments": 1,
            "clashes": 0, "harms": 0, "source_text": "合2次 他刑1次",
        },
    },
    "1998-12-06": {
        "bazi_text": "戊寅 癸亥 丁亥",
        "day_master": "丁火",
        "pattern_name": "七杀格",
        "strength_label": "身弱",
        "mbti": "ISTJ",
        "relation_markers": {
            "combinations": 3, "self_punishments": 1, "other_punishments": 0,
            "clashes": 0, "harms": 0, "source_text": "合3次 自刑1次",
        },
    },
}


def _source_path(birth_date: str) -> Path | None:
    filename = SOURCE_FILES.get(birth_date)
    if not filename:
        return None
    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        project_root / "source_profiles" / filename,
        Path.cwd() / "source_profiles" / filename,
        Path.cwd() / filename,
        Path.cwd().parent / filename,
    ]
    return next((path for path in candidates if path.exists()), None)


def _rows(sheet) -> list[list[Any]]:
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    while rows and not any(value is not None for value in rows[-1]):
        rows.pop()
    width = max((len(row) for row in rows), default=0)
    while width and all((row[width - 1] if len(row) >= width else None) is None for row in rows):
        width -= 1
    return [row[:width] for row in rows]


def load_source_document(birth_date: str) -> dict | None:
    path = _source_path(birth_date)
    if not path:
        return None
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        return {
            "birth_date": birth_date,
            "source_file": path.name,
            "source_type": "user_supplied_complete_profile",
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "sheets": {name: _rows(workbook[name]) for name in workbook.sheetnames},
        }
    finally:
        workbook.close()


def apply_source_profile(profile: dict, birth_date: str) -> bool:
    """Overlay the exact user-supplied profile and retain every original workbook cell."""
    document = load_source_document(birth_date)
    if not document:
        return False

    overview = document["sheets"]["性格总览"]
    meta = SOURCE_META[birth_date]
    profile["birth_analysis"].update({
        "bazi_text": meta["bazi_text"],
        "day_master": meta["day_master"],
        "pattern_name": meta["pattern_name"],
        "strength_label": meta["strength_label"],
        "relation_markers": meta["relation_markers"],
    })

    mbti_keys = ("ei", "sn", "tf", "jp")
    for key, row in zip(mbti_keys, overview[5:9]):
        profile["mbti_dimensions"][key].update({
            "value": float(row[1]),
            "confidence": 0.45,
            "source_tendency": row[2],
            "source_interpretation": row[4],
        })
    profile["mbti_dimensions"]["type_label"] = meta["mbti"]

    trait_lookup = {
        key: value
        for category in profile["core_traits"].values()
        for key, value in category.items()
    }
    for row in overview[12:29]:
        match = re.match(r"^([a-z_]+)", str(row[1] or ""))
        if not match or match.group(1) not in trait_lookup:
            continue
        key = match.group(1)
        tendency = str(row[4] or "").replace("⭐", "").strip()
        interpretation = str(row[5] or "").strip()
        trait_lookup[key].update({
            "value": float(row[2]),
            "confidence": 0.45,
            "tendency_label": tendency or trait_lookup[key]["tendency_label"],
            "interpretation": interpretation or tendency,
            "origin": "user_supplied_complete_profile",
        })

    portrait_rows = document["sheets"]["人物画像"][3:8]
    portrait_keys = ("essence", "strengths", "weaknesses", "core_tension", "suitable_roles")
    profile["source_portrait"] = {
        key: {
            "content": row[1],
            "confidence": 0.45,
            "origin": "user_supplied_complete_profile",
        }
        for key, row in zip(portrait_keys, portrait_rows)
    }
    profile["portrait"] = {
        key: {**value, "parameter_refs": []}
        for key, value in profile["source_portrait"].items()
    }
    profile["source_profile_document"] = document
    profile["meta"]["warnings"] = [
        warning for warning in profile["meta"].get("warnings", [])
        if "黄金样例" not in warning
    ]
    profile["meta"]["warnings"].append("已完整导入用户提供的原始画像工作簿；后续对话证据将持续校准动态画像。")
    return True
