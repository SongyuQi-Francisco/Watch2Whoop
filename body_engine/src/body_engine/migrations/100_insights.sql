-- Analytics matching WHOOP (insights/ module, docs/system-design.md §7 "body age and other analytics"). Adds tables only, all recomputable derived data.

-- Body age / pace of aging: one row per week (week_start = Monday; as_of = that week's Sunday, the current week = today).
CREATE TABLE healthspan_weekly (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_start DATE NOT NULL,
  as_of DATE NOT NULL,
  chrono_age DOUBLE PRECISION NOT NULL,    -- chronological age (with decimals)
  body_age DOUBLE PRECISION,               -- body age = chronological age + delta_years; empty when no metric can be counted
  delta_years DOUBLE PRECISION,
  pace_of_aging DOUBLE PRECISION,          -- how many years the body ages per calendar year (-1 to 3), slope over the last 8 weeks; empty when data is insufficient
  pace_raw DOUBLE PRECISION,               -- uncapped slope (to see whether it hit -1 / 3)
  contributors JSONB NOT NULL DEFAULT '[]', -- [{key, label, category, value, unit, hr, years, direction, tip, data_days}]
  insufficient JSONB NOT NULL DEFAULT '[]', -- [{key, label, reason}]
  algo_version TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, week_start)
);

-- Stress monitor: one row per day, hours = score of each hour.
CREATE TABLE stress_daily (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  score DOUBLE PRECISION,                  -- 0-3, average of the hours that have a score; empty when there is no raw heart rate
  level TEXT CHECK (level IN ('low', 'medium', 'high')),
  low_h INT NOT NULL DEFAULT 0, medium_h INT NOT NULL DEFAULT 0, high_h INT NOT NULL DEFAULT 0,
  peak_hour INT,
  hours JSONB NOT NULL DEFAULT '[]',       -- [{hour, score, level, hr, samples, hrv_z}]
  baseline JSONB,                          -- {calm_hr, spread, hrv_ln}
  flags JSONB NOT NULL DEFAULT '{}',
  algo_version TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date)
);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    GRANT SELECT ON healthspan_weekly, stress_daily TO grafana_ro;
  END IF;
END $$;
