"""Limits and thresholds of the native app sync (#340 N2, docs/ios-native-app.md sections 5 and 7.2). Change here.

None of these are health thresholds: they size pages, bound the work one request may cause and decide when the server
takes the scoring back from the app.
"""

FLAG = "native_sync"   # rollout flag: the app uses POST /v1/sync/pull, /push and PUT /scores/app only while this is on

# ---------------------------------------------------------------- pull
STREAMS = ("scores", "daily", "sleep", "records", "docs", "raw")
PULL_LIMIT_DEFAULT = 500          # changes per answer unless the app asks for fewer / more
PULL_LIMIT_MAX = 2000
RAW_PAGE_ROWS = 20000             # raw samples + statistics buckets per answer (at least one whole day is always sent)
RAW_DAYS_MAX = 7                  # explicit raw_days per request (re-scoring a few days)
# Full restore order: newest day first, so the last two weeks are on the phone after the first page or two
# ("先拉最近 14 天再往前补"). Raw data older than RAW_CELLULAR_DAYS is the app's to fetch on Wi-Fi only.
RAW_CELLULAR_DAYS = 90

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
