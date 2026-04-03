"""
AgentGate MCP Server

Exposes AgentGate's authorization API as MCP tools so any AI agent on any
platform (Claude, ChatGPT, Cursor, VS Code) can use AgentGate as a native
tool — no SDK install required.

Authentication: OAuth 2.1 + PKCE. The MCP client opens a browser for the
user to log in — no api_key parameter needed. The Bearer token is injected
by MCPBearerMiddleware (app/api/oauth_bearer.py) into a ContextVar that
these tools read.

Tools:
    authorize_transaction       — submit a transaction for authorization
    check_authorization_status  — poll a pending request
    cancel_authorization        — cancel a pending request
"""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.api.oauth_bearer import mcp_bearer_token

AGENTGATE_API_URL = os.getenv(
    "AGENTGATE_API_URL",
    "https://api.spendnod.com",
).rstrip("/")

# streamable_http_path="/" so the internal Starlette route sits at "/".
# When FastAPI mounts this sub-app at "/mcp" it strips the prefix, so the
# MCP endpoint is reachable at /mcp (not /mcp/mcp).
#
# transport_security: DNS rebinding protection is disabled because this server
# is deployed on Railway behind HTTPS — it is not a local server. The attack
# vector (malicious page tricking a local server) does not apply here.
# FastAPI's CORS middleware already handles cross-origin protection.
mcp = FastMCP(
    "SpendNod",
    streamable_http_path="/",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


def _auth_header() -> dict[str, str] | None:
    """Return Authorization header using the token injected by MCPBearerMiddleware."""
    token = mcp_bearer_token.get()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _no_auth_error() -> dict[str, Any]:
    return {
        "error": True,
        "detail": (
            "No authentication token found. "
            "Connect to this MCP server without an api_key — "
            "your MCP client will open a browser for you to log in."
        ),
    }


@mcp.tool()
async def authorize_transaction(
    action: str,
    amount: float,
    vendor: str,
    category: str | None = None,
    description: str | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """Request authorization before an AI agent makes a purchase, booking, or
    financial transaction.

    Call this BEFORE executing any transaction. The response tells you whether
    to proceed:

    - status="auto_approved"  → proceed immediately; use the approval_token if
                                 your downstream API requires it.
    - status="pending"        → a human is reviewing; poll
                                 check_authorization_status with the returned
                                 request_id until it resolves. Do NOT execute
                                 the transaction yet.
    - status="denied"         → do not proceed; explain to the user that the
                                 transaction was blocked by their spending rules.

    Args:
        action:      Short description of the action, e.g. "purchase flight tickets".
        amount:      Transaction amount as a number, e.g. 149.99.
        vendor:      Merchant or service name, e.g. "Delta Airlines".
        category:    Optional spending category, e.g. "travel", "software", "food".
        description: Optional longer description for the human reviewer.
        currency:    ISO 4217 currency code. Defaults to "USD".

    Returns:
        JSON response with at minimum: status, request_id. May include
        approval_token (on auto_approved) or expires_at (on pending).
    """
    headers = _auth_header()
    if headers is None:
        return _no_auth_error()

    payload: dict[str, Any] = {
        "action": action,
        "amount": amount,
        "vendor": vendor,
        "currency": currency,
    }
    if category is not None:
        payload["category"] = category
    if description is not None:
        payload["description"] = description

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{AGENTGATE_API_URL}/v1/authorize",
                json=payload,
                headers=headers,
            )
        if resp.status_code in (200, 202):
            return resp.json()
        return {
            "error": True,
            "status_code": resp.status_code,
            "detail": resp.json().get("detail", resp.text),
        }
    except httpx.TimeoutException:
        return {"error": True, "detail": "Request timed out. The SpendNod API did not respond in time."}
    except Exception as exc:
        return {"error": True, "detail": str(exc)}


@mcp.tool()
async def check_authorization_status(
    request_id: str,
) -> dict[str, Any]:
    """Check the status of a pending authorization request.

    Use this to poll for human approval after authorize_transaction returns
    status="pending". Poll every 5–10 seconds; stop when status changes from
    "pending" to "approved", "denied", "expired", or "cancelled".

    Typical polling loop:
        1. Call authorize_transaction → status="pending", request_id="abc-123"
        2. Wait 10 seconds.
        3. Call check_authorization_status(request_id="abc-123") → status="pending"
        4. Wait 10 seconds.
        5. Call check_authorization_status(request_id="abc-123") → status="approved"
        6. Proceed with the transaction.

    Args:
        request_id: The request ID returned by authorize_transaction.

    Returns:
        JSON with status field. If status="approved", an approval_token is
        also included.
    """
    headers = _auth_header()
    if headers is None:
        return _no_auth_error()

    url = f"{AGENTGATE_API_URL}/v1/authorize/{request_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return {
                "error": True,
                "status_code": 404,
                "detail": "Authorization request not found — it may have expired (requests expire after 5 minutes).",
                "url_called": url,
            }
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        return {
            "error": True,
            "status_code": resp.status_code,
            "detail": detail,
            "url_called": url,
        }
    except httpx.TimeoutException:
        return {"error": True, "detail": "Request timed out.", "url_called": url}
    except Exception as exc:
        return {"error": True, "detail": str(exc), "url_called": url}


@mcp.tool()
async def cancel_authorization(
    request_id: str,
) -> dict[str, Any]:
    """Cancel a pending authorization request that is no longer needed.

    Use this when you submitted a transaction for human approval but the user
    has since decided not to proceed, or the context has changed and the
    request is no longer relevant. Only pending requests can be cancelled.

    Args:
        request_id: The request ID to cancel.

    Returns:
        {"cancelled": true} on success, or an error dict on failure.
    """
    headers = _auth_header()
    if headers is None:
        return _no_auth_error()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(
                f"{AGENTGATE_API_URL}/v1/authorize/{request_id}",
                headers=headers,
            )
        if resp.status_code == 204:
            return {"cancelled": True, "request_id": request_id}
        return {
            "error": True,
            "status_code": resp.status_code,
            "detail": resp.json().get("detail", resp.text),
        }
    except httpx.TimeoutException:
        return {"error": True, "detail": "Request timed out. The SpendNod API did not respond in time."}
    except Exception as exc:
        return {"error": True, "detail": str(exc)}
