"""Tests for GET/POST /v1/agents/{id}/rules and PATCH/DELETE /v1/rules/{id}."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.database import Agent, Rule


def _make_rule(agent_id: uuid.UUID, rule_type: str = "max_per_transaction") -> Rule:
    return Rule(
        id=uuid.uuid4(),
        agent_id=agent_id,
        rule_type=rule_type,
        value={"amount": 100.0},
        is_active=True,
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


# ---------------------------------------------------------------------------
# GET /v1/agents/{id}/rules
# ---------------------------------------------------------------------------

async def test_list_rules_returns_rules(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    rules = [_make_rule(agent.id), _make_rule(agent.id)]

    mock_db.execute = AsyncMock(side_effect=[mock_scalar(agent), mock_scalars_all(rules)])

    response = await user_client.get(f"/v1/agents/{agent.id}/rules")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_list_rules_agent_not_found(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_scalar(None))

    response = await user_client.get(f"/v1/agents/{uuid.uuid4()}/rules")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/agents/{id}/rules
# ---------------------------------------------------------------------------

async def test_create_rule_returns_201(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    saved_rule = _make_rule(agent.id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(agent))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.rules.Rule", return_value=saved_rule):
        response = await user_client.post(
            f"/v1/agents/{agent.id}/rules",
            json={"rule_type": "max_per_transaction", "value": {"amount": 100.0}},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["rule_type"] == "max_per_transaction"


async def test_create_rule_agent_not_found(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_scalar(None))

    response = await user_client.post(
        f"/v1/agents/{uuid.uuid4()}/rules",
        json={"rule_type": "max_per_transaction", "value": {"amount": 100.0}},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /v1/rules/{id}
# ---------------------------------------------------------------------------

async def test_update_rule_value(user_client: AsyncClient, mock_user, mock_db):
    agent_id = uuid.uuid4()
    rule = _make_rule(agent_id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(rule))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.patch(
        f"/v1/rules/{rule.id}", json={"value": {"amount": 250.0}}
    )
    assert response.status_code == 200
    assert rule.value == {"amount": 250.0}


async def test_update_rule_toggle_active(user_client: AsyncClient, mock_user, mock_db):
    agent_id = uuid.uuid4()
    rule = _make_rule(agent_id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(rule))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.patch(f"/v1/rules/{rule.id}", json={"is_active": False})
    assert response.status_code == 200
    assert rule.is_active is False


async def test_update_rule_not_found(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_scalar(None))

    response = await user_client.patch(
        f"/v1/rules/{uuid.uuid4()}", json={"is_active": False}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /v1/rules/{id}
# ---------------------------------------------------------------------------

async def test_delete_rule_returns_204(user_client: AsyncClient, mock_user, mock_db):
    agent_id = uuid.uuid4()
    rule = _make_rule(agent_id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(rule))
    mock_db.delete = AsyncMock()

    response = await user_client.delete(f"/v1/rules/{rule.id}")
    assert response.status_code == 204
    mock_db.delete.assert_called_once_with(rule)
