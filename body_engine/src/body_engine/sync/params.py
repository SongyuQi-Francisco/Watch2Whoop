"""Data status (「数据状态」, issue #163, data_status.py): which health data types reached the server, and when a type counts
as "stale". Change here, not in the code.

These are data-freshness heuristics for a person who wears an Apple Watch every day (no clinical source; the cadences
are Apple's documented sampling behaviour):
- Heart rate, steps and active energy are recorded all day by the watch and iPhone: more than 2 days without any means
  the data is not being read (or the watch was not worn).
- HRV (SDNN, a few background readings a day / during sleep), resting heart rate (one value a day), sleep (one night),
  blood oxygen and respiratory rate (background readings, mostly during sleep), sleeping wrist temperature (each night
  with sleep tracking) and the beat-to-beat series behind each HRV reading: 3 days allows a night without the watch.
- Water: only if the user logs drinks in Health (or an app that writes there); 7 days.
- Workouts: rest weeks are normal; 14 days.
- Weight needs a smart scale or a manual entry; VO₂ max is estimated only after outdoor walks / runs / hikes with the
  watch: 30 days.
- ECG is recorded only when the user opens the ECG app: never "stale", only received or never received.

STALE_AFTER_DAYS: days since the latest data before the type counts as stale (None = never stale).
WINDOW_DAYS: the "last N days" of the counts (today included).
SOURCES_DAYS: how far back the list of sources looks.

Forced re-sync from the app's sync center (sync/resync.py, flag sync_center): operational limits, not health rules. One
job per user at a time; the minimum gap between accepted requests keeps a tap-happy user (or a looping app) from re-importing
and re-scoring all day; all history re-reads years of data on the phone and the server, so at most every 6 hours.
"""
from datetime import timedelta

WINDOW_DAYS = 7
SPARKY_TIMEOUT_S = 8   # the strength-training row (#210) asks SparkyFitness; a slow server must not hold the page for its 60 s default
SOURCES_DAYS = 30

# key -> days; the order is the order on the page and in the app
STALE_AFTER_DAYS: dict[str, int | None] = {
    "heart_rate": 2,
    "hrv": 3,
    "resting_heart_rate": 3,
    "sleep": 3,
    "steps": 2,
    "active_energy": 2,
    "workouts": 14,
    "weight": 30,
    "blood_oxygen": 3,
    "respiratory_rate": 3,
    "wrist_temperature": 3,
    "vo2max": 30,
    "heartbeats": 3,
    "ecg": None,
    "water": 7,
}

# The types the app reads itself (rawdata/hk_types.py), listed by the sync center (flag sync_center) after the ones above.
# Same reasoning: the watch records stand time, walking heart rate, physical effort and environmental sound every day it is
# worn (2 days); exercise minutes need brisk movement and daylight time outdoors, so a quiet day can have none (3); headphone
# sound only with headphones (7); stair speed only after climbing stairs and walking steadiness is estimated about once a
# week (14). Wrist temperature is in the list above.
EXTRA_STALE_AFTER_DAYS: dict[str, int | None] = {
    "stand_time": 2,
    "exercise_time": 3,
    "daylight": 3,
    "physical_effort": 2,
    "walking_heart_rate": 2,
    "environmental_audio": 2,
    "headphone_audio": 7,
    "stair_speed": 14,
    "walking_steadiness": 14,
}

RESYNC_MAX_DAYS = 3650                          # custom range: 1–3650 days (null = all history)
RESYNC_MIN_INTERVAL = timedelta(minutes=10)     # between accepted requests of one user
RESYNC_ALL_MIN_INTERVAL = timedelta(hours=6)    # before an "all history" request
RESYNC_PHONE_STALE = timedelta(hours=24)        # a phone phase without a report for this long is no longer running
RESYNC_WINDOW_DAYS = 30                         # OW import window of the server part (same as importers/ow.WINDOW)
RESYNC_TIME_BUDGET = 240                        # seconds per sync-refresh-run step (the next run continues)

# Scores within minutes of new watch data (sync/ow_watch.py, docs/hourly-batch.md "New data within minutes"): operational
# limits, not health rules. The API process asks Open Wearables every OW_WATCH_POLL_S which users uploaded (one request).
# An upload round of the iOS SDK is several batches a few seconds apart (OW's worker log on the production server,
# 2026-10-25): a user is queued once nothing new arrived for OW_WATCH_QUIET, then at most once per OW_WATCH_MIN_GAP (a
# phone that uploads all the time must not keep the server recomputing). With the per-minute job that runs the queue and
# a recompute of about 20 s this keeps "upload finished -> new scores on the Today page" under 3 minutes: 45 + 30 + 60 + 25 s.
OW_WATCH_POLL_S = 30
OW_WATCH_QUIET = timedelta(seconds=45)
OW_WATCH_MIN_GAP = timedelta(minutes=3)
OW_WATCH_LOOKBACK = timedelta(hours=6)    # older uploads are left to the hourly job
OW_WATCH_MAX_PAGES = 5                     # GET /users pages of 100 users, newest upload first

# 「手机同步已断开」 (sync/broken.py): a phone whose Open Wearables uploads stopped (seen in production: its SDK credentials
# became invalid and every upload got 401) while its owner still opens the app. Operational heuristics, not health rules:
# an iPhone records steps and heart rate every day and the app syncs on every open, so 36 h without anything new is not a
# quiet day; it is longer than a night with the phone off or a day without the watch. Older than 30 days: the phone sync was
# probably given up on purpose (another phone, Shortcut only), so the page stops asking. The server must have reached OW
# for the user within 6 h (the hourly import), otherwise OW or our job is down and it is not the phone. "Uses the app":
# a Today page session or an upload of the app's own sync within 48 h (the Today page asking counts as a visit).
SYNC_BROKEN_AFTER = timedelta(hours=36)
SYNC_BROKEN_GIVE_UP = timedelta(days=30)
SYNC_BROKEN_SERVER_OK = timedelta(hours=6)
SYNC_BROKEN_APP_SEEN = timedelta(hours=48)
