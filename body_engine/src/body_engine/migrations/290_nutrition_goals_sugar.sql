-- Issue #31: editable nutrition goals with version history, and sugar tracking.

-- Nutrition goal versions (the source of truth for rules, status cards, reports and adherence; goals/nutrition.py).
-- A version applies from effective_from until the next version's effective_from; several versions with the same
-- effective_from: the latest id wins (the earlier ones stay as history). Rows are never updated or deleted.
-- training / rest: {calories, protein_g, carbs_g, fat_g, sat_fat_max_g, added_sugar_max_g, fiber_min_g}
-- (null = no target for that field). training_weekdays: ISO weekdays (1 = Monday ... 7 = Sunday) that use the training set.
CREATE TABLE nutrition_goal_versions (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  effective_from DATE NOT NULL,
  training JSONB NOT NULL,
  rest JSONB NOT NULL,
  training_weekdays SMALLINT[] NOT NULL DEFAULT '{}',
  name TEXT CHECK (name IS NULL OR char_length(name) <= 40),   -- optional label, e.g. a plan version
  reason TEXT NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 300),   -- why it changed
  set_by TEXT NOT NULL,                     -- channel: web / api / butler / mcp:<client> / chat / import / cli
  actor_user_id INT REFERENCES users(id) ON DELETE SET NULL,          -- who changed it (the owner; null for imports)
  import_key TEXT,                          -- idempotency key of imports (e.g. sparky-plan:<id>)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX nutrition_goal_versions_user_idx ON nutrition_goal_versions (user_id, effective_from DESC, id DESC);
CREATE UNIQUE INDEX nutrition_goal_versions_import_uq ON nutrition_goal_versions (user_id, import_key)
  WHERE import_key IS NOT NULL;

-- Per-entry nutrition SparkyFitness has no field for (added sugar, sugary-drink flag), plus sugar / fiber / sodium
-- parsed from old entry notes. Keyed by the SparkyFitness food entry id; SparkyFitness values win where both exist
-- (nutrition/extras.py). Null = unknown, never 0.
CREATE TABLE food_entry_extras (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  entry_id TEXT NOT NULL,
  date DATE NOT NULL,
  sugar_g REAL CHECK (sugar_g IS NULL OR sugar_g >= 0),
  added_sugar_g REAL CHECK (added_sugar_g IS NULL OR added_sugar_g >= 0),
  fiber_g REAL CHECK (fiber_g IS NULL OR fiber_g >= 0),
  sodium_mg REAL CHECK (sodium_mg IS NULL OR sodium_mg >= 0),
  sugary_drink BOOLEAN,
  source TEXT NOT NULL CHECK (source IN ('log_food', 'notes_parse')),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, entry_id)
);
CREATE INDEX food_entry_extras_user_date_idx ON food_entry_extras (user_id, date);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    GRANT SELECT ON nutrition_goal_versions, food_entry_extras TO grafana_ro;
  END IF;
END $$;
