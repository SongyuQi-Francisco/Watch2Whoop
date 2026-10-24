-- Own storage for food, food library, weight and workout logs (issue #252, docs/own-food-weight-storage.md), replacing
-- SparkyFitness. Phase 2: the tables, dual write behind the rollout flag own_food_store (ownstore/mirror.py) and
-- `body-engine sparky-migrate` (ownstore/migrate.py). Nothing reads these tables yet (phase 3).
-- Primary keys are text and keep the SparkyFitness ids (rows created here later get a uuid), so tables keyed by entry id
-- (food_entry_extras, pantry_deductions, food_merges, ai_write_log, training_session_logs.sparky_entry_id,
-- exercise_meta.app_exercise_id) stay valid. origin: 'sparky' = copied from SparkyFitness (migration or dual write; only these
-- rows are ever changed or deleted by a sync), 'own' = written here directly; synced_at = when a sync last changed the row.
-- Dates are the user's local dates; kg, kcal.

-- Each user's food library; nutrition is per serving. Keyed by (user_id, id): a public SparkyFitness food logged by two
-- users becomes one row for each of them.
CREATE TABLE foods (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  name TEXT NOT NULL,
  brand TEXT,
  serving_size NUMERIC,
  serving_unit TEXT,
  calories REAL, protein_g REAL, carbs_g REAL, fat_g REAL, saturated_fat_g REAL,
  sugar_g REAL, fiber_g REAL, sodium_mg REAL, alcohol_g REAL,
  nutrients JSONB NOT NULL DEFAULT '{}',          -- other nutrients SparkyFitness has, per serving, kept as they were
  provider_type TEXT,
  provider_external_id TEXT,                      -- ba:<content hash> for foods the AI created (log_food reuses them)
  is_custom BOOLEAN NOT NULL DEFAULT TRUE,
  notes TEXT,
  origin TEXT NOT NULL DEFAULT 'own' CHECK (origin IN ('sparky', 'own')),
  synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id)
);
CREATE INDEX foods_user_name_idx ON foods (user_id, name);
-- Not unique yet: SparkyFitness may hold duplicates; phase 3 makes these unique once the migrated data is checked.
CREATE INDEX foods_provider_idx ON foods (user_id, provider_external_id) WHERE provider_external_id IS NOT NULL;

-- One logged food; nutrition is this entry's total (SparkyFitness stores per serving × quantity).
-- added_sugar_g / sugary_drink used to live only in food_entry_extras (no SparkyFitness field).
CREATE TABLE food_entries (
  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  time TIME,
  meal TEXT NOT NULL,                             -- breakfast / lunch / dinner / snacks (assistant/sparky_write.MEALS)
  food_id TEXT,
  food_name TEXT NOT NULL,
  quantity NUMERIC,
  unit TEXT,
  serving_size NUMERIC,
  calories REAL, protein_g REAL, carbs_g REAL, fat_g REAL, saturated_fat_g REAL,
  sugar_g REAL, added_sugar_g REAL, fiber_g REAL, sodium_mg REAL, alcohol_g REAL,
  sugary_drink BOOLEAN,
  nutrients JSONB NOT NULL DEFAULT '{}',          -- other nutrients, this entry's total
  notes TEXT,
  source TEXT,                                    -- body_assistant / manual / ...
  source_id TEXT,                                 -- idempotency key of the writer
  origin TEXT NOT NULL DEFAULT 'own' CHECK (origin IN ('sparky', 'own')),
  synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (user_id, food_id) REFERENCES foods (user_id, id) ON DELETE SET NULL (food_id)
);
CREATE INDEX food_entries_user_date_idx ON food_entries (user_id, date);
CREATE INDEX food_entries_source_idx ON food_entries (user_id, source_id) WHERE source_id IS NOT NULL;

-- Weigh-in / body composition: one row per user and day, like a SparkyFitness check-in.
CREATE TABLE body_measurements (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  id TEXT NOT NULL DEFAULT gen_random_uuid()::text,       -- SparkyFitness check-in id; undo goes by date, not by it
  weight_kg REAL, body_fat_pct REAL, waist_cm REAL, neck_cm REAL, hips_cm REAL, height_cm REAL,
  muscle_mass_kg REAL, bone_mass_kg REAL, body_water_pct REAL, bmr_kcal REAL,
  origin TEXT NOT NULL DEFAULT 'own' CHECK (origin IN ('sparky', 'own')),
  synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date),
  UNIQUE (user_id, id)
);

-- The exercise library the logs reference (SparkyFitness "my exercises"); ids stay because exercise_meta.app_exercise_id,
-- the logs and the plan mirror use them. catalog_slug: the exercise_catalog entry it matches, when known. Keyed by
-- (user_id, id) like foods (public SparkyFitness exercises are shared between users there).
CREATE TABLE user_exercises (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  name TEXT NOT NULL,
  category TEXT,
  modality TEXT,
  primary_muscles TEXT[] NOT NULL DEFAULT '{}',
  description TEXT,
  catalog_slug TEXT,
  origin TEXT NOT NULL DEFAULT 'own' CHECK (origin IN ('sparky', 'own')),
  synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id)
);

-- Logged strength / cardio sessions (named apart from the watch workouts table). kind: preset = a session of several
-- exercises, single = one exercise logged on its own (its exercise row has the same id).
CREATE TABLE logged_workouts (
  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  name TEXT,
  notes TEXT,
  source TEXT,
  kind TEXT NOT NULL DEFAULT 'preset' CHECK (kind IN ('preset', 'single')),
  total_duration_min REAL,
  origin TEXT NOT NULL DEFAULT 'own' CHECK (origin IN ('sparky', 'own')),
  synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX logged_workouts_user_date_idx ON logged_workouts (user_id, date);

-- One exercise of a session; name / category / muscles are a snapshot so a later rename does not change history.
CREATE TABLE logged_workout_exercises (
  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  workout_id TEXT NOT NULL REFERENCES logged_workouts(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sort_order INT NOT NULL DEFAULT 0,
  exercise_id TEXT,                               -- user_exercises.id (no foreign key: history outlives the library)
  exercise_name TEXT,
  category TEXT,
  primary_muscles TEXT[] NOT NULL DEFAULT '{}',
  notes TEXT,
  superset_group TEXT,
  duration_min REAL,
  calories_burned REAL,
  avg_heart_rate REAL
);
CREATE INDEX logged_workout_exercises_workout_idx ON logged_workout_exercises (workout_id);

CREATE TABLE logged_workout_sets (
  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  exercise_entry_id TEXT NOT NULL REFERENCES logged_workout_exercises(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  set_number INT NOT NULL,
  set_type TEXT,
  reps REAL, weight_kg REAL, rir REAL, rpe REAL, duration_s REAL, distance_km REAL, rest_s REAL,
  is_pr BOOLEAN,
  notes TEXT
);
CREATE INDEX logged_workout_sets_exercise_idx ON logged_workout_sets (exercise_entry_id);

-- One row per real (not --dry-run) `body-engine sparky-migrate` run with its per-table check. Phase 3's read switch only
-- applies to users whose latest run is ok. Counts only, no health values.
CREATE TABLE own_store_migrations (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  since DATE NOT NULL,
  until DATE NOT NULL,
  ok BOOLEAN NOT NULL,
  report JSONB NOT NULL,
  actor TEXT,
  ran_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX own_store_migrations_user_idx ON own_store_migrations (user_id, ran_at DESC);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    GRANT SELECT ON foods, food_entries, body_measurements, user_exercises, logged_workouts, logged_workout_exercises,
                    logged_workout_sets TO grafana_ro;
  END IF;
END $$;
