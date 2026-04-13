"""Tests for GET /v1/rules/templates (public endpoint — no auth required)."""

from httpx import AsyncClient


async def test_templates_returns_six(client: AsyncClient):
    response = await client.get("/v1/rules/templates")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 6


async def test_template_names(client: AsyncClient):
    response = await client.get("/v1/rules/templates")
    names = [t["name"] for t in response.json()]
    assert names == [
        "Conservative",
        "Moderate",
        "Permissive",
        "Online Shopping",
        "Procurement",
        "Ad Spending",
    ]


async def test_templates_have_rules(client: AsyncClient):
    response = await client.get("/v1/rules/templates")
    for template in response.json():
        assert len(template["rules"]) >= 1
        for rule in template["rules"]:
            assert "rule_type" in rule
            assert "value" in rule


async def test_conservative_requires_approval_above_25(client: AsyncClient):
    response = await client.get("/v1/rules/templates")
    conservative = next(t for t in response.json() if t["name"] == "Conservative")
    rule_types = [r["rule_type"] for r in conservative["rules"]]
    assert "require_approval_above" in rule_types
    approval_rule = next(r for r in conservative["rules"] if r["rule_type"] == "require_approval_above")
    assert approval_rule["value"]["amount"] == 25.0


async def test_permissive_auto_approves_below_500(client: AsyncClient):
    response = await client.get("/v1/rules/templates")
    permissive = next(t for t in response.json() if t["name"] == "Permissive")
    auto_rule = next(r for r in permissive["rules"] if r["rule_type"] == "auto_approve_below")
    assert auto_rule["value"]["amount"] == 500.0
