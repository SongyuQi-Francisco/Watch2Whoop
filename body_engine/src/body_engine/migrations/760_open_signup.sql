-- Open sign-up (account/open_signup.py, feature flag open_signup, limits in account/params.py): while it is on and the
-- active accounts are below the cap, a signed-in account is activated without an invite code. No grant to grafana_ro
-- (nothing here is health data).

-- One row per account that open sign-up activated, or held back because its IP had opened too many accounts that day.
-- Deleted with the account (account/delete.py removes every row with a user_id).
CREATE TABLE open_signups (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  decision TEXT NOT NULL CHECK (decision IN ('activated', 'ip_limit')),
  via TEXT NOT NULL CHECK (via IN ('signup', 'pending')),   -- signup: a new account; pending: one that was waiting for a code
  ip_sha256 TEXT,                                           -- sha256 of the sign-up IP (new accounts), cleared after 48 hours
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  activated_at TIMESTAMPTZ
);
CREATE INDEX open_signups_ip ON open_signups (ip_sha256, created_at) WHERE ip_sha256 IS NOT NULL;

-- Sign-in identities (Apple / e-mail) that opened an account by open sign-up: hash + masked hint, no user_id, so the record
-- outlives a deleted account (the same rule as invite_redemption_identities: one free account per identity).
CREATE TABLE open_signup_identities (
  identity_sha256 TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  hint TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
