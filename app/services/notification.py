"""Notification service — Resend email for pending authorization requests."""

from decimal import Decimal
from typing import Optional

import httpx

from app.config import settings


async def send_pending_notification(
    user_email: str,
    agent_name: str,
    request_id: str,
    action: str,
    amount: Optional[Decimal],
    vendor: Optional[str],
    description: Optional[str],
) -> None:
    """Send an email via Resend when a request needs human approval.

    Silently no-ops if RESEND_API_KEY is not configured.
    Intended to run as a FastAPI BackgroundTask (fire-and-forget).
    """
    if not settings.RESEND_API_KEY:
        return

    amount_str = f"${amount:,.2f}" if amount is not None else "N/A"
    vendor_str = vendor or "N/A"

    text_body = (
        f"SpendNod — Approval Required\n\n"
        f"Agent: {agent_name}\n"
        f"Action: {action}\n"
        f"Amount: {amount_str}\n"
        f"Vendor: {vendor_str}\n"
        f"Description: {description or 'N/A'}\n\n"
        f"Review at: {settings.DASHBOARD_URL}/requests/{request_id}\n"
    )

    html_body = f"""
<h2>SpendNod — Approval Required</h2>
<table>
  <tr><td><strong>Agent</strong></td><td>{agent_name}</td></tr>
  <tr><td><strong>Action</strong></td><td>{action}</td></tr>
  <tr><td><strong>Amount</strong></td><td>{amount_str}</td></tr>
  <tr><td><strong>Vendor</strong></td><td>{vendor_str}</td></tr>
  <tr><td><strong>Description</strong></td><td>{description or 'N/A'}</td></tr>
</table>
<p><a href="{settings.DASHBOARD_URL}/requests/{request_id}">Review this request</a></p>
"""

    payload = {
        "from": "SpendNod <notifications@spendnod.com>",
        "to": [user_email],
        "subject": f"[SpendNod] Approval required — {agent_name}: {action}",
        "text": text_body,
        "html": html_body,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            )
    except Exception:
        pass  # fire-and-forget — never fail the request over a notification error
