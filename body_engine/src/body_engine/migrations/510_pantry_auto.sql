-- Pantry goes down when meals are logged, best-before reminders (issue #104, docs/grocery-plan.md §3).
-- Per-user switch: grocery_settings.pantry_auto (NULL = the default in grocery/params.PANTRY_AUTO_DEFAULT).
ALTER TABLE grocery_settings ADD COLUMN pantry_auto BOOLEAN;
-- grocery_settings rows used to be created only for connected stores; a row may now exist just for the switch.

-- Best before (nullable): filled by the AI when the user says "milk expires 10/25" or reads it off a receipt / label.
ALTER TABLE pantry_items ADD COLUMN best_before DATE;

-- What each logged food entry (SparkyFitness entry id; quick meals / recipes log one entry per item / dish) took from the pantry,
-- one row per pantry row touched, so that undoing / deleting the entry or rollback_write puts exactly that back.
-- snapshot keeps the pantry row (name, unit, product, best before) in case it was used up and removed.
CREATE TABLE pantry_deductions (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  entry_id TEXT NOT NULL,                      -- SparkyFitness food entry id
  entry_date DATE NOT NULL,
  food TEXT NOT NULL CHECK (char_length(food) BETWEEN 1 AND 200),    -- the logged food / ingredient name that matched
  pantry_name TEXT NOT NULL,
  qty NUMERIC NOT NULL CHECK (qty > 0),        -- taken, in unit (the pantry row's unit)
  unit TEXT NOT NULL CHECK (unit IN ('g', 'ml', 'pcs')),
  snapshot JSONB NOT NULL,                     -- {name, unit, product_id, best_before} of the pantry row before
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  restored_at TIMESTAMPTZ
);
CREATE INDEX pantry_deductions_entry_idx ON pantry_deductions (user_id, entry_id) WHERE restored_at IS NULL;
