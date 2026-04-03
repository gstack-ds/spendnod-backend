"""Tests for GET /v1/requests and POST /v1/requests/{id}/approve|deny."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.models.database import AuthorizationRequest


def _make_req(user_id: uuid.UUID, req_status: str = "pending") -> AuthorizationRequest:
    return AuthorizationRequest(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        action="purchase",
        amount=Decimal("75.00"),
        currency="USD",
        vendor="AWS",
        category="cloud_services",
        description="Test",
        status=req_status,
        approval_token=None,
        resolved_by=None,
        resolved_at=None,
        expires_at=datetime.now(timezone.utc) if req_status == "pending" else None,
        rule_evaluation={},
        created_at=datetime.now(timezone.utc),
    )


def mock_scalar(obj) -> MagicMock:
    m = MagicMock()
    m.scalar_one_or_none.return_value = obj
    return m


def mock_scalars_all(items: list) -> MagicMock:
    m = MagicMock()
    m.scalars.return_value.all.return_value = items
    return m


def mock_all_tuples(reqs: list) -> MagicMock:
    """Return (AuthorizationRequest, agent_name) tuples — matches list_requests JOIN."""
    m = MagicMock()
    m.all.return_value = [(r, "Test Agent") for r in reqs]
    return m


# ---------------------------------------------------------------------------
# GET /v1/requests
# ---------------------------------------------------------------------------

async def test_list_requests_returns_requests(user_client: AsyncClient, mock_user, mock_db):
    reqs = [_make_req(mock_user.id) for _ in range(3)]
    mock_db.execute = AsyncMock(return_value=mock_all_tuples(reqs))

    response = await user_client.get("/v1/requests")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    # agent_name should be populated from the JOIN
    assert data[0]["agent_name"] == "Test Agent"


async def test_list_requests_empty(user_client: AsyncClient, mock_user, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_all_tuples([]))

    response = await user_client.get("/v1/requests")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# POST /v1/requests/{id}/approve
# ---------------------------------------------------------------------------

async def test_approve_pending_request(user_client: AsyncClient, mock_user, mock_db):
    req = _make_req(mock_user.id, "pending")
    mock_db.execute = AsyncMock(return_value=mock_scalar(req))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.post(f"/v1/requests/{req.id}/approve", json={})
    assert response.status_code == 200
    assert req.status == "approved"
    assert req.approval_token is not None
    assert req.resolved_by == "human"


async def test_approve_already_resolved_returns_409(user_client: AsyncClient, mock_user, mock_db):
    req = _make_req(mock_user.id, "auto_approved")
    mock_db.execute = AsyncMock(return_value=mock_scalar(req))

    response = await user_client.post(f"/v1/requests/{req.id}/approve", json={})
    assert response.status_code == 409


async def test_approve_not_found_returns_404(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_scalar(None))

    response = await user_client.post(f"/v1/requests/{uuid.uuid4()}/approve", json={})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/requests/{id}/deny
# ---------------------------------------------------------------------------

async def test_deny_pending_request(user_client: AsyncClient, mock_user, mock_db):
    req = _make_req(mock_user.id, "pending")
    mock_db.execute = AsyncMock(return_value=mock_scalar(req))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.post(f"/v1/requests/{req.id}/deny", json={})
    assert response.status_code == 200
    assert req.status == "denied"
    assert req.resolved_by == "human"


async def test_deny_stores_reason_in_rule_evaluation(user_client: AsyncClient, mock_user, mock_db):
    req = _make_req(mock_user.id, "pending")
    mock_db.execute = AsyncMock(return_value=mock_scalar(req))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.post(
        f"/v1/requests/{req.id}/deny", json={"reason": "Too large"}
    )
    assert response.status_code == 200
    assert req.rule_evaluation.get("deny_reason") == "Too large"
