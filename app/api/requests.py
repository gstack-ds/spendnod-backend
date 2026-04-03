import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import UserDep
from app.models.database import Agent, AuthorizationRequest
from app.models.schemas import ApproveRequest, AuthorizeResponse, DenyRequest, RequestStatus
from app.services import audit, token_service

router = APIRouter()


@router.get(
    "/requests",
    response_model=List[AuthorizeResponse],
    summary="List pending and recent requests",
    description="Returns the human's view of recent authorization requests, newest first.",
)
async def list_requests(
    user: UserDep,
    db: AsyncSession = Depends(get_db),
    filter_status: Optional[RequestStatus] = Query(None, alias="status"),
    limit: int = Query(50, le=200),
) -> List[AuthorizeResponse]:
    q = (
        select(AuthorizationRequest, Agent.name.label("agent_name"))
        .join(Agent, AuthorizationRequest.agent_id == Agent.id)
        .where(Agent.user_id == user.id)
        .order_by(AuthorizationRequest.created_at.desc())
        .limit(limit)
    )
    if filter_status is not None:
        q = q.where(AuthorizationRequest.status == filter_status.value)
    result = await db.execute(q)
    rows = result.all()
    return [
        AuthorizeResponse.model_validate(req).model_copy(update={"agent_name": agent_name})
        for req, agent_name in rows
    ]


@router.post(
    "/requests/{request_id}/approve",
    response_model=AuthorizeResponse,
    summary="Approve a pending request",
)
async def approve_request(
    request_id: uuid.UUID,
    body: ApproveRequest,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> AuthorizeResponse:
    result = await db.execute(
        select(AuthorizationRequest)
        .join(Agent, AuthorizationRequest.agent_id == Agent.id)
        .where(AuthorizationRequest.id == request_id, Agent.user_id == user.id)
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve a request with status '{req.status}'",
        )

    now = datetime.now(timezone.utc)
    req.approval_token = token_service.generate_approval_token(req.id, req.agent_id, req.amount)
    req.status = "approved"
    req.resolved_by = "human"
    req.resolved_at = now

    await audit.log_event(
        db,
        "human_approved",
        user_id=user.id,
        agent_id=req.agent_id,
        request_id=req.id,
        details={"note": body.note},
    )
    await db.commit()
    await db.refresh(req)
    return AuthorizeResponse.model_validate(req)


@router.post(
    "/requests/{request_id}/deny",
    response_model=AuthorizeResponse,
    summary="Deny a pending request",
)
async def deny_request(
    request_id: uuid.UUID,
    body: DenyRequest,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> AuthorizeResponse:
    result = await db.execute(
        select(AuthorizationRequest)
        .join(Agent, AuthorizationRequest.agent_id == Agent.id)
        .where(AuthorizationRequest.id == request_id, Agent.user_id == user.id)
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot deny a request with status '{req.status}'",
        )

    if body.reason:
        req.rule_evaluation = {**(req.rule_evaluation or {}), "deny_reason": body.reason}

    now = datetime.now(timezone.utc)
    req.status = "denied"
    req.resolved_by = "human"
    req.resolved_at = now

    await audit.log_event(
        db,
        "human_denied",
        user_id=user.id,
        agent_id=req.agent_id,
        request_id=req.id,
        details={"reason": body.reason},
    )
    await db.commit()
    await db.refresh(req)
    return AuthorizeResponse.model_validate(req)
