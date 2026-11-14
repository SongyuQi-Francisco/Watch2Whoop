-- UI 2.0.1 gaps (phase 4).
-- 1. When today's training started, so the app can show the elapsed time: created_at = the first save of the day's draft,
--    first_set_at = the first save with a set ticked done (the app may send the tick time it saw; training/session_log.py clamps
--    it to that day and to now). Rows from before this have neither (no default on existing rows: their time is unknown).
ALTER TABLE training_session_logs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE training_session_logs ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE training_session_logs ADD COLUMN IF NOT EXISTS first_set_at TIMESTAMPTZ;

-- 2. Starred exercises of the exercise library, per user. `slug` is the catalog slug, a custom exercise's slug or a
--    `custom:<id>` of the person's own log, as /training/exercises lists it. Private: only the person reads or writes them.
CREATE TABLE IF NOT EXISTS exercise_favorites (
  user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, slug)
);
