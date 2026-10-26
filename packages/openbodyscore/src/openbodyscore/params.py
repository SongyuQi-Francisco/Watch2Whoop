"""Scoring parameters for sleep, recovery, strain (Watch2Whoop docs/system-design.md §7). Bump ALGO_VERSION whenever any parameter that affects results changes.

Sources (category of each parameter: METHODOLOGY.md): the sleep score constants are ported from the Open Wearables 0.9.0
Sleep Score (backend/app/algorithms/sleep.py, config_algorithms.py; MIT licence); strain uses Edwards TRIMP zones and the
Tanaka HRmax formula (208 − 0.7 × age); CTL / ATL follow Banister's fitness-fatigue model. The recovery weights and logistic
(1.6 / 0.3) and the sleep-need baseline are v1 values, not yet validated; the strain constant K = 170 was calibrated on one
person's data (see its comment). Validation: issue #118.
Every scoring function takes `params=` (default: this module); `override(**changes)` builds a modified copy.
"""
import sys
from datetime import timedelta

from . import _override

ALGO_VERSION = "v1.2-2026-10-27"
# v1.1: adds muscle_load (training volume from SparkyFitness workout records, scores/muscle.py); the sleep / recovery / strain algorithms are the same as v1.0
# v1.2: a sleeping wrist temperature reading dated up to WRIST_TEMP_LEAD before the night's first sleep also belongs to the
#       night (WRIST_TEMP_LEAD below); only the wrist temperature value and its flag change, the scores are the same as v1.1

# ---------- sleep score: copied from Open Wearables 0.9.0's Sleep Score (backend/app/algorithms/sleep.py, config_algorithms.py) ----------
DURATION_IMPACT, STAGES_IMPACT, CONSISTENCY_IMPACT, INTERRUPTIONS_IMPACT = 0.40, 0.20, 0.20, 0.20
OPTIMAL_MIN_HOURS, OPTIMAL_MAX_HOURS = 7.0, 9.0
UNDERSLEEP_K, UNDERSLEEP_MIDPOINT = 1.5, 5.0
OVERSLEEP_K, OVERSLEEP_MIDPOINT = 0.8, 11.0
DEEP_TARGET_MIN, REM_TARGET_MIN = 90.0, 90.0
DEEP_WEIGHT, REM_WEIGHT = 0.5, 0.5
CONSISTENCY_GRACE_MIN = 15.0
MAX_LATE_PENALTY_WINDOW_MIN = 105.0
MAX_EARLY_PENALTY_WINDOW_MIN = 105.0
MAX_EARLY_PENALTY_POINTS = 20.0
CONSISTENCY_WINDOW_NIGHTS = 14
INTERRUPTION_DURATION_POINTS, INTERRUPTION_FREQUENCY_POINTS = 80.0, 20.0
FREQ_SCORE_FRACTIONS = (1.0, 1.0, 0.75, 0.5, 0.0)
INTERRUPTIONS_GRACE_MIN = 20.0
MAX_INTERRUPTION_PENALTY_WINDOW_MIN = 70.0
SIGNIFICANT_WAKE_MIN = 5.0

# ---------- sleep need / sleep debt ----------
SLEEP_NEED_BASELINE_MIN = 480          # baseline 8h (to be calibrated with personal data later)
DEBT_NIGHTS = 14                       # the last 14 nights
DEBT_DECAY = 0.75                      # weight of the shortfall k nights ago is 0.75^(k-1) (last night 1, the night before 0.75, a week ago 0.13), normalized over nights with data
DEBT_CAP_MIN = 120                     # sleep debt adds at most 2h to sleep need
STRAIN_NEED_FROM = 7.0                 # the part of the previous day's strain above 7 ...
STRAIN_NEED_MIN_PER_POINT = 3.0        # ... adds 3 minutes per point (strain 21 -> +42 minutes)

# ---------- recovery ----------
HRV_KIND = "sdnn"                      # "sdnn" (Apple's value) or "rmssd" (computed from heartbeat_series, rawdata/hrv.py).
                                       # The default for every user. Watch2Whoop picks the kind per user (scores/hrv_kind.py,
                                       # issue #117): rmssd only for a user switched on for it AND with enough beat-to-beat data
                                       # (below); one kind per user over the whole history, never mixed. Each stored row and its
                                       # details carry hrv_kind, so ALGO_VERSION is not bumped for the per-user choice
