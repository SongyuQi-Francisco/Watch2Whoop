"""Limits and thresholds of the native app sync (#340 N2, docs/ios-native-app.md sections 5 and 7.2). Change here.

None of these are health thresholds: they size pages, bound the work one request may cause and decide when the server
takes the scoring back from the app.
"""

FLAG = "native_sync"   # rollout flag: the app uses POST /v1/sync/pull, /push and PUT /scores/app only while this is on

# ---------------------------------------------------------------- pull
STREAMS = ("scores", "daily", "sleep", "records", "docs", "raw", "raw_history")
PULL_LIMIT_DEFAULT = 500          # changes per answer unless the app asks for fewer / more
PULL_LIMIT_MAX = 2000
RAW_PAGE_ROWS = 20000             # raw samples + statistics buckets per answer (at least one whole day is always sent)
RAW_DAYS_MAX = 7                  # explicit raw_days per request (re-scoring a few days)
# Full restore order: newest day first, so the last two weeks are on the phone after the first page or two
# ("先拉最近 14 天再往前补"). Raw data older than RAW_CELLULAR_DAYS is the app's to fetch on Wi-Fi only (the app now
# restores APP_RAW_DAYS with `raw` and everything older with `raw_history`, Wi-Fi + power unless the user allows cellular).
RAW_CELLULAR_DAYS = 90

# ---------------------------------------------------------------- the phone's own scoring (#340 N2b, nativesync/scoring.py)
# The phone keeps APP_RAW_DAYS of raw samples (heart rate, HRV, respiration, SpO2, wrist temperature, resting heart rate,
# sleep stages, beat-to-beat RMSSD) and recomputes changed days within them; a full restore pulls raw data that far back
# (pull's raw_since). Older history reaches its scoring as stored rows and per-day heart-rate histograms (seeded run).
APP_RAW_DAYS = 60
SEGMENT_DAYS = APP_RAW_DAYS + 2   # the sleep stream carries the server's stage segments of these recent nights
HIST_MAX_DAYS = 430               # GET /v1/sync/scoring?hist_from=&hist_to=: one request covers the HRmax window and a bit
MUSCLE_MAX_DAYS = 120             # ... &from=&to=: the muscle load of the days the phone recomputes

# ---------------------------------------------------------------- the phone's full history (stream raw_history, history.py)
# After the first APP_RAW_DAYS the phone downloads the rest of the history in the background (Wi-Fi + charging, or
# cellular if the user allows it), newest first, so it can recompute every day itself when the algorithm changes. A page
# is the merged scoring inputs of up to HISTORY_PAGE_DAYS days: one scores/inputs.load() over that range, i.e. about what
# the hourly compute-scores costs for two weeks of one user (tens to a few hundred ms). 3 years ≈ 80 pages.
HISTORY_STREAM = "raw_history"
HISTORY_PAGE_DAYS = 14
HISTORY_PAGE_ROWS = 60000         # samples + segments + readings per answer (an Apple Watch day is ~1,000-2,500)
# Server pressure: one user gets a page at most every HISTORY_MIN_INTERVAL_S (the phone waits Retry-After, so a whole
# download spreads over minutes, not seconds), and one API process computes at most HISTORY_CONCURRENCY pages at once
# (everyone else gets 429 + Retry-After HISTORY_BUSY_RETRY_S and the phone tries again later). Normal pulls are not
# throttled: a history page must be asked for alone.
HISTORY_MIN_INTERVAL_S = 3.0
HISTORY_CONCURRENCY = 2
HISTORY_BUSY_RETRY_S = 20

# ---------------------------------------------------------------- push
PUSH_MAX_MUTATIONS = 100
CLIENT_ID_MAX = 64
CLIENT_WRITE_KEEP_DAYS = 30       # a resend after this long is treated as new

# ---------------------------------------------------------------- app scores (PUT /v1/users/me/scores/app)
UPLOAD_MAX_DAYS = 400
# The app scores a user while it uploaded within APP_ACTIVE_DAYS with the server's own algorithm version and its last
# spot check passed. Then the hourly / nightly jobs skip the full recompute and only fill in days whose inputs changed
# more than DIRTY_GRACE_MINUTES ago without a newer app upload (the phone was not woken, or is gone).
APP_ACTIVE_DAYS = 3
DIRTY_GRACE_MINUTES = 120
DIRTY_LOOKBACK_DAYS = 30          # older changed days (history imports) are scored by their own job, not by the fallback
# Streams / record kinds whose change makes a day's score stale.
SCORE_INPUT_STREAMS = ("daily", "sleep", "raw")
SCORE_INPUT_KINDS = ("workout", "strength")

