-- Long-term memory hygiene, phase 3 (issue #113): how many times an entry was confirmed.
--   confirm_count  times the entry was explicitly confirmed after it was written: 「还准确」 on the Today page, the same note
--                  stated again, a confirm in a memory review, or the nightly check finding it true in the data
--                  (memory_params.DATA_CONFIRM_GAP_DAYS apart at the soonest). The review offers a lasting note whose count reaches
--                  memory_params.PROMOTE_MIN_CONFIRMS for merging into its profile card item.
-- Columns only. Existing entries are backfilled: confirmed later than written (by more than a minute) counts as one confirmation.
ALTER TABLE assistant_observations ADD COLUMN confirm_count INTEGER NOT NULL DEFAULT 0 CHECK (confirm_count >= 0);
ALTER TABLE assistant_profile ADD COLUMN confirm_count INTEGER NOT NULL DEFAULT 0 CHECK (confirm_count >= 0);

UPDATE assistant_observations SET confirm_count = 1 WHERE confirmed_at > created_at + interval '1 minute';
UPDATE assistant_profile SET confirm_count = 1 WHERE confirmed_at > updated_at + interval '1 minute';
