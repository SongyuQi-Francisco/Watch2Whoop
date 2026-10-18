-- One-time login codes (used in the Watch2Whoop app's setup info / QR code, instead of putting a long-lived token in plaintext).
-- Redeeming creates a dedicated API token (plaintext never leaves the server) + a Today page session; a code is single-use and expires, and only its sha256 is stored.
-- No grant to grafana_ro.
CREATE TABLE web_login_codes (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_sha256 TEXT NOT NULL UNIQUE,
  scopes TEXT[] NOT NULL,
  label TEXT,                                   -- for whom / which device, written into the redeemed token's name
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,
  token_id INT REFERENCES api_tokens(id) ON DELETE SET NULL   -- the token created on redemption (revoking it = signing out this device)
);
CREATE INDEX web_login_codes_user ON web_login_codes (user_id);
