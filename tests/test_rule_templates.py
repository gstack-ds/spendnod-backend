"""Tests for GET /v1/rule-templates."""

import pytest
from httpx import AsyncClient


async def test_templates_returns_three(user_client: AsyncClient):
    response = await user_client.get("/v1/rule-templates")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


async def test_template_names(user_client: AsyncClient):
    response = await user_client.get("/v1/rule-templates")
    names = [t["name"] for t in response.json()]
    assert names == ["Conservative", "Moderate", "Permissive"]


async def test_templates_have_rules(user_client: AsyncClient):
    response = await user_client.get("/v1/rule-templates")
    for template in response.json():
        assert len(template["rules"]) >= 1
        for rule in template["rules"]:
            assert "rule_type" in rule
            assert "value" in rule


async def test_conservative_requires_approval_above_25(user_client: AsyncClient):
    response = await user_client.get("/v1/rule-templates")
    conservative = next(t for t in response.json() if t["name"] == "Conservative")
    rule_types = [r["rule_type"] for r in conservative["rules"]]
    assert "require_approval_above" in rule_types
    approval_rule = next(r for r in conservative["rules"] if r["rule_type"] == "require_approval_above")
    assert approval_rule["value"]["amount"] == 25.0


async def test_permissive_auto_approves_below_500(user_client: AsyncClient):
    response = await user_client.get("/v1/rule-templates")
    permissive = next(t for t in response.json() if t["name"] == "Permissive")
    auto_rule = next(r for r in permissive["rules"] if r["rule_type"] == "auto_approve_below")
    assert auto_rule["value"]["amount"] == 500.0
