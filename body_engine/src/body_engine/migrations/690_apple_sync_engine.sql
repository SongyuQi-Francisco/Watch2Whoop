-- Apple sync engine, step S1 (docs/apple-sync-engine.md §3.2, flag apple_sync_engine): what the app's own HealthKit sync
-- engine uploads to POST /v1/ingest/healthkit (rawdata/engine.py, registry rawdata/hk_types.ENGINE_TYPES). Raw data only:
-- the daily values, sleep and workouts are derived from it later (step S2, from hk_dirty_days).

-- 1. Where samples come from (one row per app / device of a user); the batch sends them once, samples refer by index.
CREATE TABLE IF NOT EXISTS hk_sources (
  id           BIGSERIAL PRIMARY KEY,
  user_id      INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         TEXT NOT NULL DEFAULT '',     -- HKSource name (the device or app as Health shows it)
  bundle       TEXT NOT NULL DEFAULT '',     -- HKSource bundle identifier
  product      TEXT NOT NULL DEFAULT '',     -- HKSourceRevision productType, e.g. a watch model code
  device_type  TEXT NOT NULL DEFAULT '',     -- watch / phone / other (the app infers it)
  device_name  TEXT NOT NULL DEFAULT '',
  device_model TEXT NOT NULL DEFAULT '',
  manufacturer TEXT NOT NULL DEFAULT '',
  os_version   TEXT NOT NULL DEFAULT '',     -- latest seen
  first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, name, bundle, product, device_name, device_model)
);

-- 2. Re-sync of a date range (§3.1.5): the app re-reads the range page by page (each request carries the range), then
--    sends range_done; stored samples of those types in the range that were received before the range started and not
--    sent again are removed (deletions HealthKit's anchors missed).
CREATE TABLE IF NOT EXISTS sync_ranges (
  id         BIGSERIAL PRIMARY KEY,
  user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  client_id  UUID NOT NULL,                  -- the app's range id
  types      TEXT[] NOT NULL,                -- HealthKit identifiers
  start_at   TIMESTAMPTZ NOT NULL,
  end_at     TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  done_at    TIMESTAMPTZ,
  removed    INT,
  UNIQUE (user_id, client_id),
  CHECK (end_at > start_at)
);

-- 3. hk_samples (migration 675) also holds the engine's samples: quantities, categories (value = HealthKit's integer,
--    e.g. the sleep stage) and workouts (value = minutes, value_text = activity name, meta = statistics, events, metadata).
ALTER TABLE hk_samples ADD COLUMN IF NOT EXISTS source_id BIGINT REFERENCES hk_sources(id) ON DELETE SET NULL;
ALTER TABLE hk_samples ADD COLUMN IF NOT EXISTS value_text TEXT;
ALTER TABLE hk_samples ADD COLUMN IF NOT EXISTS tz TEXT;                       -- HKMetadataKeyTimeZone of the sample
ALTER TABLE hk_samples ADD COLUMN IF NOT EXISTS meta JSONB;
ALTER TABLE hk_samples ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'app';   -- app / ow (history moved from OW)
ALTER TABLE hk_samples ADD COLUMN IF NOT EXISTS range_id BIGINT REFERENCES sync_ranges(id) ON DELETE SET NULL;
ALTER TABLE hk_samples ADD COLUMN IF NOT EXISTS batch_id UUID;

-- 4. Cumulative types as 15-minute statistics buckets (HKStatisticsCollectionQuery: Apple's own de-duplication of
--    iPhone + watch), UTC-aligned, only non-zero buckets; a request replaces the buckets of the span it sends.
CREATE TABLE IF NOT EXISTS hk_stats (
  user_id      INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type         TEXT NOT NULL,                -- HealthKit identifier
  bucket_start TIMESTAMPTZ NOT NULL,
  value        DOUBLE PRECISION NOT NULL,    -- stored unit of the registry
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, type, bucket_start)
);

-- 5. Days (user's local date) whose raw data changed, with the types; step S2 derives them and clears the row.
CREATE TABLE IF NOT EXISTS hk_dirty_days (
  user_id   INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day       DATE NOT NULL,
  types     TEXT[] NOT NULL DEFAULT '{}',
  queued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, day)
);

-- 6. One row per sync round of the app (foreground, observer wake-up, background refresh ...): counts only, no health
--    values, to measure how reliable background sync is.
CREATE TABLE IF NOT EXISTS sync_runs (
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  run_id      UUID NOT NULL,
  trigger     TEXT NOT NULL,
  result      TEXT NOT NULL,
  error       TEXT,
  started_at  TIMESTAMPTZ NOT NULL,
  duration_ms INT NOT NULL,
  batches     INT NOT NULL DEFAULT 0,
  bytes       BIGINT NOT NULL DEFAULT 0,
  samples     INT NOT NULL DEFAULT 0,
  app_version TEXT,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, run_id)
);
CREATE INDEX IF NOT EXISTS sync_runs_time_idx ON sync_runs (user_id, started_at DESC);

-- 7. Batch bookkeeping: the sync phase (recent / backfill / incremental / range; the rate limits allow backfill more)
--    and the phone's time zone.
ALTER TABLE rawdata_uploads ADD COLUMN IF NOT EXISTS phase TEXT;
ALTER TABLE rawdata_uploads ADD COLUMN IF NOT EXISTS tz TEXT;

COMMENT ON TABLE hk_sources IS 'HealthKit sources (app / device) of the samples the Watch2Whoop sync engine uploads';
COMMENT ON TABLE sync_ranges IS 'Date-range re-syncs of the app sync engine: removes stored samples the range no longer has';
COMMENT ON TABLE hk_stats IS '15-minute HealthKit statistics buckets of cumulative types uploaded by the app sync engine';
COMMENT ON TABLE hk_dirty_days IS 'Days whose HealthKit raw data changed and still have to be derived';
COMMENT ON TABLE sync_runs IS 'Sync rounds reported by the app (trigger, duration, counts; no health values)';

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        GRANT SELECT ON hk_stats, sync_runs TO grafana_ro;
    END IF;
END $$;
