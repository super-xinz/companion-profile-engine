from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TemplatePerson:
    user_id: str
    display_name: str
    birth_date: str
    source_file: str
    mbti: str
    trait_values: dict[str, float]
    birth_analysis: dict[str, Any]
    numerology_code: str | None = None
    enneagram: dict[str, Any] | None = None


TEMPLATE_PEOPLE = (
    TemplatePerson(
        user_id="person-1988-08-09",
        display_name="1988年8月9日",
        birth_date="1988-08-09",
        source_file="1988年8月9日_机器人性格设定.xlsx",
        mbti="ENFP",
        numerology_code="9817",
        trait_values={
            "extroversion": 1.0, "social_warmth": 1.0, "assertiveness": .40, "impulsivity": .60,
            "openness": 1.0, "creativity": .52, "depth_of_thought": .50, "thinking_ratio": .31,
            "empathy": .70, "risk_tolerance": .84, "structure_pref": .18, "discipline": .60,
            "adaptability": .87, "persistence": .36, "confidence": .47, "optimism": .75,
            "romantic_orientation": 1.0,
        },
        birth_analysis={
            "bazi_text": "戊辰 庚申 丙申",
            "day_master": "丙火",
            "pattern_name": "偏财格",
            "strength_label": "身弱",
            "relation_markers": {
                "combinations": 0, "self_punishments": 0, "other_punishments": 0,
                "clashes": 0, "harms": 0, "source_text": "无刑冲合害",
            },
        },
    ),
    TemplatePerson(
        user_id="person-1989-10-15",
        display_name="1989年10月15日",
        birth_date="1989-10-15",
        source_file="1989年10月15日_机器人性格设定.xlsx",
        mbti="ENTP",
        numerology_code="6118",
        trait_values={
            "extroversion": .83, "social_warmth": .54, "assertiveness": .83, "impulsivity": .66,
            "openness": 1.0, "creativity": .66, "depth_of_thought": .57, "thinking_ratio": .51,
            "empathy": .51, "risk_tolerance": .66, "structure_pref": .10, "discipline": .39,
            "adaptability": .70, "persistence": .80, "confidence": .84, "optimism": .49,
            "romantic_orientation": .71,
        },
        birth_analysis={
            "bazi_text": "己巳 甲戌 戊申",
            "day_master": "戊土",
            "pattern_name": "七杀格",
            "strength_label": "身强",
            "relation_markers": {
                "combinations": 2, "self_punishments": 0, "other_punishments": 1,
                "clashes": 0, "harms": 0, "source_text": "合2次 他刑1次",
            },
        },
    ),
    TemplatePerson(
        user_id="person-1989-11-28",
        display_name="1989年11月28日",
        birth_date="1989-11-28",
        source_file="1989年11月28日_机器人性格设定.xlsx",
        mbti="ENTP",
        enneagram={
            "core_type": 7,
            "wing": 8,
            "primary_instinct": "SX",
            "secondary_instinct": "SO",
            "source": "expert_confirmed",
            "confidence": 0.85,
        },
        trait_values={
            "extroversion": 1.0, "social_warmth": .55, "assertiveness": 1.0, "impulsivity": .76,
            "openness": 1.0, "creativity": .72, "depth_of_thought": .67, "thinking_ratio": .80,
            "empathy": .43, "risk_tolerance": .71, "structure_pref": .33, "discipline": .55,
            "adaptability": .61, "persistence": .80, "confidence": .94, "optimism": .44,
            "romantic_orientation": .64,
        },
        birth_analysis={
            "bazi_text": "己巳 乙亥 壬辰",
            "day_master": "壬水",
            "pattern_name": "伤官格",
            "strength_label": "身强",
            "relation_markers": {
                "combinations": 0, "self_punishments": 0, "other_punishments": 0,
                "clashes": 1, "harms": 0, "source_text": "巳亥冲",
            },
        },
    ),
    TemplatePerson(
        user_id="person-1996-03-28",
        display_name="1996年3月28日",
        birth_date="1996-03-28",
        source_file="1996年3月28日_机器人性格设定.xlsx",
        mbti="ESFJ",
        enneagram={
            "core_type": 2,
            "wing": 1,
            "primary_instinct": "SO",
            "secondary_instinct": "SX",
            "source": "expert_confirmed",
            "confidence": 0.85,
        },
        trait_values={
            "extroversion": .65, "social_warmth": 1.0, "assertiveness": .70, "impulsivity": .05,
            "openness": 0.0, "creativity": .40, "depth_of_thought": .58, "thinking_ratio": .18,
            "empathy": .90, "risk_tolerance": .40, "structure_pref": .75, "discipline": .92,
            "adaptability": .58, "persistence": .65, "confidence": .64, "optimism": 1.0,
            "romantic_orientation": .94,
        },
        birth_analysis={
            "bazi_text": "丙子 辛卯 甲子",
            "day_master": "甲木",
            "pattern_name": "正官格",
            "strength_label": "身强",
            "relation_markers": {
                "combinations": 1, "self_punishments": 0, "other_punishments": 2,
                "clashes": 0, "harms": 0, "source_text": "丙辛合 子卯刑×2",
            },
        },
    ),
    TemplatePerson(
        user_id="person-1998-12-06",
        display_name="1998年12月6日",
        birth_date="1998-12-06",
        source_file="1998年12月6日_机器人性格设定.xlsx",
        mbti="ISTJ",
        numerology_code="6318",
        trait_values={
            "extroversion": .45, "social_warmth": .69, "assertiveness": .30, "impulsivity": .26,
            "openness": .31, "creativity": .49, "depth_of_thought": .58, "thinking_ratio": .56,
            "empathy": .64, "risk_tolerance": .41, "structure_pref": .68, "discipline": .84,
            "adaptability": .55, "persistence": .45, "confidence": .31, "optimism": .66,
            "romantic_orientation": .83,
        },
        birth_analysis={
            "bazi_text": "戊寅 癸亥 丁亥",
            "day_master": "丁火",
            "pattern_name": "七杀格",
            "strength_label": "身弱",
            "relation_markers": {
                "combinations": 3, "self_punishments": 1, "other_punishments": 0,
                "clashes": 0, "harms": 0, "source_text": "合3次 自刑1次",
            },
        },
    ),
)

TEMPLATE_BY_BIRTH_DATE = {person.birth_date: person for person in TEMPLATE_PEOPLE}
TEMPLATE_USER_IDS = frozenset(person.user_id for person in TEMPLATE_PEOPLE)


def template_person_for_birth_date(birth_date: str | None) -> TemplatePerson | None:
    return TEMPLATE_BY_BIRTH_DATE.get(birth_date or "")
