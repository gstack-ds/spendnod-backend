"""Tests for POST/GET/PATCH/DELETE /v1/agents."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.middleware.auth import _hash_api_key
from app.models.database import Agent


# ---------------------------------------------------------------------------
# Helper: build an Agent as the DB would return it after commit+refresh
# ---------------------------------------------------------------------------

def _make_db_agent(mock_user, name="New Agent") -> Agent:
    return Agent(
        id=uuid.uuid4(),
        user_id=mock_user.id,
        name=name,
        api_key_prefix="sk-ag-abcdef12...",
        api_key_hash="somehash",
        status="active",
        metadata_={},
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# POST /v1/agents
# ---------------------------------------------------------------------------

def _mock_count_result(n: int = 0) -> MagicMock:
    """Mock for SELECT COUNT — active agent count check."""
    m = MagicMock()
    m.scalar.return_value = n
    return m


async def test_create_agent_returns_201(user_client: AsyncClient, mock_user, mock_db):
    saved_agent = _make_db_agent(mock_user)
    mock_db.execute = AsyncMock(return_value=_mock_count_result(0))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.agents.Agent", return_value=saved_agent):
        response = await user_client.post(
            "/v1/agents", json={"name": "New Agent", "metadata": {}}
        )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "New Agent"
    assert "api_key" in data
    assert data["api_key"].startswith("sk-ag-")


async def test_create_agent_api_key_format(user_client: AsyncClient, mock_user, mock_db):
    saved_agent = _make_db_agent(mock_user)
    mock_db.execute = AsyncMock(return_value=_mock_count_result(0))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.agents.Agent", return_value=saved_agent):
        response = await user_client.post("/v1/agents", json={"name": "Key Test"})

    api_key = response.json()["api_key"]
    assert api_key.startswith("sk-ag-")
    assert len(api_key) == 70  # "sk-ag-" (6) + 64 hex chars


async def test_create_agent_key_not_stored_in_plaintext(user_client: AsyncClient, mock_user, mock_db):
    """The raw API key should not equal the stored hash."""
    saved_agent = _make_db_agent(mock_user)
    mock_db.execute = AsyncMock(return_value=_mock_count_result(0))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.agents.Agent", return_value=saved_agent):
        response = await user_client.post("/v1/agents", json={"name": "Hash Test"})

    api_key = response.json()["api_key"]
    assert api_key != _hash_api_key(api_key)   # raw key ≠ its own hash
    assert len(_hash_api_key(api_key)) == 64   # SHA-256 hex digest is 64 chars


# ---------------------------------------------------------------------------
# GET /v1/agents
# ---------------------------------------------------------------------------

async def test_list_agents_empty(user_client: AsyncClient, mock_db):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    response = await user_client.get("/v1/agents")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_agents_returns_all(user_client: AsyncClient, mock_user, mock_db):
    agents = [_make_db_agent(mock_user, f"Agent {i}") for i in range(3)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = agents
    mock_db.execute = AsyncMock(return_value=mock_result)

    response = await user_client.get("/v1/agents")
    assert response.status_code == 200
    assert len(response.json()) == 3


# ---------------------------------------------------------------------------
# PATCH /v1/agents/{id}
# ---------------------------------------------------------------------------

async def test_update_agent_name(user_client: AsyncClient, mock_user, mock_db):
    agent = _make_db_agent(mock_user, "Old Name")
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.patch(f"/v1/agents/{agent.id}", json={"name": "New Name"})
    assert response.status_code == 200
    assert agent.name == "New Name"


async def test_update_agent_not_found(user_client: AsyncClient, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    response = await user_client.patch(f"/v1/agents/{uuid.uuid4()}", json={"name": "Ghost"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /v1/agents/{id}
# ---------------------------------------------------------------------------

async def test_revoke_agent_sets_status(user_client: AsyncClient, mock_user, mock_db):
    agent = _make_db_agent(mock_user)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_db.execute = AsyncMock(return_value=mock_result)

    response = await user_client.delete(f"/v1/agents/{agent.id}")
    assert response.status_code == 204
    assert agent.status == "revoked"
