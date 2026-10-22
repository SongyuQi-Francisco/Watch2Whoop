-- AI quota cards (ai/quota_cards.py, issue #182, flag ai_cards): cards that give back or add to a person's monthly
-- Jarvis allowance (the fallback AI, ai/fallback.py). Adds tables only, no change to existing data.
-- Allowance this month = monthly quota + extend cards used this month - fallback spend since the last reset card used this month.
-- Admin / dashboard data (not health data): grafana_ro may read it for the admin dashboard.

-- One row per card. kind reset = clear this month's used allowance; extend = add amount_cny to this month's allowance.
-- status: unused -> used | expired | revoked (never back). grant_key makes a grant idempotent per person (periodic rules,
-- rewards from other modules / plugins): a second grant with the same key adds nothing.
CREATE TABLE ai_quota_cards (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('reset', 'extend')),
  amount_cny NUMERIC(8, 2),
  source TEXT NOT NULL CHECK (source IN ('admin', 'periodic', 'reward', 'other')),
  reason TEXT NOT NULL,
  granted_by TEXT NOT NULL,                    -- who / what granted it: cli:<os user>, rule:<name>, module:<name>, plugin:<name>
  grant_key TEXT,
  granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,                      -- NULL = never
  status TEXT NOT NULL DEFAULT 'unused' CHECK (status IN ('unused', 'used', 'expired', 'revoked')),
  used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  revoked_by TEXT,
  revoke_reason TEXT,
  CHECK ((kind = 'extend' AND amount_cny > 0 AND amount_cny <= 1000) OR (kind = 'reset' AND amount_cny IS NULL)),
  CHECK ((status = 'used') = (used_at IS NOT NULL))
);
CREATE UNIQUE INDEX ai_quota_cards_grant_key ON ai_quota_cards (user_id, grant_key) WHERE grant_key IS NOT NULL;
CREATE INDEX ai_quota_cards_user ON ai_quota_cards (user_id, status, expires_at);
CREATE INDEX ai_quota_cards_used ON ai_quota_cards (user_id, used_at) WHERE status = 'used';

-- Append-only ledger of every change (the audit trail): grant / use / expire / revoke. For use: spent_before_cny and
-- allowance_before_cny are what the person had just before (reset: the cleared amount = spent_before_cny).
CREATE TABLE ai_quota_ledger (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  card_id BIGINT REFERENCES ai_quota_cards(id) ON DELETE SET NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  event TEXT NOT NULL CHECK (event IN ('grant', 'use', 'expire', 'revoke')),
  kind TEXT NOT NULL,
  amount_cny NUMERIC(8, 2),
  spent_before_cny NUMERIC(12, 6),
  allowance_before_cny NUMERIC(12, 6),
  actor TEXT NOT NULL,                         -- user:<id> for a use, otherwise as granted_by / revoked_by; system for expiry
  note TEXT
);
CREATE INDEX ai_quota_ledger_user ON ai_quota_ledger (user_id, at DESC);

-- Periodic grant rules (body-engine ai-cards rule-set / rule-off; run hourly by `ai-cards periodic`, idempotent per period:
-- grant_key rule:<name>:<period>). period monthly: day = day of month (1–28); weekly: day = ISO weekday (1 = Monday).
-- active_within_days: only people who used Jarvis within that many days (NULL = everyone the flag is on for).
CREATE TABLE ai_quota_card_rules (
  name TEXT PRIMARY KEY CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
  enabled BOOLEAN NOT NULL DEFAULT true,
  period TEXT NOT NULL CHECK (period IN ('monthly', 'weekly')),
  day INT NOT NULL CHECK (day BETWEEN 1 AND 28),
  kind TEXT NOT NULL CHECK (kind IN ('reset', 'extend')),
  amount_cny NUMERIC(8, 2),
  count INT NOT NULL DEFAULT 1 CHECK (count BETWEEN 1 AND 10),
  expires_days INT CHECK (expires_days BETWEEN 1 AND 366),
  active_within_days INT CHECK (active_within_days BETWEEN 1 AND 366),
  reason TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((kind = 'extend' AND amount_cny > 0 AND amount_cny <= 1000) OR (kind = 'reset' AND amount_cny IS NULL)),
  CHECK (period = 'monthly' OR day <= 7)
);

GRANT SELECT ON ai_quota_cards, ai_quota_ledger, ai_quota_card_rules TO grafana_ro;   -- the admin dashboard (deploy/grafana)
