"""Audit logging service."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AuditLog


async def log_event(
    db: AsyncSession,
    event_type: str,
    *,
    user_id: Optional[uuid.UUID] = None,
    agent_id: Optional[uuid.UUID] = None,
    request_id: Optional[uuid.UUID] = None,
    details: Optional[dict] = None,
) -> None:
    """Add an audit log entry to the session. Caller must commit."""
    entry = AuditLog(
        id=uuid.uuid4(),
        event_type=event_type,
        user_id=user_id,
        agent_id=agent_id,
        request_id=request_id,
        details=details,
        created_at=datetime.now(timezone.utc),
    )
    db.add(entry)
