-- 2.0.0 P0 (docs/ios-native-2.0.md sections 3.2, 3.4 and 5): the state and the spot checks of the kinds the phone computes
-- and uploads with PUT /v1/users/me/derived/app (nativesync/derived.py). Generic over kinds: a package adds its module in
-- nativesync/derived_kinds/ and its own tables in its migration range (1000+), never a column here. Empty until a package's
-- flag is on for someone.

-- One row per (user, kind): what the phone uploaded last and whether its uploads may be stored as the user's (primary).
CREATE TABLE IF NOT EXISTS app_compute_state (
  user_id          INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind             TEXT NOT NULL,                     -- daily | sleep | workouts | stress | healthspan | reminders
  algo_version     TEXT NOT NULL DEFAULT '',          -- of the last accepted upload
  bundle_version   TEXT,
  last_upload_at   TIMESTAMPTZ,
  uploads          INT NOT NULL DEFAULT 0,
  days_uploaded    BIGINT NOT NULL DEFAULT 0,          -- days in accepted uploads (primary needs PRIMARY_MIN_DAYS)
  trusted          BOOLEAN NOT NULL DEFAULT TRUE,     -- false after a failed spot check, until one passes again
  server_forced    BOOLEAN NOT NULL DEFAULT FALSE,    -- admin: the server keeps this kind whatever the app uploads
  last_check_at    TIMESTAMPTZ,
  last_check_ok    BOOLEAN,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, kind)
);

-- Automatic spot checks (product safety, like app_score_checks): pass / fail and the names of the fields beyond the
-- tolerance only, never the values (section 3.4: values of non-research users are not kept for analysis).
CREATE TABLE IF NOT EXISTS app_compute_checks (
  id             BIGSERIAL PRIMARY KEY,
  user_id        INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind           TEXT NOT NULL,
  day            DATE NOT NULL,
  algo_version   TEXT NOT NULL,
  bundle_version TEXT,
  ok             BOOLEAN NOT NULL,
  compared       BOOLEAN NOT NULL DEFAULT TRUE,       -- false: the server had nothing to compare with (counts as ok)
  fields_over    TEXT[] NOT NULL DEFAULT '{}',
  checked_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_compute_checks_user_idx ON app_compute_checks (user_id, kind, checked_at DESC);
