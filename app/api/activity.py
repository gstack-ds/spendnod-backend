"""Activity export endpoint — GET /v1/activity/export"""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import UserDep
from app.models.database import Agent, AuthorizationRequest
from app.services.rate_limiter import RateLimiter

router = APIRouter()

# 10 exports per hour per user
export_limiter = RateLimiter(max_requests=10, window_seconds=3600)

_COLUMNS = [
    "timestamp",
    "agent_id",
    "agent_name",
    "vendor",
    "amount",
    "currency",
    "category",
    "status",
    "rule_matched",
    "reviewed_by",
    "reviewed_at",
]


@router.get(
    "/activity/export",
    summary="Export activity log",
    description=(
        "Download authorization activity as CSV or JSON. "
        "Scoped to the authenticated user's agents only. "
        "Rate limited to 10 exports per hour."
    ),
    tags=["activity"],
)
async def export_activity(
    user: UserDep,
    db: AsyncSession = Depends(get_db),
    export_format: str = Query(
        ...,
        alias="format",
        pattern="^(csv|json)$",
        description="Export format: 'csv' or 'json'",
    ),
    start_date: Optional[datetime] = Query(None, description="ISO 8601 start datetime (inclusive)"),
    end_date: Optional[datetime] = Query(None, description="ISO 8601 end datetime (inclusive)"),
    agent_id: Optional[UUID] = Query(None, description="Filter by agent UUID"),
    filter_status: Optional[str] = Query(None, alias="status", description="Filter by status"),
    category: Optional[str] = Query(None, description="Filter by spending category"),
) -> StreamingResponse:
    export_limiter.check(str(user.id))

    stmt = (
        select(AuthorizationRequest, Agent.name.label("agent_name"))
        .join(Agent, AuthorizationRequest.agent_id == Agent.id)
        .where(Agent.user_id == user.id)
    )
    if start_date is not None:
        stmt = stmt.where(AuthorizationRequest.created_at >= start_date)
    if end_date is not None:
        stmt = stmt.where(AuthorizationRequest.created_at <= end_date)
    if agent_id is not None:
        stmt = stmt.where(AuthorizationRequest.agent_id == agent_id)
    if filter_status is not None:
        stmt = stmt.where(AuthorizationRequest.status == filter_status)
    if category is not None:
        stmt = stmt.where(AuthorizationRequest.category == category)

    stmt = stmt.order_by(AuthorizationRequest.created_at.desc())

    result = await db.execute(stmt)
    rows = result.all()

    records = []
    for req, agent_name in rows:
        rule_matched = None
        if req.rule_evaluation and isinstance(req.rule_evaluation, dict):
            rule_matched = req.rule_evaluation.get("matched_rule_type")
        records.append(
            {
                "timestamp": req.created_at.isoformat() if req.created_at else None,
                "agent_id": str(req.agent_id) if req.agent_id else None,
                "agent_name": agent_name,
                "vendor": req.vendor,
                "amount": float(req.amount) if req.amount is not None else None,
                "currency": req.currency,
                "category": req.category,
                "status": req.status,
                "rule_matched": rule_matched,
                "reviewed_by": req.resolved_by,
                "reviewed_at": req.resolved_at.isoformat() if req.resolved_at else None,
            }
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"spendnod_activity_{ts}.{export_format}"

    if export_format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        content = buf.getvalue().encode("utf-8")
        media_type = "text/csv"
    else:
        content = json.dumps(records, indent=2, default=str).encode("utf-8")
        media_type = "application/json"

    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
