-- Workout records (watch / iOS Shortcut): Open Wearables workouts and the "workouts" uploaded by the Shortcut, one row per workout (training/importer.py).
-- Each source is stored as-is (unique key (user_id, source, external_id), re-imports do not duplicate); when the same workout exists in several sources
-- (copy imported from export.xml + live watch sync, OW + Shortcut), the lower-priority row's dup_of points to the kept one, and views only look at dup_of IS NULL.
-- Strength exercises and sets are not here: they live in SparkyFitness, and the API merges them by day (training/history.py). See docs/system-design.md §15.
CREATE TABLE workouts (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL CHECK (source ~ '^[a-z_]+$'),   -- ow / shortcut
  external_id TEXT NOT NULL,                            -- OW workout id; Shortcut = start time (UTC)|type
  origin TEXT,                                          -- original source: OW's source (apple_health_xml / watch name / Keep), the source in the Shortcut row
  is_xml BOOLEAN NOT NULL DEFAULT FALSE,                -- from OW's export.xml import (the live-synced copy takes priority)
  device TEXT,
  type TEXT NOT NULL,                                   -- OW canonical type: walking / strength_training / basketball ...
  name_zh TEXT NOT NULL,
  local_date DATE NOT NULL,                             -- date of the start time in the user's time zone (a workout crossing midnight counts for its start day)
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  duration_min DOUBLE PRECISION NOT NULL,
  active_kcal DOUBLE PRECISION,                         -- active energy (excluding basal metabolism)
  total_kcal DOUBLE PRECISION,                          -- total energy = active + basal (OW's calories_kcal)
  distance_km DOUBLE PRECISION,
  avg_hr DOUBLE PRECISION,
  max_hr DOUBLE PRECISION,
  trimp DOUBLE PRECISION,                               -- Edwards TRIMP, computed from raw heart rate during the workout (same zones as the scoring module)
  hr_zone_min DOUBLE PRECISION[],                       -- minutes in the five zones at 50/60/70/80/90% HRmax
  hr_samples INT,
  hr_max_used DOUBLE PRECISION,
  zone_offset TEXT,                                     -- time zone offset recorded by the device (reference only; dates always follow the user's time zone)
  dup_of BIGINT REFERENCES workouts(id) ON DELETE SET NULL,
  excluded TEXT,                                        -- reason for exclusion (e.g. implausible duration), NULL = normal
  flags JSONB NOT NULL DEFAULT '{}',                    -- {code: Chinese description}
  imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, source, external_id),
  CHECK (end_at >= start_at)
);
CREATE INDEX workouts_user_date_idx ON workouts (user_id, local_date);
CREATE INDEX workouts_user_start_idx ON workouts (user_id, start_at);

GRANT SELECT ON workouts TO grafana_ro;
