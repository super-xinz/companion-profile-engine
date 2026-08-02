from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Literal
from urllib.parse import unquote

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .db import get_db
from .demo import _current_pack, demo_auth
from .extractor import get_semantic_extractor
from .models import (AuditLog, ChatMessage, Conversation, ManualOverride, Memory,
                     ProfileEvidence, ProfileVersion, RulePack, RuleRevision,
                     TeamMember, User)
from .profile import TRAIT_NAMES, clone_profile, flattened_traits, rebuild_derived, recalculate_meta
from .rule_compiler import validate_rule_references
from .schemas import (Consent, EnneagramIdentityInput, ProfileInitRequest,
                      SetEnneagramRequest)
from .service import (_audit, _resolve_path, current_version, explain_profile, find_user,
                      get_profile, init_profile, set_enneagram_profile)
from .template_people import TEMPLATE_PEOPLE


router = APIRouter(prefix="/demo/api", tags=["workspaces"])


ROLE_PERMISSIONS = {
    "admin": ["people.manage", "profile.edit", "rules.edit", "rules.review", "rules.publish", "members.manage"],
    "reviewer": ["profile.edit", "rules.edit", "rules.review"],
    "expert": ["profile.edit", "rules.edit"],
    "viewer": [],
}


class PersonCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    birth_date: date | None = None
    enneagram: EnneagramIdentityInput | None = None
    notes: str | None = Field(default=None, max_length=1000)


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=256)


class ManualEditRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)
    target_path: str = Field(min_length=1, max_length=512)
    value: Any
    reason: str = Field(min_length=1, max_length=1000)


class EnneagramEditRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)
    enneagram: EnneagramIdentityInput
    reason: str = Field(min_length=1, max_length=1000)


class DraftCreate(BaseModel):
    title: str = Field(default="规则优化草稿", min_length=1, max_length=256)
    base_revision_id: str | None = None


class DraftSave(BaseModel):
    canonical_json: dict
    change_summary: str | None = Field(default=None, max_length=2000)


class RuleDocumentParse(BaseModel):
    asset: Literal["cold_start", "dialogue", "schema", "enneagram"]
    document_text: str = Field(min_length=1, max_length=2_000_000)


class RuleDocumentDump(BaseModel):
    asset: Literal["cold_start", "dialogue", "schema", "enneagram"]
    content: dict


class RuleAction(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class RuleTestRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    revision_id: str | None = None
    user_id: str | None = None


class TeamMemberRequest(BaseModel):
    account: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=128)
    role: Literal["admin", "reviewer", "expert", "viewer"]


def _actor(value: str | None) -> str:
    return unquote(value or "系统管理员").strip()[:128]


def _ensure_member(db: Session, tenant_id: str, actor: str) -> TeamMember:
    member = db.scalar(select(TeamMember).where(
        TeamMember.tenant_id == tenant_id, TeamMember.display_name == actor, TeamMember.active.is_(True)
    ))
    if member:
        return member
    existing_count = db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.tenant_id == tenant_id)) or 0
    role = "admin" if existing_count == 0 or actor == "系统管理员" else "viewer"
    member = TeamMember(tenant_id=tenant_id, account=f"{actor}@local", display_name=actor,
                        role=role, permissions=ROLE_PERMISSIONS[role])
    db.add(member)
    db.flush()
    return member


def _require(db: Session, tenant_id: str, actor: str, permission: str) -> TeamMember:
    member = _ensure_member(db, tenant_id, actor)
    if permission not in member.permissions:
        raise HTTPException(status_code=403, detail=f"{member.display_name} 没有“{permission}”权限")
    return member


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _person_view(db: Session, user: User) -> dict:
    version = current_version(db, user)
    conversations = db.scalar(select(func.count()).select_from(Conversation).where(
        Conversation.user_id == user.id, Conversation.status == "active"
    )) or 0
    last_conversation = db.scalar(select(Conversation).where(
        Conversation.user_id == user.id
    ).order_by(desc(Conversation.updated_at)).limit(1))
    profile = version.snapshot
    return {
        "user_id": user.tenant_user_id,
        "display_name": user.display_name or "未命名人物",
        "birth_date": user.birth_date.isoformat() if user.birth_date else None,
        "profile_version": version.version_no,
        "overall_confidence": profile.get("meta", {}).get("overall_confidence", 0),
        "mbti": profile.get("mbti_dimensions", {}).get("type_label", "XXXX"),
        "conversation_count": conversations,
        "updated_at": _iso(last_conversation.updated_at if last_conversation else user.updated_at),
    }


