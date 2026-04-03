"""Rule evaluation engine.

Evaluates an authorization request against an agent's active rules and returns
a decision: auto_approved, denied, or pending (needs human review).

Priority order:
  1. blocked_vendors / blocked_categories  → DENY  (immediate)
  2. auto_approve_below                    → AUTO_APPROVE (if amount < threshold)
  3. require_approval_above                → PENDING (if amount >= threshold)
  4. max_per_transaction                   → PENDING (if amount > limit)
  5. max_per_day  (aggregate DB query)     → PENDING (if would exceed daily limit)
  6. max_per_month (aggregate DB query)    → PENDING (if would exceed monthly limit)
  7. allowed_vendors / allowed_categories  → PENDING (if not in whitelist)
  8. Default                               → PENDING
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AuthorizationRequest, Rule
from app.models.schemas import AuthorizeRequest


@dataclass
class EvaluationResult:
    decision: str  # "auto_approved" | "denied" | "pending"
    matched_rule_type: Optional[str]
    reason: str
    evaluation_log: list[dict] = field(default_factory=list)


async def evaluate(
    request: AuthorizeRequest,
    agent_id: uuid.UUID,
    rules: list[Rule],
    db: AsyncSession,
) -> EvaluationResult:
    """Evaluate an authorization request against an agent's active rules.

    Processes rules in priority order and short-circuits on the first decision.
    All evaluated steps are recorded in EvaluationResult.evaluation_log for
    the audit trail stored in authorization_requests.rule_evaluation.
    """
    log: list[dict] = []
    amount = request.amount
    vendor = (request.vendor or "").lower()
    category = (request.category or "").lower()

    # ------------------------------------------------------------------
    # Step 1: blocked_vendors / blocked_categories → DENY
    # ------------------------------------------------------------------
    for rule in _rules_of_type(rules, "blocked_vendors"):
        blocked = [v.lower() for v in rule.value.get("vendors", [])]
        if vendor and vendor in blocked:
            log.append({"step": 1, "rule_type": "blocked_vendors", "matched": True})
            return EvaluationResult(
                decision="denied",
                matched_rule_type="blocked_vendors",
                reason=f"Vendor '{request.vendor}' is blocked",
                evaluation_log=log,
            )
        log.append({"step": 1, "rule_type": "blocked_vendors", "matched": False})

    for rule in _rules_of_type(rules, "blocked_categories"):
        blocked = [c.lower() for c in rule.value.get("categories", [])]
        if category and category in blocked:
            log.append({"step": 1, "rule_type": "blocked_categories", "matched": True})
            return EvaluationResult(
                decision="denied",
                matched_rule_type="blocked_categories",
                reason=f"Category '{request.category}' is blocked",
                evaluation_log=log,
            )
        log.append({"step": 1, "rule_type": "blocked_categories", "matched": False})

    # ------------------------------------------------------------------
    # Step 2: auto_approve_below → AUTO_APPROVE
    # ------------------------------------------------------------------
    for rule in _rules_of_type(rules, "auto_approve_below"):
        threshold = Decimal(str(rule.value["amount"]))
        if amount is not None and amount < threshold:
            log.append({"step": 2, "rule_type": "auto_approve_below", "matched": True, "threshold": str(threshold)})
            return EvaluationResult(
                decision="auto_approved",
                matched_rule_type="auto_approve_below",
                reason=f"Amount {amount} is below auto-approve threshold {threshold}",
                evaluation_log=log,
            )
        log.append({"step": 2, "rule_type": "auto_approve_below", "matched": False})

    # ------------------------------------------------------------------
    # Step 3: require_approval_above → PENDING
    # ------------------------------------------------------------------
    for rule in _rules_of_type(rules, "require_approval_above"):
        threshold = Decimal(str(rule.value["amount"]))
        if amount is not None and amount >= threshold:
            log.append({"step": 3, "rule_type": "require_approval_above", "matched": True, "threshold": str(threshold)})
            return EvaluationResult(
                decision="pending",
                matched_rule_type="require_approval_above",
                reason=f"Amount {amount} requires human approval (threshold: {threshold})",
                evaluation_log=log,
            )
        log.append({"step": 3, "rule_type": "require_approval_above", "matched": False})

    # ------------------------------------------------------------------
    # Step 4: max_per_transaction → PENDING
    # ------------------------------------------------------------------
    for rule in _rules_of_type(rules, "max_per_transaction"):
        limit = Decimal(str(rule.value["amount"]))
        if amount is not None and amount > limit:
            log.append({"step": 4, "rule_type": "max_per_transaction", "matched": True, "limit": str(limit)})
            return EvaluationResult(
                decision="pending",
                matched_rule_type="max_per_transaction",
                reason=f"Amount {amount} exceeds per-transaction limit {limit}",
                evaluation_log=log,
            )
        log.append({"step": 4, "rule_type": "max_per_transaction", "matched": False})

    # ------------------------------------------------------------------
    # Step 5: max_per_day → PENDING (aggregate query)
    # ------------------------------------------------------------------
    for rule in _rules_of_type(rules, "max_per_day"):
        daily_limit = Decimal(str(rule.value["amount"]))
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_spend = await _get_spend_since(db, agent_id, today_start)
        projected = daily_spend + (amount or Decimal("0"))
        if projected > daily_limit:
            log.append({
                "step": 5, "rule_type": "max_per_day", "matched": True,
                "limit": str(daily_limit), "current_spend": str(daily_spend),
            })
            return EvaluationResult(
                decision="pending",
                matched_rule_type="max_per_day",
                reason=f"Would exceed daily limit {daily_limit} (current spend: {daily_spend})",
                evaluation_log=log,
            )
        log.append({"step": 5, "rule_type": "max_per_day", "matched": False})

    # ------------------------------------------------------------------
    # Step 6: max_per_month → PENDING (aggregate query)
    # ------------------------------------------------------------------
    for rule in _rules_of_type(rules, "max_per_month"):
        monthly_limit = Decimal(str(rule.value["amount"]))
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_spend = await _get_spend_since(db, agent_id, month_start)
        projected = monthly_spend + (amount or Decimal("0"))
        if projected > monthly_limit:
            log.append({
                "step": 6, "rule_type": "max_per_month", "matched": True,
                "limit": str(monthly_limit), "current_spend": str(monthly_spend),
            })
            return EvaluationResult(
                decision="pending",
                matched_rule_type="max_per_month",
                reason=f"Would exceed monthly limit {monthly_limit} (current spend: {monthly_spend})",
                evaluation_log=log,
            )
        log.append({"step": 6, "rule_type": "max_per_month", "matched": False})

    # ------------------------------------------------------------------
    # Step 7: allowed_vendors / allowed_categories → PENDING if not whitelisted
    # ------------------------------------------------------------------
    for rule in _rules_of_type(rules, "allowed_vendors"):
        allowed = [v.lower() for v in rule.value.get("vendors", [])]
        if vendor and vendor not in allowed:
            log.append({"step": 7, "rule_type": "allowed_vendors", "matched": True})
            return EvaluationResult(
                decision="pending",
                matched_rule_type="allowed_vendors",
                reason=f"Vendor '{request.vendor}' is not in the allowed vendors list",
                evaluation_log=log,
            )
        log.append({"step": 7, "rule_type": "allowed_vendors", "matched": False})

    for rule in _rules_of_type(rules, "allowed_categories"):
        allowed = [c.lower() for c in rule.value.get("categories", [])]
        if category and category not in allowed:
            log.append({"step": 7, "rule_type": "allowed_categories", "matched": True})
            return EvaluationResult(
                decision="pending",
                matched_rule_type="allowed_categories",
                reason=f"Category '{request.category}' is not in the allowed categories list",
                evaluation_log=log,
            )
        log.append({"step": 7, "rule_type": "allowed_categories", "matched": False})

    # ------------------------------------------------------------------
    # Step 8: Default → PENDING
    # No explicit auto-approve rule matched — require human review until
    # the user configures auto_approve_below or similar thresholds.
    # ------------------------------------------------------------------
    log.append({"step": 8, "rule_type": "default", "matched": True})
    return EvaluationResult(
        decision="pending",
        matched_rule_type=None,
        reason="No rules matched — pending human review",
        evaluation_log=log,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rules_of_type(rules: list[Rule], rule_type: str) -> list[Rule]:
    return [r for r in rules if r.rule_type == rule_type and r.is_active]


async def _get_spend_since(db: AsyncSession, agent_id: uuid.UUID, since: datetime) -> Decimal:
    """Sum approved transaction amounts for an agent since a given datetime."""
    result = await db.execute(
        select(func.coalesce(func.sum(AuthorizationRequest.amount), 0)).where(
            AuthorizationRequest.agent_id == agent_id,
            AuthorizationRequest.status.in_(["auto_approved", "approved"]),
            AuthorizationRequest.created_at >= since,
            AuthorizationRequest.amount.isnot(None),
        )
    )
    return Decimal(str(result.scalar()))
