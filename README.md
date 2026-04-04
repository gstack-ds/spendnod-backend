<!-- mcp-name: io.github.gstack-ds/spendnod -->

# SpendNod

**Human authorization gateway for AI agent transactions.**

SpendNod sits between your AI agent and the real world. Before an agent executes a purchase, API call, or any consequential action, it submits a request to SpendNod. Your configurable rules engine auto-approves routine transactions; anything outside the rules routes to a human for approval — with a full audit trail of every decision.

## What it does

- Intercepts agent spending requests via a single MCP tool call
- Evaluates against your rules (amount thresholds, vendor allowlists/blocklists, daily caps, category restrictions)
- Returns `auto_approved`, `pending`, or `denied` — agents act on the response
- Pending requests appear in your dashboard at [app.spendnod.com](https://app.spendnod.com) for one-click approval or denial
- Every authorization is logged with full audit trail

## MCP Tools

| Tool | Description |
|------|-------------|
| `authorize_transaction` | Submit a transaction for authorization before executing it |
| `check_authorization_status` | Poll a pending request for human approval |
| `cancel_authorization` | Cancel a pending request that is no longer needed |

## Connect your agent

Authentication is OAuth 2.1 — your browser opens once for login, no API keys to manage.

**Claude Code (CLI)**
```bash
claude mcp add spendnod --transport http https://api.spendnod.com/mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on Mac, `%APPDATA%\Claude\claude_desktop_config.json` on Windows)
```json
{
  "mcpServers": {
    "spendnod": {
      "url": "https://api.spendnod.com/mcp"
    }
  }
}
```

**Cursor** (`.cursor/mcp.json` in your project or `~/.cursor/mcp.json` globally)
```json
{
  "mcpServers": {
    "spendnod": {
      "url": "https://api.spendnod.com/mcp"
    }
  }
}
```

**VS Code** (`.vscode/mcp.json` in your project)
```json
{
  "servers": {
    "spendnod": {
      "type": "http",
      "url": "https://api.spendnod.com/mcp"
    }
  }
}
```

**ChatGPT**
1. Settings → Apps → Developer Mode → Create
2. Paste `https://api.spendnod.com/mcp`
3. Authorize via browser when prompted

## Features

- **Configurable rules engine** — set per-transaction limits, daily/monthly caps, vendor allowlists, category restrictions
- **Human approval workflow** — pending requests land in your dashboard with one-click approve/deny
- **Spending thresholds** — auto-approve below a limit, require approval above it, hard block at $10k
- **Full audit trail** — every authorization logged with agent, amount, vendor, decision, and timestamp
- **Multi-agent support** — manage multiple agents with independent rule sets
- **OAuth 2.1** — works with Claude Code, Claude Desktop, Cursor, VS Code, and ChatGPT out of the box

## Pricing

| Plan | Price | Authorizations/mo | Agents |
|------|-------|-------------------|--------|
| Free | $0 | 200 | 2 |
| Starter | $29/mo | 5,000 | 10 |
| Pro | $99/mo | 50,000 | 50 |
| Enterprise | Custom | Unlimited | Unlimited |

## Links

- Dashboard: [app.spendnod.com](https://app.spendnod.com)
- API docs: [api.spendnod.com/docs](https://api.spendnod.com/docs)
- Website: [spendnod.com](https://spendnod.com)
- Enterprise: [spendnod.com/enterprise](https://spendnod.com/enterprise)

---

Built by [Stack Industries LLC](https://spendnod.com)
