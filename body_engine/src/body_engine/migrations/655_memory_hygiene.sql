-- Long-term memory hygiene, phase 1 (issue #113). Columns only; existing rows are backfilled here (confirmed_at, origin)
-- and by the data step 655_memory_hygiene_data (topics: a standard key's own, else inferred from the text; db.py).
--   topic        one of the profile card's standard keys (memory.STANDARD_KEYS, incl. communication); NULL = not known yet
--   confirmed_at last time the entry was written or confirmed (「还准确」 on the Today page); profile items show
--                the later of this and updated_at, so writers that do not set it (diet profile) still count
--   origin       user_said (the user's own words) / ai_inferred (the AI concluded it) / data (taken from records); NULL = unknown
--   status       active / archived; archived entries leave the AI's memory but can be restored (archived_reason, merged_into)
-- Nothing archives entries yet (phase 2 does, after the user confirms); deleting on the Today page is still a real delete.
ALTER TABLE assistant_observations
  ADD COLUMN topic TEXT CHECK (topic ~ '^[a-z][a-z0-9_]*$'),
  ADD COLUMN confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN origin TEXT CHECK (origin IN ('user_said', 'ai_inferred', 'data')),
  ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  ADD COLUMN archived_at TIMESTAMPTZ,
  ADD COLUMN archived_reason TEXT,
  ADD COLUMN merged_into BIGINT REFERENCES assistant_observations(id) ON DELETE SET NULL,
  ADD CONSTRAINT assistant_observations_archived CHECK (status = 'active' OR archived_at IS NOT NULL);

ALTER TABLE assistant_profile
  ADD COLUMN topic TEXT CHECK (topic ~ '^[a-z][a-z0-9_]*$'),
  ADD COLUMN confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN origin TEXT CHECK (origin IN ('user_said', 'ai_inferred', 'data')),
  ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  ADD COLUMN archived_at TIMESTAMPTZ,
  ADD COLUMN archived_reason TEXT,
  ADD COLUMN merged_into TEXT,          -- key of the profile card item it was merged into
  ADD CONSTRAINT assistant_profile_archived CHECK (status = 'active' OR archived_at IS NOT NULL);

-- Backfill. Last confirmed = last written. Origin only where the source says it: the user's own edits and the seed
-- (memory-init: the user's own records and documents) are user_said; what the AI wrote stays unknown (NULL), so phase 2
-- treats it as carefully as the user's own words rather than guessing it was only inferred.
UPDATE assistant_observations SET confirmed_at = created_at,
  origin = CASE WHEN source IN ('user', 'seed') THEN 'user_said' END;
UPDATE assistant_profile SET confirmed_at = updated_at,
  origin = CASE WHEN source IN ('user', 'seed') THEN 'user_said' END;

CREATE INDEX assistant_observations_topic_idx ON assistant_observations (user_id, topic) WHERE status = 'active';
