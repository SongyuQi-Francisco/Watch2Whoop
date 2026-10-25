-- Scores within minutes of new watch data (docs/hourly-batch.md "New data within minutes").
--
-- 1. ow_watch (sync/ow_watch.py): the API process asks Open Wearables every 30 s which users uploaded lately
--    (GET /users sorted by last_synced_at, one request). A user whose uploads went quiet gets an import + score recompute
--    queued in sync_refresh, which the per-minute job already runs (sync-refresh-run). One row per user.
CREATE TABLE IF NOT EXISTS ow_watch (
  user_id        INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  ow_synced_at   TIMESTAMPTZ,              -- the newest upload time OW reported for this user
  queued_through TIMESTAMPTZ,              -- the ow_synced_at the last queued import covers
  queued_at      TIMESTAMPTZ,              -- when that import was queued
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE ow_watch IS 'Per user: latest Open Wearables upload seen by the API poller and the last import it queued';

-- sync_refresh: the queued recompute first imports from OW (the poller asked for it); NULL = scores only
ALTER TABLE sync_refresh ADD COLUMN IF NOT EXISTS import_queued_at TIMESTAMPTZ;

-- 2. ow_sample_cache (importers/ow_cache.py): a local copy of the OW raw samples scoring reads (heart rate, HRV SDNN,
--    respiratory rate, SpO2, wrist temperature, steps), so a recompute does not page more than a year of samples over
--    HTTP every time. Bucketed per UTC day and source; arrays hold epoch microseconds and values in ascending time.
--    Rebuilt from OW at any time (losing it only makes the next recompute slow again).
CREATE TABLE IF NOT EXISTS ow_sample_cache (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type    TEXT NOT NULL,                   -- OW series code
  day     DATE NOT NULL,                   -- UTC date of the samples
  src     TEXT NOT NULL,                   -- importers/ow.Sample.source: device type : device : source
  ts      BIGINT[] NOT NULL,               -- epoch microseconds, ascending
  val     DOUBLE PRECISION[] NOT NULL,     -- values as importers/ow.parse_sample gives them
  PRIMARY KEY (user_id, type, day, src)
);
-- the time range each type of the copy covers completely (one contiguous range per user and type)
CREATE TABLE IF NOT EXISTS ow_sample_cache_range (
  user_id      INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type         TEXT NOT NULL,
  ow_user_id   TEXT NOT NULL,              -- the OW user it was read from (a re-link invalidates the copy)
  version      INT NOT NULL,               -- ow_cache.CACHE_VERSION
  covered_from TIMESTAMPTZ NOT NULL,
  covered_to   TIMESTAMPTZ NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, type)
);
COMMENT ON TABLE ow_sample_cache IS 'Local copy of Open Wearables raw samples used by scoring (importers/ow_cache.py)';
