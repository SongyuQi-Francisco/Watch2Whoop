-- Apple Watch hold-to-talk (docs/watch-app-plan.md 4.5): the watch gets its own narrow device token through a one-time
-- code the signed-in phone asks for. account/watch.py, auth.user_for_watch_token.
--
-- A token made for a watch hangs off the phone token that asked for its code: it is only valid while that token is
-- (auth.user_for_watch_token joins it on every request), so signing out on the phone, revoking that token or deleting the
-- account ends the watch too, whichever way it happens.
ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS parent_token_id INT REFERENCES api_tokens(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS api_tokens_parent ON api_tokens (parent_token_id) WHERE parent_token_id IS NOT NULL;

-- What a one-time code is for: 'login' (every code so far: the Today page / app sign-in) or 'watch' (only POST
-- /v1/web/login-code with client=watch takes it, and it only gives a watch token). parent_token_id: the phone token
-- that asked for a watch code.
ALTER TABLE web_login_codes ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'login';
ALTER TABLE web_login_codes DROP CONSTRAINT IF EXISTS web_login_codes_purpose_check;
ALTER TABLE web_login_codes ADD CONSTRAINT web_login_codes_purpose_check CHECK (purpose IN ('login', 'watch'));
ALTER TABLE web_login_codes ADD COLUMN IF NOT EXISTS parent_token_id INT REFERENCES api_tokens(id) ON DELETE CASCADE;

-- Which device a call came from: 'watch', or NULL (the phone / the Today page). On a conversation: the device that
-- started it (a watch token only reaches conversations its watch started); on usage rows: the device of that request.
ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS client TEXT;
ALTER TABLE ai_conversations DROP CONSTRAINT IF EXISTS ai_conversations_client_check;
ALTER TABLE ai_conversations ADD CONSTRAINT ai_conversations_client_check CHECK (client IS NULL OR client = 'watch');
ALTER TABLE ai_usage ADD COLUMN IF NOT EXISTS client TEXT;
ALTER TABLE asr_usage ADD COLUMN IF NOT EXISTS client TEXT;
