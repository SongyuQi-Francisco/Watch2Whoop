-- Exercises added to one date only ("add to plan" from the exercise detail, issue #154, rollout flag exercise_add_to_plan).
-- `exercises` = prescriptions in the same shape as a training_plans day exercise (slug, sets, rep_low, rep_high, rest_sec,
-- duration_sec, weight_kg, note); training/plan_add.py writes them, plans.get_week_plan shows them on that date as one extra
-- session. Independent of training_day_overrides (which moves whole sessions), so a re-plan never moves them.
CREATE TABLE IF NOT EXISTS training_day_extras (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  exercises JSONB NOT NULL DEFAULT '[]',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date)
);

GRANT SELECT ON training_day_extras TO grafana_ro;
