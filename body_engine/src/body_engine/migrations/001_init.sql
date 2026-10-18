-- Body Engine initial schema (docs/system-design.md §6). Every business table carries user_id.

CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  display_name TEXT NOT NULL,
  oidc_sub TEXT UNIQUE,                 -- Pocket ID user identifier (enabled once family members join)
  ow_user_id TEXT UNIQUE,               -- the matching user in Open Wearables
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
  birth_date DATE,
  sex TEXT,
  height_cm REAL,
  timezone TEXT NOT NULL DEFAULT 'Australia/Sydney',
  consent_at TIMESTAMPTZ,               -- when the family member gave written consent
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-user API / upload tokens. Only the sha256 is stored; the plaintext is shown once, at creation.
CREATE TABLE api_tokens (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  token_sha256 TEXT NOT NULL UNIQUE,
  scopes TEXT[] NOT NULL DEFAULT '{read,write}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);

CREATE TABLE shares (                  -- owner grants viewer access to which content
  owner_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  viewer_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scopes TEXT[] NOT NULL,               -- e.g. {metrics, sleep, scores, training}
  expires_at TIMESTAMPTZ,
  PRIMARY KEY (owner_id, viewer_id),
  CHECK (owner_id <> viewer_id)
);

-- Daily metrics, recomputable. source: ow > apple_export > shortcut (importers overwrite by this priority).
CREATE TABLE daily_metrics (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  metric TEXT NOT NULL,                 -- HealthKit type name in snake_case, e.g. step_count, resting_heart_rate
  value DOUBLE PRECISION NOT NULL,
  unit TEXT,
  agg TEXT NOT NULL,                    -- sum / avg
  n INT,
  min_value DOUBLE PRECISION,
  max_value DOUBLE PRECISION,
  source TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date, metric)
);
CREATE INDEX daily_metrics_metric_idx ON daily_metrics (user_id, metric, date);

CREATE TABLE sleep_sessions (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  wake_date DATE NOT NULL,              -- local date of (wake time - 4h)
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  asleep_min INT, core_min INT, deep_min INT, rem_min INT, awake_min INT,
  unspecified_min INT,                  -- portion marked only as "asleep" with no stage (counted into asleep_min)
  is_main BOOLEAN NOT NULL DEFAULT TRUE,
  source TEXT NOT NULL,
  device TEXT,
  UNIQUE (user_id, source, start_at)
);
CREATE INDEX sleep_sessions_date_idx ON sleep_sessions (user_id, wake_date);

CREATE TABLE daily_scores (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  sleep_score INT, sleep_need_min INT, sleep_debt_min INT, sleep_performance DOUBLE PRECISION,
  hrv_kind TEXT,                        -- rmssd / sdnn (never mixed for one person)
  hrv_night_ln DOUBLE PRECISION, rhr_night DOUBLE PRECISION, recovery INT,
  trimp DOUBLE PRECISION, strain DOUBLE PRECISION, muscle_load DOUBLE PRECISION,
  ctl DOUBLE PRECISION, atl DOUBLE PRECISION, tsb DOUBLE PRECISION,
  flags JSONB,
  algo_version TEXT NOT NULL,
  PRIMARY KEY (user_id, date)
);

CREATE TABLE exercise_meta (           -- extra exercise fields the logging app lacks
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  exercise_ref TEXT NOT NULL,           -- exercise ID in the logging app
  name_zh TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'caution', 'banned')),
  per_side BOOLEAN NOT NULL DEFAULT FALSE,
  cues TEXT,
  PRIMARY KEY (user_id, exercise_ref)
);

CREATE TABLE journal_items (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  label_zh TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'bool',
  unit TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  PRIMARY KEY (user_id, key)
);
CREATE TABLE journal_entries (         -- no record = missing, not "no"
  user_id INT NOT NULL,
  date DATE NOT NULL,
  key TEXT NOT NULL,
  value DOUBLE PRECISION NOT NULL,
  source TEXT,
  note TEXT,
  PRIMARY KEY (user_id, date, key),
  FOREIGN KEY (user_id, key) REFERENCES journal_items(user_id, key) ON DELETE CASCADE
);

CREATE TABLE supplements (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, dose TEXT, timing TEXT, ingredients TEXT,
  status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'not_needed')),
  started_on DATE, stopped_on DATE, rationale TEXT, journal_key TEXT
);

CREATE TABLE lab_results (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  collected_on DATE NOT NULL,
  test_name TEXT NOT NULL,
  value DOUBLE PRECISION, value_text TEXT, unit TEXT,
  ref_low DOUBLE PRECISION, ref_high DOUBLE PRECISION,
  flag TEXT, lab TEXT, note TEXT
);

CREATE TABLE events (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  occurred_at TIMESTAMPTZ NOT NULL,
  category TEXT, body_part TEXT, severity INT,
  title TEXT NOT NULL, note TEXT
);

CREATE TABLE sync_state (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  last_synced_at TIMESTAMPTZ,
  cursor TEXT,
  PRIMARY KEY (user_id, source)
);

-- AI assistant (reserved): every tool call the assistant makes is logged, for later audit.
CREATE TABLE assistant_audit (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- which user the assistant acts on behalf of
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  channel TEXT NOT NULL,                -- api (in-app chat, calls the Claude API) / mcp (Claude Code, Claude desktop)
  tool TEXT NOT NULL,
  args JSONB,
  ok BOOLEAN NOT NULL,
  error TEXT
);
CREATE INDEX assistant_audit_user_idx ON assistant_audit (user_id, at);
