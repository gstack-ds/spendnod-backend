"""Tests for OAuth 2.1 MCP authentication: metadata, PKCE, token exchange, require_agent fallback, MCP 401."""

import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import get_db
from app.middleware.auth import require_agent
from app.models.database import Agent, OAuthClient, OAuthAuthCode, OAuthToken, User
from app.services import oauth_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_code_verifier() -> str:
    return secrets.token_urlsafe(32)


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def make_oauth_client(client_id: str = "mcp-default") -> OAuthClient:
    return OAuthClient(
        client_id=client_id,
        redirect_uri_prefixes=["http://localhost"],
        client_name="Test MCP Client",
        created_at=datetime.now(timezone.utc),
    )


def make_user(user_id: uuid.UUID | None = None) -> User:
    return User(
        id=user_id or uuid.uuid4(),
        email="test@example.com",
        supabase_auth_id=uuid.uuid4(),
        notification_preferences={},
        plan="free",
        created_at=datetime.now(timezone.utc),
    )


def make_agent(user_id: uuid.UUID) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Test Agent",
        api_key_hash="unused",
        api_key_prefix="sk-ag-test...",
        status="active",
        metadata_={},
        created_at=datetime.now(timezone.utc),
    )


def mock_scalar_one_or_none(obj) -> MagicMock:
    m = MagicMock()
    m.scalar_one_or_none.return_value = obj
    return m


# ---------------------------------------------------------------------------
# 1. PKCE verification
# ---------------------------------------------------------------------------

def test_verify_pkce_s256_correct():
    verifier = _make_code_verifier()
    challenge = _s256_challenge(verifier)
    assert oauth_service.verify_pkce(verifier, challenge, "S256") is True


def test_verify_pkce_s256_wrong_verifier():
    challenge = _s256_challenge("correct-verifier")
    assert oauth_service.verify_pkce("wrong-verifier", challenge, "S256") is False


def test_verify_pkce_unsupported_method():
    assert oauth_service.verify_pkce("v", "c", "plain") is False


# ---------------------------------------------------------------------------
# 2. Login proof JWT round-trip
# ---------------------------------------------------------------------------

def test_login_proof_round_trip():
    user_id = uuid.uuid4()
    token = oauth_service.create_login_proof(
        user_id=user_id,
        client_id="mcp-default",
        redirect_uri="http://localhost:3456/callback",
        code_challenge="abc",
        code_challenge_method="S256",
        scope="authorize",
        state="xyz",
    )
    payload = oauth_service.decode_login_proof(token)
    assert payload["sub"] == str(user_id)
    assert payload["client_id"] == "mcp-default"
    assert payload["state"] == "xyz"
    assert payload["type"] == "login_proof"


# ---------------------------------------------------------------------------
# 3. OAuth metadata endpoint
# ---------------------------------------------------------------------------

async def test_oauth_metadata_returns_required_fields(client: AsyncClient):
    resp = await client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    data = resp.json()
    assert "authorization_endpoint" in data
    assert "token_endpoint" in data
    assert "registration_endpoint" in data
    assert data["code_challenge_methods_supported"] == ["S256"]
    assert data["token_endpoint_auth_methods_supported"] == ["none"]


# ---------------------------------------------------------------------------
# 4. /oauth/authorize — form GET validation
# ---------------------------------------------------------------------------

async def test_authorize_missing_params_returns_400(client: AsyncClient):
    resp = await client.get("/oauth/authorize")
    assert resp.status_code == 400


async def test_authorize_unknown_client_returns_400(client: AsyncClient):
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_scalar_one_or_none(None))
    mock_db.add = MagicMock()

    async def _db():
        yield mock_db

    app.dependency_overrides[get_db] = _db
    try:
        resp = await client.get(
            "/oauth/authorize",
            params={
                "client_id": "nonexistent",
                "redirect_uri": "http://localhost:1234/cb",
                "code_challenge": "abc123",
                "code_challenge_method": "S256",
                "response_type": "code",
            },
        )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_authorize_valid_params_returns_login_form(client: AsyncClient):
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_scalar_one_or_none(make_oauth_client()))
    mock_db.add = MagicMock()

    async def _db():
        yield mock_db

    app.dependency_overrides[get_db] = _db
    try:
        resp = await client.get(
            "/oauth/authorize",
            params={
                "client_id": "mcp-default",
                "redirect_uri": "http://localhost:1234/cb",
                "code_challenge": "abc123",
                "code_challenge_method": "S256",
                "response_type": "code",
            },
        )
        assert resp.status_code == 200
        assert b"<form" in resp.content
        assert b"email" in resp.content
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 5. /oauth/token — code exchange with PKCE
# ---------------------------------------------------------------------------

