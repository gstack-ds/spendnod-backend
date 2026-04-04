# SpendNod

**Human authorization gateway for AI agent transactions.**

SpendNod sits between your AI agent and the real world. Before an agent executes a purchase, API call, or any consequential action, it submits a request to SpendNod. Configurable rules auto-approve routine transactions; anything outside the rules routes to a human for approval. The agent waits, the human decides, the transaction proceeds — or doesn't.

## What it does

- Intercepts agent spending requests via a single MCP tool call
- Evaluates against your rules (amount limits, vendor allowlists, category blocks, etc.)
- Returns `auto_approved`, `pending`, or `denied` — agents act on the response
- Pending requests appear in your dashboard at [app.spendnod.com](https://app.spendnod.com) for one-click approval or denial

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
1. Open ChatGPT → Settings → Developer Mode
2. Add App → paste `https://api.spendnod.com/mcp`
3. Authorize via browser when prompted

## Links

- Dashboard: [app.spendnod.com](https://app.spendnod.com)
- API docs: [api.spendnod.com/docs](https://api.spendnod.com/docs)
- Website: [spendnod.com](https://spendnod.com)

---

Built by [Stack Industries LLC](https://spendnod.com)
