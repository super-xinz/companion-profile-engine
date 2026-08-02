from __future__ import annotations

import hmac
import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .extractor import DeterministicSemanticExtractor, SemanticExtractorError
from .models import ChatMessage, Conversation, RulePack, User
from .schemas import Consent, ConversationTurn, MessageContext, MessageIngestRequest, ProfileInitRequest
from .service import get_profile, ingest_message, init_profile


router = APIRouter(prefix="/demo/api", tags=["demo"])
logger = logging.getLogger(__name__)


class DemoStartRequest(BaseModel):
    display_name: str = Field(default="体验用户", min_length=1, max_length=64)
    birth_date: date | None = None


class DemoHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class DemoChatRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    expected_profile_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4000)
    history: list[DemoHistoryItem] = Field(default_factory=list, max_length=12)


def demo_auth(x_demo_code: str = Header(alias="X-Demo-Code")) -> str:
    settings = get_settings()
    expected = settings.demo_access_code
    if not expected and settings.environment == "development":
        expected = "demo"
    if not expected or not hmac.compare_digest(x_demo_code, expected):
        raise HTTPException(status_code=401, detail="访问密码不正确")
    return settings.demo_tenant_id


def _current_pack(request: Request, db: Session) -> RulePack:
    pack = db.get(RulePack, request.app.state.rule_pack_id)
    if not pack:
        pack = db.scalar(select(RulePack).where(RulePack.status == "published").order_by(desc(RulePack.published_at)).limit(1))
    if not pack:
        raise RuntimeError("没有已发布规则包")
    return pack


def _fallback_reply(text: str, hints: dict) -> str:
    if any(marker in text for marker in ("回答短一点", "说短一点", "简短一点", "听我把话说完")):
        return "明白了。之后我会尽量简短，也会先听你说完。你继续。"
    if hints.get("empathy_first"):
        return f"听起来这件事确实让你有些不好受。关于“{text[:32]}”，你更希望我先陪你聊聊，还是一起想个办法？"
    if hints.get("allow_resume_later"):
        return "听起来你现在有点累，我们先不用急着解决所有事情。要不要只说说此刻最困扰你的那一点？"
    return "我听到了。你愿意再多说一点，这件事对你最重要的部分是什么吗？"


def _generate_reply(text: str, history: list[DemoHistoryItem], profile: dict,
                    engine: dict) -> tuple[str, str]:
    settings = get_settings()
    hints = engine["reply_hints"]
    if not settings.qwen_api_key:
        return _fallback_reply(text, hints), "fallback-v1"
    portrait = profile.get("portrait", {})
    digital_code = profile.get("digital_code_profile", {})
    internal_context = {
        "reply_hints": hints,
        "portrait_essence": portrait.get("essence", {}).get("content"),
        "digital_code_profile": {
            "code": digital_code.get("code"),
            "confidence": digital_code.get("confidence"),
            "domain_summaries": {
                key: value.get("summary")
                for key, value in digital_code.get("domains", {}).items()
            },
        } if digital_code.get("status") == "derived" else None,
        "overall_confidence": profile.get("meta", {}).get("overall_confidence"),
        "current_state": profile.get("runtime", {}).get("current_state", {}),
        "interaction_preferences": profile.get("runtime", {}).get("interaction_preferences", {}),
        "committed_memories_and_facts": profile.get("runtime", {}).get("memories", [])[-20:],
        "current_semantic_frames": engine.get("semantic_frames", []),
        "accepted_trait_signals": engine.get("accepted_trait_signals", []),
        "applied_profile_patch": engine.get("profile_patch", []),
        "runtime_operations": engine.get("runtime_operations", []),
        "strategy_trace": engine.get("strategy_trace", {}),
    }
    system = (
        "你是温暖、自然、有边界感的陪伴型聊天机器人。根据内部互动策略回答用户，但绝不能提到画像、规则、"
        "参数、置信度或内部分析，也不要把用户定性。优先回应用户当下表达；信息不足时只问一个自然的问题。"
        "除非用户明确要求，避免说教、诊断和长篇建议。只把已提交的事实当作长期记忆；不要根据学校、职业等身份做刻板推断。"
        "如果 requires_fresh_information=true，而系统没有提供检索结果，必须明确说明信息可能不是实时的，不得声称‘最近’或‘当前行情’。内部策略如下：\n"
        + json.dumps(internal_context, ensure_ascii=False)
    )
    messages = [{"role": "system", "content": system}]
    messages.extend({"role": item.role, "content": item.content} for item in history[-10:])
    if not history or history[-1].role != "user" or history[-1].content != text:
        messages.append({"role": "user", "content": text})
    try:
        response = httpx.post(
            f"{settings.qwen_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.qwen_api_key}", "Content-Type": "application/json"},
            json={"model": settings.qwen_model, "messages": messages, "enable_thinking": False,
                  "temperature": 0.65, "max_tokens": 320},
            timeout=settings.qwen_timeout_seconds,
        )
        response.raise_for_status()
        reply = response.json()["choices"][0]["message"]["content"].strip()
        if not reply:
            raise ValueError("empty reply")
        return reply, f"{settings.qwen_model}-chat"
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return _fallback_reply(text, hints), "fallback-v1"


