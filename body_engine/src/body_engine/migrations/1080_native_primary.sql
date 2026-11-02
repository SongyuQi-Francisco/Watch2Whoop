-- 2.0.0 S9 (docs/ios-native-2.0.md section 4 S9 and 5): the server stops computing for app-mode users, per package.
-- nativesync/primary.py. No values of anyone's health data are kept here: ids, package / kind names, dates and counts.

-- Which packages are primary for whom: `body-engine app-compute primary --package P --user-id N | --cohort C | --all`
-- (checked against the spot-check pass rate of app_compute_checks, or --force; every change in rollout_audit).
-- A package is primary for a user only when one of these rows covers them, besides the flags (package flag +
-- native_compute_primary, the global kill switch) and the kind's own trust (compute.trusted).
CREATE TABLE IF NOT EXISTS app_compute_primary (
  id          BIGSERIAL PRIMARY KEY,
  package     TEXT NOT NULL,                        -- key of nativesync/params.COMPUTE_PACKAGES
  scope       TEXT NOT NULL CHECK (scope IN ('user', 'cohort', 'all')),
  user_id     INT REFERENCES users(id) ON DELETE CASCADE,
  cohort      TEXT,
  forced      BOOLEAN NOT NULL DEFAULT FALSE,       -- granted with --force (the pass-rate gate did not pass)
  gate        JSONB,                                -- the pass-rate gate at the time: counts and rates only
  granted_by  TEXT NOT NULL,
  granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((scope = 'user') = (user_id IS NOT NULL)),
  CHECK ((scope = 'cohort') = (cohort IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS app_compute_primary_target_idx
  ON app_compute_primary (package, scope, coalesce(user_id, 0), coalesce(cohort, ''));

-- The days the phone uploaded per kind (accepted uploads, shadow and primary): the scheduled jobs' per-day fallback
-- (a queued day the phone did not upload within the grace period is derived by the server again).
CREATE TABLE IF NOT EXISTS app_compute_days (
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,
  day         DATE NOT NULL,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, kind, day)
);

-- The server computed something on demand for a primary user because the phone had not uploaded it (yet): which package,
-- which path (endpoint / job), which day, how often. Counts only; the runbook expects these to stay near zero.
CREATE TABLE IF NOT EXISTS app_compute_fallbacks (
  user_id   INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  package   TEXT NOT NULL,
  path      TEXT NOT NULL,
  day       DATE NOT NULL,
  count     INT NOT NULL DEFAULT 1,
  last_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, package, path, day)
);
