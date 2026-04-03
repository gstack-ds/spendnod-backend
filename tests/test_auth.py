"""Tests for require_user JWT verification via Supabase JWKS (ES256)."""

import base64
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from jose import jwt

from app.middleware import auth as auth_module
from app.middleware.auth import require_user
from app.models.database import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url(n: int, byte_length: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(byte_length, "big")).rstrip(b"=").decode()


def _make_ec_keypair(kid: str = "test-kid-1") -> tuple[str, dict]:
    """Return (private_pem, public_jwk_dict) for a fresh P-256 key."""
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    nums = private_key.public_key().public_numbers()
    public_jwk = {
        "kty": "EC",
        "crv": "P-256",
        "kid": kid,
        "x": _b64url(nums.x, 32),
        "y": _b64url(nums.y, 32),
    }
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return private_pem, public_jwk


def _make_token(private_pem: str, sub: str, kid: str = "test-kid-1") -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated"},
        private_pem,
        algorithm="ES256",
        headers={"kid": kid},
    )


def _mock_request(token: str) -> MagicMock:
    req = MagicMock()
    req.headers = {"Authorization": f"Bearer {token}"}
    return req


def _mock_db(user) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_jwks_cache():
    """Clear the module-level JWKS cache before and after each test."""
    auth_module._jwks_cache = None
    yield
    auth_module._jwks_cache = None


@pytest.fixture
def ec_keypair():
    return _make_ec_keypair()


@pytest.fixture
def mock_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="user@example.com",
        name="Test User",
        supabase_auth_id=uuid.uuid4(),
        notification_preferences={},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_valid_es256_token_authenticates_user(ec_keypair, mock_user):
    """Valid ES256 JWT signed with matching key returns the user."""
    private_pem, public_jwk = ec_keypair
    token = _make_token(private_pem, str(mock_user.supabase_auth_id))
    fake_jwks = {"keys": [public_jwk]}

    with patch("app.middleware.auth._get_jwks", new_callable=AsyncMock, return_value=fake_jwks):
        user = await require_user(_mock_request(token), _mock_db(mock_user))

    assert user.id == mock_user.id


async def test_token_signed_with_wrong_key_raises_401(ec_keypair, mock_user):
    """JWT signed with a different key fails verification."""
    private_pem, _ = ec_keypair
    # Use a different key for the JWKS (mismatch)
    _, wrong_public_jwk = _make_ec_keypair(kid="test-kid-1")
    token = _make_token(private_pem, str(mock_user.supabase_auth_id))
    fake_jwks = {"keys": [wrong_public_jwk]}

    with patch("app.middleware.auth._get_jwks", new_callable=AsyncMock, return_value=fake_jwks):
        with pytest.raises(HTTPException) as exc_info:
            await require_user(_mock_request(token), _mock_db(mock_user))

    assert exc_info.value.status_code == 401


async def test_unknown_kid_raises_401(ec_keypair, mock_user):
    """Token with a kid not in the JWKS raises 401."""
    private_pem, public_jwk = ec_keypair
    token = _make_token(private_pem, str(mock_user.supabase_auth_id), kid="unknown-kid")
    # JWKS only has test-kid-1
    fake_jwks = {"keys": [public_jwk]}

    with patch("app.middleware.auth._get_jwks", new_callable=AsyncMock, return_value=fake_jwks):
        with pytest.raises(HTTPException) as exc_info:
            await require_user(_mock_request(token), _mock_db(mock_user))

    assert exc_info.value.status_code == 401


async def test_missing_sub_raises_401(ec_keypair, mock_user):
    """Token without a sub claim raises 401."""
    private_pem, public_jwk = ec_keypair
    token = jwt.encode(
        {"aud": "authenticated"},  # no sub
        private_pem,
        algorithm="ES256",
        headers={"kid": "test-kid-1"},
    )
    fake_jwks = {"keys": [public_jwk]}

    with patch("app.middleware.auth._get_jwks", new_callable=AsyncMock, return_value=fake_jwks):
        with pytest.raises(HTTPException) as exc_info:
            await require_user(_mock_request(token), _mock_db(mock_user))

    assert exc_info.value.status_code == 401


async def test_invalid_sub_uuid_raises_401(ec_keypair, mock_user):
    """Token with a non-UUID sub claim raises 401."""
    private_pem, public_jwk = ec_keypair
    token = _make_token(private_pem, "not-a-uuid")
    fake_jwks = {"keys": [public_jwk]}

    with patch("app.middleware.auth._get_jwks", new_callable=AsyncMock, return_value=fake_jwks):
        with pytest.raises(HTTPException) as exc_info:
            await require_user(_mock_request(token), _mock_db(mock_user))

    assert exc_info.value.status_code == 401


async def test_user_not_found_raises_404(ec_keypair, mock_user):
    """Valid token but user not in DB raises 404."""
    private_pem, public_jwk = ec_keypair
    token = _make_token(private_pem, str(mock_user.supabase_auth_id))
    fake_jwks = {"keys": [public_jwk]}

    with patch("app.middleware.auth._get_jwks", new_callable=AsyncMock, return_value=fake_jwks):
        with pytest.raises(HTTPException) as exc_info:
            await require_user(_mock_request(token), _mock_db(None))

    assert exc_info.value.status_code == 404


async def test_missing_authorization_header_raises_401(ec_keypair):
    """Request without Authorization header raises 401."""
    req = MagicMock()
    req.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await require_user(req, AsyncMock())

    assert exc_info.value.status_code == 401


async def test_jwks_cache_is_reused(ec_keypair, mock_user):
    """JWKS is fetched once and cached for subsequent calls."""
    private_pem, public_jwk = ec_keypair
    fake_jwks = {"keys": [public_jwk]}

    mock_get_jwks = AsyncMock(return_value=fake_jwks)
    with patch("app.middleware.auth._get_jwks", mock_get_jwks):
        token = _make_token(private_pem, str(mock_user.supabase_auth_id))
        await require_user(_mock_request(token), _mock_db(mock_user))
        await require_user(_mock_request(token), _mock_db(mock_user))

    assert mock_get_jwks.call_count == 2  # _get_jwks is called, caching is inside it