@router.post("/start")
def demo_start(body: DemoStartRequest, request: Request, tenant_id: str = Depends(demo_auth),
               db: Session = Depends(get_db)) -> dict:
    user_id = f"demo_{uuid.uuid4().hex}"
    init_body = ProfileInitRequest(
        tenant_user_id=user_id,
        display_name=body.display_name,
        birth_date=body.birth_date,
        timezone="Asia/Shanghai",
        consent=Consent(profile=True, sensitive_inference=True),
    )
    response = init_profile(db, tenant_id, init_body, _current_pack(request, db),
                            request.state.request_id, f"demo-start-{user_id}")
    profile = response["profile"]
    user = db.scalar(select(User).where(
        User.tenant_id == tenant_id, User.tenant_user_id == user_id
    ))
    conversation = Conversation(
        user_id=user.id, external_id=f"conv_{uuid.uuid4().hex}", title="新的陪伴对话"
    )
    db.add(conversation)
    db.commit()
    mbti = profile.get("mbti_dimensions", {}).get("type_label")
    if mbti == "XXXX":
        mbti = None
    return {
        "user_id": user_id,
        "profile_version": response["profile_version"],
        "display_name": body.display_name,
        "mbti": mbti,
        "overall_confidence": profile.get("meta", {}).get("overall_confidence"),
        "rule_pack": response["rule_pack"],
        "warnings": response.get("warnings", []),
        "conversation_id": conversation.external_id,
    }


@router.post("/chat")
def demo_chat(body: DemoChatRequest, request: Request, tenant_id: str = Depends(demo_auth),
              db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(
        User.tenant_id == tenant_id, User.tenant_user_id == body.user_id
    ))
    if not user:
        raise HTTPException(status_code=404, detail="人物不存在")
    conversation = db.scalar(select(Conversation).where(
        Conversation.user_id == user.id, Conversation.external_id == body.conversation_id
    ))
    if not conversation:
        conversation = Conversation(
            user_id=user.id, external_id=body.conversation_id,
            title=body.text[:24] + ("…" if len(body.text) > 24 else ""),
        )
        db.add(conversation)
        db.flush()
    existing_message = db.scalar(select(ChatMessage).where(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.external_id == body.message_id,
    ))
    if not existing_message:
        db.add(ChatMessage(
            conversation_id=conversation.id, external_id=body.message_id,
            role="user", content=body.text, profile_version=body.expected_profile_version,
        ))
        if conversation.title == "新的陪伴对话":
            conversation.title = body.text[:24] + ("…" if len(body.text) > 24 else "")
        db.flush()
    message = MessageIngestRequest(
        conversation_id=body.conversation_id,
        message_id=body.message_id,
        expected_profile_version=body.expected_profile_version,
        occurred_at=datetime.now(timezone.utc),
        text=body.text,
        context=MessageContext(
            previous_turn_count=len(body.history),
            recent_turns=[ConversationTurn(role=item.role, content=item.content) for item in body.history[-12:]],
        ),
    )
    try:
        engine = ingest_message(db, tenant_id, body.user_id, message, _current_pack(request, db),
                                request.state.request_id, f"demo-chat-{body.user_id}-{body.message_id}")
    except SemanticExtractorError as exc:
        logger.warning("Semantic extraction unavailable; using deterministic fallback: %s", exc)
        engine = ingest_message(
            db, tenant_id, body.user_id, message, _current_pack(request, db),
            request.state.request_id, f"demo-chat-{body.user_id}-{body.message_id}",
            semantic_extractor=DeterministicSemanticExtractor(),
        )
        engine["strategy_trace"]["semantic_fallback"] = "qwen_unavailable"
    current = get_profile(db, tenant_id, body.user_id)["profile"]
    reply, responder = _generate_reply(body.text, body.history, current, engine)
    engine["strategy_trace"]["consumed_by_chatbot"] = True
    engine["strategy_trace"]["chat_responder"] = responder
    assistant_message_id = f"assistant_{body.message_id}"
    assistant = db.scalar(select(ChatMessage).where(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.external_id == assistant_message_id,
    ))
    if not assistant:
        db.add(ChatMessage(
            conversation_id=conversation.id, external_id=assistant_message_id,
            role="assistant", content=reply, engine_trace=engine,
            profile_version=engine["profile_version"],
        ))
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"request_id": request.state.request_id, "assistant_reply": reply,
            "assistant_message_id": assistant_message_id,
            "chat_responder_version": responder, "engine": engine}
