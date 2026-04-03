import secrets
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import UserDep, _hash_api_key
from app.models.database import Agent
from app.models.schemas import AgentCreate, AgentCreateResponse, AgentResponse, AgentUpdate
from app.plans import PLAN_LIMITS, UPGRADE_URL, get_next_plan
from app.services import audit, usage

router = APIRouter()


@router.get(
    "/agents",
    response_model=List[AgentResponse],
    summary="List my agents",
)
async def list_agents(
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> List[AgentResponse]:
    result = await db.execute(
        select(Agent)
        .where(Agent.user_id == user.id)
        .order_by(Agent.created_at.desc())
    )
    agents = result.scalars().all()
    return [AgentResponse.model_validate(a) for a in agents]


@router.post(
    "/agents",
    response_model=AgentCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new agent",
    description=(
        "Creates a new agent and returns its API key. "
        "The API key is only returned once — store it securely."
    ),
)
async def create_agent(
    body: AgentCreate,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> AgentCreateResponse:
    plan = getattr(user, "plan", "free") or "free"
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])["max_agents"]
    if limit is not None:
        count = await usage.get_active_agents(user.id, db)
        if count >= limit:
            next_plan = get_next_plan(plan)
            next_limits = PLAN_LIMITS.get(next_plan or "", {})
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "agent_limit_reached",
                    "current_plan": plan,
                    "agents_active": count,
                    "agents_limit": limit,
                    "upgrade_to": next_plan,
                    "upgrade_limit": next_limits.get("max_agents"),
                    "upgrade_url": UPGRADE_URL,
                },
            )

    raw_key = f"sk-ag-{secrets.token_hex(32)}"
    key_hash = _hash_api_key(raw_key)
    key_prefix = raw_key[:16] + "..."

    agent = Agent(
        id=uuid.uuid4(),
        user_id=user.id,
        name=body.name,
        api_key_hash=key_hash,
        api_key_prefix=key_prefix,
        status="active",
        metadata_=body.metadata,
        created_at=datetime.now(timezone.utc),
    )
    db.add(agent)
    await audit.log_event(db, "agent_registered", agent_id=agent.id, user_id=user.id)
    await db.commit()
    await db.refresh(agent)

    response = AgentResponse.model_validate(agent)
    return AgentCreateResponse(**response.model_dump(), api_key=raw_key)


@router.patch(
    "/agents/{agent_id}",
    response_model=AgentResponse,
    summary="Update an agent",
)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if body.name is not None:
        agent.name = body.name
    if body.status is not None:
        agent.status = body.status.value

    await db.commit()
    await db.refresh(agent)
    return AgentResponse.model_validate(agent)


@router.delete(
    "/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an agent",
    description="Sets the agent's status to 'revoked'. The record is preserved for audit history.",
)
async def revoke_agent(
    agent_id: uuid.UUID,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    agent.status = "revoked"
    await audit.log_event(db, "agent_revoked", agent_id=agent.id, user_id=user.id)
    await db.commit()
