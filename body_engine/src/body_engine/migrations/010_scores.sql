-- WHOOP-style scores (docs/system-design.md §7; scores/ module). daily_scores already exists from 001; this adds the columns the scoring engine needs.
ALTER TABLE daily_scores
  ADD COLUMN sleep_actual_min INT,            -- actual sleep = main sleep + naps after the previous main sleep (minutes)
  ADD COLUMN resp_rate_night DOUBLE PRECISION, -- median respiratory rate that night (breaths/min)
  ADD COLUMN hr_max DOUBLE PRECISION,         -- HRmax used for TRIMP that day
  ADD COLUMN details JSONB,                   -- sub-scores, z-scores, baselines, heart-rate zone minutes, etc.
  ADD COLUMN computed_at TIMESTAMPTZ NOT NULL DEFAULT now();

COMMENT ON COLUMN daily_scores.flags IS 'Reasons for missing / abnormal data: {code: Chinese description}, e.g. no_raw_hr, rhr_from_daily, illness_warning';
COMMENT ON COLUMN daily_scores.sleep_debt_min IS 'Sleep debt going into that night (minutes)';
