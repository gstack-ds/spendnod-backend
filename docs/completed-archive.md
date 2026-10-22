# Completed Archive

Completed lists moved out of CLAUDE.md on 2026-10-22, copied word for word.

### Completed (2026-04-13) — session 2
- [x] Online Shopping template: `max_per_transaction` at $500 → `require_approval_above` at $500
- [x] `risk_level` field on all templates (low/medium/high)
- [x] `rule_backups` table + migration `006_rule_backups.sql`
- [x] `POST /v1/agents/{id}/rules/template` — apply template with auto-backup + audit log
- [x] `GET /v1/agents/{id}/rules/backup` — fetch most recent backup
- [x] `POST /v1/agents/{id}/rules/restore` — restore from most recent backup
- [x] `PATCH /v1/rules/{id}` logs `rules_changed` audit event with before/after values
- [x] Run `006_rule_backups.sql` in Supabase SQL editor (not yet applied to prod)

### Completed (2026-04-13) — session 1
- [x] Add Online Shopping, Procurement, Ad Spending rule templates
- [x] Conservative template auto-applied as default for all new agents

### Completed (2026-04-03/04)
- [x] Stripe billing fully tested end-to-end: free → starter → pro checkout working
- [x] Stripe webhook handler fixed (was returning 500; now processes `checkout.session.completed` correctly)
- [x] Upgrade modal shows correct options based on current plan (`available_upgrades` + `has_billing_account` on `/v1/usage`)
- [x] Manage Billing button opens Stripe customer portal for paid users
- [x] Stripe switched to live mode (all 5 env vars updated in Railway)
- [x] Stripe customer portal configured with plan switching enabled
- [x] Railway reconnected to spendnod-backend repo for auto-deploys
- [x] DATABASE_URL switched to Supabase Session Pooler (Railway IPv6 fix)
- [x] Credential rotation: DATABASE_URL password rotated, RESEND_API_KEY rotated
- [x] JWT_SECRET restored to match Supabase JWT secret
- [x] Google and GitHub OAuth sign-in enabled in Supabase
- [x] ChatGPT MCP integration verified end-to-end with OAuth
- [x] SpendNod logo deployed across all surfaces
- [x] "business" plan renamed to "enterprise" throughout codebase
- [x] Update `notification.py` Resend sender domain to `notifications@spendnod.com`
