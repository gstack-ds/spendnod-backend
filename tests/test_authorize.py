"""Tests for POST/GET/DELETE /v1/authorize."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.database import AuthorizationRequest
from app.services import usage as usage_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_auth_req(agent_id: uuid.UUID, status: str = "pending") -> AuthorizationRequest:
    return AuthorizationRequest(
        id=uuid.uuid4(),
        agent_id=agent_id,
        action="purchase",
        amount=Decimal("50.00"),
        currency="USD",
        vendor="AWS",
        category="cloud_services",
        description="Test purchase",
        status=status,
        approval_token="tok.test" if status in ("auto_approved", "approved") else None,
        resolved_by="system" if status != "pending" else None,
        resolved_at=datetime.now(timezone.utc) if status != "pending" else None,
        expires_at=datetime.now(timezone.utc) if status == "pending" else None,
        rule_evaluation={},
        created_at=datetime.now(timezone.utc),
    )


def make_rule_ns(rule_type: str, value: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        rule_type=rule_type,
        value=value,
        is_active=True,
    )


def mock_rules_result(rules: list) -> MagicMock:
    m = MagicMock()
    m.scalars.return_value.all.return_value = rules
    return m


def mock_req_result(req) -> MagicMock:
    m = MagicMock()
    m.scalar_one_or_none.return_value = req
    return m


def mock_user_result(user) -> MagicMock:
    """Mock for SELECT User — uses scalar_one_or_none."""
    m = MagicMock()
    m.scalar_one_or_none.return_value = user
    return m


def mock_count_result(n: int = 0) -> MagicMock:
    """Mock for SELECT COUNT — uses scalar."""
    m = MagicMock()
    m.scalar.return_value = n
    return m


# ---------------------------------------------------------------------------
# POST /v1/authorize
# ---------------------------------------------------------------------------

async def test_authorize_no_rules_returns_pending(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """No rules → default pending → 202, no approval_token."""
    usage_service._cache.reset()
    stored = make_auth_req(mock_agent.id, "pending")
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),   # user lookup
        mock_count_result(0),          # monthly request count
        mock_rules_result([]),         # active rules
    ])
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.authorize.AuthorizationRequest", return_value=stored):
        response = await agent_client.post(
            "/v1/authorize",
            json={"action": "purchase", "amount": 25.0, "vendor": "AWS"},
        )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert data["expires_at"] is not None


async def test_authorize_blocked_vendor_denied(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """blocked_vendors rule → 200, status=denied, no approval_token."""
    usage_service._cache.reset()
    rule = make_rule_ns("blocked_vendors", {"vendors": ["AWS"]})
    stored = make_auth_req(mock_agent.id, "denied")
    stored.approval_token = None
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
        mock_count_result(0),
        mock_rules_result([rule]),
    ])
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.authorize.AuthorizationRequest", return_value=stored):
        response = await agent_client.post(
            "/v1/authorize",
            json={"action": "purchase", "amount": 25.0, "vendor": "AWS"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "denied"
    assert data["approval_token"] is None


async def test_authorize_pending_returns_202(agent_client: AsyncClient, mock_agent, mock_user, mock_db):
    """require_approval_above rule → 202 Accepted with expires_at."""
    usage_service._cache.reset()
    rule = make_rule_ns("require_approval_above", {"amount": 10.0})
    stored = make_auth_req(mock_agent.id, "pending")
    mock_db.execute = AsyncMock(side_effect=[
        mock_user_result(mock_user),
        mock_count_result(0),
        mock_rules_result([rule]),
    ])
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.authorize.AuthorizationRequest", return_value=stored):
        response = await agent_client.post(
            "/v1/authorize",
            json={"action": "purchase", "amount": 50.0, "vendor": "AWS"},
        )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert data["expires_at"] is not None


# ---------------------------------------------------------------------------
# GET /v1/authorize/{request_id}
# ---------------------------------------------------------------------------

async def test_poll_returns_current_status(agent_client: AsyncClient, mock_agent, mock_db):
    req = make_auth_req(mock_agent.id, "pending")
    mock_db.execute = AsyncMock(return_value=mock_req_result(req))

    response = await agent_client.get(f"/v1/authorize/{req.id}")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


async def test_poll_not_found(agent_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_req_result(None))

    response = await agent_client.get(f"/v1/authorize/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /v1/authorize/{request_id}
# ---------------------------------------------------------------------------

async def test_cancel_pending_request(agent_client: AsyncClient, mock_agent, mock_db):
    req = make_auth_req(mock_agent.id, "pending")
    mock_db.execute = AsyncMock(return_value=mock_req_result(req))

    response = await agent_client.delete(f"/v1/authorize/{req.id}")
    assert response.status_code == 204
    assert req.status == "cancelled"


async def test_cancel_non_pending_returns_409(agent_client: AsyncClient, mock_agent, mock_db):
    req = make_auth_req(mock_agent.id, "auto_approved")
    mock_db.execute = AsyncMock(return_value=mock_req_result(req))

    response = await agent_client.delete(f"/v1/authorize/{req.id}")
    assert response.status_code == 409
