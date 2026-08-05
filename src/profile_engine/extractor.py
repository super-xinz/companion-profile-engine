from __future__ import annotations

import re
import uuid
import json
from typing import Protocol

import httpx

from .config import get_settings
from .model_catalog import ModelProvider
from .model_gateway import (ModelConfigurationError, ModelEndpoint,
                            chat_completion, get_model_endpoint)
from .schemas import ReplyGuidance, SemanticAnalysis, SemanticFrame, TraitSignal


FREQUENCY_MARKERS = (("总是", "always"), ("每次", "always"), ("一直", "usually"), ("通常", "usually"), ("一般", "usually"), ("经常", "often"), ("有时", "sometimes"))
NOW_MARKERS = ("今天", "现在", "刚刚", "这会儿", "此刻")
OTHER_MARKERS = ("我朋友", "朋友说", "他说", "她说", "别人", "同事他", "同事她")
UNCERTAIN_MARKERS = ("可能", "也许", "好像", "不确定")


PREDICATES = [
    ("socializing_requires_solitude_recovery", ("聚会后", "很多人待完", "社交后"), ("独处", "自己待", "很累", "恢复"), "habit"),
    ("likes_social_gathering", ("喜欢聚会", "爱聚会", "喜欢社交"), (), "social_behavior"),
    ("prefers_planning", ("喜欢计划", "列计划", "有计划", "讨厌计划", "不讨厌计划"), (), "task_behavior"),
    ("uses_data_for_decisions", ("数据列出来", "看数据", "依据数据"), (), "decision"),
    ("needs_empathy_before_advice", ("别一上来给建议", "先听我", "先安慰", "听我把话说完"), (), "preference"),
    ("prefers_short_responses", ("回答短一点", "说短一点", "简短一点", "别太长"), (), "preference"),
    ("low_energy", ("很累", "没精力", "精疲力尽", "只想休息"), (), "emotion"),
    ("high_stress", ("压力很大", "焦虑", "快崩溃", "很紧张"), (), "emotion"),
    ("dislikes_humor", ("别开玩笑", "不要幽默", "不喜欢玩笑"), (), "preference"),
]


class SemanticExtractorError(RuntimeError):
    pass


class SemanticExtractor(Protocol):
    version: str
    def analyze(self, text: str, trait_catalog: dict | None = None,
                recent_turns: list[dict] | None = None) -> SemanticAnalysis: ...
    def extract(self, text: str) -> list[SemanticFrame]: ...


class DeterministicSemanticExtractor:
    """Auditable POC extractor; replaceable by an LLM adapter returning the same schema."""

    version = "deterministic-zh-v1"

    def analyze(self, text: str, trait_catalog: dict | None = None,
                recent_turns: list[dict] | None = None) -> SemanticAnalysis:
        subject = "other_person" if any(x in text for x in OTHER_MARKERS) else "user"
        frequency = next((value for marker, value in FREQUENCY_MARKERS if marker in text), "unknown")
        temporal = "now" if any(x in text for x in NOW_MARKERS) else ("habitual" if frequency != "unknown" else "unknown")
        modality = "uncertain" if any(x in text for x in UNCERTAIN_MARKERS) else "asserted"
        explicitness = 0.65 if modality == "uncertain" else 0.92
        frames: list[SemanticFrame] = []
        for predicate, primary, secondary, domain in PREDICATES:
            if any(cue in text for cue in primary) and (not secondary or any(cue in text for cue in secondary)):
                negated = bool(re.search(rf"(?:不|别|不要).{{0,4}}(?:{'|'.join(map(re.escape, primary))})", text))
                if "不是不喜欢" in text or "不讨厌" in text:
                    negated = False
                frames.append(SemanticFrame(
                    frame_id=f"frm_{uuid.uuid4().hex}", subject=subject, predicate=predicate,
                    semantic_domain=domain, polarity="negative" if negated else "positive", negated=negated,
                    modality=modality, temporal_scope=temporal, frequency=frequency,
                    context="work" if "工作" in text else ("leisure" if "周末" in text or "出去玩" in text else "general"),
                    explicitness=explicitness, extractor_confidence=0.88, supporting_span=text[:500],
                ))
        if not frames and subject == "user" and re.search(r"(发生|明天|昨天|下周|医院|会议|见了|去了)", text):
            frames.append(SemanticFrame(
                frame_id=f"frm_{uuid.uuid4().hex}", subject="user", predicate="event_mentioned",
                semantic_domain="event", temporal_scope="recent", frequency="once", context="general",
                explicitness=0.8, extractor_confidence=0.7, supporting_span=text[:500],
            ))
        signal_routes = {
            "socializing_requires_solitude_recovery": ("extroversion", "decrease"),
            "likes_social_gathering": ("extroversion", "increase"),
            "prefers_planning": ("structure_pref", "increase"),
            "uses_data_for_decisions": ("thinking_ratio", "increase"),
        }
        signals = []
        for frame in frames:
            route = signal_routes.get(frame.predicate)
            if route and frame.subject == "user":
                signals.append(TraitSignal(
                    target_trait=route[0], direction=route[1], strength=.8,
                    confidence=frame.extractor_confidence,
                    evidence_scope="explicit_self_report" if frame.temporal_scope == "habitual" else "single_behavior_inference",
                    supporting_span=frame.supporting_span, rationale=f"确定性回归规则：{frame.predicate}",
                ))
        guidance = ReplyGuidance(
            intent="knowledge_question" if text.rstrip().endswith(("?", "？", "吗")) else "conversation",
            answer_first=text.rstrip().endswith(("?", "？", "吗")),
            empathy_first=any(x in text for x in ("难过", "焦虑", "压力", "很累")),
            max_sentences=3 if any(x in text for x in ("短一点", "简短")) else 5,
            question_count=0,
            focus="直接回应用户当前表达",
        )
        return SemanticAnalysis(frames=frames, trait_signals=signals, reply_guidance=guidance)

    def extract(self, text: str) -> list[SemanticFrame]:
        return self.analyze(text).frames


