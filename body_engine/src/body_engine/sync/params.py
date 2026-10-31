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

# Why a type is not ok, and what the person can do (data_status.explain, the per-type `reason` / `action` / `alarm`).
# Operational heuristics, not health rules. The server cannot see the phone, so "not recorded" is inferred: when the
# phone's sync delivered within DELIVERY_FRESH (= SYNC_LAG_AFTER: the background sync runs several times an hour when iOS
# lets it, and on every open) and a type is still old, the upload ran and the phone had nothing newer of it (the
# engine's anchored queries send every new sample, docs/apple-sync-engine.md §3.1). When nothing was delivered for that
# long, the type is "not delivered" and the action comes from the phone's last report (sync_device_state).
# - ALARM_REASONS: the reasons that are a sync problem (a red / orange mark); "not recorded" (the watch was not worn, no
#   scale, no outdoor walk), an on-demand type (ECG), a type the person logs elsewhere and one the app cannot read are not.
# - GAP_CORE_KINDS (below): types a worn Apple Watch records every day; never anything of them
#   while other data arrives = most likely the Health switch is off (HealthKit never tells an app, issue #163).
# - OVERNIGHT_KINDS: recorded mostly or only while the watch is worn in bed with sleep tracking on (Apple's sampling,
#   STALE_AFTER_DAYS above): "not recorded" says "wear the watch to sleep" for them, "wear the watch" for the others.
# - RECORD_ACTION: the one thing that makes a type appear at all (needs a scale, an outdoor walk, the ECG app, a drink
#   logged in Health, a workout).
DELIVERY_FRESH = timedelta(hours=6)   # the same as SYNC_LAG_AFTER (below)
ALARM_REASONS = ("not_delivered", "no_permission")
OVERNIGHT_KINDS = ("hrv", "sleep", "blood_oxygen", "respiratory_rate", "wrist_temperature", "heartbeats")
RECORD_ACTION = {"weight": "use_scale", "vo2max": "outdoor_workout", "ecg": "record_ecg", "water": "log_in_health",
                 "workouts": "log_workout", "headphone_audio": "wear_headphones", "stair_speed": "wear_watch",
                 "walking_steadiness": "wear_watch"}

RESYNC_MAX_DAYS = 3650                          # custom range: 1–3650 days (null = all history)
RESYNC_MIN_INTERVAL = timedelta(minutes=10)     # between accepted requests of one user
RESYNC_ALL_MIN_INTERVAL = timedelta(hours=6)    # before an "all history" request
RESYNC_PHONE_STALE = timedelta(hours=24)        # a phone phase without a report for this long is no longer running
RESYNC_WINDOW_DAYS = 30                         # OW import window of the server part (same as importers/ow.WINDOW)
RESYNC_TIME_BUDGET = 240                        # seconds per sync-refresh-run step (the next run continues)
RESYNC_ENGINE_WAIT = timedelta(minutes=30)      # sync mode app: the server part waits this long after phone_done for the
                                                # app's open range re-sync (range_done) and the derive queue, then scores

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

# Sync overview (sync/overview.py: 「同步状态」 panel, the 「开启数据同步」 card, GET /v1/users/me/sync/overview). Operational
# heuristics, not health rules. The app syncs in the background several times an hour when iOS lets it, and on every open:
# nothing new for 6 h while the phone is in use means the background sync is not running (Background App Refresh off, Low
# Power Mode, the phone locked when iOS woke the app), before it counts as broken (SYNC_BROKEN_AFTER). A phone report
# (sync_device_state, app 1.0.8+) older than 24 h no longer describes the phone. A brand-new connection gets 2 h before a
# missing type counts (the first sync reads 90 days, newest first, sleep and HRV can come in a later batch).
# OVERVIEW_TYPES: the types the panel lists (data_status.KINDS keys); OVERVIEW_PARTIAL: the ones whose absence (while heart
# rate arrives) is reported as "not authorised" (workouts: rest weeks are normal; weight / VO₂max need other things).
SYNC_LAG_AFTER = DELIVERY_FRESH
SYNC_DEVICE_FRESH = timedelta(hours=24)
SYNC_FIRST_GRACE = timedelta(hours=2)
OVERVIEW_TYPES = ("heart_rate", "hrv", "resting_heart_rate", "sleep", "steps", "workouts")
OVERVIEW_PARTIAL = ("hrv", "resting_heart_rate", "sleep", "steps")

