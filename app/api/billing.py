import asyncio
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import UserDep
from app.models.database import User
from app.services import audit

logger = logging.getLogger(__name__)

billing_router = APIRouter()
webhook_router = APIRouter()


class CheckoutRequest(BaseModel):
    plan: str


class CheckoutResponse(BaseModel):
    url: str


class PortalResponse(BaseModel):
    url: str


def _get_price_id(plan: str) -> str:
    if plan == "starter":
        return settings.STRIPE_STARTER_PRICE_ID
    if plan == "pro":
        return settings.STRIPE_PRO_PRICE_ID
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"Invalid plan '{plan}'. Must be 'starter' or 'pro'.",
    )


def _price_to_plan(price_id: str) -> str | None:
    if price_id == settings.STRIPE_STARTER_PRICE_ID:
        return "starter"
    if price_id == settings.STRIPE_PRO_PRICE_ID:
        return "pro"
    return None


@billing_router.post(
    "/billing/checkout",
    response_model=CheckoutResponse,
    summary="Create Stripe Checkout session",
)
async def create_checkout(
    body: CheckoutRequest,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    price_id = _get_price_id(body.plan)
    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            api_key=settings.STRIPE_SECRET_KEY,
            customer_email=user.email,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url="https://app.spendnod.com?upgraded=true",
            cancel_url="https://app.spendnod.com",
            metadata={"plan": body.plan, "user_id": str(user.id)},
        )
    except stripe.StripeError as exc:
        logger.error("Stripe checkout error for user %s: %s", user.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Stripe error")
    return CheckoutResponse(url=session.url)


@billing_router.post(
    "/billing/portal",
    response_model=PortalResponse,
    summary="Create Stripe Billing Portal session",
)
async def create_portal(
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> PortalResponse:
    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account found. Complete a purchase first.",
        )
    try:
        session = await asyncio.to_thread(
            stripe.billing_portal.Session.create,
            api_key=settings.STRIPE_SECRET_KEY,
            customer=user.stripe_customer_id,
            return_url="https://app.spendnod.com",
        )
    except stripe.StripeError as exc:
        logger.error("Stripe portal error for user %s: %s", user.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Stripe error")
    return PortalResponse(url=session.url)


@webhook_router.post("/webhooks/stripe", summary="Stripe webhook receiver")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(obj, db)
    elif event_type == "customer.subscription.updated":
        await _handle_subscription_updated(obj, db)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(obj, db)

    return {"received": True}


async def _handle_checkout_completed(session: dict, db: AsyncSession) -> None:
    email = session.get("customer_email")
    customer_id = session.get("customer")
    plan = (session.get("metadata") or {}).get("plan")

    if not email or not plan:
        logger.warning("checkout.session.completed: missing email or plan metadata")
        return

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        logger.warning("checkout.session.completed: no user found for email %s", email)
        return

    old_plan = user.plan
    user.stripe_customer_id = customer_id
    user.plan = plan
    await audit.log_event(
        db,
        "plan_upgraded",
        user_id=user.id,
        details={"old_plan": old_plan, "new_plan": plan, "stripe_customer_id": customer_id},
    )
    await db.commit()


async def _handle_subscription_updated(subscription: dict, db: AsyncSession) -> None:
    customer_id = subscription.get("customer")
    items_data = (subscription.get("items") or {}).get("data") or []
    if not items_data:
        return

    price_id = (items_data[0].get("price") or {}).get("id")
    plan = _price_to_plan(price_id) if price_id else None
    if plan is None:
        logger.warning("subscription.updated: unrecognised price_id %s", price_id)
        return

    result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
    user = result.scalar_one_or_none()
    if user is None:
        logger.warning("subscription.updated: no user found for customer %s", customer_id)
        return

    old_plan = user.plan
    user.plan = plan
    await audit.log_event(
        db,
        "plan_changed",
        user_id=user.id,
        details={"old_plan": old_plan, "new_plan": plan, "stripe_customer_id": customer_id},
    )
    await db.commit()


async def _handle_subscription_deleted(subscription: dict, db: AsyncSession) -> None:
    customer_id = subscription.get("customer")

    result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
    user = result.scalar_one_or_none()
    if user is None:
        logger.warning("subscription.deleted: no user found for customer %s", customer_id)
        return

    old_plan = user.plan
    user.plan = "free"
    await audit.log_event(
        db,
        "plan_downgraded",
        user_id=user.id,
        details={"old_plan": old_plan, "new_plan": "free", "stripe_customer_id": customer_id},
    )
    await db.commit()