MODEL_SYSTEM_PROMPT = """你是陪伴机器人画像引擎的通用理解层。你提出结构化候选，但不能直接修改画像。
请输出一个 JSON 对象，顶层必须且只能包含 frames、trait_signals、reply_guidance。
frames 中 frame_id 由服务端生成，不要输出。每个 frame 必须包含：
subject(user|other_person|robot|group|unknown), predicate, object(null或字符串),
semantic_domain(identity_fact|preference|habit|decision|task_behavior|social_behavior|relationship_behavior|emotion|self_evaluation|event|communication_behavior|correction|hypothetical|quotation),
polarity(positive|negative|neutral), negated(布尔), modality(asserted|uncertain|desired|obligated|hypothetical|quoted),
temporal_scope(now|recent|habitual|historical|future|unknown), frequency(once|sometimes|often|usually|always|never|unknown),
context(work|family|friendship|romantic|stranger|conflict|stress|leisure|general|unknown),
explicitness(0到1), extractor_confidence(0到1), supporting_span(原文中的必要短片段)。
predicate 是机器可读的英文标识。遇到下列含义时必须使用对应的规范值：
- 社交/聚会后需要独处恢复：socializing_requires_solitude_recovery
- 喜欢聚会或社交：likes_social_gathering
- 喜欢制定计划：prefers_planning
- 用数据辅助决定：uses_data_for_decisions
- 希望先共情、倾听再建议：needs_empathy_before_advice
- 希望回复简短：prefers_short_responses
- 当前疲惫或没精力：low_energy
- 当前压力大或焦虑：high_stress
- 不喜欢玩笑或幽默：dislikes_humor
其他含义也使用简短 snake_case 英文标识，不要用中文自然语言作 predicate。
identity_fact 建议使用 name、education_institution、education_status、occupation、location 等稳定 predicate，并把事实值放在 object。

trait_signals 只能引用用户消息中有直接证据支持的现有画像维度。每项包含：
target_trait, direction(increase|decrease), strength(0到1), confidence(0到1),
evidence_scope(explicit_self_report|repeated_behavior|single_behavior_inference), supporting_span, rationale。
规则：最多4项；知识问答、身份事实、别人行为、引用、假设和单纯短期状态不得推断长期人格；不要为了产生变化而强行输出。
用户对机器人回复方式的要求（例如“回答短一点”“先听我说完”“别开玩笑”）属于交互偏好，绝不能据此生成
assertiveness、confidence、empathy 或任何其他长期人格 trait_signal；设置边界也不能被当成高果断性或高自信。
每个 trait_signal 必须能对应到 frames 中同一原文片段的 user + asserted 长期习惯、自我评价或真实行为帧；
如果只有 preference、communication_behavior、emotion、event 或 identity_fact 帧，则 trait_signals 必须为空。
同一句话可以影响多个维度，但必须逐项说明依据。画像维度目录会随请求提供，禁止创造目录外字段。

reply_guidance 必须包含：intent, tone, empathy_first, answer_first, max_sentences(1到8),
question_count(0到2), structure_level(simple|steps|flexible_options), focus, avoid(字符串数组), requires_fresh_information(布尔)。
回答策略应结合当前意图；涉及“最近、行情、价格、政策、风口”等时 requires_fresh_information=true。
question_count 默认必须为0。只有回答确实依赖缺失信息，或用户明确邀请继续探索时才设为1；不要为了延续对话而追问，
不要连续使用“你觉得呢”“你希望……还是……”或“要不要……”式收尾。普通分享、情绪表达和闲聊应允许自然回应后结束。

必须正确处理否定、双重否定、转折、引用、别人作为主体、短期状态和长期习惯。无法确认时降低置信度；知识问答的 frames 和 trait_signals 可为空。
输出必须是可解析 JSON，不要使用 Markdown。"""


