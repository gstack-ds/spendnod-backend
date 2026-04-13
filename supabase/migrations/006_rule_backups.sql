-- Migration 006: Add rule_backups table
-- Stores snapshots of an agent's rules before a template is applied,
-- enabling one-click restore to any previous configuration.

CREATE TABLE IF NOT EXISTS rule_backups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rules_json JSONB NOT NULL,
    template_applied TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rule_backups_agent ON rule_backups(agent_id);
CREATE INDEX IF NOT EXISTS idx_rule_backups_created ON rule_backups(created_at DESC);
