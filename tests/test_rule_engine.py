"""Unit tests for the rule evaluation engine.

All tests are pure — no HTTP client, no DB connection. Rules and requests are
constructed as SimpleNamespace objects (no SQLAlchemy required). Aggregate-spend
tests mock db.execute() to return a predetermined spend amount.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas import AuthorizeRequest
from app.services.rule_engine import evaluate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rule(rule_type: str, value: dict) -> SimpleNamespace:
    """Create a Rule-like object without requiring SQLAlchemy ORM setup."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        rule_type=rule_type,
        value=value,
        is_active=True,
    )


def make_request(**kwargs) -> AuthorizeRequest:
    defaults = {
        "action": "purchase",
        "amount": Decimal("25.00"),
        "vendor": "AWS",
        "category": "cloud_services",
    }
    defaults.update(kwargs)
    return AuthorizeRequest(**defaults)


def mock_db_zero_spend() -> AsyncMock:
    """DB mock returning 0 for aggregate spend queries."""
    db = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar.return_value = 0
    db.execute.return_value = scalar_result
    return db


def mock_db_spend(amount: Decimal) -> AsyncMock:
    """DB mock returning a specific spend amount for aggregate queries."""
    db = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar.return_value = float(amount)
    db.execute.return_value = scalar_result
    return db


# ---------------------------------------------------------------------------
# Step 1: blocked_vendors / blocked_categories → DENY
# ---------------------------------------------------------------------------

