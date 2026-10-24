-- Long-term memory hygiene, phase 2 (issue #113): per-user state of the periodic review.
--   last_reviewed_at  the last time the user's AI finished a review (apply_memory_changes, also with nothing to change); NULL = never
--   candidates        how many entries the last nightly check found worth a look (duplicates + stale ones)
--   near_full         the memory is close to what a conversation carries (memory_params.NEAR_FULL_RATIO)
--   checked_at        when `memory-review-run` last looked
-- The briefing reminds the AI once last_reviewed_at is older than memory_params.REVIEW_INTERVAL_DAYS and enough candidates
-- are waiting. Nothing here changes memory entries; archiving happens only through apply_memory_changes (after the user agreed).
CREATE TABLE memory_review_state (
  user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  last_reviewed_at TIMESTAMPTZ,
  candidates       INTEGER NOT NULL DEFAULT 0,
  near_full        BOOLEAN NOT NULL DEFAULT FALSE,
  checked_at       TIMESTAMPTZ
);