# Enough beat-to-beat data to switch a user to rmssd (#117). Design choice, v1 value, not yet validated: RMSSD needs the
# app's RawSync uploads to keep arriving (#117: history from export.xml alone stops at the export date), so the check is
# about recent, regular coverage. A night counts when it has >= 1 RMSSD reading between RMSSD_NIGHT_HOURS (local time).
RMSSD_COVERAGE_DAYS = 28               # look back this many nights from the scoring end
RMSSD_MIN_NIGHTS = 21                  # ... at least this many of them (75%) with an RMSSD reading
RMSSD_MAX_GAP_DAYS = 3                 # and the newest RMSSD reading at most this many days before the scoring end
RMSSD_NIGHT_HOURS = (18, 12)           # a reading from 18:00 to 12:00 the next day belongs to the night ending that morning
BASELINE_DAYS = 60
MIN_BASELINE_NIGHTS = 14               # the recovery score needs a full 14 nights of HRV
RHR_WINDOW = timedelta(minutes=5)      # that night's resting heart rate = lowest 5-minute mean during sleep
HR_HOLD_CAP = timedelta(minutes=10)    # when computing the 5-minute mean, a heart-rate sample counts until the next one or 10 minutes at most
RECOVERY_WEIGHTS = {"hrv": 0.60, "rhr": 0.20, "sleep_performance": 0.15, "resp_rate": 0.05}
RECOVERY_SIGNS = {"hrv": 1, "rhr": -1, "sleep_performance": 1, "resp_rate": -1}
SD_FLOOR = {"hrv": 0.05, "rhr": 1.0, "sleep_performance": 0.05, "resp_rate": 0.5}   # floor for baseline standard deviation
Z_CLIP = 3.0
RECOVERY_SLOPE = 1.6                   # recovery = 100 / (1 + e^-(slope × weighted z + offset))
RECOVERY_OFFSET = 0.3                  # weighted z = 0 (equal to baseline) -> 57; +0.5 -> 75, −0.5 -> 38
ILLNESS_RHR_DELTA = 5.0                # illness warning: that night's resting heart rate > baseline + 5 ...
ILLNESS_HRV_RATIO = 0.85               # ... and HRV < 0.85 × baseline
WRIST_TEMP_DELTA = 0.6                 # wrist temperature 0.6°C or more above (or below) the 60-day baseline median -> flag
MIN_TEMP_BASELINE = 5
# Which night a sleeping wrist temperature reading belongs to. Apple writes one reading per night and dates it from the start
# of the sleep schedule / Sleep Focus, not from the first sleep the watch detects (real readings start up to about 7 h before
# the night's first sleep stage when someone goes to bed late; about a third were dropped by the old ±2 h window). A reading within WRIST_TEMP_PAD of the night
# counts as before; a night without one takes a reading from up to WRIST_TEMP_LEAD before its start, never one before the
# end (+ WRIST_TEMP_PAD) of the previous main sleep. Data matching, not a health threshold.
WRIST_TEMP_PAD = timedelta(hours=2)
WRIST_TEMP_LEAD = timedelta(hours=8)
SPO2_LOW_MEDIAN = 94.0                 # that night's median SpO2 < 94% -> flag
SPO2_LOW_READING, SPO2_LOW_COUNT = 90.0, 2   # or >= 2 readings < 90%

# ---------- strain ----------
HR_SAMPLE_CAP = timedelta(minutes=5)   # a heart-rate sample counts until the next one, 5 minutes at most (the watch records about one every 2–5 minutes normally, one every 5 seconds during workouts)
HRMAX_PERCENTILE = 99.5
HRMAX_WINDOW_DAYS = 365
EDWARDS_ZONES = ((0.90, 5), (0.80, 4), (0.70, 3), (0.60, 2), (0.50, 1))   # (%HRmax lower bound, weight)
STRAIN_MAX = 21.0
STRAIN_K = 170.0                       # strain = 21 × (1 − e^(−TRIMP/K)); K puts a typical training day (whole-day TRIMP about 145) at 12, see system design §7
MIN_HR_HOURS = 12                      # fewer than 12 hours with heart rate counts as insufficient coverage (watch not worn / not fully synced):
MIN_PARTIAL_TRIMP = 40.0               # insufficient coverage but a workout was recorded (TRIMP >= 40): score as usual and flag "may be low"; otherwise leave strain empty and keep it out of CTL / ATL
CTL_DAYS, ATL_DAYS = 42, 7
LOAD_SEED_DAYS = 14                    # initial CTL / ATL = mean TRIMP of the first 14 days with data


def override(**changes):
    """These parameters with some replaced, for the `params=` argument of the scoring functions, e.g.
    `recovery_score(z, params=params.override(RECOVERY_SLOPE=1.4))`. The module itself stays unchanged."""
    return _override.override(sys.modules[__name__], changes)
