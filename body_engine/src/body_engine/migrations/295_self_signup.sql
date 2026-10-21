-- Self-service sign-up (docs/self-signup-plan.md, body_engine/src/body_engine/account/):
-- people sign in with an e-mail code or Sign in with Apple, a new account is `pending` until it redeems an admin-issued
-- invite code, then provisioning (Open Wearables user, SparkyFitness account + keys, default journal items) runs as an
-- idempotent job. Only hashes of codes are stored. No grant to grafana_ro (nothing here is health data).

-- pending: signed in, waiting for an invite code (sees nothing else); active: normal; disabled: cannot sign in.
-- Existing accounts (admin onboarding) are active.
ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('pending', 'active', 'disabled'));

-- Sign-in identities; one user may have several (Apple + e-mail). subject = Apple's `sub`, or the normalised e-mail.
CREATE TABLE user_identities (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL CHECK (provider IN ('apple', 'email')),
  subject TEXT NOT NULL,
  email TEXT,                                   -- verified address (Apple: may be a private relay address), for login only
  verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at TIMESTAMPTZ,
  UNIQUE (provider, subject)
);
CREATE INDEX user_identities_user ON user_identities (user_id);
CREATE INDEX user_identities_email ON user_identities (lower(email));

-- One-time 6-digit e-mail codes: valid 10 minutes, 5 attempts. Only a salted sha256 is stored.
CREATE TABLE email_login_codes (
  id SERIAL PRIMARY KEY,
  email TEXT NOT NULL,                          -- normalised (lower-case, trimmed)
  code_sha256 TEXT NOT NULL,
  salt TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  attempts INT NOT NULL DEFAULT 0,
  used_at TIMESTAMPTZ
);
CREATE INDEX email_login_codes_email ON email_login_codes (email, created_at DESC);

-- Invite codes come in batches (e.g. one per channel / group).
CREATE TABLE invite_batches (
  id SERIAL PRIMARY KEY,
  label TEXT NOT NULL,
  created_by TEXT,                              -- free text: who ran the command
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,                       -- NULL = no expiry
  max_uses INT NOT NULL DEFAULT 1 CHECK (max_uses >= 1),   -- per code
  revoked_at TIMESTAMPTZ
);

CREATE TABLE invite_codes (
  id SERIAL PRIMARY KEY,
  batch_id INT NOT NULL REFERENCES invite_batches(id) ON DELETE CASCADE,
  code_sha256 TEXT NOT NULL UNIQUE,
  hint TEXT NOT NULL,                           -- last 3 characters, to tell codes apart in listings
  max_uses INT NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
  uses INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);
CREATE INDEX invite_codes_batch ON invite_codes (batch_id);

-- Who redeemed which code when. user_id becomes NULL when that account is deleted (the count stays).
CREATE TABLE invite_redemptions (
  id SERIAL PRIMARY KEY,
  code_id INT NOT NULL REFERENCES invite_codes(id) ON DELETE CASCADE,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,
  redeemed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX invite_redemptions_code ON invite_redemptions (code_id);
CREATE UNIQUE INDEX invite_redemptions_user ON invite_redemptions (user_id) WHERE user_id IS NOT NULL;

-- Provisioning job state per user (account/provision.py), plus the SparkyFitness credentials it created,
-- encrypted with the AI master key (ai/crypto.py, AES-256-GCM bound to user_id). Admin-onboarded users keep their
-- key files under secrets/ and have no row here.
CREATE TABLE user_provisioning (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'done', 'failed')),
  steps JSONB NOT NULL DEFAULT '{}',            -- {"ow": "done", "sparky": "error: …", "journal": "done", "memory": "done"}
  attempts INT NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sparky_email TEXT,                            -- synthetic login (not the person's e-mail)
  sparky_password_enc BYTEA,
  sparky_key_ro_enc BYTEA,
  sparky_key_rw_enc BYTEA
);
