"""Tests for GET /v1/activity/export"""

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.api.activity import export_limiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_req(
    *,
    agent_name: str = "Test Agent",
    vendor: str = "AWS",
    amount: Decimal = Decimal("99.00"),
    currency: str = "USD",
    category: str = "cloud",
    status: str = "approved",
    rule_matched: str | None = "require_approval_above",
    resolved_by: str | None = "human",
):
    req = MagicMock()
    req.id = uuid.uuid4()
    req.agent_id = uuid.uuid4()
    req.vendor = vendor
    req.amount = amount
    req.currency = currency
    req.category = category
    req.status = status
    req.rule_evaluation = {"matched_rule_type": rule_matched} if rule_matched else None
    req.resolved_by = resolved_by
    req.resolved_at = datetime.now(timezone.utc)
    req.created_at = datetime.now(timezone.utc)
    return (req, agent_name)


def _mock_rows(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.fixture(autouse=True)
def reset_limiter():
    export_limiter.reset()
    yield
    export_limiter.reset()


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

async def test_export_csv_returns_attachment(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=_mock_rows([_make_req()]))

    r = await user_client.get("/v1/activity/export?format=csv")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert ".csv" in r.headers["content-disposition"]


async def test_export_csv_columns_and_values(user_client: AsyncClient, mock_db):
    row = _make_req(vendor="Stripe", amount=Decimal("42.50"), status="approved")
    mock_db.execute = AsyncMock(return_value=_mock_rows([row]))

    r = await user_client.get("/v1/activity/export?format=csv")
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)

    assert len(rows) == 1
    assert rows[0]["vendor"] == "Stripe"
    assert rows[0]["amount"] == "42.5"
    assert rows[0]["status"] == "approved"
    assert rows[0]["agent_name"] == "Test Agent"
    assert rows[0]["reviewed_by"] == "human"
    assert rows[0]["rule_matched"] == "require_approval_above"


async def test_export_csv_empty_results(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=_mock_rows([]))

    r = await user_client.get("/v1/activity/export?format=csv")

    assert r.status_code == 200
    reader = csv.DictReader(io.StringIO(r.text))
    # Header only — no data rows
    assert list(reader) == []
    assert "timestamp" in r.text


async def test_export_csv_null_rule_evaluation(user_client: AsyncClient, mock_db):
    row = _make_req(rule_matched=None)
    mock_db.execute = AsyncMock(return_value=_mock_rows([row]))

    r = await user_client.get("/v1/activity/export?format=csv")
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)

    assert rows[0]["rule_matched"] == ""


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

async def test_export_json_returns_attachment(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=_mock_rows([_make_req()]))

    r = await user_client.get("/v1/activity/export?format=json")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    assert ".json" in r.headers["content-disposition"]


async def test_export_json_structure(user_client: AsyncClient, mock_db):
    row = _make_req(vendor="OpenAI", amount=Decimal("5.00"), status="auto_approved")
    mock_db.execute = AsyncMock(return_value=_mock_rows([row]))

    r = await user_client.get("/v1/activity/export?format=json")
    data = json.loads(r.content)

    assert isinstance(data, list)
    assert len(data) == 1
    item = data[0]
    assert item["vendor"] == "OpenAI"
    assert item["amount"] == 5.0
    assert item["status"] == "auto_approved"
    for field in ("timestamp", "agent_id", "agent_name", "vendor", "amount",
                  "currency", "category", "status", "rule_matched",
                  "reviewed_by", "reviewed_at"):
        assert field in item


async def test_export_json_multiple_rows(user_client: AsyncClient, mock_db):
    rows = [_make_req(vendor=f"vendor-{i}") for i in range(5)]
    mock_db.execute = AsyncMock(return_value=_mock_rows(rows))

    r = await user_client.get("/v1/activity/export?format=json")
    data = json.loads(r.content)

    assert len(data) == 5


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

async def test_export_invalid_format_rejected(user_client: AsyncClient, mock_db):
    r = await user_client.get("/v1/activity/export?format=xml")

    assert r.status_code == 422


async def test_export_format_required(user_client: AsyncClient, mock_db):
    r = await user_client.get("/v1/activity/export")

    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Query filters passed through (verify db.execute is called once)
# ---------------------------------------------------------------------------

async def test_export_with_status_filter(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=_mock_rows([]))

    r = await user_client.get("/v1/activity/export?format=csv&status=approved")

    assert r.status_code == 200
    mock_db.execute.assert_awaited_once()


async def test_export_with_agent_id_filter(user_client: AsyncClient, mock_db):
    agent_id = uuid.uuid4()
    mock_db.execute = AsyncMock(return_value=_mock_rows([]))

    r = await user_client.get(f"/v1/activity/export?format=csv&agent_id={agent_id}")

    assert r.status_code == 200
    mock_db.execute.assert_awaited_once()


async def test_export_with_date_range(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=_mock_rows([]))

    r = await user_client.get(
        "/v1/activity/export?format=json"
        "&start_date=2025-01-01T00:00:00Z"
        "&end_date=2025-12-31T23:59:59Z"
    )

    assert r.status_code == 200
    mock_db.execute.assert_awaited_once()


async def test_export_with_category_filter(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=_mock_rows([]))

    r = await user_client.get("/v1/activity/export?format=csv&category=cloud_services")

    assert r.status_code == 200
    mock_db.execute.assert_awaited_once()


async def test_export_invalid_agent_id_rejected(user_client: AsyncClient, mock_db):
    r = await user_client.get("/v1/activity/export?format=csv&agent_id=not-a-uuid")

    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

async def test_export_rate_limit_allows_ten(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=_mock_rows([]))

    for _ in range(10):
        r = await user_client.get("/v1/activity/export?format=csv")
        assert r.status_code == 200


async def test_export_rate_limit_blocks_eleventh(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=_mock_rows([]))

    for _ in range(10):
        await user_client.get("/v1/activity/export?format=csv")

    r = await user_client.get("/v1/activity/export?format=csv")
    assert r.status_code == 429
