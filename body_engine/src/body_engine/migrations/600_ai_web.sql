-- Server-side web tools of the in-page chat (issue #150, ai/web/): every web_search / web_fetch the server runs for a user is
-- one ai_usage row (purpose 'web_search' / 'web_fetch', no tokens, cost_usd 0, cost_cny = what the search API costs the
-- admin). They count against the fallback quota (admin money per person per month) and the per-day limits in
-- ai/web/params.py, never against the user's own USD budget, and are not model calls (the fallback's rate limits skip them).
ALTER TABLE ai_usage DROP CONSTRAINT IF EXISTS ai_usage_purpose_check;
ALTER TABLE ai_usage ADD CONSTRAINT ai_usage_purpose_check CHECK (purpose IN ('chat', 'compact', 'web_search', 'web_fetch'));
CREATE INDEX IF NOT EXISTS ai_usage_web_idx ON ai_usage (user_id, at) WHERE purpose IN ('web_search', 'web_fetch');
