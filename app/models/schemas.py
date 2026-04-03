from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(str, Enum):
    active = "active"
    paused = "paused"
    revoked = "revoked"


class RequestStatus(str, Enum):
    auto_approved = "auto_approved"
    pending = "pending"
    approved = "approved"
    denied = "denied"
    expired = "expired"
    cancelled = "cancelled"


class RuleType(str, Enum):
    max_per_transaction = "max_per_transaction"
    max_per_day = "max_per_day"
    max_per_month = "max_per_month"
    allowed_vendors = "allowed_vendors"
    blocked_vendors = "blocked_vendors"
    allowed_categories = "allowed_categories"
    blocked_categories = "blocked_categories"
    require_approval_above = "require_approval_above"
    auto_approve_below = "auto_approve_below"


# --- Agent schemas ---

class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    api_key_prefix: str
    status: AgentStatus
    # validation_alias reads the ORM attribute "metadata_" (avoids SQLAlchemy's
    # class-level MetaData). populate_by_name=True still accepts "metadata" key
    # in dict inputs (e.g. model_dump() round-trips and test fixtures).
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AgentCreateResponse(AgentResponse):
    api_key: str  # full key — returned once on creation, never stored or returned again


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    status: Optional[AgentStatus] = None


# --- Rule schemas ---

class RuleCreate(BaseModel):
    rule_type: RuleType
    value: dict[str, Any] = Field(
        ...,
        description=(
            'Rule value payload. Examples: {"amount": 50.0} for amount rules, '
            '{"vendors": ["AWS", "GCP"]} for vendor rules.'
        ),
    )


class RuleResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    rule_type: RuleType
    value: dict[str, Any]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RuleUpdate(BaseModel):
    value: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


# --- Authorization request schemas ---

class AuthorizeRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=100)
    amount: Optional[Decimal] = Field(None, ge=0)
    currency: str = Field(default="USD", max_length=3)
    vendor: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class AuthorizeResponse(BaseModel):
    id: uuid.UUID
    agent_id: Optional[uuid.UUID] = None
    agent_name: Optional[str] = None  # populated via JOIN in list_requests
    action: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    vendor: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    status: RequestStatus
    approval_token: Optional[str] = None
    resolved_by: Optional[str] = None
    rule_evaluation: Optional[dict[str, Any]] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    plan_warning: Optional[str] = None  # set when usage is in overage grace period

    model_config = ConfigDict(from_attributes=True)


class ApproveRequest(BaseModel):
    note: Optional[str] = None


class DenyRequest(BaseModel):
    reason: Optional[str] = None


# --- Rule template schemas ---

class RuleTemplateRule(BaseModel):
    rule_type: RuleType
    value: dict[str, Any]


class RuleTemplate(BaseModel):
    name: str
    description: str
    rules: list[RuleTemplateRule]


# --- Dashboard schemas ---

class DashboardStats(BaseModel):
    total_requests: int
    auto_approved: int
    pending: int
    approved: int
    denied: int
    expired: int
    total_spend: Decimal
    approval_rate: float
    agents_active: int


class ActivityItem(BaseModel):
    id: uuid.UUID
    event_type: str  # mapped from request status (auto_approved, human_approved, etc.)
    agent_name: str
    action: str
    amount: Optional[Decimal]
    vendor: Optional[str]
    description: Optional[str]
    created_at: datetime


# --- Usage schemas ---

class UsageResponse(BaseModel):
    plan: str
    authorizations_this_month: int  # count of POST /v1/authorize calls this calendar month
    requests_limit: Optional[int]   # None = unlimited (field name kept for backwards compat)
    agents_active: int
    agents_limit: Optional[int]     # None = unlimited
