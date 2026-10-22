-- Per-user scoring choices (issue #117). rmssd_enabled: this user may be scored with RMSSD from beat-to-beat series
-- instead of Apple's SDNN; the kind actually used also needs enough beat data (scores/hrv_kind.py, thresholds in
-- openbodyscore params RMSSD_*). No row = false = SDNN, so this migration changes no score. Set only with
-- `body-engine hrv-kind`; the change takes effect at the next full compute-scores (never mixed within one person).
CREATE TABLE IF NOT EXISTS score_settings (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  rmssd_enabled BOOLEAN NOT NULL DEFAULT false,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT
);
