-- Body butler: status card (Today page "Me" tab) and meal templates (docs/ai-assistant-plan.md §7).

-- Status items: an at-a-glance version of the body profile. The profile card (assistant_profile) is long text for the butler; here each item is one short line.
-- source: auto (computed by the backend from the database, assistant/status.py, refreshed hourly and on read) / assistant (written after the butler's judgment) / seed (initial version)
-- Not edited directly by the user; auto keys are written only by the backend.
CREATE TABLE assistant_status (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]*$'),
  label TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 6),
  value TEXT NOT NULL CHECK (char_length(value) BETWEEN 1 AND 12),
  unit TEXT CHECK (unit IS NULL OR char_length(unit) <= 8),
  tone TEXT NOT NULL CHECK (tone IN ('good', 'warn', 'bad', 'neutral')),
  trend TEXT CHECK (trend IN ('up', 'down', 'flat')),
  good_direction TEXT CHECK (good_direction IN ('up', 'down')),   -- which way the trend is good; NULL = neither good nor bad
  "group" TEXT NOT NULL,                  -- 身体 / 目标 / 健康 / 训练 / 作息 / 饮食 / 其他 (Chinese group names: body / goal / health / training / routine / diet / other)
  spark JSONB,                            -- recent values (old -> new, at most 30, may contain null), the front end draws a mini trend line
  note TEXT CHECK (note IS NULL OR char_length(note) <= 20),
  sort INT NOT NULL DEFAULT 100,
  source TEXT NOT NULL CHECK (source IN ('auto', 'assistant', 'seed')),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

-- Meal templates (e.g. a fixed breakfast). items: [{food, grams, kcal, protein_g, carbs_g, fat_g, sat_fat_g, purine_level, red_meat_g, note}]
-- needs_confirmation: the grams are estimates, awaiting user confirmation (the Today page shows 「克数待确认」, "grams to be confirmed").
CREATE TABLE meal_templates (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 30),
  meal TEXT NOT NULL CHECK (meal IN ('breakfast', 'lunch', 'dinner', 'snacks')),
  items JSONB NOT NULL,
  needs_confirmation BOOLEAN NOT NULL DEFAULT TRUE,
  note TEXT,
  sort INT NOT NULL DEFAULT 100,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, name)
);

-- Each "tap to log": the SparkyFitness entry ids written are recorded here, and undo deletes by them.
CREATE TABLE meal_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  template_id INT REFERENCES meal_templates(id) ON DELETE SET NULL,
  date DATE NOT NULL,
  meal TEXT NOT NULL,
  items JSONB NOT NULL,                   -- actual logged portions and nutrition (after any gram override)
  entries JSONB NOT NULL,                 -- [{id, food}]: SparkyFitness food entries
  channel TEXT NOT NULL,                  -- web / butler / api
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  undone_at TIMESTAMPTZ
);
CREATE INDEX meal_logs_user_date_idx ON meal_logs (user_id, date);
