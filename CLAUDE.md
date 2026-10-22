# SpendNod Backend (formerly AgentGate)

## CRITICAL — Do Not Break These

- **JWT_SECRET must match Supabase's JWT secret** — NEVER rotate it independently. If rotated, all user sessions break instantly. The value lives in Supabase → Project Settings → API → JWT Secret.
- **DATABASE_URL must use the Supabase Session Pooler** (`aws-0-*.pooler.supabase.com:5432`, prefix `postgresql+asyncpg://`) — Cloud Run outbound traffic is IPv4-only by default; Supabase's direct connection string is IPv6-only and will fail with `ENETUNREACH`.

FastAPI authorization gateway. Agents POST to `/v1/authorize`; humans approve via the dashboard.

## Stack
- FastAPI + SQLAlchemy async + asyncpg
- Supabase (PostgreSQL + auth)
- Deployed on Google Cloud Run (see Hosting & Deploy)
- MCP server at `/mcp` (Streamable HTTP)

## Hosting & Deploy
- Google Cloud Run: GCP project `spendnod`, service `spendnod-backend`, region `us-east1`. Served at https://api.spendnod.com through a Cloud Run domain mapping.
- **Pushes to main do NOT deploy.** Deploy manually from the repo root on `main`:
  ```bash
  gcloud run deploy spendnod-backend --source . --region us-east1 --port 8000
  ```
- `--port 8000` is required. The Dockerfile hardcodes 8000 and Cloud Run defaults to 8080.
- Environment variables live on the Cloud Run service. Running the deploy command without env flags keeps them. Secret files must never be stored inside this repo.
- All direct dependencies are pinned to exact versions because every Cloud Run deploy installs from scratch. Upgrades are deliberate: bump the version, run tests, then deploy. mcp must stay on 1.x until the code is migrated to the 2.x API.
- Railway is retired.

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
pytest          # 126 tests, all must pass before any commit
```

---

## Gotchas Log

**Date:** 2026-10-21
**What happened:** Cloud Run build failed. A fresh install pulled mcp 2.x, which removed `mcp.server.fastmcp`, and `app/mcp_server.py` imports `FastMCP` from it.
**Root cause:** `requirements.txt` had only a lower bound on mcp (`mcp[cli]>=1.2.0`).
**Fix:** Pinned `mcp[cli]>=1.2.0,<2` (2f5f123). Live on Cloud Run as revision spendnod-backend-00005.
**Rule:** Every Cloud Run deploy is a fresh install. Since 2026-10-22 every direct dependency in `requirements.txt` is pinned with `==`, but transitive dependencies still float, so a new release of one can still break the build with no code change. When a build fails on an import, check for a major-version bump first.

---

**Date:** 2026-04-03
**What happened:** Dashboard showed "Failed to load stats" after rotating DATABASE_URL in Railway. Backend returning 500 on all DB-hitting endpoints, which also caused false CORS errors in the browser (FastAPI crashes before CORSMiddleware can add headers).
**Root cause (1):** Supabase direct connections (`db.[ref].supabase.co`) are IPv6-only. Railway containers only have IPv4 outbound routing → `OSError: [Errno 101] Network is unreachable (ENETUNREACH)`.
**Root cause (2):** Session pooler URL copied from Supabase uses `postgresql://` prefix — SQLAlchemy defaults to psycopg2 which isn't installed → `ModuleNotFoundError: No module named 'psycopg2'`.
**Root cause (3):** Wrong password in DATABASE_URL → `InvalidPasswordError` → Supabase circuit breaker fires after repeated failures: "Too many authentication errors". Must pause/suspend Railway service (or delete DATABASE_URL var) to stop hammering Supabase, then wait ~5 min before retrying.
**Fix:** Use the Supabase **Session Pooler** URL (NOT direct, NOT transaction pooler/pgBouncer on port 6543). Format:
`postgresql+asyncpg://postgres.[project-ref]:[PASSWORD]@aws-0-[region].pooler.supabase.com:5432/postgres`
Note: username includes the project ref (`postgres.lddmvlkvfgxzshdvgrbg`), port is 5432, prefix must be `postgresql+asyncpg://`.
**Rule:** Always use the Supabase session pooler URL on Railway. Direct connections are IPv6 and Railway doesn't support IPv6. Transaction pooler (port 6543) breaks asyncpg. Password must have no special characters or must be percent-encoded.

---

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

---