def _decode_json_object(content: str) -> dict:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("模型结果不是 JSON 对象")
    return payload


class ModelSemanticExtractor:
    def __init__(self, endpoint: ModelEndpoint):
        self.endpoint = endpoint
        self.version = f"{endpoint.provider}:{endpoint.model}:universal-v2"

    def analyze(self, text: str, trait_catalog: dict | None = None,
                recent_turns: list[dict] | None = None) -> SemanticAnalysis:
        try:
            user_payload = {
                "current_message": text,
                "recent_turns": (recent_turns or [])[-8:],
                "allowed_profile_dimensions": trait_catalog or {},
            }
            content, _ = chat_completion(
                self.endpoint,
                [{"role": "system", "content": MODEL_SYSTEM_PROMPT},
                 {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
                temperature=0.1,
                max_tokens=2000,
                json_response=True,
            )
            payload = _decode_json_object(content)
            raw_frames = payload.get("frames", [])
            if not isinstance(raw_frames, list):
                raise ValueError("frames 不是数组")
            frames = []
            for raw in raw_frames:
                if not isinstance(raw, dict):
                    continue
                # Model-generated identifiers are neither stable nor trustworthy (some
                # Some model responses use integers. Generate a collision-resistant audit ID
                # at the service boundary instead of rejecting otherwise valid frames.
                raw["frame_id"] = f"frm_{uuid.uuid4().hex}"
                frames.append(SemanticFrame.model_validate(raw))
            analysis_payload = {
                "frames": [frame.model_dump() for frame in frames],
                "trait_signals": payload.get("trait_signals", []),
                "reply_guidance": payload.get("reply_guidance", {}),
            }
            return SemanticAnalysis.model_validate(analysis_payload)
        except httpx.HTTPStatusError as exc:
            raise SemanticExtractorError(
                f"{self.endpoint.label} 语义抽取失败: HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ModelConfigurationError, KeyError, IndexError,
                json.JSONDecodeError, ValueError) as exc:
            raise SemanticExtractorError(
                f"{self.endpoint.label} 语义抽取失败: {type(exc).__name__}"
            ) from exc

    def extract(self, text: str) -> list[SemanticFrame]:
        return self.analyze(text).frames


def get_semantic_extractor(provider: ModelProvider | None = None) -> SemanticExtractor:
    settings = get_settings()
    if provider is None and settings.semantic_extractor == "deterministic":
        return DeterministicSemanticExtractor()
    if provider is not None or settings.semantic_extractor == "model":
        if not settings.allow_external_semantic_processing:
            raise SemanticExtractorError(
                "外部模型会处理用户原话；需明确设置 PROFILE_ALLOW_EXTERNAL_SEMANTIC_PROCESSING=true"
            )
        try:
            endpoint = get_model_endpoint(provider)
        except ModelConfigurationError as exc:
            raise SemanticExtractorError(str(exc)) from exc
        if not endpoint.api_key:
            raise SemanticExtractorError(f"已选择 {endpoint.label}，但服务器未配置对应 API Key")
        return ModelSemanticExtractor(endpoint)
    raise SemanticExtractorError(f"未知语义抽取器: {settings.semantic_extractor}")
