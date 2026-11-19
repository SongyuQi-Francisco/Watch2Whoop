-- Evidence that the phone's background sync actually runs (sync/overview.py, docs/ios-native-app.md "Background sync:
-- evidence, not the flag"; app 2.1.0+). `background_refresh` is what iOS says; these say what happened: when iOS last woke
-- the app in the background (a BGTask, a HealthKit observer wake, a silent push, a finished background transfer), which
-- kind of wake that was, how many wakes the last 7 days had, and the app's own verdict from both (its BackgroundCheck
-- rule: running / waiting / off / low_power / restricted / not_running). Timestamps, a count and codes only. The API maps
-- an unknown code to NULL, so a newer app never fails these checks.
ALTER TABLE sync_device_state
  ADD COLUMN IF NOT EXISTS last_background_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS background_kind TEXT
    CHECK (background_kind IN ('refresh', 'processing', 'observer', 'push', 'transfer')),
  ADD COLUMN IF NOT EXISTS background_wakes_7d INT CHECK (background_wakes_7d >= 0),
  ADD COLUMN IF NOT EXISTS background_state TEXT
    CHECK (background_state IN ('running', 'waiting', 'off', 'low_power', 'restricted', 'not_running'));
