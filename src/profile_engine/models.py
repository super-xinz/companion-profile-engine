from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "tenant_user_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("usr"))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_user_id: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    birth_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    birth_time: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    timezone_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    profile_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitive_inference_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    inference_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["ProfileVersion"]] = relationship(back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class ProfileVersion(Base):
    __tablename__ = "profile_versions"
    __table_args__ = (UniqueConstraint("user_id", "version_no"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("prv"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(32))
    cold_start_rule_pack_version: Mapped[str] = mapped_column(String(64))
    dialogue_rule_pack_version: Mapped[str] = mapped_column(String(64))
    overall_confidence: Mapped[float] = mapped_column(Float)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="versions")


class ProfileEvidence(Base):
    __tablename__ = "profile_evidence"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("evd"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(64))
    source_message_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    semantic_frame: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    target_path: Mapped[str] = mapped_column(String(512), index=True)
    direction: Mapped[int] = mapped_column(Integer)
    base_delta: Mapped[float] = mapped_column(Float)
    impact: Mapped[float] = mapped_column(Float)
    factors: Mapped[dict] = mapped_column(JSON)
    rule_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(Text)
    invalidated: Mapped[bool] = mapped_column(Boolean, default=False)
    invalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("mem"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    memory_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[dict] = mapped_column(JSON)
    source_message_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CurrentState(Base):
    __tablename__ = "current_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sta"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    state_key: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict] = mapped_column(JSON)
    source_message_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuntimePreference(Base):
    __tablename__ = "runtime_preferences"
    __table_args__ = (UniqueConstraint("user_id", "preference_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pre"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    preference_key: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict] = mapped_column(JSON)
    source_message_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("idem"))
    tenant_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(256))
    resource_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RulePack(Base):
    __tablename__ = "rule_packs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rpk"))
    version: Mapped[str] = mapped_column(String(64), index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    canonical_json: Mapped[dict] = mapped_column(JSON)
    validation_report: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("aud"))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    before: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String(64), default="api")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("user_id", "external_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("con"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(String(256), default="新对话")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "external_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    engine_trace: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    profile_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ManualOverride(Base):
    __tablename__ = "manual_overrides"
    __table_args__ = (UniqueConstraint("user_id", "target_path"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ovr"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_path: Mapped[str] = mapped_column(String(512))
    value: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("tenant_id", "account"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("membr"))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    account: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="viewer")
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RuleRevision(Base):
    __tablename__ = "rule_revisions"
    __table_args__ = (UniqueConstraint("tenant_id", "revision_no"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rrv"))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), index=True, default="draft")
    base_rule_pack_id: Mapped[Optional[str]] = mapped_column(ForeignKey("rule_packs.id"), nullable=True)
    canonical_json: Mapped[dict] = mapped_column(JSON)
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
