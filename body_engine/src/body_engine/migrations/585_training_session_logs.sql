-- Training day v2 (FB-5, rollout flag training_day_v2): the sets a user fills in on the training page, one row per user and day.
-- `exercises` = [{slug, name?, sets: [{reps?, weight_kg?, duration_s?, rir?, done}]}] in the order shown. Submitting writes the
-- sets marked done to SparkyFitness as one workout (training/session_log.py, the same path as the AI's log_workout);
-- sparky_entry_id is that workout, so a resubmit replaces it. Editing after a submit sets status back to draft.
CREATE TABLE IF NOT EXISTS training_session_logs (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,                        -- the user's local date of the workout
  exercises JSONB NOT NULL DEFAULT '[]',
  notes TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted')),
  sparky_entry_id TEXT,
  submitted_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date)
);
GRANT SELECT ON training_session_logs TO grafana_ro;
