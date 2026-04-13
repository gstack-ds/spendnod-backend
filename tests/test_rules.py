"""Tests for GET/POST /v1/agents/{id}/rules and PATCH/DELETE /v1/rules/{id}."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.database import Agent, Rule, RuleBackup


def _make_rule(agent_id: uuid.UUID, rule_type: str = "max_per_transaction") -> Rule:
    return Rule(
        id=uuid.uuid4(),
        agent_id=agent_id,
        rule_type=rule_type,
        value={"amount": 100.0},
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


def mock_scalar(obj) -> MagicMock:
    m = MagicMock()
    m.scalar_one_or_none.return_value = obj
    return m


def mock_scalars_all(items: list) -> MagicMock:
    m = MagicMock()
    m.scalars.return_value.all.return_value = items
    return m


# ---------------------------------------------------------------------------
# GET /v1/agents/{id}/rules
# ---------------------------------------------------------------------------

async def test_list_rules_returns_rules(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    rules = [_make_rule(agent.id), _make_rule(agent.id)]

    mock_db.execute = AsyncMock(side_effect=[mock_scalar(agent), mock_scalars_all(rules)])

    response = await user_client.get(f"/v1/agents/{agent.id}/rules")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_list_rules_agent_not_found(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_scalar(None))

    response = await user_client.get(f"/v1/agents/{uuid.uuid4()}/rules")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/agents/{id}/rules
# ---------------------------------------------------------------------------

async def test_create_rule_returns_201(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    saved_rule = _make_rule(agent.id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(agent))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.rules.Rule", return_value=saved_rule):
        response = await user_client.post(
            f"/v1/agents/{agent.id}/rules",
            json={"rule_type": "max_per_transaction", "value": {"amount": 100.0}},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["rule_type"] == "max_per_transaction"


async def test_create_rule_agent_not_found(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_scalar(None))

    response = await user_client.post(
        f"/v1/agents/{uuid.uuid4()}/rules",
        json={"rule_type": "max_per_transaction", "value": {"amount": 100.0}},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /v1/rules/{id}
# ---------------------------------------------------------------------------

async def test_update_rule_value(user_client: AsyncClient, mock_user, mock_db):
    agent_id = uuid.uuid4()
    rule = _make_rule(agent_id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(rule))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.patch(
        f"/v1/rules/{rule.id}", json={"value": {"amount": 250.0}}
    )
    assert response.status_code == 200
    assert rule.value == {"amount": 250.0}


async def test_update_rule_toggle_active(user_client: AsyncClient, mock_user, mock_db):
    agent_id = uuid.uuid4()
    rule = _make_rule(agent_id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(rule))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.patch(f"/v1/rules/{rule.id}", json={"is_active": False})
    assert response.status_code == 200
    assert rule.is_active is False


async def test_update_rule_not_found(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_scalar(None))

    response = await user_client.patch(
        f"/v1/rules/{uuid.uuid4()}", json={"is_active": False}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /v1/rules/{id}
# ---------------------------------------------------------------------------

async def test_delete_rule_returns_204(user_client: AsyncClient, mock_user, mock_db):
    agent_id = uuid.uuid4()
    rule = _make_rule(agent_id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(rule))
    mock_db.delete = AsyncMock()

    response = await user_client.delete(f"/v1/rules/{rule.id}")
    assert response.status_code == 204
    mock_db.delete.assert_called_once_with(rule)


# ---------------------------------------------------------------------------
# GET /v1/rules/templates — risk_level field
# ---------------------------------------------------------------------------

async def test_templates_include_risk_level(client):
    response = await client.get("/v1/rules/templates")
    assert response.status_code == 200
    templates = response.json()
    names_to_risk = {t["name"]: t["risk_level"] for t in templates}
    assert names_to_risk["Conservative"] == "low"
    assert names_to_risk["Moderate"] == "medium"
    assert names_to_risk["Permissive"] == "high"
    assert names_to_risk["Online Shopping"] == "medium"
    assert names_to_risk["Procurement"] == "medium"
    assert names_to_risk["Ad Spending"] == "medium"


async def test_online_shopping_template_uses_require_approval_above(client):
    response = await client.get("/v1/rules/templates")
    templates = {t["name"]: t for t in response.json()}
    online = templates["Online Shopping"]
    rule_types = [r["rule_type"] for r in online["rules"]]
    assert "max_per_transaction" not in rule_types
    require_above_amounts = [
        r["value"]["amount"] for r in online["rules"]
        if r["rule_type"] == "require_approval_above"
    ]
    assert 500.0 in require_above_amounts


# ---------------------------------------------------------------------------
# POST /v1/agents/{id}/rules/template
# ---------------------------------------------------------------------------

async def test_apply_template_replaces_rules(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    existing_rule = _make_rule(agent.id)

    mock_db.execute = AsyncMock(side_effect=[
        mock_scalar(agent),            # _get_agent_for_user
        mock_scalars_all([existing_rule]),  # fetch current rules
    ])
    mock_db.delete = AsyncMock()
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.post(
        f"/v1/agents/{agent.id}/rules/template",
        json={"template_name": "Moderate"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 4  # Moderate has 4 rules
    rule_types = {r["rule_type"] for r in data}
    assert "auto_approve_below" in rule_types
    assert "require_approval_above" in rule_types


async def test_apply_template_not_found(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    mock_db.execute = AsyncMock(side_effect=[
        mock_scalar(agent),
        mock_scalars_all([]),
    ])

    response = await user_client.post(
        f"/v1/agents/{agent.id}/rules/template",
        json={"template_name": "Nonexistent"},
    )
    assert response.status_code == 404


async def test_apply_template_agent_not_found(user_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(return_value=mock_scalar(None))
    response = await user_client.post(
        f"/v1/agents/{uuid.uuid4()}/rules/template",
        json={"template_name": "Moderate"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/agents/{id}/rules/backup
# ---------------------------------------------------------------------------

def _make_backup(agent_id: uuid.UUID, user_id: uuid.UUID, template_applied: str | None = "Moderate") -> RuleBackup:
    return RuleBackup(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=user_id,
        rules_json=[{"rule_type": "max_per_transaction", "value": {"amount": 100.0}, "is_active": True}],
        template_applied=template_applied,
        created_at=datetime.now(timezone.utc),
    )


async def test_get_backup_returns_most_recent(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    backup = _make_backup(agent.id, mock_user.id)
    mock_db.execute = AsyncMock(side_effect=[
        mock_scalar(agent),
        mock_scalar(backup),
    ])

    response = await user_client.get(f"/v1/agents/{agent.id}/rules/backup")
    assert response.status_code == 200
    data = response.json()
    assert data["template_applied"] == "Moderate"
    assert len(data["rules_json"]) == 1


async def test_get_backup_not_found(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    mock_db.execute = AsyncMock(side_effect=[
        mock_scalar(agent),
        mock_scalar(None),
    ])

    response = await user_client.get(f"/v1/agents/{agent.id}/rules/backup")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/agents/{id}/rules/restore
# ---------------------------------------------------------------------------

async def test_restore_rules_from_backup(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    backup = _make_backup(agent.id, mock_user.id)
    existing_rule = _make_rule(agent.id)

    mock_db.execute = AsyncMock(side_effect=[
        mock_scalar(agent),           # _get_agent_for_user
        mock_scalar(backup),          # fetch backup
        mock_scalars_all([existing_rule]),  # fetch current rules to delete
    ])
    mock_db.delete = AsyncMock()
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    response = await user_client.post(f"/v1/agents/{agent.id}/rules/restore")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["rule_type"] == "max_per_transaction"


async def test_restore_no_backup_returns_404(user_client: AsyncClient, mock_user, mock_db):
    agent = Agent(
        id=uuid.uuid4(), user_id=mock_user.id, name="A", api_key_hash="h",
        api_key_prefix="p", status="active", metadata_={},
        created_at=datetime.now(timezone.utc),
    )
    mock_db.execute = AsyncMock(side_effect=[
        mock_scalar(agent),
        mock_scalar(None),
    ])

    response = await user_client.post(f"/v1/agents/{agent.id}/rules/restore")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /v1/rules/{id} — audit log on change
# ---------------------------------------------------------------------------

async def test_update_rule_logs_rules_changed(user_client: AsyncClient, mock_user, mock_db):
    agent_id = uuid.uuid4()
    rule = _make_rule(agent_id)
    mock_db.execute = AsyncMock(return_value=mock_scalar(rule))
    mock_db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("app.api.rules.audit.log_event", new_callable=AsyncMock) as mock_log:
        response = await user_client.patch(
            f"/v1/rules/{rule.id}", json={"value": {"amount": 999.0}}
        )
    assert response.status_code == 200
    mock_log.assert_called_once()
    call_kwargs = mock_log.call_args
    assert call_kwargs.args[1] == "rules_changed"
    assert call_kwargs.kwargs["details"]["source"] == "manual_edit"
