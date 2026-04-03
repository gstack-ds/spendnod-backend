"""Tests for GET /v1/dashboard/stats and /v1/dashboard/activity."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient


def _stats_row(
    total=0, auto_approved=0, pending=0, approved=0,
    denied=0, expired=0, total_spend=0,
):
    row = MagicMock()
    row.total = total
    row.auto_approved = auto_approved
    row.pending = pending
    row.approved = approved
    row.denied = denied
    row.expired = expired
    row.total_spend = total_spend
    return row


def _activity_row(agent_name="Test Agent"):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.agent_name = agent_name
    row.action = "purchase"
    row.amount = Decimal("50.00")
    row.vendor = "AWS"
    row.description = None
    row.status = "auto_approved"
    row.created_at = datetime.now(timezone.utc)
    return row


# ---------------------------------------------------------------------------
# GET /v1/dashboard/stats
# ---------------------------------------------------------------------------

async def test_get_stats_returns_schema(user_client: AsyncClient, mock_db):
    stats_result = MagicMock()
    stats_result.one.return_value = _stats_row(total=10, auto_approved=8, pending=1, approved=1)

    agent_count_result = MagicMock()
    agent_count_result.scalar.return_value = 3

    mock_db.execute = AsyncMock(side_effect=[stats_result, agent_count_result])

    response = await user_client.get("/v1/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    for field in ("total_requests", "auto_approved", "pending", "approved",
                  "denied", "expired", "total_spend", "approval_rate", "agents_active"):
        assert field in data
    assert data["total_requests"] == 10
    assert data["agents_active"] == 3


async def test_get_stats_zero_baseline(user_client: AsyncClient, mock_db):
    stats_result = MagicMock()
    stats_result.one.return_value = _stats_row()

    agent_count_result = MagicMock()
    agent_count_result.scalar.return_value = 0

    mock_db.execute = AsyncMock(side_effect=[stats_result, agent_count_result])

    response = await user_client.get("/v1/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_requests"] == 0
    assert float(data["total_spend"]) == 0.0
    assert data["agents_active"] == 0


# ---------------------------------------------------------------------------
# GET /v1/dashboard/activity
# ---------------------------------------------------------------------------

async def test_get_activity_returns_items(user_client: AsyncClient, mock_db):
    rows = [_activity_row() for _ in range(5)]
    result = MagicMock()
    result.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=result)

    response = await user_client.get("/v1/dashboard/activity")
    assert response.status_code == 200
    assert len(response.json()) == 5


async def test_get_activity_empty(user_client: AsyncClient, mock_db):
    result = MagicMock()
    result.all.return_value = []
    mock_db.execute = AsyncMock(return_value=result)

    response = await user_client.get("/v1/dashboard/activity")
    assert response.status_code == 200
    assert response.json() == []