def _conversation_view(db: Session, item: Conversation) -> dict:
    count = db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.conversation_id == item.id)) or 0
    last = db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == item.id)
                     .order_by(desc(ChatMessage.created_at)).limit(1))
    return {
        "conversation_id": item.external_id,
        "title": item.title,
        "summary": item.summary,
        "message_count": count,
        "last_message": last.content[:90] if last else None,
        "updated_at": _iso(item.updated_at),
    }


def _ensure_conversation(db: Session, user: User, external_id: str | None = None,
                         title: str = "新的陪伴对话") -> Conversation:
    if external_id:
        existing = db.scalar(select(Conversation).where(
            Conversation.user_id == user.id, Conversation.external_id == external_id
        ))
        if existing:
            return existing
    item = Conversation(user_id=user.id, external_id=external_id or f"conv_{uuid.uuid4().hex}", title=title)
    db.add(item)
    db.flush()
    return item


def _seed_people(db: Session, tenant_id: str, request: Request) -> None:
    pack = _current_pack(request, db)
    for person in TEMPLATE_PEOPLE:
        user_id, name, birthday = person.user_id, person.display_name, person.birth_date
        exists = db.scalar(select(User).where(User.tenant_id == tenant_id, User.tenant_user_id == user_id))
        if exists:
            continue
        response = init_profile(
            db, tenant_id,
            ProfileInitRequest(tenant_user_id=user_id, display_name=name, birth_date=date.fromisoformat(birthday),
                               timezone="Asia/Shanghai", consent=Consent(profile=True, sensitive_inference=True)),
            pack, f"seed_{uuid.uuid4().hex}", f"seed-{tenant_id}-{user_id}",
        )
        user = find_user(db, tenant_id, user_id)
        _ensure_conversation(db, user, title=f"{name}的第一段对话")
        response["profile_version"]
        db.commit()


