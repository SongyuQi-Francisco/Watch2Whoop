-- Hevy CSV import (issue #161, rollout flag hevy_import, training/hevy_import.py, docs/hevy-benchmark.md §6).
-- training_session_logs gets a `source`: 'page' = the training page's own log (#148), 'hevy' = sets imported from a Hevy
-- export. The key becomes (user_id, date, source), so an import never touches a day the user logged on the page. Imported
-- rows have status 'imported'; each exercise carries `workout` = the external_id of its workouts row (source 'hevy').
ALTER TABLE training_session_logs ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'page'
  CHECK (source IN ('page', 'hevy'));
ALTER TABLE training_session_logs DROP CONSTRAINT IF EXISTS training_session_logs_status_check;
ALTER TABLE training_session_logs ADD CONSTRAINT training_session_logs_status_check
  CHECK (status IN ('draft', 'submitted', 'imported'));
ALTER TABLE training_session_logs DROP CONSTRAINT IF EXISTS training_session_logs_pkey;
ALTER TABLE training_session_logs ADD PRIMARY KEY (user_id, date, source);

-- One row per uploaded file: the summary shown after the import (workouts, sets, unmatched exercise names, skipped rows).
CREATE TABLE IF NOT EXISTS hevy_imports (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  file_sha256 TEXT NOT NULL,
  result JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hevy_imports_user_idx ON hevy_imports (user_id, created_at DESC);

-- The user's own answer for an imported exercise name that did not match the catalog: name (as in the file) -> catalog slug.
CREATE TABLE IF NOT EXISTS imported_exercise_maps (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL CHECK (source ~ '^[a-z_]+$'),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, source, name)
);
