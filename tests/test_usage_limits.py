"""Tests for the $10k hard ceiling, GET /v1/usage, and agent/request counting."""

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
# POST /v1/authorize — happy path (no limits in open-source build)
# ---------------------------------------------------------------------------

async def test_authorize_succeeds(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """Requests pass through without any usage limit checks."""
    usage_service._cache.reset()
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
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


# ---------------------------------------------------------------------------
# $10,000 hard ceiling
# ---------------------------------------------------------------------------

async def test_authorize_over_10k_forces_pending(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """auto_approve_below rule with limit > $10k still forces amounts > $10k to pending."""
    usage_service._cache.reset()
    rule = make_rule_ns("auto_approve_below", {"amount": 50000.0})
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
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
# POST /v1/agents — no agent limit in open-source build
# ---------------------------------------------------------------------------

async def test_create_agent_succeeds(user_client: AsyncClient, mock_user, mock_db):
    """Agent creation succeeds without any plan-based limit check."""
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
    mock_db.execute = AsyncMock(return_value=mock_count_result(1))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.agents.Agent", return_value=saved_agent):
        response = await user_client.post("/v1/agents", json={"name": "New Agent"})

    assert response.status_code == 201


async def test_create_many_agents_no_limit(user_client: AsyncClient, mock_user, mock_db):
    """Many active agents do not trigger a 403 — no plan limit is enforced."""
    from app.models.database import Agent as AgentModel

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
    mock_db.execute = AsyncMock(return_value=MagicMock())
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.agents.Agent", return_value=saved_agent):
        response = await user_client.post("/v1/agents", json={"name": "Unlimited Agent"})

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# GET /v1/usage
# ---------------------------------------------------------------------------

async def test_get_usage_returns_correct_counts(user_client: AsyncClient, mock_user, mock_db):
    """GET /v1/usage returns counts and null limits (unlimited)."""
    usage_service._cache.reset()
    mock_db.execute = AsyncMock(side_effect=[
        mock_count_result(47),   # requests this month
        mock_count_result(1),    # active agents
    ])

    response = await user_client.get("/v1/usage")

    assert response.status_code == 200
    data = response.json()
    assert data["plan"] == "free"
    assert data["authorizations_this_month"] == 47
    assert data["requests_limit"] is None
    assert data["agents_active"] == 1
    assert data["agents_limit"] is None
    assert data["available_upgrades"] == []
    assert data["has_billing_account"] is False


async def test_get_usage_null_limits(user_client: AsyncClient, mock_user, mock_db):
    """Usage response always has null limits regardless of usage volume."""
    usage_service._cache.reset()
    mock_db.execute = AsyncMock(side_effect=[
        mock_count_result(9999),
        mock_count_result(50),
    ])

    response = await user_client.get("/v1/usage")

    assert response.status_code == 200
    data = response.json()
    assert data["requests_limit"] is None
    assert data["agents_limit"] is None
    assert data["available_upgrades"] == []
