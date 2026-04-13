import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import UserDep
from app.models.database import Agent, Rule, RuleBackup
from app.models.schemas import (
    ApplyTemplateRequest,
    RuleBackupResponse,
    RuleCreate,
    RuleResponse,
    RuleTemplate,
    RuleTemplateRule,
    RuleType,
    RuleUpdate,
)
from app.services import audit

_TEMPLATES: list[RuleTemplate] = [
    RuleTemplate(
        name="Conservative",
        description="Tight controls: requires approval for anything over $25, hard daily/monthly caps.",
        risk_level="low",
        rules=[
            RuleTemplateRule(rule_type=RuleType.require_approval_above, value={"amount": 25.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_day, value={"amount": 100.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_month, value={"amount": 500.0}),
        ],
    ),
    RuleTemplate(
        name="Moderate",
        description="Balanced: auto-approves small purchases, flags large ones, reasonable spend caps.",
        risk_level="medium",
        rules=[
            RuleTemplateRule(rule_type=RuleType.auto_approve_below, value={"amount": 50.0}),
            RuleTemplateRule(rule_type=RuleType.require_approval_above, value={"amount": 200.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_day, value={"amount": 500.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_month, value={"amount": 2000.0}),
        ],
    ),
    RuleTemplate(
        name="Permissive",
        description="Minimal friction: auto-approves most purchases, only blocks truly large spends.",
        risk_level="high",
        rules=[
            RuleTemplateRule(rule_type=RuleType.auto_approve_below, value={"amount": 500.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_day, value={"amount": 2000.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_month, value={"amount": 10000.0}),
        ],
    ),
    RuleTemplate(
        name="Online Shopping",
        description="E-commerce focused: auto-approves small purchases, flags mid-range, pending review above $500 with a daily cap.",
        risk_level="medium",
        rules=[
            RuleTemplateRule(rule_type=RuleType.auto_approve_below, value={"amount": 50.0}),
            RuleTemplateRule(rule_type=RuleType.require_approval_above, value={"amount": 50.0}),
            RuleTemplateRule(rule_type=RuleType.require_approval_above, value={"amount": 500.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_day, value={"amount": 300.0}),
        ],
    ),
    RuleTemplate(
        name="Procurement",
        description="Business purchasing: auto-approves small orders from any vendor, flags larger purchases, with a daily spend cap.",
        risk_level="medium",
        rules=[
            RuleTemplateRule(rule_type=RuleType.auto_approve_below, value={"amount": 100.0}),
            RuleTemplateRule(rule_type=RuleType.require_approval_above, value={"amount": 100.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_day, value={"amount": 1000.0}),
        ],
    ),
    RuleTemplate(
        name="Ad Spending",
        description="Advertising budget control: auto-approves small ad buys, flags anything larger, with a tight daily cap.",
        risk_level="medium",
        rules=[
            RuleTemplateRule(rule_type=RuleType.auto_approve_below, value={"amount": 25.0}),
            RuleTemplateRule(rule_type=RuleType.require_approval_above, value={"amount": 25.0}),
            RuleTemplateRule(rule_type=RuleType.max_per_day, value={"amount": 200.0}),
        ],
    ),
]

router = APIRouter()


async def _get_agent_for_user(agent_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Agent:
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


async def _get_rule_for_user(rule_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Rule:
    result = await db.execute(
        select(Rule)
        .join(Agent, Rule.agent_id == Agent.id)
        .where(Rule.id == rule_id, Agent.user_id == user_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return rule


async def _create_backup(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    current_rules: list[Rule],
    template_applied: str | None = None,
) -> RuleBackup:
    rules_json = [
        {"rule_type": r.rule_type, "value": r.value, "is_active": r.is_active}
        for r in current_rules
    ]
    backup = RuleBackup(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=user_id,
        rules_json=rules_json,
        template_applied=template_applied,
        created_at=datetime.now(timezone.utc),
    )
    db.add(backup)
    return backup


@router.get(
    "/rules/templates",
    response_model=list[RuleTemplate],
    summary="List rule templates",
    description="Returns preset rule configurations: Conservative, Moderate, Permissive, Online Shopping, Procurement, and Ad Spending.",
)
async def list_rule_templates() -> list[RuleTemplate]:
    return _TEMPLATES


@router.get(
    "/agents/{agent_id}/rules",
    response_model=List[RuleResponse],
    summary="List rules for an agent",
)
async def list_rules(
    agent_id: uuid.UUID,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> List[RuleResponse]:
    await _get_agent_for_user(agent_id, user.id, db)
    result = await db.execute(
        select(Rule).where(Rule.agent_id == agent_id).order_by(Rule.created_at)
    )
    rules = result.scalars().all()
    return [RuleResponse.model_validate(r) for r in rules]


@router.post(
    "/agents/{agent_id}/rules",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a rule to an agent",
)
async def create_rule(
    agent_id: uuid.UUID,
    body: RuleCreate,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    agent = await _get_agent_for_user(agent_id, user.id, db)

    rule = Rule(
        id=uuid.uuid4(),
        agent_id=agent.id,
        rule_type=body.rule_type.value,
        value=body.value,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(rule)
    await audit.log_event(
        db,
        "rule_created",
        agent_id=agent.id,
        user_id=user.id,
        details={"rule_type": body.rule_type.value, "value": body.value},
    )
    await db.commit()
    await db.refresh(rule)
    return RuleResponse.model_validate(rule)


@router.patch(
    "/rules/{rule_id}",
    response_model=RuleResponse,
    summary="Update a rule",
)
async def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdate,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    rule = await _get_rule_for_user(rule_id, user.id, db)

    changed: dict = {}
    if body.value is not None:
        changed["value"] = {"before": rule.value, "after": body.value}
        rule.value = body.value
    if body.is_active is not None:
        changed["is_active"] = {"before": rule.is_active, "after": body.is_active}
        rule.is_active = body.is_active

    if changed:
        await audit.log_event(
            db,
            "rules_changed",
            agent_id=rule.agent_id,
            user_id=user.id,
            details={"source": "manual_edit", "rule_id": str(rule_id), "changes": changed},
        )

    await db.commit()
    await db.refresh(rule)
    return RuleResponse.model_validate(rule)


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a rule",
)
async def delete_rule(
    rule_id: uuid.UUID,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> None:
    rule = await _get_rule_for_user(rule_id, user.id, db)
    await audit.log_event(
        db,
        "rule_deleted",
        agent_id=rule.agent_id,
        user_id=user.id,
        details={"rule_type": rule.rule_type},
    )
    await db.delete(rule)
    await db.commit()


@router.post(
    "/agents/{agent_id}/rules/template",
    response_model=List[RuleResponse],
    summary="Apply a rule template to an agent",
    description="Backs up the agent's current rules, then replaces them with the named template.",
)
async def apply_template(
    agent_id: uuid.UUID,
    body: ApplyTemplateRequest,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> List[RuleResponse]:
    agent = await _get_agent_for_user(agent_id, user.id, db)

    template = next((t for t in _TEMPLATES if t.name.lower() == body.template_name.lower()), None)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{body.template_name}' not found",
        )

    result = await db.execute(
        select(Rule).where(Rule.agent_id == agent_id).order_by(Rule.created_at)
    )
    current_rules = list(result.scalars().all())

    await _create_backup(db, agent.id, user.id, current_rules, template_applied=template.name)

    for rule in current_rules:
        await db.delete(rule)

    new_rules: list[Rule] = []
    for template_rule in template.rules:
        rule = Rule(
            id=uuid.uuid4(),
            agent_id=agent.id,
            rule_type=template_rule.rule_type.value,
            value=template_rule.value,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(rule)
        new_rules.append(rule)

    await audit.log_event(
        db,
        "rules_changed",
        agent_id=agent.id,
        user_id=user.id,
        details={
            "source": "template",
            "template_name": template.name,
            "previous_rule_count": len(current_rules),
            "new_rule_count": len(new_rules),
        },
    )

    await db.commit()
    for rule in new_rules:
        await db.refresh(rule)

    return [RuleResponse.model_validate(r) for r in new_rules]


@router.get(
    "/agents/{agent_id}/rules/backup",
    response_model=RuleBackupResponse,
    summary="Get the most recent rule backup for an agent",
)
async def get_rule_backup(
    agent_id: uuid.UUID,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> RuleBackupResponse:
    await _get_agent_for_user(agent_id, user.id, db)

    result = await db.execute(
        select(RuleBackup)
        .where(RuleBackup.agent_id == agent_id, RuleBackup.user_id == user.id)
        .order_by(desc(RuleBackup.created_at))
        .limit(1)
    )
    backup = result.scalar_one_or_none()
    if backup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No backup found")

    return RuleBackupResponse.model_validate(backup)


@router.post(
    "/agents/{agent_id}/rules/restore",
    response_model=List[RuleResponse],
    summary="Restore rules from the most recent backup",
    description="Deletes current rules and recreates them from the most recent backup.",
)
async def restore_rules(
    agent_id: uuid.UUID,
    user: UserDep,
    db: AsyncSession = Depends(get_db),
) -> List[RuleResponse]:
    agent = await _get_agent_for_user(agent_id, user.id, db)

    result = await db.execute(
        select(RuleBackup)
        .where(RuleBackup.agent_id == agent_id, RuleBackup.user_id == user.id)
        .order_by(desc(RuleBackup.created_at))
        .limit(1)
    )
    backup = result.scalar_one_or_none()
    if backup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No backup found")

    existing = await db.execute(
        select(Rule).where(Rule.agent_id == agent_id)
    )
    for rule in existing.scalars().all():
        await db.delete(rule)

    restored: list[Rule] = []
    for entry in backup.rules_json:
        rule = Rule(
            id=uuid.uuid4(),
            agent_id=agent.id,
            rule_type=entry["rule_type"],
            value=entry["value"],
            is_active=entry.get("is_active", True),
            created_at=datetime.now(timezone.utc),
        )
        db.add(rule)
        restored.append(rule)

    await audit.log_event(
        db,
        "rules_changed",
        agent_id=agent.id,
        user_id=user.id,
        details={"source": "restore", "backup_id": str(backup.id), "rule_count": len(restored)},
    )

    await db.commit()
    for rule in restored:
        await db.refresh(rule)

    return [RuleResponse.model_validate(r) for r in restored]
