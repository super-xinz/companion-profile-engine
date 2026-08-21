from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .models import ChatMessage, Conversation, ProfileVersion, User


@dataclass(frozen=True)
class PublicTemplateIdentity:
    public_id: str
    display_name: str
    tagline: str
    summary: str
    default_preferences: tuple[tuple[str, str | float | bool], ...]


PUBLIC_TEMPLATE_IDENTITIES: dict[str, PublicTemplateIdentity] = {
    "person-1988-08-09": PublicTemplateIdentity(
        "profile-aurora",
        "互动样本 A",
        "重视灵感与自由表达",
        "容易被新鲜想法和自由表达调动，交流时适合先留出探索空间，再一起收拢到清晰、可执行的下一步。",
        (
            ("response_length", "medium"),
            ("directness", 0.55),
            ("empathy_first", True),
            ("question_load", 0.45),
            ("humor_level", 0.70),
        ),
    ),
    "person-1989-10-15": PublicTemplateIdentity(
        "profile-river",
        "互动样本 B",
        "善于探索不同可能",
        "习惯从多个角度快速展开思路，交流时适合用清楚的问题激发讨论，并在关键处帮助聚焦选择。",
        (
            ("response_length", "medium"),
            ("directness", 0.78),
            ("empathy_first", False),
            ("question_load", 0.65),
            ("humor_level", 0.65),
        ),
    ),
    "person-1989-11-28": PublicTemplateIdentity(
        "profile-harbor",
        "互动样本 C",
        "偏好有活力的互动节奏",
        "互动节奏较有活力，表达观点时通常直接而有推动感；回应时适合先接住核心意图，再留出有来有回的讨论空间。",
        (
            ("response_length", "short"),
            ("directness", 0.88),
            ("empathy_first", False),
            ("question_load", 0.50),
            ("humor_level", 0.75),
        ),
    ),
    "person-1996-03-28": PublicTemplateIdentity(
        "profile-forest",
        "互动样本 D",
        "重视关系中的体贴与回应",
        "对关系氛围和他人的感受较敏锐，交流时适合先确认感受与需求，再用清楚、温和的方式推进事情。",
        (
            ("response_length", "medium"),
            ("directness", 0.45),
            ("empathy_first", True),
            ("question_load", 0.35),
            ("humor_level", 0.40),
        ),
    ),
    "person-1998-12-06": PublicTemplateIdentity(
        "profile-sky",
        "互动样本 E",
        "兼顾目标推进与情绪感受",
        "倾向在目标推进和情绪感受之间寻找平衡，交流时适合说明重点与步骤，同时保留体贴、不过度施压的表达。",
        (
            ("response_length", "medium"),
            ("directness", 0.65),
            ("empathy_first", True),
            ("question_load", 0.30),
            ("humor_level", 0.25),
        ),
    ),
}

_PUBLIC_TO_INTERNAL = {
    identity.public_id: internal_id
    for internal_id, identity in PUBLIC_TEMPLATE_IDENTITIES.items()
}

