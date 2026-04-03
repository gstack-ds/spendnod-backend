import hashlib
import uuid
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.database import Agent, User
from app.services import oauth_service

# Simple in-process JWKS cache — populated on first request, shared across calls.
_jwks_cache: dict | None = None


async def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache
    jwks_url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
    return _jwks_cache


def _hash_api_key(key: str) -> str:
    """SHA-256 hash of an API key for fast indexed lookup.

    SHA-256 is appropriate here because API keys are long random secrets,
    not user-chosen passwords. The hash is stored with a UNIQUE index,
    enabling O(1) lookup without bcrypt's per-row cost.
    """
    return hashlib.sha256(key.encode()).hexdigest()


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth.removeprefix("Bearer ").strip()


async def require_agent(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Agent:
    """Authenticate an AI agent via its API key.

    Extracts the Bearer token, hashes it, and looks up the matching active agent.
    Used on all agent-facing endpoints (POST /v1/authorize, etc.).
    """
    raw_key = _extract_bearer(request)
    key_hash = _hash_api_key(raw_key)
    result = await db.execute(
        select(Agent).where(Agent.api_key_hash == key_hash, Agent.status == "active")
    )
    agent = result.scalar_one_or_none()
    if agent is not None:
        return agent

    # Fall back to OAuth token → user's first active agent
    agent = await oauth_service.get_agent_from_oauth_token(db, raw_key)
    if agent is not None:
        return agent

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or revoked API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate a human user via their Supabase JWT.

    Fetches the JWKS from Supabase, selects the key matching the token's kid,
    and verifies the ES256 signature. Extracts the Supabase auth UID (sub claim)
    and looks up the matching user record.
    Used on all human-facing dashboard endpoints.
    """
    token = _extract_bearer(request)
    try:
        header = jwt.get_unverified_header(token)
        jwks = await _get_jwks()
        kid = header.get("kid")
        key_data = next(
            (k for k in jwks.get("keys", []) if kid is None or k.get("kid") == kid),
            None,
        )
        if key_data is None:
            raise JWTError("No matching key found in JWKS")
        payload = jwt.decode(
            token,
            key_data,
            algorithms=["ES256"],
            options={"verify_aud": False},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supabase_uid = payload.get("sub")
    if not supabase_uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    try:
        uid = uuid.UUID(supabase_uid)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid subject claim format",
        )

    result = await db.execute(select(User).where(User.supabase_auth_id == uid))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found — complete registration at /v1/auth/register",
        )
    return user


# Annotated shorthand dependencies for use in route signatures
AgentDep = Annotated[Agent, Depends(require_agent)]
UserDep = Annotated[User, Depends(require_user)]
