-- Reusable activation codes for the Watch2Whoop app (web/activation.py): one long-lived link per person
-- (<public url>/app/activate.html#<code>) that can be opened again on a new phone or after a reinstall until it is revoked.
-- Each use mints a fresh one-time Today page login code (web_login_codes) and, when configured, a fresh Open Wearables
-- invitation code, and hands the app a watch2whoop://setup link. Only the sha256 of the code is stored. No grant to grafana_ro.
CREATE TABLE activation_codes (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_sha256 TEXT NOT NULL UNIQUE,
  label TEXT,                                   -- for whom, e.g. "Mum"; written into the minted login code's label
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,                       -- NULL = valid until revoked
  revoked_at TIMESTAMPTZ,
  last_used_at TIMESTAMPTZ,
  uses INT NOT NULL DEFAULT 0
);
CREATE INDEX activation_codes_user ON activation_codes (user_id);
