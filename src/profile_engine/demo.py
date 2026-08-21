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
from .public_demo import (public_dynamic_summary, public_metrics, public_preferences,
                          public_update_summary, resolve_public_conversation,
                          resolve_public_user, sanitize_public_text)
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
    public_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    expected_profile_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4000)
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
        model_allowed, _, model_retry_after = _demo_model_rate_limiter.check(
            source, settings.demo_model_rate_limit_per_minute
        )
        if not model_allowed:
            raise HTTPException(
                status_code=429, detail="回应生成请求过于频繁",
                headers={"Retry-After": str(model_retry_after)},
            )
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


def _turn_guidance(engine: dict) -> list[str]:
    hints = engine.get("reply_hints", {})
    guidance: list[str] = []
    max_sentences = hints.get("max_sentences")
    if isinstance(max_sentences, int) and 1 <= max_sentences <= 12:
        guidance.append(f"回复尽量控制在 {max_sentences} 句以内")
    if hints.get("answer_first") is True:
        guidance.append("先直接回应用户当前的问题")
    if hints.get("empathy_first") is True:
        guidance.append("先自然回应用户当下的感受，再给信息或建议")
    question_count = hints.get("question_count")
    if isinstance(question_count, int) and 0 <= question_count <= 3:
        guidance.append(f"最多使用 {question_count} 个必要问题")
    humor_level = hints.get("humor_level")
    if isinstance(humor_level, (int, float)) and not isinstance(humor_level, bool):
        if humor_level <= 0.2:
            guidance.append("避免主动使用玩笑")
        elif humor_level >= 0.7:
            guidance.append("在合适时可以加入轻微幽默")
    return guidance or ["自然、简洁地回应用户当前表达"]


def _generate_reply(text: str, history: list[DemoHistoryItem], profile: dict,
                    engine: dict, provider: ModelProvider) -> tuple[str, str]:
    try:
        endpoint = get_model_endpoint(provider)
    except ModelConfigurationError as exc:
        raise ModelNoResponseError(
            f"{provider} 模型无返回：{exc}", provider=provider, model="未配置"
        ) from exc
    if not endpoint.api_key:
        exc = ModelConfigurationError("未配置 OpenRouter API Key")
        raise _no_response_error(endpoint, exc) from exc
    internal_context = {
        "interaction_summary": public_dynamic_summary(profile),
        "communication_preferences": public_preferences(profile),
        "safe_indicators": [
            {"name": item["name"], "value": item["value"]}
            for item in public_metrics(profile)
        ],
        "turn_guidance": _turn_guidance(engine),
    }
    system = (
        "你是温暖、自然、有边界感的陪伴型聊天机器人。根据内部互动策略回答用户，但绝不能提到画像、规则、"
        "参数、置信度或内部分析，也不要把用户定性。优先回应用户当下表达。默认不提问，也不要为了延续对话强行追问；"
        "只有回答确实缺少关键条件，或用户明显想继续展开时，才自然问一个问题。不要每次都用问题结尾。"
        "像朋友聊天一样，可以直接回应、分享看法、轻微调侃或自然停住；避免反复使用‘听起来……你觉得呢/你希望哪种/要不要’模板。"
        "除非用户要求方案或内容本身需要步骤，否则使用自然短段落，不加标题、清单、总结或固定的共情—分析—追问结构。"
        "执行优先级是：安全要求和用户当前明确诉求 > 当前状态与明确偏好 > 本轮互动指引 > 长期互动倾向。"
        "互动指引只用于决定目标、禁区与表达结构，不能复制成固定话术，也不能强化用户的防御模式。"
        "除非用户明确要求，避免说教、诊断和长篇建议。只把已提交的事实当作长期记忆；不要根据学校、职业等身份做刻板推断。"
        "系统没有提供检索结果时，必须明确说明信息可能不是实时的，不得声称掌握最新动态。"
        "可用的中性互动摘要如下：\n"
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
    provider = get_settings().default_model_provider
    user = resolve_public_user(db, tenant_id, body.public_id)
    conversation = resolve_public_conversation(db, user, body.conversation_id)
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
            profile_version = existing_assistant.profile_version or body.expected_profile_version
            return {
                "assistant_reply": sanitize_public_text(
                    existing_assistant.content,
                    fallback="我在认真听，你可以继续说。",
                ),
                "profile_version": profile_version,
                "update_summary": public_update_summary(
                    profile_version, body.expected_profile_version
                ),
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
            conversation_id=conversation.external_id,
            message_id=body.message_id,
            expected_profile_version=body.expected_profile_version,
            occurred_at=datetime.now(timezone.utc),
            text=body.text,
            model_provider=provider,
            context=MessageContext(
                previous_turn_count=len(body.history),
                recent_turns=[ConversationTurn(role=item.role, content=item.content) for item in body.history[-12:]],
            ),
        )
        try:
            engine = ingest_message(
                db, tenant_id, user.tenant_user_id, message, _current_pack(request, db),
                request.state.request_id,
                f"demo-chat-{user.tenant_user_id}-{body.message_id}",
            )
        except SemanticExtractorError as exc:
            logger.warning("Semantic extraction unavailable; using deterministic fallback: %s", exc)
            engine = ingest_message(
                db, tenant_id, user.tenant_user_id, message, _current_pack(request, db),
                request.state.request_id,
                f"demo-chat-{user.tenant_user_id}-{body.message_id}",
                semantic_extractor=DeterministicSemanticExtractor(),
            )
            engine["strategy_trace"]["semantic_fallback"] = "external_analysis_unavailable"
    else:
        engine["strategy_trace"].pop("chat_model_error", None)
    current = get_profile(db, tenant_id, user.tenant_user_id)["profile"]
    engine["strategy_trace"]["model_provider"] = provider
    try:
        reply, responder = _generate_reply(body.text, body.history, current, engine, provider)
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
            "code": "assistant_temporarily_unavailable",
            "message": "暂时无法生成回复，请稍后重试。",
            "profile_version": engine["profile_version"],
            "update_summary": public_update_summary(
                engine["profile_version"], body.expected_profile_version
            ),
        })
    engine["strategy_trace"]["consumed_by_chatbot"] = True
    engine["strategy_trace"]["chat_responder"] = responder
    existing_message.engine_trace = None
    public_reply = sanitize_public_text(reply, fallback="我在认真听，你可以继续说。")
    db.add(ChatMessage(
        conversation_id=conversation.id, external_id=assistant_message_id,
        role="assistant", content=public_reply, engine_trace=engine,
        profile_version=engine["profile_version"],
    ))
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "assistant_reply": public_reply,
        "profile_version": engine["profile_version"],
        "update_summary": public_update_summary(
            engine["profile_version"], body.expected_profile_version
        ),
    }
