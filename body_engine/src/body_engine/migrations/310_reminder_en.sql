-- English text of each reminder (issue #42): the rules write both languages at evaluation time; the Today page reads the _en
-- columns when it asks for English (X-Body-Lang: en). NULL (rows written before this migration) = fall back to title / message
-- until the next evaluate-rules run rewrites the row.
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS title_en TEXT;
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS message_en TEXT;
COMMENT ON COLUMN reminders.title_en IS 'English title (same facts as title); NULL = show title';
COMMENT ON COLUMN reminders.message_en IS 'English message (same facts as message); NULL = show message';
