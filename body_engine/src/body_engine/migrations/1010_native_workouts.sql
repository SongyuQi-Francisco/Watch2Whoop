-- 2.0.0 package M1c (docs/ios-native-2.0.md sections 3.2 and 4): workouts the phone computes itself (TRIMP, five-zone
-- minutes, heart-rate recovery, duplicates) and uploads (kind `workouts`, nativesync/derived_kinds/workouts.py). Columns
-- the upload wrote (only while the kind is primary for that user) say so: computed_by 'app' + the bundle that computed
-- them; rows the server enriches itself keep NULL. (`workouts.origin` is taken: it is the workout's source app.)

ALTER TABLE workouts ADD COLUMN IF NOT EXISTS computed_by TEXT;
ALTER TABLE workouts ADD COLUMN IF NOT EXISTS bundle_version TEXT;
ALTER TABLE workout_hrr ADD COLUMN IF NOT EXISTS computed_by TEXT;
ALTER TABLE workout_hrr ADD COLUMN IF NOT EXISTS bundle_version TEXT;

-- The `records` pull stream's `workout` bodies carry each workout's heart-rate recovery (derived_kinds/workouts.extend_pull):
-- a change of workout_hrr is a change of that day's workouts.
SELECT sync_attach('workout_hrr', 'records', 'workout', 'local_date');
