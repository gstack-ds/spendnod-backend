# SpendNod Backend (formerly AgentGate)

FastAPI authorization gateway. Agents POST to `/v1/authorize`; humans approve via the dashboard.

## Stack
- FastAPI + SQLAlchemy async + asyncpg
- Supabase (PostgreSQL + auth)
- Deployed on Railway
- MCP server at `/mcp` (Streamable HTTP)

## Key Files
- `app/main.py` — app factory, middleware, lifespan, router registration, MCP mount
- `app/mcp_server.py` — FastMCP instance + 3 MCP tools
- `app/api/authorize.py` — core authorization endpoints (POST/GET/DELETE `/v1/authorize`)
- `app/api/rules.py` — rule CRUD
- `app/services/rule_engine.py` — rule evaluation logic (DO NOT TOUCH without careful thought)
- `app/config.py` — pydantic-settings config

## Running Locally
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Tests
```bash
pytest          # 104 tests, all must pass before any commit
```

---

## Gotchas Log

**Date:** 2026-03-29
**What happened:** `app.mount("/mcp", mcp.streamable_http_app())` caused every /mcp request to 500 with "Task group is not initialized."
**Root cause:** FastAPI does NOT call the lifespan of mounted sub-applications. `StreamableHTTPSessionManager` requires its `run()` context manager to initialize the anyio task group.
**Fix:** Call `mcp.streamable_http_app()` at module level (initializes `_session_manager`), then add `async with mcp.session_manager.run():` inside the FastAPI lifespan.
**Rule:** Any FastMCP server mounted inside FastAPI must have its session manager started explicitly in the FastAPI lifespan.

---

**Date:** 2026-03-29
**What happened:** `/mcp` was returning 404 — MCP endpoint unreachable.
**Root cause:** `streamable_http_path` defaults to `"/mcp"` inside the Starlette sub-app. When FastAPI mounts the sub-app at `/mcp` it strips the prefix, so requests arrive at `/` but the internal route was at `/mcp`.
**Fix:** Set `streamable_http_path="/"` in the FastMCP constructor so the internal route is at `/`.
**Rule:** When mounting FastMCP as a FastAPI sub-app, always set `streamable_http_path="/"`.

---

**Date:** 2026-03-29
**What happened:** Remote calls to `/mcp` returned "Invalid Host header" (421).
**Root cause:** FastMCP auto-enables DNS rebinding protection when `host` defaults to `127.0.0.1`, blocking any Host header that isn't localhost.
**Fix:** Pass `transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)`. DNS rebinding is a local-server attack; Railway HTTPS + FastAPI CORS already cover protection.
**Rule:** Always disable DNS rebinding protection when mounting FastMCP inside a deployed FastAPI app.

---

