from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_catalog import ModelProvider


class Consent(BaseModel):
    profile: bool
    sensitive_inference: bool = False


class EnneagramIdentityInput(BaseModel):
    core_type: int = Field(ge=1, le=9)
    wing: int | None = Field(default=None, ge=1, le=9)
    primary_instinct: Literal["SP", "SX", "SO"]
    secondary_instinct: Literal["SP", "SX", "SO"]
    source: Literal["user_supplied", "external_assessment", "expert_confirmed"] = "user_supplied"
    confidence: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def validate_combination(self):
        adjacency = {
            1: {9, 2}, 2: {1, 3}, 3: {2, 4}, 4: {3, 5}, 5: {4, 6},
            6: {5, 7}, 7: {6, 8}, 8: {7, 9}, 9: {8, 1},
        }
        if self.wing is not None and self.wing not in adjacency[self.core_type]:
            raise ValueError("侧翼必须是主型的相邻类型")
        if self.primary_instinct == self.secondary_instinct:
            raise ValueError("第一本能和第二本能不能相同")
        return self


class ProfileInitRequest(BaseModel):
    tenant_user_id: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)
    birth_date: date | None = None
    birth_time: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    enneagram: EnneagramIdentityInput | None = None
    consent: Consent


class MessageContext(BaseModel):
    topic: str | None = Field(default=None, max_length=256)
    previous_turn_count: int = Field(default=0, ge=0)
    recent_turns: list["ConversationTurn"] = Field(default_factory=list, max_length=12)


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class MessageIngestRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    expected_profile_version: int = Field(ge=1)
    occurred_at: datetime
    text: str = Field(min_length=1, max_length=10000)
    model_provider: ModelProvider | None = None
    context: MessageContext = Field(default_factory=MessageContext)


class CorrectionRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)
    target_path: str = Field(min_length=1, max_length=512)
    value: Any
    reason: str = Field(min_length=1, max_length=1000)


class SetEnneagramRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)
    enneagram: EnneagramIdentityInput
    reason: str = Field(min_length=1, max_length=1000)


class ForgetRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)
    scope: Literal["memory", "evidence", "birth_inference", "enneagram", "all_profile"]
    target_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_target_for_item_scope(self):
        if self.scope in {"memory", "evidence"} and not self.target_id:
            raise ValueError("memory/evidence scope requires target_id")
        return self


class ResetProfileRequest(BaseModel):
    """Explicit confirmation payload for destructive demo-user resets."""

    confirm: Literal[True]
    display_name: str | None = Field(default=None, max_length=256)


class DeleteProfileRequest(BaseModel):
    """Explicit confirmation for permanent profile and conversation deletion."""

    expected_profile_version: int = Field(ge=1)
    confirm: Literal[True]
    reason: str = Field(min_length=1, max_length=1000)


class SemanticFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(min_length=1, max_length=64)
    subject: Literal["user", "other_person", "robot", "group", "unknown"]
    predicate: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    object: str | None = Field(default=None, max_length=500)
    semantic_domain: Literal[
        "identity_fact", "preference", "habit", "decision", "task_behavior",
        "social_behavior", "relationship_behavior", "emotion", "self_evaluation",
        "event", "communication_behavior", "correction", "hypothetical", "quotation",
    ]
    polarity: Literal["positive", "negative", "neutral"] = "neutral"
    negated: bool = False
    modality: Literal["asserted", "uncertain", "desired", "obligated", "hypothetical", "quoted"] = "asserted"
    temporal_scope: Literal["now", "recent", "habitual", "historical", "future", "unknown"] = "unknown"
    frequency: Literal["once", "sometimes", "often", "usually", "always", "never", "unknown"] = "unknown"
    context: Literal["work", "family", "friendship", "romantic", "stranger", "conflict", "stress", "leisure", "general", "unknown"] = "general"
    explicitness: float = Field(ge=0, le=1)
    extractor_confidence: float = Field(ge=0, le=1)
    supporting_span: str = Field(min_length=1, max_length=500)


class TraitSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_trait: str = Field(min_length=1, max_length=64)
    direction: Literal["increase", "decrease"]
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_scope: Literal["explicit_self_report", "repeated_behavior", "single_behavior_inference"]
    supporting_span: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=500)


class ReplyGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(default="conversation", max_length=100)
    tone: str = Field(default="natural", max_length=100)
    empathy_first: bool = False
    answer_first: bool = False
    max_sentences: int = Field(default=4, ge=1, le=8)
    question_count: int = Field(default=0, ge=0, le=2)
    structure_level: Literal["simple", "steps", "flexible_options"] = "simple"
    focus: str = Field(default="respond_to_current_message", max_length=300)
    avoid: list[str] = Field(default_factory=list, max_length=8)
    requires_fresh_information: bool = False


class SemanticAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frames: list[SemanticFrame] = Field(default_factory=list, max_length=12)
    trait_signals: list[TraitSignal] = Field(default_factory=list, max_length=4)
    reply_guidance: ReplyGuidance = Field(default_factory=ReplyGuidance)


class ErrorBody(BaseModel):
    request_id: str
    code: str
    message: str
    details: dict = Field(default_factory=dict)
