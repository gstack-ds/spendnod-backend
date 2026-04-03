"""Usage counting service with 60-second in-memory cache."""

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Agent, AuthorizationRequest


class _UsageCache:
    """60-second TTL in-memory cache for monthly request counts per user."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._ttl = ttl_seconds
        self._counts: dict[str, tuple[int, float]] = {}  # user_id -> (count, monotonic_ts)

    def get(self, user_id: str) -> Optional[int]:
        entry = self._counts.get(user_id)
        if entry is None:
            return None
        count, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._counts[user_id]
            return None
        return count

    def set(self, user_id: str, count: int) -> None:
        self._counts[user_id] = (count, time.monotonic())

    def invalidate(self, user_id: str) -> None:
        self._counts.pop(user_id, None)

    def reset(self) -> None:
        """Clear all cached counts — for use in tests."""
        self._counts.clear()


_cache = _UsageCache()


async def get_authorizations_this_month(user_id: uuid.UUID, db: AsyncSession) -> int:
    """Return the number of authorization requests (POST /v1/authorize) submitted
    this calendar month for a user.

    Only POST /v1/authorize creates rows in authorization_requests — GET and DELETE
    do not insert rows — so this count accurately reflects billable authorizations.
    Result is cached for 60 seconds per user to avoid a COUNT query on every call.
    """
    key = str(user_id)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(func.count())
        .select_from(AuthorizationRequest)
        .join(Agent, AuthorizationRequest.agent_id == Agent.id)
        .where(
            Agent.user_id == user_id,
            AuthorizationRequest.created_at >= first_of_month,
        )
    )
    count = result.scalar() or 0
    _cache.set(key, count)
    return count


async def get_active_agents(user_id: uuid.UUID, db: AsyncSession) -> int:
    """Return the number of active agents for a user."""
    result = await db.execute(
        select(func.count())
        .select_from(Agent)
        .where(Agent.user_id == user_id, Agent.status == "active")
    )
    return result.scalar() or 0
