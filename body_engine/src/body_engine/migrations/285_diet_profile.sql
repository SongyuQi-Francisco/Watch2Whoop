-- 「我的饮食档案」(issue #32): the diet profile is part of the AI's profile card (assistant_profile), not a parallel store.
-- Its sections are profile card items with keys diet_* whose structured content lives in the new `data` column; `value` keeps a
-- short text rendering so get_memory / get_briefing and the 「我」 page keep working. A text edit of an item (update_profile_card,
-- PUT /memory/profile/{key}) clears `data`, so the text is then the truth. See assistant/diet_profile.py.
ALTER TABLE assistant_profile ADD COLUMN data JSONB;

-- Version log of the diet profile: every change of targets / plan gets a dated entry (what changed, why).
CREATE TABLE diet_profile_versions (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  version TEXT NOT NULL CHECK (version ~ '^V[0-9]{1,3}\.[0-9]{1,3}$'),
  changed_on DATE NOT NULL,              -- user's local date of the change
  changes TEXT NOT NULL CHECK (length(btrim(changes)) > 0),
  reason TEXT,
  sections TEXT[] NOT NULL DEFAULT '{}', -- which sections this change rewrote
  source TEXT NOT NULL CHECK (source IN ('assistant', 'user', 'seed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, version)
);
CREATE INDEX diet_profile_versions_user_idx ON diet_profile_versions (user_id, created_at DESC);
