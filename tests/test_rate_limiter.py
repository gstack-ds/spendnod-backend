"""Tests for the rate limiter service and authorize endpoint enforcement."""

import uuid
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.models.database import AuthorizationRequest
from app.services.rate_limiter import RateLimiter
from app.services import rate_limiter as rl_module


# ---------------------------------------------------------------------------
# Unit tests — RateLimiter class
# ---------------------------------------------------------------------------

def test_rate_limiter_allows_up_to_limit():
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check("agent-1")  # no exception


def test_rate_limiter_blocks_on_exceeded():
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check("agent-1")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("agent-1")
    assert exc_info.value.status_code == 429


def test_rate_limiter_independent_keys():
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    limiter.check("agent-a")
    limiter.check("agent-a")
    # agent-b unaffected by agent-a's usage
    limiter.check("agent-b")
    limiter.check("agent-b")


def test_rate_limiter_reset_clears_state():
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    limiter.check("agent-x")
    with pytest.raises(HTTPException):
        limiter.check("agent-x")
    limiter.reset("agent-x")
    limiter.check("agent-x")  # should pass after reset


def test_rate_limiter_reset_all():
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    limiter.check("a")
    limiter.check("b")
    limiter.reset()
    limiter.check("a")  # both cleared
    limiter.check("b")


def test_rate_limiter_window_expires():
    """Old timestamps outside the window don't count toward the limit."""
    import time
    limiter = RateLimiter(max_requests=2, window_seconds=1)
    limiter.check("agent-1")
    limiter.check("agent-1")
    # Manually backdate both entries so they fall outside the 1s window
    limiter._window["agent-1"] = [t - 2 for t in limiter._window["agent-1"]]
    # Now should accept new requests again
    limiter.check("agent-1")


# ---------------------------------------------------------------------------
# Integration test — authorize endpoint returns 429 when rate limit hit
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_authorize_limiter():
    """Reset the module-level limiter before each test in this module."""
    rl_module.authorize_limiter.reset()
    yield
    rl_module.authorize_limiter.reset()


async def test_authorize_rate_limit_returns_429(agent_client: AsyncClient, mock_agent, mock_db):
    """Exceed the rate limit on POST /v1/authorize → 429."""
    # Temporarily set a very low limit on the module-level limiter
    original_max = rl_module.authorize_limiter.max_requests
    rl_module.authorize_limiter.max_requests = 2

    stored = AuthorizationRequest(
        id=uuid.uuid4(),
        agent_id=mock_agent.id,
        action="purchase",
        amount=Decimal("10.00"),
        currency="USD",
        vendor="AWS",
        category=None,
        description=None,
        status="auto_approved",
        approval_token="tok.test",
        resolved_by="system",
        resolved_at=datetime.now(timezone.utc),
        expires_at=None,
        rule_evaluation={},
        created_at=datetime.now(timezone.utc),
    )
    def _make_exec_mock():
        m = MagicMock()
        m.scalar.return_value = 0   # count=0 → under any plan limit
        m.scalars.return_value.all.return_value = []
        return m

    mock_db.execute = AsyncMock(side_effect=lambda *a, **kw: _make_exec_mock())
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    try:
        with patch("app.api.authorize.AuthorizationRequest", return_value=stored):
            for _ in range(2):
                r = await agent_client.post(
                    "/v1/authorize",
                    json={"action": "purchase", "amount": 10.0},
                )
                assert r.status_code != 429

            r = await agent_client.post(
                "/v1/authorize",
                json={"action": "purchase", "amount": 10.0},
            )
            assert r.status_code == 429
    finally:
        rl_module.authorize_limiter.max_requests = original_max
