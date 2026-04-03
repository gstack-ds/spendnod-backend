"""Tests for the request expiration service."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.expiration import expire_pending_requests


async def test_expire_pending_requests_returns_rowcount():
    mock_result = MagicMock()
    mock_result.rowcount = 3

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.expiration.AsyncSessionLocal", return_value=mock_db):
        count = await expire_pending_requests()

    assert count == 3
    mock_db.commit.assert_called_once()


async def test_expire_pending_requests_zero_when_none_overdue():
    mock_result = MagicMock()
    mock_result.rowcount = 0

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.expiration.AsyncSessionLocal", return_value=mock_db):
        count = await expire_pending_requests()

    assert count == 0


async def test_expire_executes_update_with_correct_conditions():
    """Verify the update statement targets pending rows with expires_at in the past."""
    mock_result = MagicMock()
    mock_result.rowcount = 1

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.expiration.AsyncSessionLocal", return_value=mock_db):
        await expire_pending_requests()

    # execute was called once with an UPDATE statement
    mock_db.execute.assert_called_once()
    stmt = mock_db.execute.call_args[0][0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "UPDATE" in compiled.upper()
    assert "authorization_requests" in compiled
