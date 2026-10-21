-- The Watch2Whoop app reads beat-to-beat HRV series and ECG recordings from HealthKit itself and uploads them straight to
-- Body Engine (POST /v1/ingest/healthkit, rawdata/ingest.py); they land in heartbeat_series / ecg_recordings (migration 150,
-- origin 'app'). Samples are deduplicated by their HealthKit UUID; one row per upload batch for auditing and so a re-sent
-- batch (same batch_id) is answered from here without touching the data again.

CREATE INDEX IF NOT EXISTS heartbeat_series_hk_uuid_idx ON heartbeat_series (user_id, hk_uuid) WHERE hk_uuid IS NOT NULL;
CREATE INDEX IF NOT EXISTS ecg_recordings_hk_uuid_idx ON ecg_recordings (user_id, hk_uuid) WHERE hk_uuid IS NOT NULL;

CREATE TABLE IF NOT EXISTS rawdata_uploads (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  batch_id UUID NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  device TEXT,
  app_version TEXT,
  counts JSONB NOT NULL DEFAULT '{}',   -- {heartbeats, ecgs, deleted, skipped}
  bytes INT,
  PRIMARY KEY (user_id, batch_id)
);
CREATE INDEX IF NOT EXISTS rawdata_uploads_user_time_idx ON rawdata_uploads (user_id, received_at DESC);
