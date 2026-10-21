-- Training module v2 (docs/training-v2-plan.md):
-- 1. exercise_catalog: shared standard exercise dictionary (free-exercise-db Unlicense base + bilingual Chinese curated overlay).
--    Per-user overrides (banned / caution / active, personal cues, reason, app_exercise_id) stay in exercise_meta, joined by slug = exercise_ref.
-- 2. training_plans: per-user weekly split and workout plan (3 / 4 / 5-day splits or custom AI-built plans), mirrored to SparkyFitness when connected.

CREATE TABLE exercise_catalog (
  slug TEXT PRIMARY KEY,
  name_zh TEXT NOT NULL,
  name_en TEXT NOT NULL,
  aliases TEXT[] NOT NULL DEFAULT '{}',
  category TEXT NOT NULL DEFAULT 'strength'
    CHECK (category IN ('strength', 'stretching', 'cardio', 'mobility')),
  modality TEXT NOT NULL DEFAULT 'weight_reps'
    CHECK (modality IN ('weight_reps', 'bodyweight_reps', 'reps_only', 'duration')),
  force TEXT CHECK (force IS NULL OR force IN ('push', 'pull', 'static')),
  mechanic TEXT CHECK (mechanic IS NULL OR mechanic IN ('compound', 'isolation')),
  level TEXT NOT NULL DEFAULT 'intermediate'
    CHECK (level IN ('beginner', 'intermediate', 'expert')),
  movement_pattern TEXT,
  equipment TEXT[] NOT NULL DEFAULT '{}',
  primary_muscles TEXT[] NOT NULL DEFAULT '{}',
  secondary_muscles TEXT[] NOT NULL DEFAULT '{}',
  joint_stress TEXT[] NOT NULL DEFAULT '{}',
  per_side BOOLEAN NOT NULL DEFAULT FALSE,
  default_increment_kg DOUBLE PRECISION,
  cues_zh TEXT,
  instructions_zh TEXT[] NOT NULL DEFAULT '{}',
  images TEXT[] NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT 'free-exercise-db',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX exercise_catalog_pattern_idx ON exercise_catalog (movement_pattern);
CREATE INDEX exercise_catalog_muscles_idx ON exercise_catalog USING GIN (primary_muscles);

CREATE TABLE training_plans (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  split_type TEXT NOT NULL
    CHECK (split_type IN ('3_day_ppl', '3_day_full', '4_day_upper_lower', '5_day_ul_ppl', 'custom')),
  days_per_week INT NOT NULL DEFAULT 4 CHECK (days_per_week BETWEEN 1 AND 7),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  start_date DATE NOT NULL DEFAULT CURRENT_DATE,
  days JSONB NOT NULL DEFAULT '[]',
  notes TEXT,
  sparky_plan_id TEXT,
  sparky_preset_ids JSONB NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT 'assistant'
    CHECK (source IN ('seed', 'assistant', 'user')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX training_plans_user_active_idx ON training_plans (user_id, active, updated_at DESC);

GRANT SELECT ON exercise_catalog, training_plans TO grafana_ro;
