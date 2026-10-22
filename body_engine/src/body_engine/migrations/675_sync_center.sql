-- Sync center (flag sync_center) and the HealthKit types the app reads itself (rawdata/hk_types.py).
--
-- 1. hk_samples: raw HealthKit quantity samples the Watch2Whoop app uploads (POST /v1/ingest/healthkit `quantities[]`,
--    rawdata/quantities.py) for the types the Open Wearables SDK cannot read (stand time, exercise minutes, daylight,
--    physical effort, sleeping wrist temperature, audio exposure, walking heart rate, stair speed, walking steadiness).
--    Deduplicated by the HealthKit UUID; daily values are made from them per (type, local day) and written to
--    daily_metric_sources with source 'app' (importers/common.py resolve_daily picks among the sources).
CREATE TABLE IF NOT EXISTS hk_samples (
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  hk_uuid     UUID NOT NULL,
  type        TEXT NOT NULL,                 -- HealthKit identifier, a key of rawdata/hk_types.BY_HK
  start_at    TIMESTAMPTZ NOT NULL,
  end_at      TIMESTAMPTZ NOT NULL,
  local_date  DATE NOT NULL,                 -- day of start_at in the user's time zone (the export import's rule)
  value       DOUBLE PRECISION NOT NULL,     -- in the registry's unit
  source      TEXT NOT NULL DEFAULT '',      -- HealthKit source name (device / app)
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, hk_uuid)
);
CREATE INDEX IF NOT EXISTS hk_samples_day_idx ON hk_samples (user_id, type, local_date);
CREATE INDEX IF NOT EXISTS hk_samples_time_idx ON hk_samples (user_id, type, start_at);
COMMENT ON TABLE hk_samples IS 'Raw HealthKit quantity samples uploaded by the app for types Open Wearables does not carry';

-- 2. Score recompute from an older day: the app's uploads (and a forced re-sync) can change days before the usual window
--    of sync-refresh-run (the last 3 days). scores_from = the earliest day to recompute (NULL = the usual window);
--    scores_queued_at = when that was last asked, so a recompute that started earlier does not swallow a newer ask.
ALTER TABLE sync_refresh ADD COLUMN IF NOT EXISTS scores_from DATE;
ALTER TABLE sync_refresh ADD COLUMN IF NOT EXISTS scores_queued_at TIMESTAMPTZ;

-- 3. Forced re-sync from the sync center (sync/resync.py): one row per user. The phone re-reads Apple Health from
--    range_from (status 'phone', it reports its progress), then the server imports that range again and recomputes the
--    scores from it ('queued' -> 'importing' -> 'scoring' -> 'done'), within sync-refresh-run's time budget.
CREATE TABLE IF NOT EXISTS sync_resync (
  user_id        INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  status         TEXT NOT NULL DEFAULT 'idle'
                 CHECK (status IN ('idle', 'phone', 'queued', 'importing', 'scoring', 'done', 'error', 'cancelled')),
  days           INT CHECK (days IS NULL OR days BETWEEN 1 AND 3650),   -- NULL = all history
  range_from     DATE,                      -- NULL = all history
  requested_at   TIMESTAMPTZ,
  phone_percent  SMALLINT NOT NULL DEFAULT 0 CHECK (phone_percent BETWEEN 0 AND 100),
  phone_counts   JSONB NOT NULL DEFAULT '{}',
  phone_done_at  TIMESTAMPTZ,
  import_from    DATE,
  import_until   DATE,
  import_cursor  DATE,
  finished_at    TIMESTAMPTZ,
  error          TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE sync_resync IS 'Forced re-sync of a date range from the app sync center: phone re-upload, server re-import and re-score';

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        GRANT SELECT ON hk_samples, sync_resync TO grafana_ro;
    END IF;
END $$;
