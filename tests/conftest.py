import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import get_db
from app.middleware.auth import require_agent, require_user
from app.models.database import Agent, User


# ---------------------------------------------------------------------------
# Reusable object fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def agent_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_user(user_id) -> User:
    return User(
        id=user_id,
        email="test@example.com",
        name="Test User",
        supabase_auth_id=uuid.uuid4(),
        notification_preferences={"email": True, "sms": False},
        plan="free",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_agent(agent_id, mock_user) -> Agent:
    return Agent(
        id=agent_id,
        user_id=mock_user.id,
        name="Test Agent",
        api_key_hash="testhash",
        api_key_prefix="sk-ag-testpref...",
        status="active",
        metadata_={},
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()  # synchronous
    return db


# ---------------------------------------------------------------------------
# HTTP client fixtures with dependency overrides
# ---------------------------------------------------------------------------

@pytest.fixture
async def client():
    """Unauthenticated client for health/meta endpoints."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
async def agent_client(mock_agent, mock_db):
    """Client with require_agent and get_db overridden."""
    async def _agent():
        return mock_agent

    async def _db():
        yield mock_db

    app.dependency_overrides[require_agent] = _agent
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def user_client(mock_user, mock_db):
    """Client with require_user and get_db overridden."""
    async def _user():
        return mock_user

    async def _db():
        yield mock_db

    app.dependency_overrides[require_user] = _user
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
