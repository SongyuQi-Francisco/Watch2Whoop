-- Health samples uploaded by the iPhone Shortcut (docs/shortcut-sync-spec.md). Until the OW iPhone app is back, daily data relies entirely on it.
-- Each upload is first stored as-is in health_data/archive/ (by the receiver); import-shortcut parses it and writes here: the same sample appears repeatedly across uploads,
-- so only one row is kept per (type, start, source), and a newer upload overwrites the older (an hourly-grouped sample keeps growing within that hour).
-- Scoring (raw heart rate / HRV / respiration / SpO2 / wrist temperature, sleep stages) reads from here; the shortcut-sourced daily_metrics and sleep_sessions are also recomputed from it.
CREATE TABLE shortcut_samples (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,                   -- type name in the Shortcut: Heart Rate, Sleep, Steps ...
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  value DOUBLE PRECISION,               -- numeric value; NULL for sleep (the stage is in value_text)
  value_text TEXT,                      -- sleep stage Core / Deep / REM / Awake / Asleep
  unit TEXT,
  source TEXT NOT NULL DEFAULT '',      -- name of the watch / phone; hourly-grouped samples and sleep have no source = ''
  agg TEXT NOT NULL CHECK (agg IN ('raw', 'hour')),   -- raw = raw sample / hour = Shortcut sum grouped by hour
  upload TEXT NOT NULL,                 -- the upload file that last wrote it (file name in archive, sorts by time)
  PRIMARY KEY (user_id, type, start_at, source),
  CHECK (end_at >= start_at)
);
CREATE INDEX shortcut_samples_user_type_start_idx ON shortcut_samples (user_id, type, start_at);

-- One row per upload file once imported (incremental import skips imported ones; --all re-imports everything).
CREATE TABLE shortcut_uploads (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  file TEXT NOT NULL,                   -- file name in archive
  format TEXT NOT NULL,                 -- rows-v0 (old, one row per sample) / cols-v1 (by column)
  bytes INT NOT NULL,
  rows INT NOT NULL,                    -- number of parsed samples
  counts JSONB NOT NULL DEFAULT '{}',   -- {type: count}
  notes JSONB NOT NULL DEFAULT '{}',    -- {code: Chinese description}: misaligned columns, heart rate summed by hour, etc.
  imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, file)
);

GRANT SELECT ON shortcut_samples, shortcut_uploads TO grafana_ro;
