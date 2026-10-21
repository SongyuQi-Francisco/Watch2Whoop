-- Per-user diet constraints (issue #76). They used to be one person's health constraints hard-coded for everybody (red
-- meat per week, a dish per week, low purine, low saturated fat). Now each user has their own, set by their AI or carried
-- over from their own profile card; a user without rows has no diet constraint at all. Vocabulary and the generic,
-- literature-backed reference values: nutrition/constraint_params.py; logic: nutrition/constraints.py.
--
-- Versioned like the nutrition goals: a change closes the row in effect (effective_to = the change date) and inserts a new
-- one, so the rows of one (user, kind, target) are its history. In effect on day d: effective_from <= d < effective_to.
-- The one-time carry-over of existing data is Python (db.DATA_STEPS "450_diet_constraints_data"): it reads each user's
-- own profile card text from this database; no personal value is written here.
CREATE TABLE diet_constraints (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind ~ '^[a-z_]{2,40}$'),
  target TEXT NOT NULL DEFAULT '',          -- food_times_per_week: the food (its name); '' for the other kinds
  match TEXT[] NOT NULL DEFAULT '{}',       -- extra names / keywords that count as the target (aliases, English)
  value DOUBLE PRECISION,                   -- the limit (g / meals / times per week); NULL for on/off kinds
  basis TEXT,                               -- 出处 / why: doctor's advice, a lab result, the user's own choice
  effective_from DATE NOT NULL,
  effective_to DATE,                        -- exclusive; NULL = in effect
  source TEXT NOT NULL CHECK (source IN ('assistant', 'user', 'migration')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);
CREATE UNIQUE INDEX diet_constraints_current_idx ON diet_constraints (user_id, kind, target) WHERE effective_to IS NULL;
CREATE INDEX diet_constraints_user_idx ON diet_constraints (user_id, effective_from);