_METHOD_PATTERNS = (
    re.compile(
        r"(?i)(?:\bM[\s._-]*B[\s._-]*T[\s._-]*I\b|"
        r"Myers[\s._-]*Briggs|迈尔斯[·\s-]*布里格斯|"
        r"十\s*六\s*型\s*人\s*格|16\s*(?:型\s*人格|personalities)|"
        r"人格\s*类型\s*测试|荣格\s*八维)"
    ),
    re.compile(
        r"(?i)(?:\bE[\s._-]*N[\s._-]*N[\s._-]*E[\s._-]*A[\s._-]*G"
        r"[\s._-]*R[\s._-]*A[\s._-]*M\b|"
        r"九\s*型(?:\s*人\s*格|\s*分析|\s*测评)?)"
    ),
    re.compile(
        r"(?i)(?:\bN[\s._-]*U[\s._-]*M[\s._-]*E[\s._-]*R[\s._-]*O"
        r"[\s._-]*L[\s._-]*O[\s._-]*G[\s._-]*Y\b|"
        r"数\s*字\s*(?:密\s*码|学|能\s*量)|生\s*命\s*(?:灵\s*数|数\s*字))"
    ),
    re.compile(
        r"(?i)(?:\bB[\s._-]*A[\s._-]*Z[\s._-]*I\b|"
        r"生\s*辰\s*八\s*字|八\s*字(?:\s*命\s*理)?|四\s*柱(?:\s*命\s*理)?|"
        r"日\s*主|身\s*[强弱]|偏\s*财\s*格|七\s*杀\s*格|"
        r"伤\s*官\s*格|正\s*官\s*格)"
    ),
    re.compile(
        r"(?:完\s*美\s*型|助\s*人\s*型|成\s*就\s*型|观\s*察\s*型|"
        r"忠\s*诚\s*型|探\s*索\s*型|挑\s*战\s*型|和\s*平\s*型|"
        r"[1-9]\s*[号型])"
    ),
    re.compile(
        r"(?:内\s*心\s*码|制\s*约\s*数|天\s*赋\s*数|坐\s*镇\s*码|缺\s*[1-9])"
    ),
)
_MODEL_PATTERNS = (
    re.compile(r"(?i)\bD[\s._-]*E[\s._-]*E[\s._-]*P[\s._-]*S[\s._-]*E[\s._-]*E[\s._-]*K\b"),
    re.compile(r"(?i)\bO[\s._-]*P[\s._-]*E[\s._-]*N[\s._-]*A[\s._-]*I\b"),
    re.compile(r"(?i)\bG[\s._-]*P[\s._-]*T(?:[\s._-]*\d+(?:\.\d+)?)?\b"),
    re.compile(r"(?i)\bC[\s._-]*L[\s._-]*A[\s._-]*U[\s._-]*D[\s._-]*E\b"),
    re.compile(r"(?i)\bA[\s._-]*N[\s._-]*T[\s._-]*H[\s._-]*R[\s._-]*O[\s._-]*P[\s._-]*I[\s._-]*C\b"),
    re.compile(r"(?i)\bG[\s._-]*E[\s._-]*M[\s._-]*I[\s._-]*N[\s._-]*I\b"),
    re.compile(r"(?i)\bG[\s._-]*L[\s._-]*M(?:[\s._-]*\d+(?:\.\d+)?)?\b"),
    re.compile(r"(?i)\bK[\s._-]*I[\s._-]*M[\s._-]*I\b"),
    re.compile(r"(?i)\bM[\s._-]*O[\s._-]*O[\s._-]*N[\s._-]*S[\s._-]*H[\s._-]*O[\s._-]*T\b"),
    re.compile(r"(?i)\bO[\s._-]*P[\s._-]*E[\s._-]*N[\s._-]*R[\s._-]*O[\s._-]*U[\s._-]*T[\s._-]*E[\s._-]*R\b"),
)
_TYPE_CODE = re.compile(
    r"(?i)(?<![A-Z])(?:[EI][\s._-]*[NS][\s._-]*[TF][\s._-]*[JP])(?![A-Z])"
)
_WING_CODE = re.compile(r"(?i)(?<![A-Z0-9])[1-9]\s*w\s*[1-9](?![A-Z0-9])")
_STACK_CODE = re.compile(
    r"(?i)(?<![A-Z])(?:S[\s._-]*[PXO])\s*[/／]\s*"
    r"(?:S[\s._-]*[PXO])(?![A-Z])"
)
_PRIVATE_NUMBER_CODES = re.compile(r"(?<!\d)(?:9817|6118|6318)(?!\d)")
_STEM_BRANCH_SEQUENCE = re.compile(
    r"[甲乙丙丁戊己庚辛壬癸]\s*[子丑寅卯辰巳午未申酉戌亥]"
    r"(?:[\s、，,]*[甲乙丙丁戊己庚辛壬癸]\s*[子丑寅卯辰巳午未申酉戌亥]){1,3}"
)
_TEMPLATE_PERSON_ID = re.compile(r"(?i)\bperson[-_]\d{4}[-_]\d{2}[-_]\d{2}\b")
_SOURCE_FILE = re.compile(
    r"(?i)(?:[A-Z]:)?[^\s，。；;、<>\"']{1,100}\.(?:xlsx?|csv|docx?|pdf|ya?ml|json)\b"
)
_BIRTH_CONTEXT = re.compile(
    r"(?i)(?:生日|出生日期|出生于|born(?:\s+on)?)\s*[：:]?\s*"
    r"(?:19|20)\d{2}(?:[-/.年])\d{1,2}(?:[-/.月])\d{1,2}日?"
)
_TEMPLATE_DATES = tuple(
    re.compile(pattern)
    for pattern in (
        r"1988(?:-08-09|年0?8月0?9日)",
        r"1989(?:-10-15|年10月15日)",
        r"1989(?:-11-28|年11月28日)",
        r"1996(?:-03-28|年0?3月28日)",
        r"1998(?:-12-06|年12月0?6日)",
    )
)
_REDACTION = "内部分析信息"

