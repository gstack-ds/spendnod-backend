"""Plan stubs for open-source SpendNod — all limits are None (unlimited)."""

from typing import Optional

PLAN_LIMITS: dict[str, dict[str, Optional[int]]] = {
    "free": {"max_requests_per_month": None, "max_agents": None},
}

PLAN_ORDER: list[str] = ["free"]

UPGRADE_URL: str = ""


def get_next_plan(plan: str) -> Optional[str]:
    return None
