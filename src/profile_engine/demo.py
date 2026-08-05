from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from .config import get_settings
from .db import get_db
from .extractor import DeterministicSemanticExtractor, SemanticExtractorError
from .model_catalog import ModelProvider
from .model_gateway import (ModelConfigurationError, chat_completion,
                            get_model_endpoint)
from .models import ChatMessage, Conversation, RulePack, User
from .schemas import Consent, ConversationTurn, MessageContext, MessageIngestRequest, ProfileInitRequest
from .security import SlidingWindowRateLimiter, constant_time_equal
from .service import get_profile, ingest_message, init_profile


router = APIRouter(prefix="/demo/api", tags=["demo"])
logger = logging.getLogger(__name__)
_demo_rate_limiter = SlidingWindowRateLimiter()
_demo_model_rate_limiter = SlidingWindowRateLimiter()


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


def demo_auth(request: Request, response: Response,
              x_demo_code: str = Header(alias="X-Demo-Code", max_length=256)) -> str:
    settings = get_settings()
    if not settings.demo_features_active:
        raise HTTPException(status_code=404, detail="Demo 功能未启用")
    expected = settings.demo_access_code
    if not expected and settings.environment == "development":
        expected = "demo"
    source = request.client.host if request.client else "unknown"
    allowed, remaining, retry_after = _demo_rate_limiter.check(
        source, settings.demo_rate_limit_per_minute
    )
    if not allowed:
        raise HTTPException(
            status_code=429, detail="请求过于频繁",
            headers={"Retry-After": str(retry_after)},
        )
    if request.url.path in {"/demo/api/chat", "/demo/api/rules/test"}:
        model_allowed, model_remaining, model_retry_after = _demo_model_rate_limiter.check(
            source, settings.demo_model_rate_limit_per_minute
        )
        if not model_allowed:
            raise HTTPException(
                status_code=429, detail="模型调用过于频繁",
                headers={"Retry-After": str(model_retry_after)},
            )
        response.headers["X-Model-RateLimit-Remaining"] = str(model_remaining)
    response.headers["X-Demo-RateLimit-Remaining"] = str(remaining)
    if not expected or not constant_time_equal(x_demo_code, expected):
        raise HTTPException(status_code=401, detail="访问密码不正确")
    return settings.demo_tenant_id


def _current_pack(request: Request, db: Session) -> RulePack:
    pack = db.get(RulePack, request.app.state.rule_pack_id)
    if not pack:
        pack = db.scalar(select(RulePack).where(RulePack.status == "published").order_by(desc(RulePack.published_at)).limit(1))
    if not pack:
        raise RuntimeError("没有已发布规则包")
    return pack


class ModelNoResponseError(RuntimeError):
    def __init__(self, message: str, *, provider: ModelProvider, model: str,
                 http_status: int | None = None, upstream_message: str | None = None):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.http_status = http_status
        self.upstream_message = upstream_message


def _upstream_error_message(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"].strip()[:500] or None
    if isinstance(error, str):
        return error.strip()[:500] or None
    return None


def _no_response_error(endpoint, exc: Exception) -> ModelNoResponseError:
    status = None
    upstream_message = None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        upstream_message = _upstream_error_message(exc.response)
        reason = f"OpenRouter HTTP {status}"
    elif isinstance(exc, httpx.RequestError):
        reason = f"OpenRouter 网络无响应（{type(exc).__name__}）"
    elif isinstance(exc, ModelConfigurationError):
        reason = str(exc)
    else:
        reason = "OpenRouter 返回内容中没有有效回复"
    message = f"{endpoint.label}（{endpoint.model}）模型无返回：{reason}"
    if upstream_message:
        message += f"；{upstream_message}"
    return ModelNoResponseError(
        message,
        provider=endpoint.provider,
        model=endpoint.model,
        http_status=status,
        upstream_message=upstream_message,
    )


def _generate_reply(text: str, history: list[DemoHistoryItem], profile: dict,
                    engine: dict, provider: ModelProvider) -> tuple[str, str]:
    hints = engine["reply_hints"]
    try:
        endpoint = get_model_endpoint(provider)
    except ModelConfigurationError as exc:
        raise ModelNoResponseError(
            f"{provider} 模型无返回：{exc}", provider=provider, model="未配置"
        ) from exc
    if not endpoint.api_key:
        exc = ModelConfigurationError("未配置 OpenRouter API Key")
        raise _no_response_error(endpoint, exc) from exc
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
    except (httpx.HTTPError, ModelConfigurationError, KeyError, IndexError, ValueError) as exc:
        raise _no_response_error(endpoint, exc) from exc


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
    assistant_message_id = f"assistant_{body.message_id}"
    if existing_message:
        if existing_message.role != "user" or existing_message.content != body.text:
            raise HTTPException(status_code=409, detail="同一 message_id 不能用于不同消息")
        existing_assistant = db.scalar(select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.external_id == assistant_message_id,
        ))
        if existing_assistant:
            cached_engine = existing_assistant.engine_trace or {}
            return {
                "request_id": request.state.request_id,
                "assistant_reply": existing_assistant.content,
                "assistant_message_id": assistant_message_id,
                "chat_responder_version": cached_engine.get("strategy_trace", {}).get(
                    "chat_responder", "cached-response"
                ),
                "engine": cached_engine,
            }
    if not existing_message:
        existing_message = ChatMessage(
            conversation_id=conversation.id, external_id=body.message_id,
            role="user", content=body.text, profile_version=body.expected_profile_version,
        )
        db.add(existing_message)
        if conversation.title == "新的陪伴对话":
            conversation.title = body.text[:24] + ("…" if len(body.text) > 24 else "")
        db.flush()
    engine = existing_message.engine_trace
    if engine is None:
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
    else:
        engine["strategy_trace"].pop("chat_model_error", None)
    current = get_profile(db, tenant_id, body.user_id)["profile"]
    engine["strategy_trace"]["model_provider"] = body.model_provider
    try:
        reply, responder = _generate_reply(body.text, body.history, current, engine, body.model_provider)
    except ModelNoResponseError as exc:
        engine["strategy_trace"]["consumed_by_chatbot"] = False
        engine["strategy_trace"]["chat_responder"] = "no-response"
        engine["strategy_trace"]["chat_model_error"] = {
            "provider": exc.provider,
            "model": exc.model,
            "http_status": exc.http_status,
        }
        existing_message.engine_trace = engine
        existing_message.profile_version = engine["profile_version"]
        conversation.updated_at = datetime.now(timezone.utc)
        db.commit()
        return JSONResponse(status_code=502, content={
            "request_id": request.state.request_id,
            "code": "model_no_response",
            "message": str(exc),
            "details": {
                "provider": exc.provider,
                "model": exc.model,
                "http_status": exc.http_status,
                "upstream_message": exc.upstream_message,
                "profile_version": engine["profile_version"],
                "engine": engine,
            },
        })
    engine["strategy_trace"]["consumed_by_chatbot"] = True
    engine["strategy_trace"]["chat_responder"] = responder
    existing_message.engine_trace = None
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
