"""Tests for usage limit enforcement: request throttle, agent throttle, $10k ceiling, GET /v1/usage."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.database import Agent, AuthorizationRequest
from app.services import usage as usage_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mock_user_result(user) -> MagicMock:
    m = MagicMock()
    m.scalar_one_or_none.return_value = user
    return m


def mock_count_result(n: int) -> MagicMock:
    m = MagicMock()
    m.scalar.return_value = n
    return m


def mock_rules_result(rules: list) -> MagicMock:
    m = MagicMock()
    m.scalars.return_value.all.return_value = rules
    return m


def make_rule_ns(rule_type: str, value: dict) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), rule_type=rule_type, value=value, is_active=True)


def make_stored_req(agent_id: uuid.UUID, decision: str) -> AuthorizationRequest:
    return AuthorizationRequest(
        id=uuid.uuid4(),
        agent_id=agent_id,
        action="purchase",
        amount=Decimal("50.00"),
        currency="USD",
        status=decision,
        rule_evaluation={},
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Request throttle — POST /v1/authorize
# ---------------------------------------------------------------------------

async def test_authorize_under_limit_succeeds(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """Requests well under limit pass through normally."""
    usage_service._cache.reset()
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
        mock_count_result(100),      # 100 of 200 — under limit
        mock_rules_result([]),
    ])
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)
    stored = make_stored_req(mock_agent.id, "pending")

    with patch("app.api.authorize.AuthorizationRequest", return_value=stored):
        response = await agent_client.post(
            "/v1/authorize", json={"action": "purchase", "amount": 10.0}
        )

    assert response.status_code == 202
    assert response.json().get("plan_warning") is None


async def test_authorize_at_limit_warns_but_allows(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """At exactly the limit (200/200), request is allowed but plan_warning is set."""
    usage_service._cache.reset()
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
        mock_count_result(200),      # at limit — grace period applies
        mock_rules_result([]),
    ])
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)
    stored = make_stored_req(mock_agent.id, "pending")

    with patch("app.api.authorize.AuthorizationRequest", return_value=stored):
        response = await agent_client.post(
            "/v1/authorize", json={"action": "purchase", "amount": 10.0}
        )

    assert response.status_code == 202
    warning = response.json().get("plan_warning")
    assert warning is not None
    assert "authorization" in warning.lower()
    assert "220" in warning   # hard_cap = int(200 * 1.1) = 220


async def test_authorize_at_hard_cap_returns_429(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """At 220/200 (110% = hard cap), returns 429 with upsell body."""
    usage_service._cache.reset()
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
        mock_count_result(220),      # >= 220 hard cap
    ])

    response = await agent_client.post(
        "/v1/authorize", json={"action": "purchase", "amount": 10.0}
    )

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["error"] == "authorization_limit_reached"
    assert detail["current_plan"] == "free"
    assert detail["requests_limit"] == 200
    assert detail["upgrade_to"] == "starter"
    assert detail["upgrade_limit"] == 5000
    assert "upgrade_url" in detail


async def test_authorize_business_plan_no_limit(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """Business plan (unlimited) skips the request count check entirely."""
    usage_service._cache.reset()
    mock_user.plan = "business"
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
        # No count query — unlimited plan skips it
        mock_rules_result([]),
    ])
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)
    stored = make_stored_req(mock_agent.id, "pending")

    with patch("app.api.authorize.AuthorizationRequest", return_value=stored):
        response = await agent_client.post(
            "/v1/authorize", json={"action": "purchase", "amount": 10.0}
        )

    assert response.status_code == 202


# ---------------------------------------------------------------------------
# $10,000 hard ceiling
# ---------------------------------------------------------------------------

async def test_authorize_over_10k_forces_pending(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """auto_approve_below rule with limit > $10k still forces amounts > $10k to pending."""
    usage_service._cache.reset()
    rule = make_rule_ns("auto_approve_below", {"amount": 50000.0})
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
        mock_count_result(0),
        mock_rules_result([rule]),
    ])
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await agent_client.post(
        "/v1/authorize",
        json={"action": "purchase", "amount": 11000.0, "vendor": "BigSpend"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "pending"


async def test_authorize_under_10k_auto_approves(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """auto_approve_below rule still works for amounts under $10k."""
    usage_service._cache.reset()
    rule = make_rule_ns("auto_approve_below", {"amount": 50000.0})
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
        mock_count_result(0),
        mock_rules_result([rule]),
    ])
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await agent_client.post(
        "/v1/authorize",
        json={"action": "purchase", "amount": 500.0, "vendor": "SmallSpend"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "auto_approved"


# ---------------------------------------------------------------------------
# Agent throttle — POST /v1/agents
# ---------------------------------------------------------------------------

async def test_create_agent_under_limit(user_client: AsyncClient, mock_user, mock_db):
    """Under the free plan 2-agent limit, creation succeeds."""
    from app.models.database import Agent as AgentModel

    saved_agent = AgentModel(
        id=uuid.uuid4(),
        user_id=mock_user.id,
        name="New Agent",
        api_key_hash="h",
        api_key_prefix="sk-ag-pref...",
        status="active",
        metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    mock_db.execute = AsyncMock(return_value=mock_count_result(1))  # 1 active < 2 limit
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.agents.Agent", return_value=saved_agent):
        response = await user_client.post("/v1/agents", json={"name": "New Agent"})

    assert response.status_code == 201


async def test_create_agent_at_limit_returns_403(user_client: AsyncClient, mock_user, mock_db):
    """At the free plan 2-agent limit, creation is blocked with 403 + upsell body."""
    mock_db.execute = AsyncMock(return_value=mock_count_result(2))  # 2 active = limit

    response = await user_client.post("/v1/agents", json={"name": "Too Many"})

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error"] == "agent_limit_reached"
    assert detail["current_plan"] == "free"
    assert detail["agents_limit"] == 2
    assert detail["upgrade_to"] == "starter"
    assert detail["upgrade_limit"] == 10


async def test_create_agent_business_no_limit(user_client: AsyncClient, mock_user, mock_db):
    """Business plan (unlimited) skips the agent count check."""
    from app.models.database import Agent as AgentModel

    mock_user.plan = "business"
    saved_agent = AgentModel(
        id=uuid.uuid4(),
        user_id=mock_user.id,
        name="Unlimited Agent",
        api_key_hash="h2",
        api_key_prefix="sk-ag-pref2...",
        status="active",
        metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    # No count query expected for unlimited plan
    mock_db.execute = AsyncMock(return_value=MagicMock())
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.agents.Agent", return_value=saved_agent):
        response = await user_client.post("/v1/agents", json={"name": "Unlimited Agent"})

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# GET /v1/usage
# ---------------------------------------------------------------------------

async def test_get_usage_returns_correct_counts(user_client: AsyncClient, mock_user, mock_db):
    """GET /v1/usage returns plan, counts, and limits."""
    usage_service._cache.reset()
    # Two execute calls: get_requests_this_month, get_active_agents
    mock_db.execute = AsyncMock(side_effect=[
        mock_count_result(47),   # requests this month
        mock_count_result(1),    # active agents
    ])

    response = await user_client.get("/v1/usage")

    assert response.status_code == 200
    data = response.json()
    assert data["plan"] == "free"
    assert data["authorizations_this_month"] == 47
    assert data["requests_limit"] == 200
    assert data["agents_active"] == 1
    assert data["agents_limit"] == 2


async def test_get_usage_business_plan_null_limits(user_client: AsyncClient, mock_user, mock_db):
    """Business plan usage response has null limits."""
    usage_service._cache.reset()
    mock_user.plan = "business"
    mock_db.execute = AsyncMock(side_effect=[
        mock_count_result(9999),
        mock_count_result(50),
    ])

    response = await user_client.get("/v1/usage")

    assert response.status_code == 200
    data = response.json()
    assert data["plan"] == "business"
    assert data["requests_limit"] is None
    assert data["agents_limit"] is None
