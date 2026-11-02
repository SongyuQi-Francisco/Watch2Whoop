-- 2.0.0 package M6 (docs/ios-native-2.0.md sections 3.2 and 4): rule reminders the phone evaluates itself and uploads
-- (kind `reminders`, nativesync/derived_kinds/reminders.py). A row stored from an upload (only while the kind is primary for
-- that user) says so: origin 'app' + the bundle that computed it; the server's own rows keep origin NULL.

ALTER TABLE reminders ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS bundle_version TEXT;

-- The `records` pull stream carries the rows back (record kind `reminders`, whole-day replace): a status changed elsewhere
-- (the AI's dismiss_reminder, another device) reaches the phone, and a reinstalled phone has its reminders.
SELECT sync_attach('reminders', 'records', 'reminders', 'date');
