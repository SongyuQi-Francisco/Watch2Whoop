-- Raw Apple Watch data beyond OW / the Shortcut (docs/raw-data-plan.md, rawdata/, cardio/).
-- Size estimate (docs/raw-data-plan.md §7.2): heartbeat series ~1 MB per person per year, ECG ~25 KB per recording,
-- workout_hrr a few KB per year; 5 people × 3 years < 50 MB. All covered by the nightly pg_dump (whole database).

-- One row per Apple HRV reading: about one minute of beat times (HKHeartbeatSeriesSample; in export.xml the
-- <HeartRateVariabilityMetadataList> under each HeartRateVariabilitySDNN record). RMSSD / SDNN are computed on insert (rawdata/hrv.py).
-- The same reading from export.xml and from the app (RawSync, later) shares (start second, source): the app copy replaces the export one.
CREATE TABLE heartbeat_series (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  start_at TIMESTAMPTZ NOT NULL,        -- truncated to whole seconds (export.xml precision)
  end_at TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT '',      -- watch name
  origin TEXT NOT NULL CHECK (origin IN ('apple_export', 'app')),
  hk_uuid UUID,                         -- HealthKit UUID (app only)
  beats_ms INT[] NOT NULL,              -- ms since the first beat (export.xml: 10 ms resolution)
  gaps BOOLEAN[],                       -- precededByGap per beat (app only; export.xml has none)
  apple_sdnn DOUBLE PRECISION,          -- Apple's own SDNN value for this reading (ms)
  rmssd DOUBLE PRECISION,               -- ms, NULL when fewer than 20 clean successive differences
  sdnn DOUBLE PRECISION,
  mean_rr DOUBLE PRECISION,
  n_beats INT NOT NULL,
  n_valid INT NOT NULL,
  algo TEXT NOT NULL,                   -- rawdata/hrv.py ALGO
  import_id BIGINT REFERENCES history_imports(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, start_at, source),
  CHECK (end_at >= start_at)
);

-- ECG recordings made with the watch's ECG app (HKElectrocardiogram; electrocardiograms/*.csv in the export).
-- Not shared through ordinary shares: viewing someone else's needs the separate share scope 'ecg'. Not readable by Grafana.
CREATE TABLE ecg_recordings (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  start_at TIMESTAMPTZ NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('apple_export', 'app')),
  hk_uuid UUID,
  sample_hz DOUBLE PRECISION NOT NULL CHECK (sample_hz > 0),
  n_samples INT NOT NULL,
  voltage BYTEA NOT NULL,               -- zlib(int16 little-endian, µV) — rawdata/ecg.py pack()
  classification TEXT NOT NULL,         -- HKElectrocardiogram.Classification raw name (sinusRhythm, atrialFibrillation, …)
  classification_label TEXT,            -- label as Apple wrote it
  symptoms TEXT,
  apple_avg_hr DOUBLE PRECISION,        -- averageHeartRate (app only)
  device TEXT,
  software_version TEXT,
  derived JSONB NOT NULL DEFAULT '{}',  -- R peaks, RR, heart rate, RMSSD, quality (rawdata/ecg.py derive())
  import_id BIGINT REFERENCES history_imports(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, start_at)
);

-- Heart rate recovery after each workout (cardio/hrr.py): peak in the last 2 minutes − heart rate 60 s after the end.
-- Written by compute-scores from the same raw heart rate it already loads. valid = the peak was high enough to mean something.
CREATE TABLE workout_hrr (
  workout_id BIGINT PRIMARY KEY REFERENCES workouts(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  local_date DATE NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  peak_hr DOUBLE PRECISION,
  hr_60s DOUBLE PRECISION,
  hrr60 DOUBLE PRECISION,
  hr_max_used DOUBLE PRECISION,
  valid BOOLEAN NOT NULL,
  reason TEXT,                          -- why not valid (Chinese)
  apple_hrr60 DOUBLE PRECISION,         -- Apple's heartRateRecoveryOneMinute for this workout (app, later)
  algo TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workout_hrr_user_date_idx ON workout_hrr (user_id, local_date);

GRANT SELECT ON heartbeat_series, workout_hrr TO grafana_ro;
