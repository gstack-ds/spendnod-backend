from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import UserDep
from app.models.schemas import UsageResponse
from app.plans import PLAN_LIMITS, PLAN_ORDER
from app.services import usage

router = APIRouter()


@router.get(
    "/usage",
    response_model=UsageResponse,
    summary="Get current usage",
    description="Returns the authenticated user's current plan, monthly request count, and active agent count.",
)
async def get_usage(
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    plan = getattr(user, "plan", "free") or "free"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    authorizations_this_month = await usage.get_authorizations_this_month(user.id, db)
    agents_active = await usage.get_active_agents(user.id, db)

    try:
        plan_index = PLAN_ORDER.index(plan)
        available_upgrades = PLAN_ORDER[plan_index + 1:]
    except ValueError:
        available_upgrades = []

    return UsageResponse(
        plan=plan,
        authorizations_this_month=authorizations_this_month,
        requests_limit=limits["max_requests_per_month"],
        agents_active=agents_active,
        agents_limit=limits["max_agents"],
        available_upgrades=available_upgrades,
        has_billing_account=bool(getattr(user, "stripe_customer_id", None)),
    )
