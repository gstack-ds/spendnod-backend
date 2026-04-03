import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from jose import jwt

from app.config import settings


def generate_approval_token(
    request_id: uuid.UUID,
    agent_id: uuid.UUID,
    amount: Optional[Decimal],
) -> str:
    """Generate a signed JWT approval token for an authorized request.

    The token is passed to the vendor as proof that this transaction was
    authorized. Vendors can verify it via the token introspection endpoint
    (Phase 3). Claims include the request ID, agent ID, amount, and expiry.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(request_id),
        "agent_id": str(agent_id),
        "amount": float(amount) if amount is not None else None,
        "type": "approval",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.APPROVAL_TOKEN_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