@router.post("/workspace/bootstrap")
def workspace_bootstrap(request: Request, tenant_id: str = Depends(demo_auth),
                        x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                        db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    member = _ensure_member(db, tenant_id, actor)
    _seed_people(db, tenant_id, request)
    people = db.scalars(select(User).where(User.tenant_id == tenant_id).order_by(desc(User.updated_at))).all()
    db.commit()
    return {
        "actor": {"display_name": member.display_name, "role": member.role, "permissions": member.permissions},
        "people": [_person_view(db, user) for user in people],
        "rule_pack": {"version": _current_pack(request, db).version, "status": "published"},
    }


@router.get("/people")
def list_people(q: str | None = None, tenant_id: str = Depends(demo_auth),
                db: Session = Depends(get_db)) -> dict:
    query = select(User).where(User.tenant_id == tenant_id)
    if q:
        query = query.where(User.display_name.ilike(f"%{q.strip()}%"))
    people = db.scalars(query.order_by(desc(User.updated_at))).all()
    return {"people": [_person_view(db, user) for user in people]}


@router.post("/people")
def create_person(body: PersonCreate, request: Request, tenant_id: str = Depends(demo_auth),
                  x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                  db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "people.manage")
    user_id = f"person_{uuid.uuid4().hex}"
    response = init_profile(
        db, tenant_id,
        ProfileInitRequest(tenant_user_id=user_id, display_name=body.display_name, birth_date=body.birth_date,
                           timezone="Asia/Shanghai", enneagram=body.enneagram,
                           consent=Consent(profile=True, sensitive_inference=bool(body.birth_date or body.enneagram))),
        _current_pack(request, db), request.state.request_id, f"workspace-create-{user_id}",
    )
    user = find_user(db, tenant_id, user_id)
    conversation = _ensure_conversation(db, user)
    db.commit()
    return {"person": _person_view(db, user), "conversation": _conversation_view(db, conversation),
            "profile": response["profile"]}


@router.get("/people/{user_id}")
def person_detail(user_id: str, tenant_id: str = Depends(demo_auth), db: Session = Depends(get_db)) -> dict:
    user = find_user(db, tenant_id, user_id)
    profile = get_profile(db, tenant_id, user_id)
    conversations = db.scalars(select(Conversation).where(
        Conversation.user_id == user.id, Conversation.status == "active"
    ).order_by(desc(Conversation.updated_at))).all()
    overrides = db.scalars(select(ManualOverride).where(
        ManualOverride.user_id == user.id, ManualOverride.active.is_(True)
    )).all()
    return {
        "person": _person_view(db, user),
        **profile,
        "conversations": [_conversation_view(db, item) for item in conversations],
        "manual_overrides": [{
            "id": item.id, "target_path": item.target_path, "value": item.value.get("value"),
            "reason": item.reason, "created_by": item.created_by, "updated_at": _iso(item.updated_at),
        } for item in overrides],
    }


@router.post("/people/{user_id}/conversations")
def create_conversation(user_id: str, body: ConversationCreate, tenant_id: str = Depends(demo_auth),
                        db: Session = Depends(get_db)) -> dict:
    user = find_user(db, tenant_id, user_id)
    item = _ensure_conversation(db, user, title=body.title)
    db.commit()
    return {"conversation": _conversation_view(db, item)}


@router.get("/people/{user_id}/conversations")
def list_conversations(user_id: str, tenant_id: str = Depends(demo_auth), db: Session = Depends(get_db)) -> dict:
    user = find_user(db, tenant_id, user_id)
    items = db.scalars(select(Conversation).where(
        Conversation.user_id == user.id, Conversation.status == "active"
    ).order_by(desc(Conversation.updated_at))).all()
    return {"conversations": [_conversation_view(db, item) for item in items]}


@router.get("/people/{user_id}/conversations/{conversation_id}/messages")
def list_messages(user_id: str, conversation_id: str, tenant_id: str = Depends(demo_auth),
                  db: Session = Depends(get_db)) -> dict:
    user = find_user(db, tenant_id, user_id)
    conversation = db.scalar(select(Conversation).where(
        Conversation.user_id == user.id, Conversation.external_id == conversation_id
    ))
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    items = db.scalars(select(ChatMessage).where(
        ChatMessage.conversation_id == conversation.id
    ).order_by(ChatMessage.created_at)).all()
    return {
        "conversation": _conversation_view(db, conversation),
        "messages": [{
            "message_id": item.external_id, "role": item.role, "content": item.content,
            "profile_version": item.profile_version, "engine_trace": item.engine_trace,
            "created_at": _iso(item.created_at),
        } for item in items],
    }


@router.get("/people/{user_id}/profile-explain")
def profile_explain(user_id: str, field: str | None = None, tenant_id: str = Depends(demo_auth),
                    db: Session = Depends(get_db)) -> dict:
    user = find_user(db, tenant_id, user_id)
    explanation = explain_profile(db, tenant_id, user_id, field)
    audits = db.scalars(select(AuditLog).where(
        AuditLog.tenant_id == tenant_id, AuditLog.user_id == user.id
    ).order_by(desc(AuditLog.created_at)).limit(60)).all()
    memories = db.scalars(select(Memory).where(Memory.user_id == user.id).order_by(desc(Memory.updated_at))).all()
    return {
        **explanation,
        "memories": [{
            "memory_id": item.id, "type": item.memory_type, "content": item.content,
            "active": item.active, "updated_at": _iso(item.updated_at),
        } for item in memories],
        "audit_log": [{
            "id": item.id, "action": item.action, "actor": item.actor,
            "before": item.before, "after": item.after, "created_at": _iso(item.created_at),
        } for item in audits],
    }


@router.post("/people/{user_id}/manual-edit")
def manual_edit(user_id: str, body: ManualEditRequest, request: Request,
                tenant_id: str = Depends(demo_auth),
                x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "profile.edit")
    user = find_user(db, tenant_id, user_id)
    version = current_version(db, user)
    if version.version_no != body.expected_profile_version:
        raise HTTPException(status_code=409, detail=f"画像已更新为 v{version.version_no}，请刷新后重试")
    if body.target_path.startswith(
        ("mbti_dimensions", "behavior_style", "language_style", "portrait", "digital_code_profile",
         "enneagram_profile", "meta")
    ):
        raise HTTPException(status_code=422, detail="派生字段不能直接修改，请编辑底层画像维度或事实")
    before = clone_profile(version.snapshot)
    profile = clone_profile(version.snapshot)
    parent, key = _resolve_path(profile, body.target_path)
    old = clone_profile(parent[key]) if isinstance(parent[key], dict) else parent[key]
    if body.target_path.startswith("core_traits."):
        if not isinstance(body.value, (int, float)) or not 0 <= float(body.value) <= 1:
            raise HTTPException(status_code=422, detail="画像维度必须在 0 到 1 之间")
        entry = parent[key]
        before_value = entry["value"]
        applied = round(float(body.value), 4)
        evidence = ProfileEvidence(
            user_id=user.id, source_type="manual_expert_override", target_path=body.target_path,
            direction=1 if applied > before_value else (-1 if applied < before_value else 0),
            base_delta=abs(applied - before_value), impact=1.0,
            factors={"reliability": 1.0, "manual_priority": True, "actor": actor},
            rule_id="EXPERT-MANUAL-OVERRIDE", reason=body.reason,
        )
        db.add(evidence)
        db.flush()
        entry.update(value=applied, confidence=1.0, evidence_refs=[*entry.get("evidence_refs", []), evidence.id],
                     updated_at=datetime.now(timezone.utc).isoformat(),
                     interpretation=f"由 {actor} 人工确认：{body.reason}")
        rebuild_derived(profile, _current_pack(request, db).canonical_json["schema"])
        evidence_refs = [evidence.id]
    else:
        before_value = old
        parent[key] = body.value
        applied = body.value
        evidence_refs = []
        if body.target_path == "identity.display_name":
            user.display_name = str(body.value)
        recalculate_meta(profile)
    locked = db.scalar(select(ManualOverride).where(
        ManualOverride.user_id == user.id, ManualOverride.target_path == body.target_path
    ))
    if locked:
        locked.value, locked.reason, locked.created_by, locked.active = {"value": applied}, body.reason, actor, True
    else:
        db.add(ManualOverride(user_id=user.id, target_path=body.target_path, value={"value": applied},
                              reason=body.reason, created_by=actor))
    new_no = version.version_no + 1
    profile["meta"]["profile_version"] = new_no
    db.add(ProfileVersion(
        user_id=user.id, version_no=new_no, schema_version=profile["meta"]["schema_version"],
        cold_start_rule_pack_version=version.cold_start_rule_pack_version,
        dialogue_rule_pack_version=_current_pack(request, db).version,
        overall_confidence=profile["meta"]["overall_confidence"], snapshot=profile,
    ))
    _audit(db, request.state.request_id, tenant_id, "profile.manual_override", user, before, profile,
           evidence_refs, ["EXPERT-MANUAL-OVERRIDE"], f"manual-{uuid.uuid4().hex}")
    audit = db.scalar(select(AuditLog).where(AuditLog.request_id == request.state.request_id))
    if audit:
        audit.actor = actor
    db.commit()
    return {"profile_version": new_no, "target_path": body.target_path, "before": before_value,
            "after": applied, "locked": True, "actor": actor}


@router.post("/people/{user_id}/enneagram")
def edit_enneagram(
    user_id: str,
    body: EnneagramEditRequest,
    request: Request,
    tenant_id: str = Depends(demo_auth),
    x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
    db: Session = Depends(get_db),
) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "profile.edit")
    response = set_enneagram_profile(
        db,
        tenant_id,
        user_id,
        SetEnneagramRequest(
            expected_profile_version=body.expected_profile_version,
            enneagram=body.enneagram,
            reason=body.reason,
        ),
        _current_pack(request, db),
        request.state.request_id,
        f"workspace-enneagram-{uuid.uuid4().hex}",
    )
    audit = db.scalar(select(AuditLog).where(AuditLog.request_id == request.state.request_id))
    if audit:
        audit.actor = actor
        db.commit()
    return {**response, "actor": actor}


