-- 2.0.0 package M4 (docs/ios-native-2.0.md sections 3.2 and 4): body-age weeks the phone computes itself and uploads
-- (kind `healthspan`, nativesync/derived_kinds/healthspan.py). A row stored from an upload (only while the kind is primary
-- for that user) says so: origin 'app' + the bundle that computed it; the server's own rows keep origin NULL.

ALTER TABLE healthspan_weekly ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE healthspan_weekly ADD COLUMN IF NOT EXISTS bundle_version TEXT;

-- The `records` pull stream carries the rows back (record kind `healthspan`, whole-week replace keyed by week_start), so a
-- reinstalled phone has them. healthspan_store.write upserts (an unchanged week is no change in the log).
SELECT sync_attach('healthspan_weekly', 'records', 'healthspan', 'week_start');