_TRAIT_LABELS = {
    "extroversion": "外向表达",
    "social_warmth": "社交温度",
    "assertiveness": "表达坚定度",
    "impulsivity": "即时行动倾向",
    "openness": "开放程度",
    "creativity": "创造倾向",
    "depth_of_thought": "思考深度",
    "thinking_ratio": "理性决策倾向",
    "empathy": "共情倾向",
    "risk_tolerance": "风险接受度",
    "structure_pref": "结构偏好",
    "discipline": "自律程度",
    "adaptability": "适应程度",
    "persistence": "持续投入度",
    "confidence": "自信表达",
    "optimism": "积极预期",
    "romantic_orientation": "情感投入倾向",
}

_PREFERENCE_LABELS = {
    "response_length": "回复篇幅",
    "directness": "表达直接度",
    "empathy_first": "优先回应感受",
    "question_load": "追问频率",
    "humor_level": "幽默程度",
}
_RESPONSE_LENGTH_LABELS = {"short": "简短", "medium": "适中", "long": "充分"}

CONFIDENCE_EXPLANATION = (
    "可信度表示当前结论中有多少得到多轮一致信息支持，"
    "它不是对一个人的判断准确率；随着持续对话和信息相互印证，这个数值会逐步变化。"
)


def sanitize_public_text(value: Any, *, fallback: str = "") -> str:
    """Remove implementation clues from free text before it crosses the public boundary."""
    if value is None:
        return fallback
    text = unicodedata.normalize("NFKC", str(value))
    for pattern in _METHOD_PATTERNS:
        text = pattern.sub(_REDACTION, text)
    for pattern in _MODEL_PATTERNS:
        text = pattern.sub("智能助手", text)
    text = _TYPE_CODE.sub(_REDACTION, text)
    text = _WING_CODE.sub(_REDACTION, text)
    text = _STACK_CODE.sub(_REDACTION, text)
    text = _PRIVATE_NUMBER_CODES.sub(_REDACTION, text)
    text = _STEM_BRANCH_SEQUENCE.sub(_REDACTION, text)
    text = _TEMPLATE_PERSON_ID.sub("公开人物", text)
    text = _BIRTH_CONTEXT.sub("出生信息已保护", text)
    for pattern in _TEMPLATE_DATES:
        text = pattern.sub("日期信息已保护", text)
    text = _SOURCE_FILE.sub("来源资料", text)
    text = re.sub(rf"(?:{re.escape(_REDACTION)}[、，,；;：:\s]*){{2,}}", _REDACTION, text)
    cleaned = text.strip()
    return cleaned or fallback


def _digest(prefix: str, *parts: str) -> str:
    raw = "\0".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:16]}"


