-- 2.0.0 package M2 (docs/ios-native-2.0.md sections 3.2 and 4): stress rows the phone computes itself and uploads
-- (kind `stress`, nativesync/derived_kinds/stress.py). A row stored from an upload (only while the kind is primary for that
-- user) says so: origin 'app' + the bundle that computed it; the server's own rows keep origin NULL.

ALTER TABLE stress_daily ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE stress_daily ADD COLUMN IF NOT EXISTS bundle_version TEXT;

-- The `records` pull stream carries the rows back (record kind `stress`, whole-day replace), so a reinstalled phone has them.
SELECT sync_attach('stress_daily', 'records', 'stress', 'date');
