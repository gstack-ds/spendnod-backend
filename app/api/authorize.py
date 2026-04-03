import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import AgentDep
from app.models.database import AuthorizationRequest, Rule, User
from app.models.schemas import AuthorizeRequest, AuthorizeResponse
from app.plans import PLAN_LIMITS, UPGRADE_URL, get_next_plan
from app.services import audit, notification, rule_engine, token_service
from app.services import usage as usage_service
from app.services.rate_limiter import authorize_limiter

router = APIRouter()

_PENDING_EXPIRY_SECONDS = 300  # 5 minutes for human to respond
_HARD_CEILING = Decimal("10000")  # auto-approvals above this are always forced to pending


@router.post(
    "/authorize",
    response_model=AuthorizeResponse,
    summary="Submit an authorization request",
    description=(
        "Agent submits a transaction for authorization. "
        "Returns 200 with status=auto_approved or status=denied for immediate decisions. "
        "Returns 202 Accepted with status=pending if human review is required."
    ),
)
async def create_authorization_request(
    body: AuthorizeRequest,
    agent: AgentDep,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    response: Response = None,
) -> AuthorizeResponse:
    # Enforce per-agent rate limit
    authorize_limiter.check(str(agent.id))

    # Load the user (needed for plan limits and notifications)
    user_result = await db.execute(select(User).where(User.id == agent.user_id))
    user = user_result.scalar_one_or_none()

    # Enforce monthly request limit
    plan_warning: str | None = None
    if user is not None:
        plan = getattr(user, "plan", "free") or "free"
        limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])["max_requests_per_month"]
        if limit is not None:
            count = await usage_service.get_authorizations_this_month(user.id, db)
            hard_cap = int(limit * 1.1)
            if count >= hard_cap:
                next_plan = get_next_plan(plan)
                next_limits = PLAN_LIMITS.get(next_plan or "", {})
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "authorization_limit_reached",
                        "current_plan": plan,
                        "requests_used": count,
                        "requests_limit": limit,
                        "upgrade_to": next_plan,
                        "upgrade_limit": next_limits.get("max_requests_per_month"),
                        "upgrade_url": UPGRADE_URL,
                    },
                )
            elif count >= limit:
                plan_warning = (
                    f"You are over your monthly authorization limit. "
                    f"Authorizations will be blocked at {hard_cap}/{limit}."
                )

    # Load the agent's active rules
    rules_result = await db.execute(
        select(Rule).where(Rule.agent_id == agent.id, Rule.is_active.is_(True))
    )
    rules = rules_result.scalars().all()

    # Evaluate against rules
    result = await rule_engine.evaluate(body, agent.id, rules, db)

    # Hard ceiling: never auto-approve transactions above $10,000 regardless of rules
    decision = result.decision
    if decision == "auto_approved" and body.amount is not None and body.amount > _HARD_CEILING:
        decision = "pending"

    now = datetime.now(timezone.utc)
    req = AuthorizationRequest(
        id=uuid.uuid4(),
        agent_id=agent.id,
        action=body.action,
        amount=body.amount,
        currency=body.currency,
        vendor=body.vendor,
        category=body.category,
        description=body.description,
        status=decision,
        rule_evaluation={
            "decision": result.decision,
            "reason": result.reason,
            "matched_rule_type": result.matched_rule_type,
            "log": result.evaluation_log,
        },
        created_at=now,
    )

    if decision == "auto_approved":
        req.approval_token = token_service.generate_approval_token(req.id, agent.id, body.amount)
        req.resolved_by = "system"
        req.resolved_at = now
        if response is not None:
            response.status_code = status.HTTP_200_OK
    elif decision == "denied":
        req.resolved_by = "system"
        req.resolved_at = now
        if response is not None:
            response.status_code = status.HTTP_200_OK
    else:  # pending
        req.expires_at = now + timedelta(seconds=_PENDING_EXPIRY_SECONDS)
        if response is not None:
            response.status_code = status.HTTP_202_ACCEPTED

    await audit.log_event(db, "request_created", agent_id=agent.id, request_id=req.id)
    if decision == "auto_approved":
        await audit.log_event(db, "auto_approved", agent_id=agent.id, request_id=req.id)
    elif decision == "denied":
        await audit.log_event(
            db,
            "request_denied_by_rule",
            agent_id=agent.id,
            request_id=req.id,
            details={"matched_rule_type": result.matched_rule_type},
        )
    else:
        await audit.log_event(db, "request_pending", agent_id=agent.id, request_id=req.id)

    db.add(req)
    await db.commit()
    await db.refresh(req)

    if decision == "pending" and settings.RESEND_API_KEY and user is not None:
        background_tasks.add_task(
            notification.send_pending_notification,
            user_email=user.email,
            agent_name=agent.name,
            request_id=str(req.id),
            action=body.action,
            amount=body.amount,
            vendor=body.vendor,
            description=body.description,
        )

    resp = AuthorizeResponse.model_validate(req)
    if plan_warning:
        resp.plan_warning = plan_warning
    return resp


@router.get(
    "/authorize/{request_id}",
    response_model=AuthorizeResponse,
    summary="Poll authorization request status",
    description="Agent polls for the current status of a pending authorization request.",
)
async def get_authorization_request(
    request_id: uuid.UUID,
    agent: AgentDep,
    db: AsyncSession = Depends(get_db),
) -> AuthorizeResponse:
    result = await db.execute(
        select(AuthorizationRequest).where(
            AuthorizationRequest.id == request_id,
            AuthorizationRequest.agent_id == agent.id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return AuthorizeResponse.model_validate(req)


@router.delete(
    "/authorize/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a pending authorization request",
    description="Agent cancels a request it previously submitted that is still pending.",
)
async def cancel_authorization_request(
    request_id: uuid.UUID,
    agent: AgentDep,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(AuthorizationRequest).where(
            AuthorizationRequest.id == request_id,
            AuthorizationRequest.agent_id == agent.id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a request with status '{req.status}'",
        )
    req.status = "cancelled"
    req.resolved_at = datetime.now(timezone.utc)
    req.resolved_by = "agent"
    await audit.log_event(db, "request_cancelled", agent_id=agent.id, request_id=req.id)
    await db.commit()
