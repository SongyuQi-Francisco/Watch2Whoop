-- Audit trail of sign-in identity changes (account/identities.py): who bound / moved / removed which e-mail or Apple ID,
-- and every automatic step (an Apple sign-in joined to an existing account by its verified e-mail; a pending sign-up that
-- entered an existing account's activation / login code). Only masked addresses are stored. No grant to grafana_ro.
CREATE TABLE identity_events (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,   -- the account the identity now belongs to (or was removed from)
  from_user_id INT,                                     -- moved / claimed from (no FK: that account may be deleted)
  provider TEXT NOT NULL CHECK (provider IN ('apple', 'email')),
  hint TEXT,                                            -- masked e-mail / Apple ID tail
  kind TEXT NOT NULL CHECK (kind IN ('link', 'move', 'unlink', 'auto_link_apple', 'claim_by_code')),
  actor TEXT NOT NULL,                                  -- admin-cli / user / sign-in
  at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX identity_events_user ON identity_events (user_id, at);
