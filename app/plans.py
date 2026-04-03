"""Plan definitions and limit helpers for AgentGate billing tiers."""

from typing import Optional

PLAN_LIMITS: dict[str, dict[str, Optional[int]]] = {
    "free":     {"max_requests_per_month": 200,   "max_agents": 2},
    "starter":  {"max_requests_per_month": 5000,  "max_agents": 10},
    "pro":      {"max_requests_per_month": 50000, "max_agents": 50},
    "business": {"max_requests_per_month": None,  "max_agents": None},  # unlimited
}

PLAN_ORDER = ["free", "starter", "pro", "business"]

UPGRADE_URL = "https://app.spendnod.com/billing"


def get_next_plan(plan: str) -> Optional[str]:
    """Return the next tier above the given plan, or None if already at the top."""
    try:
        idx = PLAN_ORDER.index(plan)
        if idx + 1 < len(PLAN_ORDER):
            return PLAN_ORDER[idx + 1]
    except ValueError:
        pass
    return None
