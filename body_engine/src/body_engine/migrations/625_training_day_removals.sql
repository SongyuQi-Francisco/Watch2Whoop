-- Planned exercises the user removed for one date only (issue #172): the training page's 「只删这一天」.
-- training/plan_remove.py writes / deletes the rows (delete = undo); plans.get_week_plan leaves the exercise out of that
-- date's plan (template and day-added ones alike) and lists it under the day's `removed`, so the AI's next plan respects it.
-- Independent of training_day_overrides (whole sessions) and training_day_extras (added exercises).
CREATE TABLE IF NOT EXISTS training_day_removals (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  slug TEXT NOT NULL,
  name_zh TEXT,
  name_en TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date, slug)
);

GRANT SELECT ON training_day_removals TO grafana_ro;
