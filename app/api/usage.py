from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import UserDep
from app.models.schemas import UsageResponse
from app.services import usage

router = APIRouter()


@router.get(
    "/usage",
    response_model=UsageResponse,
    summary="Get current usage",
    description="Returns the authenticated user's current monthly request count and active agent count.",
)
async def get_usage(
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    authorizations_this_month = await usage.get_authorizations_this_month(user.id, db)
    agents_active = await usage.get_active_agents(user.id, db)

    return UsageResponse(
        plan="free",
        authorizations_this_month=authorizations_this_month,
        requests_limit=None,
        agents_active=agents_active,
        agents_limit=None,
        available_upgrades=[],
        has_billing_account=False,
    )
