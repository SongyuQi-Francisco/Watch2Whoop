-- Sign in with Apple refresh tokens (account/apple_tokens.py): kept only so that deleting an account can revoke the
-- app's access at Apple (App Store Review Guideline 5.1.1(v)). One row per Apple ID; the token is AES-256-GCM
-- encrypted under the AI master key (additional data = the Apple subject). Deleted with the account (after revocation).
-- Not health data; no grant to grafana_ro.
CREATE TABLE IF NOT EXISTS apple_signin_tokens (
  subject           TEXT PRIMARY KEY,                                  -- Apple's `sub` (user_identities.subject)
  user_id           INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  client_id         TEXT NOT NULL,                                     -- the bundle ID the code was issued for
  refresh_token_enc BYTEA NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS apple_signin_tokens_user_idx ON apple_signin_tokens (user_id);
COMMENT ON TABLE apple_signin_tokens IS 'Encrypted Sign in with Apple refresh tokens, revoked on account deletion';