# History top-up (「补全历史数据」, sync/backfill.py): the app's first sync reads only the last 14 days (app 1.0.8, so the new
# data comes in fast); the rest of the Apple Health history is topped up later, on request. Operational heuristics for how much
# history the scores want, not health rules; the two numbers the scores need come from the score engine's own parameters.
# - BACKFILL_TARGET_DAYS: the history we ask for, counted as days with watch data (heart rate, HRV or sleep) inside the last
#   this-many days.
# - The baseline the recovery score compares with is the last BASELINE_DAYS days and needs MIN_BASELINE_NIGHTS nights of HRV in
#   it (openbodyscore/params.py): fewer HRV nights = no recovery score yet ("baseline"), fewer days than the window = a
#   baseline that works but is thin ("thin").
# - Reminders: the card opens this many days after the first sync (local dates), once each; after the last of them at most once
#   every BACKFILL_REMIND_EVERY_DAYS days. A reminder is a day: the card stays up that whole local day, then waits for the next
#   date. "Don't remind me" stops them for good; reaching the target stops them too.
# - BACKFILL_*_METRICS: the daily_metrics names counted as watch heart-rate and HRV data.
# - BACKFILL_RESYNC_OPEN: after a top-up started from the card, the app may use /v1/ingest/resync for this long even where the
#   sync_center flag is off (the card, not the flag, started that job).
# - BACKFILL_BASELINE_MIN_FILL: share of the baseline window that must have data before the baseline counts as steady.
# - BACKFILL_ERROR_SHOWN: a failed top-up stays on the card this long, with a retry button.
# - BACKFILL_PHONE_STALL: a top-up whose phone part has not reported anything after this long was never taken up by the app.
# - BACKFILL_EXHAUSTED_FOR: after a whole-history top-up finished and the target is still not reached, the reminders pause this
#   long (the phone may simply have no more; the server's import may also have run before Open Wearables finished processing).
from openbodyscore import params as _score  # noqa: E402

BACKFILL_TARGET_DAYS = 90
BASELINE_DAYS = _score.BASELINE_DAYS
MIN_BASELINE_NIGHTS = _score.MIN_BASELINE_NIGHTS
BACKFILL_REMIND_AFTER_DAYS = (1, 3, 7)
BACKFILL_REMIND_EVERY_DAYS = 7
BACKFILL_HEART_METRICS = ("heart_rate", "resting_heart_rate")
BACKFILL_HRV_METRICS = ("heart_rate_variability_sdnn", "heart_rate_variability_rmssd")
BACKFILL_RESYNC_OPEN = timedelta(hours=48)
BACKFILL_ERROR_SHOWN = timedelta(hours=24)
BACKFILL_BASELINE_MIN_FILL = 0.9
BACKFILL_PHONE_STALL = timedelta(minutes=15)
BACKFILL_EXHAUSTED_FOR = timedelta(days=30)

# Data gaps (sync/gaps.py, `body-engine data-gaps`, the doctor's 「数据断流」 row, FB-18): a type that used to arrive regularly
# and stopped. Operational heuristics for noticing a broken way in, not health rules. A gap = no data on the last GAP_DAYS full
# days (today not counted; a type whose own STALE_AFTER_DAYS is longer waits that long), for a type that had data on at least
# GAP_MIN_PRIOR_DAYS of the GAP_LOOKBACK_DAYS before its last day (about twice a week: sparse types such as a weekly walking
# steadiness estimate or an occasional weigh-in never count as a gap). Gaps longer than GAP_GIVE_UP_DAYS are no longer listed
# (the person stopped using the app or the type; the doctor's phone-sync row covers people who stopped syncing).
GAP_DAYS = 3
GAP_LOOKBACK_DAYS = 30
GAP_MIN_PRIOR_DAYS = 8
GAP_GIVE_UP_DAYS = 30
# Only these "core" kinds (the ones the scores stand on, read live by the Open Wearables SDK) make a doctor ⚠; the others
# (Apple-computed types such as stand time, exercise minutes, daylight, audio, walking heart rate, wrist temperature, which
# only the app's own sync reads from 1.0.7, and workouts / weight / VO2max / water ...) are listed as information only.
GAP_CORE_KINDS = ("heart_rate", "hrv", "resting_heart_rate", "sleep", "steps", "active_energy", "blood_oxygen",
                  "respiratory_rate")
# Sources that are a history import, not a live way in: they never make a type "regular" or end a gap.
GAP_HISTORY_SOURCES = frozenset({"apple_export"})
