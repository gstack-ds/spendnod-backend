from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import UserDep
from app.models.database import Agent, AuthorizationRequest
from app.models.schemas import ActivityItem, DashboardStats

router = APIRouter()

req = AuthorizationRequest

_STATUS_TO_EVENT_TYPE = {
    "auto_approved": "auto_approved",
    "approved": "human_approved",
    "pending": "request_pending",
    "denied": "human_denied",
    "expired": "request_expired",
    "cancelled": "request_cancelled",
}


@router.get(
    "/dashboard/stats",
    response_model=DashboardStats,
    summary="Spending summary and approval rate stats",
)
async def get_stats(
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> DashboardStats:
    stats_result = await db.execute(
        select(
            func.count().label("total"),
            func.sum(case((req.status == "auto_approved", 1), else_=0)).label("auto_approved"),
            func.sum(case((req.status == "pending", 1), else_=0)).label("pending"),
            func.sum(case((req.status == "approved", 1), else_=0)).label("approved"),
            func.sum(case((req.status == "denied", 1), else_=0)).label("denied"),
            func.sum(case((req.status == "expired", 1), else_=0)).label("expired"),
            func.coalesce(
                func.sum(
                    case(
                        (req.status.in_(["auto_approved", "approved"]), req.amount),
                        else_=None,
                    )
                ),
                0,
            ).label("total_spend"),
        )
        .join(Agent, req.agent_id == Agent.id)
        .where(Agent.user_id == user.id)
    )
    row = stats_result.one()

    agent_count_result = await db.execute(
        select(func.count()).where(Agent.user_id == user.id, Agent.status == "active")
    )
    agents_active = agent_count_result.scalar() or 0

    total = row.total or 0
    approved_count = (row.auto_approved or 0) + (row.approved or 0)
    approval_rate = approved_count / total if total > 0 else 0.0

    return DashboardStats(
        total_requests=total,
        auto_approved=row.auto_approved or 0,
        pending=row.pending or 0,
        approved=row.approved or 0,
        denied=row.denied or 0,
        expired=row.expired or 0,
        total_spend=Decimal(str(row.total_spend or 0)),
        approval_rate=approval_rate,
        agents_active=agents_active,
    )


@router.get(
    "/dashboard/activity",
    response_model=List[ActivityItem],
    summary="Recent activity feed",
)
async def get_activity(
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> List[ActivityItem]:
    result = await db.execute(
        select(
            req.id,
            Agent.name.label("agent_name"),
            req.action,
            req.amount,
            req.vendor,
            req.description,
            req.status,
            req.created_at,
        )
        .join(Agent, req.agent_id == Agent.id)
        .where(Agent.user_id == user.id)
        .order_by(req.created_at.desc())
        .limit(100)
    )
    rows = result.all()
    return [
        ActivityItem(
            id=r.id,
            event_type=_STATUS_TO_EVENT_TYPE.get(r.status, r.status),
            agent_name=r.agent_name,
            action=r.action,
            amount=r.amount,
            vendor=r.vendor,
            description=r.description,
            created_at=r.created_at,
        )
        for r in rows
    ]
