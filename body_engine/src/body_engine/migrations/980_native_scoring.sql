-- #340 N2b (docs/ios-native-app.md section 9.1): the iOS app scores app-mode users on the phone. Beat-to-beat readings are
-- a scoring input (RMSSD, scores/hrv_kind.py), so a change to them is a change of the raw stream on its local day: the
-- app pulls it (body "beats", nativesync/bodies.raw) and the server's dirty-day fallback (store.score_dirty_days) sees it.
SELECT sync_attach('heartbeat_series', 'raw', '',
                   '(start_at AT TIME ZONE (SELECT tzu.timezone FROM users tzu WHERE tzu.id = user_id))');