def _validate_rules(canonical: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    schema = canonical.get("schema", {})
    cold = canonical.get("cold_start", {})
    dialogue = canonical.get("dialogue", {})
    enneagram = canonical.get("enneagram", {})
    categories = schema.get("canonical_profile", {}).get("core_traits", {}).get("categories", {})
    traits = [key for category in categories.values() for key in category.get("fields", {})]
    mappings = dialogue.get("trait_mapping_rules", {})
    target_schema = f"01_profile_schema.yaml@{schema.get('schema_version')}"
    for name, rules in (("冷启动", cold), ("对话维护", dialogue), ("九型互动", enneagram)):
        if rules.get("target_schema") != target_schema:
            errors.append(f"{name}规则目标结构不兼容：{rules.get('target_schema')}")
    if len(traits) != 17 or len(set(traits)) != 17:
        errors.append(f"核心画像维度应为 17 个，当前为 {len(set(traits))} 个")
    errors.extend(validate_rule_references(schema, dialogue))
    signals = cold.get("semantic_signal_extraction", {}).get("generalized_signal_dictionary", {})
    for signal_id, signal in signals.items():
        for target, direction in signal.get("effects", {}).items():
            if target not in traits:
                errors.append(f"冷启动信号 {signal_id} 引用了未知维度 {target}")
            if direction not in (-1, 0, 1):
                errors.append(f"冷启动信号 {signal_id}.{target} 的方向必须为 -1、0 或 1")
    cue_effects: dict[str, set[tuple[str, int]]] = {}
    for signal in signals.values():
        for cue in signal.get("cues", []):
            for target, direction in signal.get("effects", {}).items():
                cue_effects.setdefault(str(cue), set()).add((target, int(direction)))
    conflicts = []
    for cue, effects in cue_effects.items():
        by_target: dict[str, set[int]] = {}
        for target, direction in effects:
            by_target.setdefault(target, set()).add(direction)
        if any(len(values - {0}) > 1 for values in by_target.values()):
            conflicts.append(cue)
    if conflicts:
        errors.append(f"发现 {len(conflicts)} 个方向冲突的语义线索：{', '.join(conflicts[:5])}")
    if not signals:
        warnings.append("冷启动语义信号为空，生日画像将只保留中性先验")
    core_types = enneagram.get("core_types", {})
    wings = enneagram.get("wings", {})
    stacks = enneagram.get("instinct_stacks", {})
    scenes = enneagram.get("scene_adaptation", {})
    if len(core_types) != 9:
        errors.append(f"九型主型应为 9 个，当前为 {len(core_types)} 个")
    if len(wings) != 18:
        errors.append(f"九型侧翼应为 18 个，当前为 {len(wings)} 个")
    if len(stacks) != 6:
        errors.append(f"本能叠层应为 6 个，当前为 {len(stacks)} 个")
    weights = enneagram.get("weights", {})
    weight_total = sum(float(weights.get(key, 0)) for key in (
        "core_type", "primary_instinct", "secondary_instinct", "wing", "dynamic_state"
    ))
    if abs(weight_total - 1.0) > 1e-9:
        errors.append("九型参数权重总和必须为 1")
    return {
        "valid": not errors, "errors": errors, "warnings": warnings,
        "checks": {
            "trait_count": len(set(traits)),
            "dialogue_mapping_count": len(mappings),
            "semantic_signal_count": len(signals),
            "conflict_count": len(conflicts),
            "enneagram_core_type_count": len(core_types),
            "enneagram_wing_count": len(wings),
            "enneagram_instinct_stack_count": len(stacks),
            "enneagram_resolved_combination_count": len(core_types) * len(stacks),
            "enneagram_scene_count": len(scenes),
        },
    }


def _next_revision_no(db: Session, tenant_id: str) -> int:
    return (db.scalar(select(func.max(RuleRevision.revision_no)).where(RuleRevision.tenant_id == tenant_id)) or 0) + 1


def _ensure_rule_revision(db: Session, tenant_id: str, request: Request) -> RuleRevision:
    latest = db.scalar(select(RuleRevision).where(
        RuleRevision.tenant_id == tenant_id, RuleRevision.status == "published"
    ).order_by(desc(RuleRevision.revision_no)).limit(1))
    if latest and "enneagram" in latest.canonical_json:
        return latest
    if latest:
        latest.status = "superseded"
    pack = _current_pack(request, db)
    latest = RuleRevision(
        tenant_id=tenant_id, revision_no=_next_revision_no(db, tenant_id),
        title=f"生产规则 · {pack.version}", status="published", base_rule_pack_id=pack.id,
        canonical_json=pack.canonical_json, validation_report=pack.validation_report,
        change_summary="从现有生产规则导入", created_by="系统导入",
        reviewed_by="系统导入", published_at=pack.published_at or datetime.now(timezone.utc),
    )
    db.add(latest)
    db.commit()
    return latest


def _revision_view(item: RuleRevision, include_content: bool = False) -> dict:
    value = {
        "id": item.id, "revision_no": item.revision_no, "title": item.title, "status": item.status,
        "change_summary": item.change_summary, "created_by": item.created_by, "reviewed_by": item.reviewed_by,
        "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at),
        "submitted_at": _iso(item.submitted_at), "published_at": _iso(item.published_at),
        "validation_report": item.validation_report,
    }
    if include_content:
        value["canonical_json"] = item.canonical_json
    return value


@router.get("/rules/workspace")
def rules_workspace(request: Request, tenant_id: str = Depends(demo_auth),
                    x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                    db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    member = _ensure_member(db, tenant_id, actor)
    current = _ensure_rule_revision(db, tenant_id, request)
    revisions = db.scalars(select(RuleRevision).where(
        RuleRevision.tenant_id == tenant_id
    ).order_by(desc(RuleRevision.revision_no))).all()
    members = db.scalars(select(TeamMember).where(
        TeamMember.tenant_id == tenant_id, TeamMember.active.is_(True)
    ).order_by(TeamMember.created_at)).all()
    db.commit()
    return {
        "actor": {"display_name": member.display_name, "role": member.role, "permissions": member.permissions},
        "current": _revision_view(current, include_content=True),
        "revisions": [_revision_view(item) for item in revisions],
        "members": [{
            "id": item.id, "account": item.account, "display_name": item.display_name,
            "role": item.role, "permissions": item.permissions, "active": item.active,
        } for item in members],
    }


@router.get("/rules/revisions/{revision_id}")
def get_revision(revision_id: str, tenant_id: str = Depends(demo_auth),
                 db: Session = Depends(get_db)) -> dict:
    item = db.get(RuleRevision, revision_id)
    if not item or item.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    return {"revision": _revision_view(item, include_content=True)}


@router.get("/rules/revisions/{revision_id}/documents/{asset}")
def get_rule_document(
    revision_id: str,
    asset: Literal["cold_start", "dialogue", "schema", "enneagram"],
                      tenant_id: str = Depends(demo_auth), db: Session = Depends(get_db)) -> dict:
    item = db.get(RuleRevision, revision_id)
    if not item or item.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    content = item.canonical_json.get(asset)
    if not isinstance(content, dict):
        raise HTTPException(status_code=422, detail="规则文档结构不完整")
    return {
        "asset": asset,
        "document_text": yaml.safe_dump(
            content, allow_unicode=True, sort_keys=False, width=110, default_flow_style=False
        ),
    }


@router.post("/rules/documents/parse")
def parse_rule_document(body: RuleDocumentParse, tenant_id: str = Depends(demo_auth),
                        x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                        db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "rules.edit")
    try:
        content = yaml.safe_load(body.document_text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f"第 {mark.line + 1} 行、第 {mark.column + 1} 列" if mark else "文档中"
        raise HTTPException(status_code=422, detail=f"{location}存在格式问题，请检查缩进和标点") from exc
    if not isinstance(content, dict):
        raise HTTPException(status_code=422, detail="完整规则文档最外层必须是一个章节结构")
    return {"asset": body.asset, "content": content}


@router.post("/rules/documents/dump")
def dump_rule_document(body: RuleDocumentDump, tenant_id: str = Depends(demo_auth)) -> dict:
    return {
        "asset": body.asset,
        "document_text": yaml.safe_dump(
            body.content, allow_unicode=True, sort_keys=False, width=110, default_flow_style=False
        ),
    }


@router.post("/rules/drafts")
def create_draft(body: DraftCreate, request: Request, tenant_id: str = Depends(demo_auth),
                 x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                 db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "rules.edit")
    base = db.get(RuleRevision, body.base_revision_id) if body.base_revision_id else _ensure_rule_revision(db, tenant_id, request)
    if not base or base.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="基线规则版本不存在")
    item = RuleRevision(
        tenant_id=tenant_id, revision_no=_next_revision_no(db, tenant_id), title=body.title,
        status="draft", base_rule_pack_id=base.base_rule_pack_id, canonical_json=clone_profile(base.canonical_json),
        validation_report=_validate_rules(base.canonical_json), change_summary="",
        created_by=actor,
    )
    db.add(item)
    db.commit()
    return {"revision": _revision_view(item, include_content=True)}


@router.put("/rules/drafts/{revision_id}")
def save_draft(revision_id: str, body: DraftSave, tenant_id: str = Depends(demo_auth),
               x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
               db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "rules.edit")
    item = db.get(RuleRevision, revision_id)
    if not item or item.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="规则草稿不存在")
    if item.status != "draft":
        raise HTTPException(status_code=409, detail="只有草稿可以编辑")
    item.canonical_json = body.canonical_json
    item.change_summary = body.change_summary
    item.validation_report = _validate_rules(body.canonical_json)
    item.created_by = actor
    db.commit()
    return {"revision": _revision_view(item, include_content=True)}


@router.post("/rules/revisions/{revision_id}/submit")
def submit_revision(revision_id: str, body: RuleAction, tenant_id: str = Depends(demo_auth),
                    x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                    db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "rules.edit")
    item = db.get(RuleRevision, revision_id)
    if not item or item.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    report = _validate_rules(item.canonical_json)
    item.validation_report = report
    if not report["valid"]:
        raise HTTPException(status_code=422, detail={"message": "规则检查未通过", "report": report})
    item.status = "pending_review"
    item.submitted_at = datetime.now(timezone.utc)
    item.change_summary = body.note or item.change_summary
    db.commit()
    return {"revision": _revision_view(item, include_content=True)}


@router.post("/rules/revisions/{revision_id}/approve")
def approve_revision(revision_id: str, body: RuleAction, tenant_id: str = Depends(demo_auth),
                     x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                     db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "rules.review")
    item = db.get(RuleRevision, revision_id)
    if not item or item.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    if item.status != "pending_review":
        raise HTTPException(status_code=409, detail="规则版本当前不在待审核状态")
    item.status, item.reviewed_by = "approved", actor
    if body.note:
        item.change_summary = body.note
    db.commit()
    return {"revision": _revision_view(item, include_content=True)}


def _publish_revision(db: Session, request: Request, item: RuleRevision, actor: str) -> RulePack:
    report = _validate_rules(item.canonical_json)
    if not report["valid"]:
        raise HTTPException(status_code=422, detail={"message": "规则检查未通过", "report": report})
    serialized = json.dumps(item.canonical_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    for revision in db.scalars(select(RuleRevision).where(
        RuleRevision.tenant_id == item.tenant_id, RuleRevision.status == "published"
    )):
        revision.status = "superseded"
    pack = db.scalar(select(RulePack).where(RulePack.sha256 == digest))
    for old in db.scalars(select(RulePack).where(RulePack.status == "published")):
        old.status = "superseded"
    if pack:
        pack.status, pack.published_at = "published", datetime.now(timezone.utc)
    else:
        pack = RulePack(
            version=f"workspace-r{item.revision_no}", sha256=digest, status="published",
            canonical_json=item.canonical_json, validation_report=report,
            published_at=datetime.now(timezone.utc),
        )
        db.add(pack)
        db.flush()
    item.status = "published"
    item.reviewed_by = item.reviewed_by or actor
    item.published_at = datetime.now(timezone.utc)
    item.validation_report = report
    item.base_rule_pack_id = pack.id
    request.app.state.rule_pack_id = pack.id
    db.add(AuditLog(
        request_id=request.state.request_id, tenant_id=item.tenant_id, action="rules.publish",
        before=None, after={"revision_id": item.id, "revision_no": item.revision_no, "sha256": digest},
        evidence_refs=[], rule_ids=[pack.version], actor=actor,
    ))
    return pack


@router.post("/rules/revisions/{revision_id}/publish")
def publish_revision(revision_id: str, body: RuleAction, request: Request,
                     tenant_id: str = Depends(demo_auth),
                     x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                     db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "rules.publish")
    item = db.get(RuleRevision, revision_id)
    if not item or item.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    if item.status != "approved":
        raise HTTPException(status_code=409, detail="规则必须先通过审核才能发布")
    if body.note:
        item.change_summary = body.note
    pack = _publish_revision(db, request, item, actor)
    db.commit()
    return {"revision": _revision_view(item, include_content=True),
            "rule_pack": {"version": pack.version, "sha256": pack.sha256, "status": pack.status}}


@router.post("/rules/revisions/{revision_id}/rollback")
def rollback_revision(revision_id: str, body: RuleAction, request: Request,
                      tenant_id: str = Depends(demo_auth),
                      x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                      db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "rules.publish")
    source = db.get(RuleRevision, revision_id)
    if not source or source.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    restored = RuleRevision(
        tenant_id=tenant_id, revision_no=_next_revision_no(db, tenant_id),
        title=f"回滚至 r{source.revision_no}", status="approved",
        base_rule_pack_id=source.base_rule_pack_id, canonical_json=clone_profile(source.canonical_json),
        validation_report=_validate_rules(source.canonical_json),
        change_summary=body.note or f"由 {actor} 回滚至 r{source.revision_no}",
        created_by=actor, reviewed_by=actor,
    )
    db.add(restored)
    db.flush()
    pack = _publish_revision(db, request, restored, actor)
    db.commit()
    return {"revision": _revision_view(restored, include_content=True),
            "rule_pack": {"version": pack.version, "sha256": pack.sha256, "status": pack.status}}


def _flatten(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            result.update(_flatten(child, child_path))
        return result
    if isinstance(value, list):
        return {path: value}
    return {path: value}


@router.get("/rules/compare")
def compare_rules(left: str, right: str, tenant_id: str = Depends(demo_auth),
                  db: Session = Depends(get_db)) -> dict:
    a, b = db.get(RuleRevision, left), db.get(RuleRevision, right)
    if not a or not b or a.tenant_id != tenant_id or b.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="比较版本不存在")
    fa, fb = _flatten(a.canonical_json), _flatten(b.canonical_json)
    changes = []
    for path in sorted(set(fa) | set(fb)):
        if fa.get(path) != fb.get(path):
            changes.append({"path": path, "before": fa.get(path), "after": fb.get(path)})
    return {"left": _revision_view(a), "right": _revision_view(b), "changes": changes[:500],
            "change_count": len(changes)}


def _simulate(text: str, canonical: dict, profile: dict | None = None) -> dict:
    if profile:
        traits = flattened_traits(profile)
        catalog = {key: {"label": TRAIT_NAMES.get(key, key), "current_value": item["value"],
                         "current_confidence": item["confidence"]} for key, item in traits.items()}
    else:
        categories = canonical.get("schema", {}).get("canonical_profile", {}).get("core_traits", {}).get("categories", {})
        catalog = {key: {"label": TRAIT_NAMES.get(key, key), "current_value": .5, "current_confidence": .1}
                   for category in categories.values() for key in category.get("fields", {})}
    analysis = get_semantic_extractor().analyze(text, trait_catalog=catalog, recent_turns=[])
    mappings = canonical.get("dialogue", {}).get("trait_mapping_rules", {})
    changes, hits = [], []
    for signal in analysis.trait_signals:
        if signal.target_trait not in catalog or signal.target_trait not in mappings:
            continue
        before = catalog[signal.target_trait]["current_value"]
        delta = min(.06, .06 * signal.confidence * signal.strength)
        after = max(0, min(1, before + (delta if signal.direction == "increase" else -delta)))
        changes.append({"field": signal.target_trait, "before": before, "after": round(after, 4),
                        "confidence": signal.confidence})
        hits.append({"rule": f"trait_mapping_rules.{signal.target_trait}", "evidence": signal.supporting_span})
    guidance = analysis.reply_guidance.model_dump()
    return {
        "understanding": [frame.model_dump() for frame in analysis.frames],
        "rule_hits": hits,
        "profile_changes": changes,
        "reply_strategy": guidance,
        "extractor_version": get_semantic_extractor().version,
    }


@router.post("/rules/test")
def test_rules(body: RuleTestRequest, request: Request, tenant_id: str = Depends(demo_auth),
               db: Session = Depends(get_db)) -> dict:
    current = _ensure_rule_revision(db, tenant_id, request)
    candidate = db.get(RuleRevision, body.revision_id) if body.revision_id else current
    if not candidate or candidate.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="测试规则版本不存在")
    profile = get_profile(db, tenant_id, body.user_id)["profile"] if body.user_id else None
    before_versions = db.scalar(select(func.count()).select_from(ProfileVersion))
    old_result = _simulate(body.text, current.canonical_json, profile)
    candidate_result = _simulate(body.text, candidate.canonical_json, profile)
    after_versions = db.scalar(select(func.count()).select_from(ProfileVersion))
    return {
        "isolated": True,
        "production_profile_unchanged": before_versions == after_versions,
        "production": {"revision": _revision_view(current), **old_result},
        "candidate": {"revision": _revision_view(candidate), **candidate_result},
    }


@router.post("/members")
def create_member(body: TeamMemberRequest, tenant_id: str = Depends(demo_auth),
                  x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
                  db: Session = Depends(get_db)) -> dict:
    actor = _actor(x_actor_name)
    _require(db, tenant_id, actor, "members.manage")
    existing = db.scalar(select(TeamMember).where(
        TeamMember.tenant_id == tenant_id, TeamMember.account == body.account
    ))
    if existing:
        existing.display_name, existing.role = body.display_name, body.role
        existing.permissions, existing.active = ROLE_PERMISSIONS[body.role], True
        item = existing
    else:
        item = TeamMember(tenant_id=tenant_id, account=body.account, display_name=body.display_name,
                          role=body.role, permissions=ROLE_PERMISSIONS[body.role])
        db.add(item)
    db.commit()
    return {"member": {"id": item.id, "account": item.account, "display_name": item.display_name,
                       "role": item.role, "permissions": item.permissions, "active": item.active}}
