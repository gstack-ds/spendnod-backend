from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    Text,
    TIMESTAMP,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    supabase_auth_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), unique=True, nullable=True
    )
    notification_preferences: Mapped[dict] = mapped_column(
        JSON, server_default=text("""'{"email": true, "sms": false}'::jsonb""")
    )
    plan: Mapped[str] = mapped_column(Text, server_default=text("'free'"), default="free")
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )

    agents: Mapped[list[Agent]] = relationship(
        "Agent", back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship("AuditLog", back_populates="user")


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'revoked')", name="agents_status_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )

    user: Mapped[User] = relationship("User", back_populates="agents")
    rules: Mapped[list[Rule]] = relationship(
        "Rule", back_populates="agent", cascade="all, delete-orphan"
    )
    authorization_requests: Mapped[list[AuthorizationRequest]] = relationship(
        "AuthorizationRequest", back_populates="agent"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship("AuditLog", back_populates="agent")


class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (
        CheckConstraint(
            "rule_type IN ("
            "'max_per_transaction','max_per_day','max_per_month',"
            "'allowed_vendors','blocked_vendors','allowed_categories',"
            "'blocked_categories','require_approval_above','auto_approve_below'"
            ")",
            name="rules_rule_type_check",
        ),
        Index("idx_rules_agent", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    rule_type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("TRUE"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )

    agent: Mapped[Agent] = relationship("Agent", back_populates="rules")


class AuthorizationRequest(Base):
    __tablename__ = "authorization_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('auto_approved','pending','approved','denied','expired','cancelled')",
            name="auth_requests_status_check",
        ),
        Index("idx_auth_requests_agent", "agent_id"),
        Index("idx_auth_requests_status", "status"),
        Index("idx_auth_requests_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(Text, server_default=text("'USD'"))
    vendor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    approval_token: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)
    rule_evaluation: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )

    agent: Mapped[Optional[Agent]] = relationship(
        "Agent", back_populates="authorization_requests"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        "AuditLog", back_populates="request"
    )


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(Text, primary_key=True)
    redirect_uri_prefixes: Mapped[list] = mapped_column(
        JSON, server_default=text("'[]'::jsonb")
    )
    client_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )


class OAuthAuthCode(Base):
    __tablename__ = "oauth_auth_codes"
    __table_args__ = (
        Index("idx_oauth_codes_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    code_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[str] = mapped_column(
        Text, ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    code_challenge: Mapped[str] = mapped_column(Text, nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(
        Text, server_default=text("'S256'")
    )
    scope: Mapped[str] = mapped_column(Text, server_default=text("'authorize'"))
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    __table_args__ = (
        Index("idx_oauth_tokens_user", "user_id"),
        Index("idx_oauth_tokens_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, server_default=text("'authorize'"))
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_log_user", "user_id"),
        Index("idx_audit_log_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("authorization_requests.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )

    user: Mapped[Optional[User]] = relationship("User", back_populates="audit_logs")
    agent: Mapped[Optional[Agent]] = relationship("Agent", back_populates="audit_logs")
    request: Mapped[Optional[AuthorizationRequest]] = relationship(
        "AuthorizationRequest", back_populates="audit_logs"
    )