async def test_token_exchange_valid_pkce_returns_token(client: AsyncClient):
    user_id = uuid.uuid4()
    verifier = _make_code_verifier()
    challenge = _s256_challenge(verifier)

    # Build a real OAuthAuthCode record (not expired, not used)
    auth_code = OAuthAuthCode(
        id=uuid.uuid4(),
        code_hash=oauth_service._sha256("test-code-abc"),
        user_id=user_id,
        client_id="mcp-default",
        redirect_uri="http://localhost:1234/cb",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="authorize",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        used_at=None,
        created_at=datetime.now(timezone.utc),
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_scalar_one_or_none(auth_code))
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    async def _db():
        yield mock_db

    app.dependency_overrides[get_db] = _db
    try:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "test-code-abc",
                "redirect_uri": "http://localhost:1234/cb",
                "client_id": "mcp-default",
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_token_exchange_wrong_verifier_returns_400(client: AsyncClient):
    user_id = uuid.uuid4()
    real_challenge = _s256_challenge("correct-verifier")

    auth_code = OAuthAuthCode(
        id=uuid.uuid4(),
        code_hash=oauth_service._sha256("test-code-xyz"),
        user_id=user_id,
        client_id="mcp-default",
        redirect_uri="http://localhost:1234/cb",
        code_challenge=real_challenge,
        code_challenge_method="S256",
        scope="authorize",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        used_at=None,
        created_at=datetime.now(timezone.utc),
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_scalar_one_or_none(auth_code))
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    async def _db():
        yield mock_db

    app.dependency_overrides[get_db] = _db
    try:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "test-code-xyz",
                "redirect_uri": "http://localhost:1234/cb",
                "client_id": "mcp-default",
                "code_verifier": "wrong-verifier",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_token_exchange_expired_code_returns_400(client: AsyncClient):
    auth_code = OAuthAuthCode(
        id=uuid.uuid4(),
        code_hash=oauth_service._sha256("expired-code"),
        user_id=uuid.uuid4(),
        client_id="mcp-default",
        redirect_uri="http://localhost:1234/cb",
        code_challenge="whatever",
        code_challenge_method="S256",
        scope="authorize",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),  # expired
        used_at=None,
        created_at=datetime.now(timezone.utc),
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_scalar_one_or_none(auth_code))
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    async def _db():
        yield mock_db

    app.dependency_overrides[get_db] = _db
    try:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "expired-code",
                "redirect_uri": "http://localhost:1234/cb",
                "client_id": "mcp-default",
                "code_verifier": "any",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 6. require_agent falls back to OAuth token
# ---------------------------------------------------------------------------

async def test_require_agent_accepts_oauth_token():
    """require_agent resolves an OAuth Bearer token to the user's first active agent."""
    user_id = uuid.uuid4()
    user = make_user(user_id)
    agent = make_agent(user_id)

    # agent API key lookup returns nothing; OAuth token lookup returns agent
    with patch("app.services.oauth_service.get_agent_from_oauth_token", new=AsyncMock(return_value=agent)):
        mock_db = AsyncMock()
        # First execute: agent API key lookup → not found
        not_found = MagicMock()
        not_found.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=not_found)

        request = MagicMock()
        request.headers.get.return_value = "Bearer oauth-test-token-xyz"

        from app.middleware.auth import require_agent as _require_agent
        result = await _require_agent(request=request, db=mock_db)
        assert result is agent


# ---------------------------------------------------------------------------
# 7. /mcp returns 401 without Bearer token
# ---------------------------------------------------------------------------

async def test_mcp_returns_401_without_auth(client: AsyncClient):
    # /mcp redirects to /mcp/ — follow it so the middleware processes the request
    resp = await client.post(
        "/mcp/",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers
    www_auth = resp.headers["WWW-Authenticate"]
    assert "Bearer" in www_auth
    assert "resource_metadata" in www_auth


# ---------------------------------------------------------------------------
# 8. Dynamic client registration
# ---------------------------------------------------------------------------

async def test_oauth_register_creates_client(client: AsyncClient):
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    async def _db():
        yield mock_db

    app.dependency_overrides[get_db] = _db
    try:
        resp = await client.post(
            "/oauth/register",
            json={
                "redirect_uris": ["http://localhost:9876/callback"],
                "client_name": "My MCP App",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "client_id" in data
        assert data["client_id"].startswith("dyn-")
        assert data["redirect_uris"] == ["http://localhost:9876/callback"]
    finally:
        app.dependency_overrides.pop(get_db, None)
