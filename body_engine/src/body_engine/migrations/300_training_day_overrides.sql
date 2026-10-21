-- Day-level changes to the weekly training plan (training/day_overrides.py, docs/training-v2-plan.md "Day changes").
-- One row = "on this date do these sessions instead of the plan's weekday": sessions is a list of
--   {"dow": 1..7}  the content of that weekday in the active weekly plan (Body Engine training_plans or SparkyFitness), or
--   {"label": ""}  a free activity outside the plan (basketball, swimming);
-- an empty list is a rest day. source: user = the user asked for this date (reschedule_day / swap_days),
-- replan = moved by replan_week (may be moved again by the next re-plan). No row = the plan's own weekday.
CREATE TABLE training_day_overrides (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  sessions JSONB NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'replan')),
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date)
);

GRANT SELECT ON training_day_overrides TO grafana_ro;
