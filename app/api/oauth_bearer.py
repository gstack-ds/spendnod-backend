"""ASGI middleware that extracts the Bearer token from MCP requests.

Sets a ContextVar so MCP tool functions can read the token without needing
it as an explicit function parameter.
Returns 401 + WWW-Authenticate when no Bearer token is present.
"""

import json
import logging
from contextvars import ContextVar

from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Shared ContextVar: set by the middleware, read by MCP tools.
mcp_bearer_token: ContextVar[str | None] = ContextVar("mcp_bearer_token", default=None)

_NO_AUTH_BODY = json.dumps({
    "error": "unauthorized",
    "detail": (
        "Authentication required. "
        "Connect to this MCP server without an api_key — your MCP client will open "
        "a browser for you to log in with your SpendNod account."
    ),
}).encode()


class MCPBearerMiddleware:
    """Wraps the MCP sub-app. Injects Bearer token into ContextVar; blocks unauthenticated requests."""

    def __init__(self, app: ASGIApp, api_url: str) -> None:
        self.app = app
        self.api_url = api_url.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Starlette Mount strips the path prefix; a request to exactly /mcp
        # arrives here with path="". Normalize to "/" so FastMCP's internal
        # route (registered at "/") can match without a redirect.
        if scope.get("path") == "":
            scope = {**scope, "path": "/"}

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
        path = scope.get("path", "")

        logger.info(
            "MCPBearerMiddleware: path=%s auth_header=%r",
            path,
            (auth[:30] + "...") if len(auth) > 30 else auth,
        )

        # RFC 6750 §2.1: the "Bearer" keyword is case-insensitive.
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()  # skip "Bearer " (7 chars) regardless of case
            logger.info(
                "MCPBearerMiddleware: Bearer token found path=%s token_prefix=%.8s...",
                path, token,
            )
            mcp_bearer_token.set(token)
            await self.app(scope, receive, send)
        else:
            logger.warning(
                "MCPBearerMiddleware: no Bearer token path=%s auth_header=%r — returning 401",
                path, auth[:50] if auth else "(empty)",
            )
            resource_metadata = f"{self.api_url}/.well-known/oauth-authorization-server"
            www_auth = f'Bearer realm="{self.api_url}", resource_metadata="{resource_metadata}"'
            response = Response(
                content=_NO_AUTH_BODY,
                status_code=401,
                headers={
                    "Content-Type": "application/json",
                    "WWW-Authenticate": www_auth,
                },
            )
            await response(scope, receive, send)