async def test_blocked_vendor_returns_denied():
    rules = [make_rule("blocked_vendors", {"vendors": ["AWS", "Azure"]})]
    result = await evaluate(make_request(vendor="AWS"), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "denied"
    assert result.matched_rule_type == "blocked_vendors"


async def test_blocked_vendor_case_insensitive():
    rules = [make_rule("blocked_vendors", {"vendors": ["aws"]})]
    result = await evaluate(make_request(vendor="AWS"), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "denied"


async def test_non_blocked_vendor_passes():
    rules = [make_rule("blocked_vendors", {"vendors": ["Azure"]})]
    result = await evaluate(make_request(vendor="AWS"), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"


async def test_blocked_category_returns_denied():
    rules = [make_rule("blocked_categories", {"categories": ["gambling", "crypto"]})]
    result = await evaluate(make_request(category="gambling"), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "denied"
    assert result.matched_rule_type == "blocked_categories"


# ---------------------------------------------------------------------------
# Step 2: auto_approve_below → AUTO_APPROVE
# ---------------------------------------------------------------------------

async def test_auto_approve_below_threshold():
    rules = [make_rule("auto_approve_below", {"amount": 50.0})]
    result = await evaluate(make_request(amount=Decimal("25.00")), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "auto_approved"
    assert result.matched_rule_type == "auto_approve_below"


async def test_auto_approve_not_fired_at_threshold():
    """Amount exactly equal to threshold is NOT auto-approved (strict less-than)."""
    rules = [make_rule("auto_approve_below", {"amount": 25.0})]
    # 25.00 is NOT < 25.00, so auto_approve_below doesn't fire → default pending
    result = await evaluate(make_request(amount=Decimal("25.00")), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"  # default
    assert result.matched_rule_type is None     # via default, not auto_approve_below


async def test_auto_approve_skipped_when_no_amount():
    """If no amount is provided, auto_approve_below doesn't fire."""
    rules = [make_rule("auto_approve_below", {"amount": 50.0})]
    result = await evaluate(make_request(amount=None), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"  # falls to default


# ---------------------------------------------------------------------------
# Step 3: require_approval_above → PENDING
# ---------------------------------------------------------------------------

async def test_require_approval_above_threshold():
    rules = [make_rule("require_approval_above", {"amount": 100.0})]
    result = await evaluate(make_request(amount=Decimal("150.00")), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"
    assert result.matched_rule_type == "require_approval_above"


async def test_require_approval_at_threshold():
    """Amount exactly at threshold IS flagged (>=)."""
    rules = [make_rule("require_approval_above", {"amount": 100.0})]
    result = await evaluate(make_request(amount=Decimal("100.00")), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"


# ---------------------------------------------------------------------------
# Step 4: max_per_transaction → PENDING
# ---------------------------------------------------------------------------

async def test_max_per_transaction_exceeded():
    rules = [make_rule("max_per_transaction", {"amount": 20.0})]
    result = await evaluate(make_request(amount=Decimal("25.00")), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"
    assert result.matched_rule_type == "max_per_transaction"


async def test_max_per_transaction_at_limit_passes():
    """Amount exactly at limit is NOT flagged (strict >)."""
    rules = [make_rule("max_per_transaction", {"amount": 25.0})]
    result = await evaluate(make_request(amount=Decimal("25.00")), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"


# ---------------------------------------------------------------------------
# Step 5: max_per_day (aggregate)
# ---------------------------------------------------------------------------

async def test_max_per_day_would_exceed():
    rules = [make_rule("max_per_day", {"amount": 100.0})]
    db = mock_db_spend(Decimal("80.00"))  # already spent 80 today
    result = await evaluate(make_request(amount=Decimal("30.00")), uuid.uuid4(), rules, db)
    assert result.decision == "pending"
    assert result.matched_rule_type == "max_per_day"


async def test_max_per_day_within_limit():
    rules = [make_rule("max_per_day", {"amount": 100.0})]
    db = mock_db_spend(Decimal("50.00"))  # already spent 50 today
    result = await evaluate(make_request(amount=Decimal("10.00")), uuid.uuid4(), rules, db)
    assert result.decision == "pending"


# ---------------------------------------------------------------------------
# Step 6: max_per_month (aggregate)
# ---------------------------------------------------------------------------

async def test_max_per_month_would_exceed():
    rules = [make_rule("max_per_month", {"amount": 500.0})]
    db = mock_db_spend(Decimal("450.00"))  # already spent 450 this month
    result = await evaluate(make_request(amount=Decimal("100.00")), uuid.uuid4(), rules, db)
    assert result.decision == "pending"
    assert result.matched_rule_type == "max_per_month"


# ---------------------------------------------------------------------------
# Step 7: allowed_vendors / allowed_categories → PENDING if not whitelisted
# ---------------------------------------------------------------------------

async def test_allowed_vendors_vendor_not_in_list():
    rules = [make_rule("allowed_vendors", {"vendors": ["AWS", "GCP"]})]
    result = await evaluate(make_request(vendor="DigitalOcean"), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"
    assert result.matched_rule_type == "allowed_vendors"


async def test_allowed_vendors_vendor_in_list():
    rules = [make_rule("allowed_vendors", {"vendors": ["AWS", "GCP"]})]
    result = await evaluate(make_request(vendor="AWS"), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"


async def test_allowed_categories_category_not_in_list():
    rules = [make_rule("allowed_categories", {"categories": ["cloud_services"]})]
    result = await evaluate(make_request(category="office_supplies"), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "pending"
    assert result.matched_rule_type == "allowed_categories"


# ---------------------------------------------------------------------------
# Step 8: Default → PENDING
# ---------------------------------------------------------------------------

async def test_default_no_rules_returns_pending():
    result = await evaluate(make_request(), uuid.uuid4(), [], mock_db_zero_spend())
    assert result.decision == "pending"
    assert result.matched_rule_type is None


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

async def test_block_takes_priority_over_auto_approve():
    """blocked_vendors (step 1) fires before auto_approve_below (step 2)."""
    rules = [
        make_rule("blocked_vendors", {"vendors": ["AWS"]}),
        make_rule("auto_approve_below", {"amount": 1000.0}),
    ]
    result = await evaluate(make_request(vendor="AWS", amount=Decimal("5.00")), uuid.uuid4(), rules, mock_db_zero_spend())
    assert result.decision == "denied"


async def test_evaluation_log_is_populated():
    rules = [make_rule("max_per_transaction", {"amount": 10.0})]
    result = await evaluate(make_request(amount=Decimal("25.00")), uuid.uuid4(), rules, mock_db_zero_spend())
    assert len(result.evaluation_log) > 0
    assert any(e["rule_type"] == "max_per_transaction" for e in result.evaluation_log)
