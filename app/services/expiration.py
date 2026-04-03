"""Background task that expires pending authorization requests and OAuth tokens past their expires_at."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, update

from app.database import AsyncSessionLocal
from app.models.database import AuthorizationRequest, OAuthAuthCode, OAuthToken

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 30


async def expire_pending_requests() -> int:
    """Mark all overdue pending requests as expired. Returns count of rows updated."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(AuthorizationRequest)
            .where(
                AuthorizationRequest.status == "pending",
                AuthorizationRequest.expires_at <= datetime.now(timezone.utc),
            )
            .values(status="expired")
        )
        await db.commit()
        return result.rowcount


async def expire_oauth_rows() -> int:
    """Delete expired OAuth tokens and unused auth codes. Returns total rows deleted."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        r1 = await db.execute(
            delete(OAuthToken).where(OAuthToken.expires_at <= now)
        )
        r2 = await db.execute(
            delete(OAuthAuthCode).where(
                OAuthAuthCode.expires_at <= now,
                OAuthAuthCode.used_at.is_(None),
            )
        )
        await db.commit()
        return r1.rowcount + r2.rowcount


async def run_expiration_loop(interval: float = _INTERVAL_SECONDS) -> None:
    """Continuously expire overdue requests. Designed to run as an asyncio background task."""
    while True:
        try:
            expired = await expire_pending_requests()
            if expired:
                logger.info("Expired %d pending authorization request(s)", expired)
        except Exception:
            logger.exception("Error in expiration loop (auth requests)")
        try:
            cleaned = await expire_oauth_rows()
            if cleaned:
                logger.info("Cleaned %d expired OAuth row(s)", cleaned)
        except Exception:
            logger.exception("Error in expiration loop (oauth)")
        await asyncio.sleep(interval)
