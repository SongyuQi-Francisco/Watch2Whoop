-- Gradual rollout and A/B tests (rollout/, docs/rollout.md): feature flags, user cohorts (e.g. beta), experiments with
-- stable bucketing and exposure counts, the consent base version, and an audit log of every change.
-- Nothing here is health data; no grant to grafana_ro.

-- One row per flag key declared in rollout/registry.py. off: nobody; cohort: members of `cohort`;
-- percent: `percent` % of users by a stable hash of (key, user_id), plus the cohort; all: everyone.
CREATE TABLE feature_flags (
  key TEXT PRIMARY KEY,
  description TEXT,
  state TEXT NOT NULL DEFAULT 'off' CHECK (state IN ('off', 'cohort', 'percent', 'all')),
  cohort TEXT NOT NULL DEFAULT 'beta',
  percent INT NOT NULL DEFAULT 0 CHECK (percent BETWEEN 0 AND 100),
  min_consent_version TEXT,                    -- consent version a user must have agreed to before the feature turns on
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT
);

-- Named groups of users (beta = internal testers). The list lives here, never in code.
CREATE TABLE user_cohorts (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  cohort TEXT NOT NULL,
  added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  added_by TEXT,
  PRIMARY KEY (user_id, cohort)
);
CREATE INDEX user_cohorts_cohort ON user_cohorts (cohort);

-- A/B tests: variants [{"name": "control", "weight": 50}, …]; optional cohort restriction and time window.
CREATE TABLE experiments (
  key TEXT PRIMARY KEY,
  description TEXT,
  variants JSONB NOT NULL,
  cohort TEXT,                                 -- NULL = everyone
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'stopped')),
  starts_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ends_at TIMESTAMPTZ,                         -- NULL = until stopped
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT
);

-- Exposures: who saw which variant on which (local) day and how often. No content.
CREATE TABLE experiment_exposures (
  experiment_key TEXT NOT NULL REFERENCES experiments(key) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day DATE NOT NULL,
  variant TEXT NOT NULL,
  exposures INT NOT NULL DEFAULT 1,
  first_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (experiment_key, user_id, day, variant)
);

-- Small settings of the rollout machinery, e.g. consent_base_version (absent = the version in consent.md).
CREATE TABLE rollout_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT
);

-- Every change to flags, cohorts, experiments and rollout settings.
CREATE TABLE rollout_audit (
  id BIGSERIAL PRIMARY KEY,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor TEXT,
  action TEXT NOT NULL,                        -- flag.set / cohort.add / cohort.remove / experiment.start / experiment.stop / setting.set …
  target TEXT NOT NULL,
  before JSONB,
  after JSONB
);
CREATE INDEX rollout_audit_at ON rollout_audit (at DESC);
