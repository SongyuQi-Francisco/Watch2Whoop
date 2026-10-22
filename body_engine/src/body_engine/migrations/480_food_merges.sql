-- Several food entries of one day merged into one (issue #93): the merged SparkyFitness entry replaces them; the originals
-- are kept here whole (as SparkyFitness returned them) so the merge can be undone (split) from the Today page or by the AI
-- (rollback_write, kind merge). Every row carries user_id; only the owner can read or undo it.
CREATE TABLE food_merges (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  entry_id TEXT NOT NULL,                 -- the merged SparkyFitness entry
  name TEXT NOT NULL,
  originals JSONB NOT NULL,               -- the replaced entries, as SparkyFitness returned them
  totals JSONB NOT NULL,
  channel TEXT NOT NULL,                  -- web / mcp / chat ...
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  undone_at TIMESTAMPTZ,
  restored_ids TEXT[]
);
CREATE INDEX food_merges_user_date_idx ON food_merges (user_id, date);

-- The AI write log gets the two new actions (kind merge: merge = rollback-able, split = the undo itself).
ALTER TABLE ai_write_log DROP CONSTRAINT ai_write_log_action_check;
ALTER TABLE ai_write_log ADD CONSTRAINT ai_write_log_action_check
  CHECK (action IN ('create', 'update', 'delete', 'rollback', 'other', 'merge', 'split'));
