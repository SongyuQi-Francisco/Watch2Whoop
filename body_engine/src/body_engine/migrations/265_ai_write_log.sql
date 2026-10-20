-- Issue #30: the AI can edit / delete the user's own data; every AI write is logged with before / after values and can be rolled back.

-- One row per change an AI tool made (assistant/writelog.py). kind says how to roll it back:
--   food (SparkyFitness food entry), session (SparkyFitness workout), sets (sets of one SparkyFitness exercise entry),
--   weight (SparkyFitness check-in of one day), workout (watch workout override), lab (lab_results rows),
--   other (no automatic rollback: before is NULL, after holds the arguments).
CREATE TABLE ai_write_log (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,                      -- butler / mcp:<client> / chat:<provider> ...
  tool TEXT NOT NULL,
  kind TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete', 'rollback', 'other')),
  target TEXT,                                -- id of the changed record (entry id, workout id, date ...)
  before JSONB,                               -- NULL = did not exist
  after JSONB,                                -- NULL = deleted
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  rolled_back_at TIMESTAMPTZ,
  rolled_back_by BIGINT REFERENCES ai_write_log(id) ON DELETE SET NULL
);
CREATE INDEX ai_write_log_user_idx ON ai_write_log (user_id, created_at DESC);

-- User corrections of watch / Shortcut workouts. Keyed by (source, external_id) so a re-import (even a delete + insert)
-- applies them again and never overwrites them (training/overrides.py). hidden = wrong / duplicate workout, not counted.
CREATE TABLE workout_overrides (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  type TEXT,
  name_zh TEXT,
  hidden BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, source, external_id)
);

-- Type mapping rules for workouts the watch / Shortcut only knows as generic ("other"): same weekday and a start time
-- within params.RULE_HOUR_WINDOW of start_hour -> to_type. Created from update_workout(apply_to_similar=true).
CREATE TABLE workout_type_rules (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  weekday SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),   -- Monday = 0
  start_hour DOUBLE PRECISION NOT NULL,                         -- local hour, e.g. 18.5
  to_type TEXT NOT NULL,
  name_zh TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, weekday, start_hour)
);

GRANT SELECT ON workout_overrides, workout_type_rules TO grafana_ro;
