-- 2.0.0 package M1 (docs/ios-native-2.0.md sections 3.2 and 4): daily values and sleep the phone derives itself and
-- uploads (kinds `daily` and `sleep`, nativesync/derived_kinds/). Rows stored from an upload (only while the kind is
-- primary for that user) say so: origin 'app' + the bundle that computed them; rows the server derives keep origin NULL.

ALTER TABLE daily_metric_sources ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE daily_metric_sources ADD COLUMN IF NOT EXISTS bundle_version TEXT;
ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bundle_version TEXT;
ALTER TABLE sleep_sessions ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE sleep_sessions ADD COLUMN IF NOT EXISTS bundle_version TEXT;
-- the stage segments of an uploaded session ([[start, end, stage], ...], UTC seconds); NULL for the server's own rows
ALTER TABLE sleep_sessions ADD COLUMN IF NOT EXISTS segments JSONB;

-- The `daily` pull stream carries every source's value of a day (`sources`, derived_kinds/daily.extend_pull), so the
-- phone can choose the day's value itself: a change of any source's row is a change of that day.
SELECT sync_attach('daily_metric_sources', 'daily', '', 'date');
