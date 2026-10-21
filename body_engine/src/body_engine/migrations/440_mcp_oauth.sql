-- OAuth 2.1 for the remote MCP endpoint /mcp (ai/oauth.py, docs/ai-assistant-plan.md 9.16): Claude / ChatGPT / Gemini app
-- connectors register themselves (RFC 7591), the user approves on the Today page, the client gets a short-lived access token
-- (an api_tokens row with scope mcp, like the static AI tokens) and a rotating refresh token. Only sha256 of every secret is stored.

-- Access tokens issued through OAuth expire and belong to a grant (one approved connection)
ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS oauth_grant_id BIGINT;

-- Dynamically registered clients. All are public clients (token_endpoint_auth_method none + PKCE): no client secret exists.
CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY,
  client_name TEXT,
  redirect_uris TEXT[] NOT NULL,
  scope TEXT,
  metadata JSONB NOT NULL,              -- the registered client information (RFC 7591 response), no secrets
  registered_ip_sha256 TEXT,            -- for the registration rate limit
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS oauth_clients_ip_idx ON oauth_clients (registered_ip_sha256, created_at);

-- Authorization requests waiting for the user's decision on the consent page (/app/oauth.html?req=…)
CREATE TABLE IF NOT EXISTS oauth_requests (
  request_sha256 TEXT PRIMARY KEY,
  client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  redirect_uri TEXT NOT NULL,
  redirect_uri_provided_explicitly BOOLEAN NOT NULL,
  code_challenge TEXT NOT NULL,
  state TEXT,
  scopes TEXT[] NOT NULL,
  resource TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);

-- One approved connection = one grant: what the user sees under 「我 → AI 设置」 and can revoke (revoking ends every token of it)
CREATE TABLE IF NOT EXISTS oauth_grants (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  client_name TEXT,
  redirect_host TEXT NOT NULL,
  scopes TEXT[] NOT NULL,
  resource TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  revoke_reason TEXT                   -- user / refresh_reuse / code_reuse / revoke_endpoint / admin
);
CREATE INDEX IF NOT EXISTS oauth_grants_user_idx ON oauth_grants (user_id) WHERE revoked_at IS NULL;

-- Authorization codes: one use, 5 minutes
CREATE TABLE IF NOT EXISTS oauth_codes (
  code_sha256 TEXT PRIMARY KEY,
  grant_id BIGINT NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  redirect_uri_provided_explicitly BOOLEAN NOT NULL,
  code_challenge TEXT NOT NULL,
  scopes TEXT[] NOT NULL,
  resource TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ
);

-- Refresh tokens: rotated on every use; presenting a used one again revokes the whole grant
CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
  token_sha256 TEXT PRIMARY KEY,
  grant_id BIGINT NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
  client_id TEXT NOT NULL,
  scopes TEXT[] NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS oauth_refresh_grant_idx ON oauth_refresh_tokens (grant_id);

ALTER TABLE api_tokens DROP CONSTRAINT IF EXISTS api_tokens_oauth_grant_fk;
ALTER TABLE api_tokens ADD CONSTRAINT api_tokens_oauth_grant_fk FOREIGN KEY (oauth_grant_id)
  REFERENCES oauth_grants(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS api_tokens_oauth_grant_idx ON api_tokens (oauth_grant_id) WHERE oauth_grant_id IS NOT NULL;