**Date:** 2026-03-29
**What happened:** Bearer token from OAuth flow was rejected on every `GET /mcp/` request with 401.
**Root cause:** `MCPBearerMiddleware` used `auth.startswith("Bearer ")` — a case-sensitive check. RFC 6750 §2.1 says the `Bearer` keyword is case-insensitive. The OAuth token response sets `"token_type": "bearer"` (lowercase), and some MCP clients (including Claude Code's built-in OAuth handler) send `Authorization: bearer <token>` (lowercase b), so the middleware never matched and always returned 401.
**Fix:** Changed to `auth.lower().startswith("bearer ")` + extract token with `auth[7:].strip()`. Also added structured logging to the middleware and `get_agent_from_oauth_token` to make future failures diagnosable from Railway logs.
**Rule:** Always do case-insensitive checks for HTTP scheme keywords (`Bearer`, `Basic`, etc.). Never use `str.startswith("Bearer ")` for Authorization header parsing.

---

**Date:** 2026-03-30
**What happened:** `POST /mcp` returned 404 even after `streamable_http_path="/"` was already set.
**Root cause:** `StreamableHTTPSessionManager._handle_stateful_request` returns HTTP 404 (per MCP spec) when a client sends an `mcp-session-id` header that isn't in the server's in-memory session map. After every Railway container restart, all sessions are wiped. Any MCP client that reconnects while still holding a cached session ID hits this branch.
**Fix:** Set `stateless_http=True` on the `FastMCP` constructor. Stateless mode handles each POST as a self-contained exchange — no session tracking, no stale-session 404s.
**Rule:** Always use `stateless_http=True` for FastMCP servers deployed on Railway (or any ephemeral/stateless container platform). Stateful mode is only suitable when session state can survive across requests (e.g., in-memory store with sticky sessions or external session DB).

---

**Date:** 2026-03-30/31
**What happened:** Even with `stateless_http=True`, `POST /mcp` returned `{"detail":"Not Found"}` (FastAPI's 404). The `_RequestLogger` middleware logged nothing despite requests clearly reaching FastAPI.
**Root cause:** Starlette 1.0.0 changed `Mount` path regex from `^/mcp(?P<path>.*)$` to `^/mcp/(?P<path>.*)$` (trailing slash now required). `app.mount("/mcp", ...)` silently stopped matching bare `POST /mcp`. The `_RequestLogger` not logging was a red herring — uvicorn on Railway doesn't configure application loggers (root logger defaults to WARNING), so INFO-level logs are suppressed.
**Fix:** Replaced `app.mount("/mcp", ...)` with two explicit `app.add_api_route` calls at `/mcp` and `/mcp/`. Each route uses an async ASGI proxy (`_mcp_proxy`) that rewrites `scope["path"] = "/"` before forwarding to `MCPBearerMiddleware → FastMCP`. Uses `asyncio.Queue + StreamingResponse` to handle both streaming (SSE) and buffered (JSON) responses.
**Rule:** `app.mount("/path", sub_app)` in Starlette 1.0+ requires a trailing slash on the path (i.e., mount at `/path/`, not `/path`). For exact-path matching use `app.add_api_route` instead of `app.mount`.

---

## Design Decisions Log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-03-29 | MCP server mounted at `/mcp` inside main FastAPI app | Single Railway process, no separate port/service needed |
| 2026-03-29 | MCP tools are thin HTTP passthroughs to live API | All business logic stays in FastAPI; MCP is just a protocol adapter |
| 2026-03-29 | Disabled DNS rebinding protection on MCP server | Deployed HTTPS server, not a local server — attack vector doesn't apply |
| 2026-03-29 | `AGENTGATE_API_URL` env var for MCP tool base URL | Allows local dev to point at localhost instead of production |
| 2026-03-29 | Plan limits in `app/plans.py`, not `app/config.py` | `config.py` is a file not a directory; kept plans separate and importable |
| 2026-03-29 | 60s in-memory cache for monthly request count | COUNT on every authorize call would be expensive at scale; 60s staleness is acceptable given 10% grace period |
| 2026-03-29 | $10k ceiling applied after rule evaluation, not inside rule engine | Rule engine is frozen; ceiling is a platform-level guardrail separate from user-configured rules |
| 2026-03-29 | `plan_warning` added to `AuthorizeResponse` (additive) | Allows agents to surface overage warnings without breaking existing schema consumers |
| 2026-03-29 | OAuth token → agent resolution via `require_agent` fallback | Raw API keys never stored; OAuth tokens hash-lookup the token, find user, return first active agent — no API changes needed for existing agent endpoints |
| 2026-03-29 | Login proof JWT (5-min TTL) carries OAuth params between login and consent steps | Stateless — no server-side session table; signed with JWT_SECRET so it can't be forged |
| 2026-03-29 | MCP tools no longer take `api_key` parameter | Bearer token injected by `MCPBearerMiddleware` into ContextVar; tools read it without needing it in the tool signature |
| 2026-03-30 | `stateless_http=True` on FastMCP constructor | Railway restarts wipe in-memory session state; stateful mode causes stale-session 404s on every redeploy |
| 2026-03-30 | `_RequestLogger` pure ASGI middleware (added via `add_middleware`) | Logs method + path + key headers before routing; safe for SSE streaming unlike `BaseHTTPMiddleware` |
| 2026-03-31 | Explicit `add_api_route("/mcp")` instead of `app.mount("/mcp")` | Starlette 1.0 Mount regex requires trailing slash; explicit routes match bare `/mcp` |

---

## Current TODOs

- [ ] Run `supabase/migrations/002_add_plan_column.sql` in Supabase SQL editor (adds `plan` column to `users`)
- [ ] Run `supabase/migrations/003_oauth_tables.sql` in Supabase SQL editor (adds `oauth_clients`, `oauth_auth_codes`, `oauth_tokens`)
- [ ] Set `API_URL` env var on Railway to the production URL (defaults correctly but explicit is safer)
- [ ] Verify usage bar shows on live dashboard after deploy
- [ ] **Verify on Railway** — after deploy, run `curl -v -X POST https://agent-gate-production.up.railway.app/mcp -H "Authorization: Bearer test-token" -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"initialize","id":1,...}'` and confirm 200 + valid MCP response (not 404).
- [ ] Remove debug middleware (`_RequestLogger` in `main.py`) once `/mcp` is confirmed working
- [ ] Remove startup route-table debug loop in `lifespan()` once `/mcp` is confirmed working
- [ ] Verify MCP OAuth flow end-to-end: connect Claude Desktop with just the URL, confirm browser login opens
- [ ] Add Stripe billing (upgrade button charges + sets `user.plan`) — `UPGRADE_URL` already points to `/billing`
- [ ] Check Railway startup logs after next deploy: compare "raw env" vs "pydantic" lines for SUPABASE_ANON_KEY to confirm env var is being read correctly
- [x] Update `notification.py` Resend sender domain to `notifications@spendnod.com` (domain purchased)