**Date:** 2026-04-27
**What happened:** Vercel build failed with `Type error: Property 'asChild' does not exist on type 'IntrinsicAttributes & DialogCloseProps'` in `export-modal.tsx`.
**Root cause:** The local `DialogClose` wrapper in `dashboard/src/components/ui/dialog.tsx` is a thin re-export with no Radix `asChild` support — it's not the raw Radix primitive.
**Fix:** Replaced `<DialogClose asChild><Button ...>` with a plain `<Button onClick={() => onOpenChange(false)}>`.
**Rule:** Never use `<DialogClose asChild>` in the dashboard. Close a dialog from a button by calling `onOpenChange(false)` directly.

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
| 2026-04-13 | Conservative rules auto-applied on `POST /agents` | Agents should have safe defaults out of the box; avoids unconfigured agents with no spend controls |
| 2026-04-13 | New templates: Online Shopping, Procurement, Ad Spending | Covers common agent use cases beyond the generic Conservative/Moderate/Permissive trio |
| 2026-04-13 | `rule_backups` table + backup/restore endpoints | Allows safe template switching — users can always roll back to previous rules |
| 2026-04-13 | Online Shopping uses `require_approval_above` at $500, not `max_per_transaction` | `max_per_transaction` was misleadingly named as a "block"; explicit `require_approval_above` makes the pending-not-denied intent clear |
| 2026-04-13 | `risk_level` field on `RuleTemplate` schema | Dashboard can color-code templates by risk without parsing rule contents |
| 2026-04-13 | `PATCH /v1/rules/{id}` now logs `rules_changed` with before/after | Audit trail for manual edits, matching template-apply audit coverage |
| 2026-04-27 | `GET /v1/activity/export` uses `export_limiter` defined in `activity.py`, not `rate_limiter.py` | Kept limiter co-located with the endpoint; rate is 10 exports/hr/user |
| 2026-04-27 | Activity export enforces user RLS via INNER JOIN on `agents.user_id` | Requests with null agent_id are correctly excluded; no separate WHERE needed |
| 2026-10-21 | Backend hosted on Google Cloud Run; deploys are a manual `gcloud run deploy`, pushes to main do not deploy | Decided outside this repo; Railway retired. Setup and deploy command in Hosting & Deploy |
| 2026-10-21 | `mcp[cli]` pinned `<2` (exact pin `==1.26.0` since 2026-10-22) | mcp 2.x removed `mcp.server.fastmcp`; lift only after migrating to the 2.x API |
| 2026-10-22 | Top-level `status` is the source of truth for a request's current decision. `rule_evaluation` is a frozen snapshot of what the rules decided before any human action. Accepted exception: a human deny with a reason adds a `deny_reason` key to `rule_evaluation`; the rules' own fields are never changed | Kept for the audit trail. No code change |
| 2026-10-22 | Every direct dependency in `requirements.txt` pinned to the exact tested version (`==`), extras kept | Every Cloud Run deploy installs from scratch. Upgrades are deliberate: bump the version, run tests, then deploy |
| 2026-10-22 | `pytest` and `pytest-asyncio` stay in `requirements.txt`; no separate dev requirements file | Decided in chat |

---

## Current TODOs

**Next session starts with:** No open backend work. Backlog: demo video or GIF, MCP marketplace listings, reconcile ~/.claude config drift in the claude-skills repo.

- [ ] Demo video or GIF for marketing/listings
- [ ] MCP marketplace listings: mcpmarket.com, PulseMCP, LobeHub
- [ ] Attorney review of ToS / Privacy Policy
- [ ] Stack Industries LinkedIn company page
- [ ] Remove debug middleware (`_RequestLogger` in `main.py`) once `/mcp` is confirmed stable
- [ ] Remove startup route-table debug loop in `lifespan()` once `/mcp` is confirmed stable
- [ ] Backlog: reconcile `~/.claude` config drift in the claude-skills repo, keeping lines from both sides

### Completed (2026-10-22)
- [x] Railway subscription cancelled (manual, by Gary)
- [x] `agentgate` MCP entry removed by hand from the agent-gate project's Claude Code config; no `agentgate` entry left in `~/.claude.json`. This repo's Claude Code has no spendnod MCP server (`claude mcp list`, 2026-10-22), so it cannot run an authorize check itself
- [x] Exact pins verified on the production interpreter: built and run on Cloud Run (Python 3.11, Linux) as revision spendnod-backend-00006-q7p at 100% traffic. `/health` 200 ok; a $5 authorize through the spendnod MCP connection was auto-approved (request 0746350c-5ccf-49c4-912c-d8f507418912, from Gary's run; health and serving revision re-checked in Claude Code with curl and gcloud)
- [x] Pinned every direct dependency in `requirements.txt` to the exact version installed locally (`==`, extras kept); 126 tests pass on the pinned set. Deployed as revision spendnod-backend-00006-q7p
- [x] Closed by decision, not by a new run: the auto-approve-through-api.spendnod.com TODO. The $52 request through api.spendnod.com exercised the rule engine on the production domain
- [x] Managed certificate for api.spendnod.com provisioned: domain mapping Ready and CertificateProvisioned since 2026-10-22 03:57 UTC (Google Trust Services, valid from 2026-10-22)
- [x] Production verified on Cloud Run, revision spendnod-backend-00005-nc8 at 100% traffic: `/health` 200 ok; OAuth login through the main spendnod MCP connection; auto-approve under $50 (via the service's run.app URL); human approval above $50 via the dashboard (via api.spendnod.com)

### Completed (2026-10-21)
- [x] Pinned `mcp[cli]>=1.2.0,<2` to fix the Cloud Run build (2f5f123); live on Cloud Run as revision spendnod-backend-00005
- [x] CLAUDE.md: Hosting & Deploy section for Cloud Run (ceef250); DATABASE_URL pooler rule reason updated for Cloud Run (6fb4a86). Docs-only, no deploy needed

Older completed items: see docs/completed-archive.md.
