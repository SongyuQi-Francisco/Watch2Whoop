-- Fallback AI (ai/fallback.py): users who have not configured their own AI can chat on the Today page with the admin's DeepSeek key,
-- capped per person per natural month (user's time zone) in CNY. Adds tables and columns only, no change to existing data.
-- Fallback calls are recorded in ai_usage with kind = 'system_fallback', provider_id NULL, cost_usd 0 (not the user's own money)
-- and the real cost in cost_cny; the in-page chat budget of the user's own providers is unaffected.

ALTER TABLE ai_usage ADD COLUMN cost_cny NUMERIC(12, 6);
CREATE INDEX ai_usage_fallback_idx ON ai_usage (user_id, at) WHERE kind = 'system_fallback';

-- Separate, optional consent: the fallback sends the data the model asks for to DeepSeek (a third party, servers in China).
-- One row per consent; revoking sets revoked_at (a new consent adds a new row). Version = fallback.CONSENT_VERSION.
CREATE TABLE ai_fallback_consents (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  version TEXT NOT NULL,
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ,
  user_agent TEXT
);
CREATE INDEX ai_fallback_consents_user_idx ON ai_fallback_consents (user_id, accepted_at);

-- Per-person monthly quota set by the admin (body-engine ai-fallback quota); no row = the default (fallback_params.MONTHLY_QUOTA_CNY).
-- 0 = this person may not use the fallback.
CREATE TABLE ai_fallback_quota (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  monthly_cny NUMERIC(8, 2) NOT NULL CHECK (monthly_cny >= 0 AND monthly_cny <= 1000),
  note TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Global switch and the last DeepSeek balance check (single row). Below the stop threshold the fallback pauses for everyone.
CREATE TABLE ai_fallback_state (
  id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  enabled BOOLEAN NOT NULL DEFAULT true,
  balance_cny NUMERIC(12, 2),
  balance_checked_at TIMESTAMPTZ,
  balance_error TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO ai_fallback_state (id) VALUES (1);