def public_person_id(tenant_id: str, internal_id: str) -> str:
    template = PUBLIC_TEMPLATE_IDENTITIES.get(internal_id)
    if template:
        return template.public_id
    return _digest("profile", tenant_id, internal_id)


def public_display_name(user: User) -> str:
    template = PUBLIC_TEMPLATE_IDENTITIES.get(user.tenant_user_id)
    if template:
        return template.display_name
    return sanitize_public_text(user.display_name, fallback="互动人物")


def public_tagline(user: User) -> str:
    template = PUBLIC_TEMPLATE_IDENTITIES.get(user.tenant_user_id)
    if template:
        return template.tagline
    return "通过持续对话逐步完善互动理解"


def resolve_public_user(db: Session, tenant_id: str, public_id: str) -> User:
    internal_id = _PUBLIC_TO_INTERNAL.get(public_id)
    if internal_id:
        user = db.scalar(select(User).where(
            User.tenant_id == tenant_id,
            User.tenant_user_id == internal_id,
            User.profile_consent.is_(True),
            User.inference_enabled.is_(True),
        ))
        if user:
            return user
        raise HTTPException(status_code=404, detail="人物不存在")

    raise HTTPException(status_code=404, detail="人物不存在")


def public_conversation_id(user: User, internal_id: str) -> str:
    return _digest("conversation", user.id, internal_id)


def resolve_public_conversation(
    db: Session, user: User, public_id: str, *, active_only: bool = True,
) -> Conversation:
    query = select(Conversation).where(Conversation.user_id == user.id)
    if active_only:
        query = query.where(Conversation.status == "active")
    items = db.scalars(query).all()
    for item in items:
        if public_conversation_id(user, item.external_id) == public_id:
            return item
    raise HTTPException(status_code=404, detail="对话不存在")


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return round(min(1.0, max(0.0, float(value))), 4)


def _current_version(db: Session, user: User) -> ProfileVersion:
    version = db.scalar(select(ProfileVersion).where(
        ProfileVersion.user_id == user.id
    ).order_by(desc(ProfileVersion.version_no)).limit(1))
    if not version:
        raise HTTPException(status_code=404, detail="人物资料不存在")
    return version


