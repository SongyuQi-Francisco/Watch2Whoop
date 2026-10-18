-- Tester self-service: in-app electronic consent (consent form profile/consent.md, versioned), and filling in / editing one's own profile (profile/).
-- Before consent (role = member and users.consent_at is empty) this person's health data is not processed: import / scoring commands skip them and history imports are rejected;
-- data synced by the app to OW and uploaded by the Shortcut is staged as-is, and the first hourly job after consent starts processing it.

ALTER TABLE users ADD COLUMN consent_version TEXT;   -- consent form version currently in effect (recorded on in-app consent; paper = signed offline)
COMMENT ON COLUMN users.consent_at IS 'Consent time: electronic consent in the app, or the offline signing date recorded by the admin (onboard.py --consent-at)';

-- One row per consent (re-consent after a new version, offline signatures recorded later)
CREATE TABLE user_consents (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  version TEXT NOT NULL,                -- consent form version (the "版本：" line in consent.md), offline signature = paper
  method TEXT NOT NULL CHECK (method IN ('app', 'paper')),
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_agent TEXT
);
CREATE INDEX user_consents_user_idx ON user_consents (user_id, accepted_at);

-- Goal weight is filled in by the user in "Profile": written to the status card's goal_weight, source user
ALTER TABLE assistant_status DROP CONSTRAINT IF EXISTS assistant_status_source_check;
ALTER TABLE assistant_status ADD CONSTRAINT assistant_status_source_check
  CHECK (source IN ('auto', 'assistant', 'seed', 'user'));

GRANT SELECT ON user_consents TO grafana_ro;