# Spot check: the server recomputes one uploaded day in Python and compares. Run for the first upload of a bundle version
# and at most every CHECK_EVERY_HOURS otherwise (a recompute loads the user's history: seconds, not milliseconds).
CHECK_EVERY_HOURS = 6
CHECK_WITHIN_DAYS = 14            # only recent days are checked (the ones that are shown)
# field -> largest difference that still counts as equal. The JS bundle matches Python to ~1e-13 before rounding
# (packages/openbodyscore-js/README.md); these only absorb the last rounding step of each stored value.
CHECK_TOLERANCE: dict[str, float] = {
    "sleep_score": 1, "recovery": 1, "sleep_need_min": 1, "sleep_debt_min": 1, "sleep_actual_min": 1,
    "sleep_performance": 0.01, "hrv_night_ln": 0.001, "rhr_night": 0.1, "resp_rate_night": 0.1,
    "trimp": 0.1, "strain": 0.1, "muscle_load": 0.1, "ctl": 0.1, "atl": 0.1, "tsb": 0.1, "hr_max": 1,
}

# ---------------------------------------------------------------- silent push (notify/silent.py)
SILENT_TICK_S = 20                # how often the API process looks for new changes
SILENT_MIN_INTERVAL_S = 60        # at most one silent push per user per minute (Apple throttles more than ~3/hour anyway)
SILENT_LOOKBACK_MINUTES = 60      # changes older than this are not pushed any more (the app pulls on open)

# ---------------------------------------------------------------- 2.0.0: every own-data computation on the phone
# (docs/ios-native-2.0.md sections 2.2, 3 and 5; compute.py, derived.py, derived_kinds/, ext_records.py)
# package (= one compute stage of the app, Sync/Compute/<Stage>Stage.swift) -> its rollout flag, the keys of its algorithms
# in OBS.ALGO_VERSIONS (algo/versions.py) and the kinds it uploads (PUT /v1/users/me/derived/app, one module each in
# derived_kinds/). A package is "implemented" on this server when every algorithm key has a Python constant and is in the
# served bundle, and every upload kind has its module; until then GET /v1/sync/compute reports it off whatever the flags.
COMPUTE_PACKAGES: dict[str, dict] = {
    "derive": {"flag": "native_derive", "algo": ("derive",), "kinds": ("daily", "sleep")},
    "workouts": {"flag": "native_workouts", "algo": ("workouts",), "kinds": ("workouts",)},
    "stress": {"flag": "native_stress", "algo": ("stress_cards",), "kinds": ("stress",)},
    "series": {"flag": "native_series", "algo": ("series",), "kinds": ()},
    "coach": {"flag": "native_coach", "algo": ("coach",), "kinds": ("healthspan",)},
    "reports": {"flag": "native_reports", "algo": ("reports",), "kinds": ()},
    "rules": {"flag": "native_rules", "algo": ("rules", "explain"), "kinds": ("reminders",)},
}
COMPUTE_PRIMARY_FLAG = "native_compute_primary"
COMPUTE_STATES = ("off", "shadow", "primary")
# The first app version that implements each package (header X-App-Version; "2.0.0+57" = version 2.0.0 from build 57 on,
# header X-App-Build, for internal builds). A package is never enabled for an older client or one that sends no version.
MIN_APP_VERSION_BY_KIND: dict[str, str] = {p: "2.0.0" for p in COMPUTE_PACKAGES}
# primary needs the kind's last spot check passed and at least this many days uploaded (section 5)
PRIMARY_MIN_DAYS = 3
# PUT /v1/users/me/derived/app: at most this many days per request (newest batch first) and rows per day
DERIVED_MAX_DAYS = 400
DERIVED_MAX_ROWS_PER_DAY = 2000
# The derived spot check (section 3.4) runs like the scores' one: first upload of a bundle, then at most every
# CHECK_EVERY_HOURS, the newest uploaded day within CHECK_WITHIN_DAYS (both above). Tolerances are per kind, in the kind's
# own module (derived_kinds/<kind>.py TOLERANCE, with their sources), so a package never edits this shared file.

# ---------------------------------------------------------------- 2.0.0 S9: primary per package (primary.py)
# `body-engine app-compute primary --package P ...` moves a package from shadow to primary for a user / cohort / everyone
# only when, over the targeted users' spot checks (app_compute_checks) of the last PRIMARY_CHECK_DAYS days, every upload
# kind of the package has at least PRIMARY_MIN_CHECKS checks and at least PRIMARY_MIN_PASS_RATE of them passed (else
# --force, audited). Product-safety numbers, not health thresholds: a failing check already makes the kind untrusted.
PRIMARY_CHECK_DAYS = 14
PRIMARY_MIN_CHECKS = 3
PRIMARY_MIN_PASS_RATE = 0.95
# The server's scheduled jobs leave a primary package to the phone while the phone uploaded its kinds within
# APP_ACTIVE_DAYS (as the scores, above); after that the server computes the package for that user again by itself.
# A day queued for derivation (hk_dirty_days) that the phone has not uploaded within DIRTY_GRACE_MINUTES (above) is
# derived by the server again (the per-day fallback, as for the scores).
# An app-serving endpoint asked for a primary package's rows the phone has not uploaded computes them on demand (not
# stored, counted in app_compute_fallbacks); at most this many of the newest missing days per request (bounds the cost).
FALLBACK_MAX_DAYS = 31

# ---------------------------------------------------------------- ext_records (GET /v1/users/me/ext-records, pull only)
EXT_MAX_DAYS = 400                # one request covers at most this many days
EXT_DEFAULT_DAYS = 30             # without from / to: the last 30 days