def public_person_summary(db: Session, user: User) -> dict[str, Any]:
    version = _current_version(db, user)
    conversation_count = db.scalar(select(func.count()).select_from(Conversation).where(
        Conversation.user_id == user.id,
        Conversation.status == "active",
    )) or 0
    last_conversation = db.scalar(select(Conversation).where(
        Conversation.user_id == user.id,
    ).order_by(desc(Conversation.updated_at)).limit(1))
    updated_at = last_conversation.updated_at if last_conversation else user.updated_at
    confidence = _number(version.snapshot.get("meta", {}).get("overall_confidence"))
    return {
        "public_id": public_person_id(user.tenant_id, user.tenant_user_id),
        "display_name": public_display_name(user),
        "tagline": public_tagline(user),
        "profile_version": version.version_no,
        "confidence": confidence,
        "confidence_explanation": CONFIDENCE_EXPLANATION,
        "conversation_count": int(conversation_count),
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def public_conversation_summary(db: Session, user: User, item: Conversation) -> dict[str, Any]:
    count = db.scalar(select(func.count()).select_from(ChatMessage).where(
        ChatMessage.conversation_id == item.id,
    )) or 0
    last = db.scalar(select(ChatMessage).where(
        ChatMessage.conversation_id == item.id,
    ).order_by(desc(ChatMessage.created_at)).limit(1))
    return {
        "conversation_id": public_conversation_id(user, item.external_id),
        "title": sanitize_public_text(item.title, fallback="新的陪伴对话"),
        "summary": sanitize_public_text(item.summary) or None,
        "message_count": int(count),
        "last_message": sanitize_public_text(last.content[:90]) if last else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def public_metrics(profile: dict[str, Any]) -> list[dict[str, Any]]:
    traits: dict[str, dict[str, Any]] = {}
    for category in profile.get("core_traits", {}).values():
        if isinstance(category, dict):
            for key, entry in category.items():
                if key in _TRAIT_LABELS and isinstance(entry, dict):
                    traits[key] = entry
    return [
        {
            "name": label,
            "value": _number(traits.get(key, {}).get("value"), 0.5),
            "confidence": _number(traits.get(key, {}).get("confidence")),
        }
        for key, label in _TRAIT_LABELS.items()
    ]


def _public_preference_value(key: str, value: Any) -> str | float | bool | None:
    if key == "response_length":
        return _RESPONSE_LENGTH_LABELS.get(str(value).lower())
    if key == "empathy_first" and isinstance(value, bool):
        return value
    if key in _PREFERENCE_LABELS and isinstance(value, (int, float)) and not isinstance(value, bool):
        return _number(value)
    return None


def _template_identity_for_profile(profile: dict[str, Any]) -> PublicTemplateIdentity | None:
    identity = profile.get("identity", {})
    if not isinstance(identity, dict):
        return None
    for field in ("template_person_id", "user_id"):
        internal_id = identity.get(field)
        if isinstance(internal_id, str) and internal_id in PUBLIC_TEMPLATE_IDENTITIES:
            return PUBLIC_TEMPLATE_IDENTITIES[internal_id]
    return None


def public_preferences(profile: dict[str, Any]) -> list[dict[str, Any]]:
    source = profile.get("runtime", {}).get("interaction_preferences", {})
    template = _template_identity_for_profile(profile)
    merged: dict[str, Any] = dict(template.default_preferences) if template else {}
    if isinstance(source, dict):
        for key, value in source.items():
            if _public_preference_value(key, value) is not None:
                merged[key] = value
    output = []
    for key, label in _PREFERENCE_LABELS.items():
        if key not in merged:
            continue
        value = _public_preference_value(key, merged[key])
        if value is not None:
            output.append({"name": label, "value": value})
    return output


def public_dynamic_summary(profile: dict[str, Any]) -> str:
    runtime = profile.get("runtime", {})
    states = runtime.get("current_state", {}) if isinstance(runtime, dict) else {}
    memories = runtime.get("memories", []) if isinstance(runtime, dict) else []
    preferences = runtime.get("interaction_preferences", {}) if isinstance(runtime, dict) else {}
    state_count = len(states) if isinstance(states, dict) else 0
    memory_count = len(memories) if isinstance(memories, list) else 0
    preference_count = (
        sum(
            _public_preference_value(key, value) is not None
            for key, value in preferences.items()
        )
        if isinstance(preferences, dict)
        else 0
    )
    template = _template_identity_for_profile(profile)
    activity = []
    if state_count:
        activity.append(f"已结合 {state_count} 项近期互动状态")
    if preference_count:
        activity.append(f"已确认 {preference_count} 项沟通偏好")
    if memory_count:
        activity.append(f"已沉淀 {memory_count} 项持续对话信息")
    if template:
        if not activity:
            return template.summary
        return template.summary + " 当前" + "，".join(activity) + "。"
    if not (state_count or memory_count or preference_count):
        return "当前以基础互动特征为主，后续会根据持续对话逐步更新。"
    return "，".join(activity) + "。"


def public_profile_detail(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "metrics": public_metrics(profile),
        "dynamic_summary": public_dynamic_summary(profile),
        "communication_preferences": public_preferences(profile),
        "confidence": _number(profile.get("meta", {}).get("overall_confidence")),
        "confidence_explanation": CONFIDENCE_EXPLANATION,
        "profile_version": int(profile.get("meta", {}).get("profile_version", 1)),
    }


def public_update_summary(profile_version: int, expected_profile_version: int) -> str:
    if profile_version > expected_profile_version:
        return "已结合本轮信息更新互动理解。"
    return "本轮没有新增需要长期保留的稳定信息。"
