"""OAuth 2.1 service: PKCE verification, code/token generation, token lookup."""

import base64
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import Agent, OAuthAuthCode, OAuthClient, OAuthToken, User

logger = logging.getLogger(__name__)

_CODE_TTL_SECONDS = 600       # 10 minutes
_TOKEN_TTL_DAYS = 30
_LOGIN_PROOF_TTL_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    """Return True if code_verifier matches the stored code_challenge."""
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, code_challenge)


# ---------------------------------------------------------------------------
# Login proof JWT (stateless; carries OAuth params through the consent step)
# ---------------------------------------------------------------------------

def create_login_proof(
    user_id: uuid.UUID,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    state: Optional[str],
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "state": state or "",
        "type": "login_proof",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_LOGIN_PROOF_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_login_proof(token: str) -> dict:
    """Decode and verify a login_proof JWT. Raises JWTError on invalid/expired."""
    payload = jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
    )
    if payload.get("type") != "login_proof":
        raise JWTError("Not a login_proof token")
    return payload


# ---------------------------------------------------------------------------
# Supabase credential validation
# ---------------------------------------------------------------------------

async def send_password_reset(email: str) -> None:
    """Call Supabase Auth's password recovery endpoint. Always succeeds silently."""
    url = f"{settings.SUPABASE_URL}/auth/v1/recover"
    headers = {"apikey": settings.SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"email": email}, headers=headers)
        if resp.status_code not in (200, 204):
            logger.warning(
                "Supabase password reset non-success: status=%d email=%s body=%s",
                resp.status_code, email, resp.text,
            )
    except Exception:
        logger.exception("Supabase password reset request raised an exception: email=%s", email)


async def validate_supabase_credentials(email: str, password: str) -> Optional[str]:
    """Validate email/password against Supabase. Returns Supabase user UUID or None."""
    url = f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=password"
    logger.info("Supabase auth attempt: POST %s (email=%s)", url, email)
    headers = {
        "apikey": settings.SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"email": email, "password": password}, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            uid = data.get("user", {}).get("id")
            logger.info("Supabase auth succeeded for email=%s uid=%s", email, uid)
            return uid
        # Log the full Supabase error so we can diagnose failures in Railway logs
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text
        logger.warning(
            "Supabase auth failed: status=%d url=%s email=%s body=%s",
            resp.status_code, url, email, err_body,
        )
    except Exception:
        logger.exception("Supabase auth request raised an exception: url=%s email=%s", url, email)
    return None


# ---------------------------------------------------------------------------
# Client validation
# ---------------------------------------------------------------------------

async def get_client(db: AsyncSession, client_id: str) -> Optional[OAuthClient]:
    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    return result.scalar_one_or_none()


def validate_redirect_uri(client: OAuthClient, redirect_uri: str) -> bool:
    """Return True if redirect_uri starts with one of the client's allowed prefixes."""
    for prefix in (client.redirect_uri_prefixes or []):
        if redirect_uri.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Auth code lifecycle
# ---------------------------------------------------------------------------

async def create_auth_code(
    db: AsyncSession,
    user_id: uuid.UUID,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str = "authorize",
) -> str:
    code = secrets.token_urlsafe(32)
    record = OAuthAuthCode(
        code_hash=_sha256(code),
        user_id=user_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=_CODE_TTL_SECONDS),
    )
    db.add(record)
    await db.commit()
    return code


async def exchange_code_for_token(
    db: AsyncSession,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """Exchange an auth code for an access token.

    Returns (token, None) on success, (None, error_code) on failure.
    """
    code_hash = _sha256(code)
    result = await db.execute(
        select(OAuthAuthCode).where(OAuthAuthCode.code_hash == code_hash)
    )
    auth_code = result.scalar_one_or_none()

    if auth_code is None or auth_code.used_at is not None:
        return None, "invalid_grant"
    if auth_code.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None, "invalid_grant"
    if auth_code.client_id != client_id:
        return None, "invalid_client"
    if auth_code.redirect_uri != redirect_uri:
        return None, "invalid_grant"
    if not verify_pkce(code_verifier, auth_code.code_challenge, auth_code.code_challenge_method):
        return None, "invalid_grant"

    # Mark code consumed (single-use)
    auth_code.used_at = datetime.now(timezone.utc)

    token = secrets.token_urlsafe(48)
    token_record = OAuthToken(
        token_hash=_sha256(token),
        user_id=auth_code.user_id,
        client_id=client_id,
        scope=auth_code.scope,
        expires_at=datetime.now(timezone.utc) + timedelta(days=_TOKEN_TTL_DAYS),
    )
    db.add(token_record)
    await db.commit()
    return token, None


# ---------------------------------------------------------------------------
# Token → User/Agent resolution (used by require_agent)
# ---------------------------------------------------------------------------

async def get_agent_from_oauth_token(
    db: AsyncSession, raw_token: str
) -> Optional[Agent]:
    """Resolve an OAuth Bearer token to the user's first active agent."""
    token_hash = _sha256(raw_token)
    logger.info(
        "oauth_token_lookup: token_prefix=%.8s... token_hash_prefix=%.8s...",
        raw_token, token_hash,
    )
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(OAuthToken).where(
            OAuthToken.token_hash == token_hash,
            OAuthToken.expires_at > now,
        )
    )
    token_record = result.scalar_one_or_none()
    if token_record is None:
        # Run a second query without the expiry filter to distinguish
        # "token not found" from "token found but expired".
        check = await db.execute(
            select(OAuthToken).where(OAuthToken.token_hash == token_hash)
        )
        any_record = check.scalar_one_or_none()
        if any_record is None:
            logger.warning(
                "oauth_token_lookup: token NOT found in oauth_tokens (hash=%.8s...)",
                token_hash,
            )
        else:
            logger.warning(
                "oauth_token_lookup: token FOUND but EXPIRED (hash=%.8s... expires_at=%s now=%s)",
                token_hash, any_record.expires_at, now,
            )
        return None

    logger.info(
        "oauth_token_lookup: token found user_id=%s expires_at=%s",
        token_record.user_id, token_record.expires_at,
    )
    result = await db.execute(
        select(Agent)
        .where(Agent.user_id == token_record.user_id, Agent.status == "active")
        .order_by(Agent.created_at)
        .limit(1)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        logger.warning(
            "oauth_token_lookup: token valid but no active agent found for user_id=%s",
            token_record.user_id,
        )
    else:
        logger.info(
            "oauth_token_lookup: resolved to agent_id=%s agent_name=%s",
            agent.id, agent.name,
        )
    return agent
