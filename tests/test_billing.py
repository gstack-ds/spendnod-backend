import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import get_db
from app.middleware.auth import require_user
from app.models.database import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_user(user_id) -> User:
    return User(
        id=user_id,
        email="test@example.com",
        name="Test User",
        supabase_auth_id=uuid.uuid4(),
        notification_preferences={"email": True, "sms": False},
        plan="free",
        stripe_customer_id=None,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_user_with_customer(user_id) -> User:
    return User(
        id=user_id,
        email="test@example.com",
        name="Test User",
        supabase_auth_id=uuid.uuid4(),
        notification_preferences={"email": True, "sms": False},
        plan="starter",
        stripe_customer_id="cus_test123",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
async def user_client(mock_user, mock_db):
    async def _user():
        return mock_user

    async def _db():
        yield mock_db

    app.dependency_overrides[require_user] = _user
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def customer_client(mock_user_with_customer, mock_db):
    async def _user():
        return mock_user_with_customer

    async def _db():
        yield mock_db

    app.dependency_overrides[require_user] = _user
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def webhook_client(mock_db):
    """Unauthenticated client with db overridden — for /webhooks/stripe."""
    async def _db():
        yield mock_db

    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def unauthed_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# POST /v1/billing/checkout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkout_starter_plan(user_client):
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay/cs_test_starter"

    with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        resp = await user_client.post("/v1/billing/checkout", json={"plan": "starter"})

    assert resp.status_code == 200
    assert resp.json()["url"] == "https://checkout.stripe.com/pay/cs_test_starter"
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["mode"] == "subscription"
    assert call_kwargs["success_url"] == "https://app.spendnod.com?upgraded=true"
    assert call_kwargs["cancel_url"] == "https://app.spendnod.com"
    assert call_kwargs["metadata"]["plan"] == "starter"


@pytest.mark.asyncio
async def test_checkout_pro_plan(user_client):
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay/cs_test_pro"

    with patch("stripe.checkout.Session.create", return_value=mock_session):
        resp = await user_client.post("/v1/billing/checkout", json={"plan": "pro"})

    assert resp.status_code == 200
    assert resp.json()["url"] == "https://checkout.stripe.com/pay/cs_test_pro"


@pytest.mark.asyncio
async def test_checkout_invalid_plan_returns_422(user_client):
    with patch("stripe.checkout.Session.create"):
        resp = await user_client.post("/v1/billing/checkout", json={"plan": "enterprise"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_checkout_requires_auth(unauthed_client):
    resp = await unauthed_client.post("/v1/billing/checkout", json={"plan": "starter"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_checkout_stripe_error_returns_502(user_client):
    import stripe as stripe_lib

    with patch("stripe.checkout.Session.create", side_effect=stripe_lib.StripeError("network error")):
        resp = await user_client.post("/v1/billing/checkout", json={"plan": "starter"})

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# POST /v1/billing/portal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_portal_returns_url(customer_client):
    mock_session = MagicMock()
    mock_session.url = "https://billing.stripe.com/session/bps_test"

    with patch("stripe.billing_portal.Session.create", return_value=mock_session):
        resp = await customer_client.post("/v1/billing/portal")

    assert resp.status_code == 200
    assert resp.json()["url"] == "https://billing.stripe.com/session/bps_test"


@pytest.mark.asyncio
async def test_portal_no_customer_id_returns_400(user_client):
    resp = await user_client.post("/v1/billing/portal")
    assert resp.status_code == 400
    assert "billing account" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_portal_requires_auth(unauthed_client):
    resp = await unauthed_client.post("/v1/billing/portal")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /webhooks/stripe
# ---------------------------------------------------------------------------

def _make_event(event_type: str, obj: dict) -> dict:
    return {"type": event_type, "data": {"object": obj}}


@pytest.mark.asyncio
async def test_webhook_checkout_completed_updates_user(webhook_client, mock_db, mock_user):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_db.execute.return_value = mock_result

    event = _make_event("checkout.session.completed", {
        "customer_email": "test@example.com",
        "customer": "cus_new123",
        "metadata": {"plan": "starter", "user_id": str(mock_user.id)},
    })

    with patch("stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/webhooks/stripe",
            content=b"payload",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"received": True}
    assert mock_user.plan == "starter"
    assert mock_user.stripe_customer_id == "cus_new123"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_subscription_updated_changes_plan(webhook_client, mock_db, mock_user):
    mock_user.stripe_customer_id = "cus_existing"
    mock_user.plan = "starter"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_db.execute.return_value = mock_result

    from app.config import settings
    event = _make_event("customer.subscription.updated", {
        "customer": "cus_existing",
        "items": {"data": [{"price": {"id": settings.STRIPE_PRO_PRICE_ID or "price_pro"}}]},
    })

    with patch("stripe.Webhook.construct_event", return_value=event):
        with patch("app.api.billing.settings") as mock_settings:
            mock_settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
            mock_settings.STRIPE_STARTER_PRICE_ID = "price_starter"
            mock_settings.STRIPE_PRO_PRICE_ID = "price_pro"
            resp = await webhook_client.post(
                "/webhooks/stripe",
                content=b"payload",
                headers={"stripe-signature": "t=1,v1=sig"},
            )

    assert resp.status_code == 200
    assert mock_user.plan == "pro"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_subscription_deleted_sets_free(webhook_client, mock_db, mock_user):
    mock_user.stripe_customer_id = "cus_existing"
    mock_user.plan = "starter"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_db.execute.return_value = mock_result

    event = _make_event("customer.subscription.deleted", {"customer": "cus_existing"})

    with patch("stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/webhooks/stripe",
            content=b"payload",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    assert mock_user.plan == "free"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_invalid_signature_returns_400(webhook_client):
    import stripe as stripe_lib

    with patch(
        "stripe.Webhook.construct_event",
        side_effect=stripe_lib.SignatureVerificationError("bad sig", "sig_header"),
    ):
        resp = await webhook_client.post(
            "/webhooks/stripe",
            content=b"payload",
            headers={"stripe-signature": "bad"},
        )

    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_unknown_event_returns_200(webhook_client):
    event = _make_event("payment_intent.created", {"id": "pi_test"})

    with patch("stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/webhooks/stripe",
            content=b"payload",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"received": True}
