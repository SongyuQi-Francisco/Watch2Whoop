"""Thresholds of the native app's Today tab (2.0.0 package M4, docs/ios-native-2.0.md: "move the thresholds of
TodayParams.swift into a JS params block with the same sources", decision D7).

The app's 「需要注意」 block and the direction arrows of Trends' 「今天」 list (#357) compare the user with their own baseline
(or a status the algorithms already computed), never with one person's numbers. These values are the reference: the JS
bundle carries the same block (`OpenBodyScore.coach.TODAY_PARAMS`, src/34_coach.js; tests/test_js_golden_coach.py compares
them) and the app's `TodayParams.swift` is checked against the bundle (Watch2WhoopTests/LocalCoachTests.swift). Change a value
here, in the JS block and in the Swift file together.

Baselines are the server's (trends/series.py `baseline_band`: mean ± 1 SD of the 60 days before the day, at least 14 readings).
"""

# ---------------------------------------------------------------- which day the documents describe
# Days of the /trends document behind Trends' 「今天」 and the Today tab's blocks: the 60 days before the day (the baseline
# window above) plus the day itself.
TRENDS_DAYS = 61
# Readings needed in the 60-day window for a phone-side baseline (trends/series.py BASELINE_MIN_N).
BASELINE_MIN_N = 14
# Days of the small spark line in Trends' 「今天」 rows.
SPARK_DAYS = 7

# ---------------------------------------------------------------- direction arrows (Trends 「今天」)
# Within this many standard deviations of the baseline the arrow is flat: 0.5 SD is the "smallest worthwhile change" used
# for day-to-day HRV and resting HR (Plews et al. 2013, Int J Sports Physiol Perform 8:688; Buchheit 2014, Front Physiol 5:73).
FLAT_Z = 0.5
# Without a standard deviation (too few readings to spread), a relative change below this is flat.
FLAT_RELATIVE = 0.03

# ---------------------------------------------------------------- 需要注意: metrics clearly off the user's own baseline
# (series key, bad direction −1 below / +1 above, z at or beyond which it is flagged, minimum distance from the mean in the
# metric's own unit, absolute value instead of a z-score). 1.5 SD: outside the "normal range" band WHOOP / Oura draw (± 1 SD)
# by a clear margin, about the worst 7 % of days of a normal distribution.
# - HRV low, resting HR high: the classic recovery / illness signals (WHOOP recovery, Oura readiness contributors).
# - Respiratory rate high by >= 1 breath/min: WHOOP's illness signal (Miller et al. 2020, respiratory rate during sleep).
# - SpO2 low by >= 1.5 points (nightly average; the health monitor separately flags values out of its range).
# - Wrist temperature: the series is already the night's deviation from the 60-day mean; >= +0.5 °C is the edge of the
#   normal band of the Trends card and Apple's / Oura's "elevated" deviation.
WATCH = (
    {"key": "hrv", "bad": -1, "z": 1.5, "min_delta": 5, "absolute": None},
    {"key": "resting_hr", "bad": 1, "z": 1.5, "min_delta": 3, "absolute": None},
    {"key": "resp_rate", "bad": 1, "z": 1.5, "min_delta": 1, "absolute": None},
    {"key": "spo2", "bad": -1, "z": 1.5, "min_delta": 1.5, "absolute": None},
    {"key": "wrist_temp", "bad": 1, "z": 0, "min_delta": 0, "absolute": 0.5},
)

# ---------------------------------------------------------------- 需要注意: status cards and the health monitor
# Status cards (trends/calc.py) that count when their tone is at least this bad ("mid" = yellow, "bad" = red).
# Resilience, body battery, lowest-HR timing and heart-rate recovery only when red: their yellow is an ordinary day.
STATUS_MIN_TONE = {
    "illness": "mid",          # watch / high (calc.illness_risk)
    "hrv_status": "mid",       # low / poor (calc.hrv_status, Garmin HRV Status)
    "training_status": "mid",  # strained / unproductive / detraining / overreaching (calc.training_status)
    "resilience": "bad",
    "body_battery": "bad",     # < 30
    "recovery_index": "bad",
    "hr_recovery": "bad",
}

# ---------------------------------------------------------------- ranking
# Severity, in z-score units so metric items and status items sort together: a metric's severity is its |z| (capped); an
# absolute rule scores AT_THRESHOLD at its threshold and grows with the value; a status card in red / yellow and a
# health-monitor vital out of its range have fixed severities. At most MAX_ITEMS items (the block is hidden without any).
SEVERITY_CAP = 4.0
AT_THRESHOLD = 1.5
STATUS_BAD = 3.0
STATUS_MID = 1.6
MONITOR_OFF = 2.0
MAX_ITEMS = 3

# ---------------------------------------------------------------- 今日核心
# Stress score bands (0–3): packages/openbodyscore stress.BANDS — < 1 low, 1–2 medium, >= 2 high.
STRESS_MEDIUM = 1.0
STRESS_HIGH = 2.0


def as_dict() -> dict:
    """The block as the bundle carries it (OpenBodyScore.coach.TODAY_PARAMS)."""
    return {"TRENDS_DAYS": TRENDS_DAYS, "BASELINE_MIN_N": BASELINE_MIN_N, "SPARK_DAYS": SPARK_DAYS, "FLAT_Z": FLAT_Z,
            "FLAT_RELATIVE": FLAT_RELATIVE, "WATCH": [dict(w) for w in WATCH], "STATUS_MIN_TONE": dict(STATUS_MIN_TONE),
            "SEVERITY_CAP": SEVERITY_CAP, "AT_THRESHOLD": AT_THRESHOLD, "STATUS_BAD": STATUS_BAD, "STATUS_MID": STATUS_MID,
            "MONITOR_OFF": MONITOR_OFF, "MAX_ITEMS": MAX_ITEMS, "STRESS_MEDIUM": STRESS_MEDIUM, "STRESS_HIGH": STRESS_HIGH}
