-- Each user's own exercises (issue #189): exercises the shared catalog does not know, added by the user's AI (add_custom_exercise)
-- or on the Today page (动作库 → 我的动作). Matching (training/loads.Index, catalog search / detail) uses the shared catalog plus the
-- owner's rows; nobody else ever sees them. Muscle keys are the catalog's (training/catalog_data.MUSCLE_ZH), equipment keys
-- EQUIPMENT_ZH. Admins only see names and counts (body-engine exercises custom-list), never who added them. See training/custom.py.
CREATE TABLE IF NOT EXISTS custom_exercises (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name_zh TEXT NOT NULL CHECK (length(name_zh) BETWEEN 1 AND 80),
  name_en TEXT CHECK (name_en IS NULL OR length(name_en) <= 80),
  aliases TEXT[] NOT NULL DEFAULT '{}',
  primary_muscles TEXT[] NOT NULL CHECK (cardinality(primary_muscles) >= 1),
  secondary_muscles TEXT[] NOT NULL DEFAULT '{}',
  equipment TEXT[] NOT NULL DEFAULT '{}',
  source TEXT NOT NULL CHECK (source IN ('ai', 'manual')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS custom_exercises_user_idx ON custom_exercises (user_id);
