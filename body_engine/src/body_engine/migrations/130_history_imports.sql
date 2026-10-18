-- Apple Health history import for new users (docs/tester-handbook.md "importing history data", body_engine/history/).
-- The app's first sync covers only 90 days; older history is uploaded by the user on the Today page: 「我」→「导入健康历史」 ("Me" -> "Import health history"), the export.zip exported from the iPhone Health app:
-- uploaded in chunks to health_data/imports/u<id>/ (per-user auth, size limit, disk check first), then a background job (body-engine history-import-run)
-- unzips + parses as a stream (nothing extracted to disk), writes daily_metrics / sleep_sessions / workouts (source = apple_export,
-- lower priority than OW and the Shortcut) and the raw samples scoring needs (export_samples), deletes the original file once parsed, then recomputes scores.

CREATE TABLE history_imports (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('uploading', 'queued', 'processing', 'done', 'failed', 'cancelled')),
  filename TEXT NOT NULL,
  size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
  received_bytes BIGINT NOT NULL DEFAULT 0,
  phase TEXT,                           -- while processing: parsing / saving / scores
  progress DOUBLE PRECISION,            -- 0-1 (parsing phase = XML bytes unzipped so far / XML size)
  message TEXT,                         -- Chinese message shown to the user (failure reason, completion summary)
  result JSONB NOT NULL DEFAULT '{}',   -- days, nights, workouts, samples, date range
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- refreshed on every progress update by the background job (heartbeat; no change for 30 minutes counts as crashed, re-queued)
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  CHECK (received_bytes BETWEEN 0 AND size_bytes)
);
-- Only one import in progress per person at a time
CREATE UNIQUE INDEX history_imports_one_active_idx ON history_imports (user_id)
  WHERE status IN ('uploading', 'queued', 'processing');
CREATE INDEX history_imports_queue_idx ON history_imports (status, id);

-- Raw samples from export.xml needed by scoring: heart rate, HRV, respiratory rate, SpO2, sleeping wrist temperature, and sleep stage segments.
-- Scoring (scores/inputs.py) treats them as the XML source (yielding to OW live-sync / Shortcut samples at the same moment).
-- Re-importing the same / a newer export: only one row per (kind, start, source).
CREATE TABLE export_samples (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('hr', 'hrv', 'resp', 'spo2', 'temp', 'sleep')),
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  value DOUBLE PRECISION,               -- NULL for sleep
  stage TEXT,                           -- sleep stage core / deep / rem / awake / unspecified
  source TEXT NOT NULL DEFAULT '',      -- sourceName in export.xml (watch / phone name)
  import_id BIGINT REFERENCES history_imports(id) ON DELETE SET NULL,
  PRIMARY KEY (user_id, kind, start_at, source),
  CHECK (end_at >= start_at)
);

GRANT SELECT ON history_imports, export_samples TO grafana_ro;
