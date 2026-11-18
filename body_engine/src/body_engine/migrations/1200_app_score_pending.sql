-- App score uploads waiting for their spot check (nativesync/appscores.py, docs/api-memory.md "2026-11").
-- The check recomputes a day in Python from the user's whole raw history (a year of samples: ~200-300 MB of Python
-- objects for a few seconds), which killed the API process at its memory limit. So the upload only stores the rows here
-- and queues the check; the per-minute job (sync-refresh-run, a one-off container with its own memory limit) or, at the
-- latest, the hourly job runs it and then stores or drops these rows exactly as the request used to.
-- One row per user: uploads that arrive before the check are merged into it (newest row of a day wins).
CREATE TABLE IF NOT EXISTS app_score_pending (
  user_id        INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  algo_version   TEXT NOT NULL,
  bundle_version TEXT,
  check_date     DATE NOT NULL,                 -- the day the check recomputes (newest recent day of the first upload)
  rows           JSONB NOT NULL,                -- {"YYYY-MM-DD": daily_scores row as uploaded and cleaned}
  attempts       INT NOT NULL DEFAULT 0,        -- checks started (a job killed mid-check retries; given up after CHECK_MAX_ATTEMPTS)
  queued_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A queued check that could not be finished (CHECK_MAX_ATTEMPTS starts, its job process died each time): the waiting rows
-- are stored as the app's, unchecked (trust, the checked bundle and the last check stay as they were), counted here, and
-- no new check is started for this user for CHECK_EVERY_HOURS. The deployment's health check shows recent ones.
ALTER TABLE app_score_state ADD COLUMN IF NOT EXISTS checks_given_up INT NOT NULL DEFAULT 0;
ALTER TABLE app_score_state ADD COLUMN IF NOT EXISTS last_given_up_at TIMESTAMPTZ;
