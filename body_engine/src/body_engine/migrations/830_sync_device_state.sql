-- What the Watch2Whoop app last said about the phone side of its sync (sync/overview.py; the app reports it from 1.0.8,
-- docs/sync-onboarding-app-requirements.md): the iOS settings that stop a background sync (Background App Refresh, Low
-- Power Mode), whether Health was readable at the last background attempt (false = the phone was locked), the sync's
-- last error and the Health types iOS has not asked the person about yet. One row per user, replaced by every report.
-- Phone state, not health data: no grant to grafana_ro.
CREATE TABLE IF NOT EXISTS sync_device_state (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  app_version TEXT,
  background_refresh TEXT CHECK (background_refresh IN ('available', 'denied', 'restricted')),
  low_power BOOLEAN,
  health_readable BOOLEAN,
  last_attempt_at TIMESTAMPTZ,
  last_upload_at TIMESTAMPTZ,
  last_error TEXT CHECK (last_error IN ('auth', 'network', 'locked', 'health_denied', 'server', 'other')),
  unasked_types TEXT[] NOT NULL DEFAULT '{}'
);
