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
from .model_gateway import (ModelConfigurationError, ModelProvider,
                            chat_completion, get_model_endpoint)
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
    model_provider: ModelProvider = "deepseek"
    history: list[DemoHistoryItem] = Field(default_factory=list, max_length=12)


def demo_auth(x_demo_code: str = Header(alias="X-Demo-Code")) -> str:
    settings = get_settings()
    if not settings.demo_features_active:
        raise HTTPException(status_code=404, detail="Demo 功能未启用")
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
        return "听起来这件事确实让你有些不好受。先不用急着把它解决，我在这儿陪你缓一缓。"
    if hints.get("allow_resume_later"):
        return "听起来你现在有点累，先不用急着解决所有事情。等你想继续的时候，我们再接着聊。"
    return f"我听到了。关于“{text[:32]}”，我们就像朋友一样慢慢聊，不用急着马上得出结论。"


def _generate_reply(text: str, history: list[DemoHistoryItem], profile: dict,
                    engine: dict, provider: ModelProvider) -> tuple[str, str]:
    hints = engine["reply_hints"]
    try:
        endpoint = get_model_endpoint(provider)
    except ModelConfigurationError:
        return _fallback_reply(text, hints), "fallback-v1"
    if not endpoint.api_key:
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
        "参数、置信度或内部分析，也不要把用户定性。优先回应用户当下表达。默认不提问，也不要为了延续对话强行追问；"
        "只有回答确实缺少关键条件，或用户明显想继续展开时，才自然问一个问题。不要每次都用问题结尾。"
        "像朋友聊天一样，可以直接回应、分享看法、轻微调侃或自然停住；避免反复使用‘听起来……你觉得呢/你希望哪种/要不要’模板。"
        "除非用户要求方案或内容本身需要步骤，否则使用自然短段落，不加标题、清单、总结或固定的共情—分析—追问结构。"
        "执行优先级是：安全要求和用户当前明确诉求 > 当前状态与明确偏好 > turn_plan中的本轮活跃模块 > 长期画像。"
        "turn_plan和场景案例只用于决定目标、禁区与表达结构，不能复制成固定话术，也不能强化用户的防御模式。"
        "除非用户明确要求，避免说教、诊断和长篇建议。只把已提交的事实当作长期记忆；不要根据学校、职业等身份做刻板推断。"
        "如果 requires_fresh_information=true，而系统没有提供检索结果，必须明确说明信息可能不是实时的，不得声称‘最近’或‘当前行情’。内部策略如下：\n"
        + json.dumps(internal_context, ensure_ascii=False)
    )
    messages = [{"role": "system", "content": system}]
    messages.extend({"role": item.role, "content": item.content} for item in history[-10:])
    if not history or history[-1].role != "user" or history[-1].content != text:
        messages.append({"role": "user", "content": text})
    try:
        reply, resolved_model = chat_completion(
            endpoint,
            messages,
            temperature=0.65,
            max_tokens=320,
        )
        return reply, f"{provider}:{resolved_model}"
    except (httpx.HTTPError, ModelConfigurationError, KeyError, IndexError, ValueError):
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
        model_provider=body.model_provider,
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
        engine["strategy_trace"]["semantic_fallback"] = f"{body.model_provider}_unavailable"
    current = get_profile(db, tenant_id, body.user_id)["profile"]
    reply, responder = _generate_reply(body.text, body.history, current, engine, body.model_provider)
    engine["strategy_trace"]["model_provider"] = body.model_provider
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
