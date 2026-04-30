<!-- mcp-name: io.github.gstack-ds/spendnod -->

# SpendNod

**Open-source human-in-the-loop authorization gateway for AI agents.**

SpendNod sits between your AI agent and the real world. Before an agent executes a purchase, API call, or any consequential action, it submits a request to SpendNod. Your configurable rules engine auto-approves routine transactions; anything outside the rules routes to a human for approval — with a full audit trail of every decision.

## Features

- **Configurable rules engine** — per-transaction limits, daily/monthly caps, vendor allowlists/blocklists, category restrictions
- **Human approval workflow** — pending requests land in your dashboard with one-click approve/deny
- **Spending thresholds** — auto-approve below a limit, require approval above it, hard block at $10k
- **Full audit trail** — every authorization logged with agent, amount, vendor, decision, and timestamp
- **Multi-agent support** — manage multiple agents with independent rule sets
- **OAuth 2.1** — works with Claude Code, Claude Desktop, Cursor, VS Code, and ChatGPT out of the box
- **MCP server** — Streamable HTTP transport at `/mcp`

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/gstack-ds/spendnod-backend
cd spendnod-backend
pip install -r requirements.txt
```

### 2. Set up environment

```bash
cp .env.example .env
# Edit .env with your values (see comments in the file)
```

### 3. Set up the database

SpendNod uses [Supabase](https://supabase.com) (PostgreSQL). Create a project, then run the migration files in `migrations/` against your database in order.

### 4. Run the server

```bash
uvicorn app.main:app --reload
```

The API is now live at `http://localhost:8000`. Visit `/docs` for the interactive API reference.

### 5. Connect an agent via MCP

```bash
claude mcp add spendnod --transport http http://localhost:8000/mcp
```

## Self-Hosting

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | Supabase Session Pooler URL (`postgresql+asyncpg://...`) |
| `SUPABASE_URL` | Yes | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | Yes | Supabase anon/public key |
| `JWT_SECRET` | Yes | Must match Supabase's JWT secret (Project Settings → API) |
| `RESEND_API_KEY` | No | Email notifications for pending approvals (Resend) |
| `DASHBOARD_URL` | No | URL of your dashboard app (used in notification links) |
| `API_URL` | No | Public URL of this API (used by MCP tools) |
| `CORS_ORIGINS` | No | Comma-separated allowed origins beyond defaults |

### Deploying to Railway

1. Fork this repo and connect it to a new Railway service
2. Set all required environment variables
3. Railway auto-deploys on push to `main`

> **Important:** Use the Supabase **Session Pooler** URL for `DATABASE_URL` (port 5432), not the direct connection or transaction pooler. Railway containers are IPv4-only; Supabase direct connections are IPv6-only.

### Docker

```bash
docker build -t spendnod .
docker run -p 8000:8000 --env-file .env spendnod
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `authorize_transaction` | Submit a transaction for authorization before executing it |
| `check_authorization_status` | Poll a pending request for human approval |
| `cancel_authorization` | Cancel a pending request that is no longer needed |

## Connect Your Agent

Authentication is OAuth 2.1 — your browser opens once for login, no API keys to manage.

**Claude Code (CLI)**
```bash
claude mcp add spendnod --transport http https://your-api-url/mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on Mac)
```json
{
  "mcpServers": {
    "spendnod": {
      "url": "https://your-api-url/mcp"
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "spendnod": {
      "url": "https://your-api-url/mcp"
    }
  }
}
```

**VS Code** (`.vscode/mcp.json`)
```json
{
  "servers": {
    "spendnod": {
      "type": "http",
      "url": "https://your-api-url/mcp"
    }
  }
}
```

## Running Tests

```bash
pytest
```

146 tests, all must pass before any commit.

## License

MIT — see [LICENSE](LICENSE).

Built by [Stack Industries LLC](https://spendnod.com)
