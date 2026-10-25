"""Parameters of the sync engine's derivation (rawdata/derive.py, docs/apple-sync-engine.md §3.2.3–3.2.5), with sources.

The sleep rules copy what Open Wearables does with the same HealthKit samples (vendor/open-wearables, pinned commit in
.gitmodules / the submodule; MIT, THIRD_PARTY_NOTICES.md), so the app's sleep matches the OW import's
(importers/ow.py sleep_segments + importers/common.py sessionize, which both paths share).
"""
from datetime import timedelta

# HKCategoryValueSleepAnalysis -> the stage name the OW iOS SDK sends for it (OpenWearablesHealthSDK 0.15.0,
# Internal/Types.swift `_sleepStageString`: 0 in_bed, 1 sleeping = asleepUnspecified, 2 awake, 3 light = asleepCore,
# 4 deep, 5 rem); importers/ow.STAGE_MAP then gives the Body Engine stages (in bed is not sleep). Other values: unknown.
SLEEP_STAGES = {0: "in_bed", 1: "sleeping", 2: "awake", 3: "light", 4: "deep", 5: "rem"}

# OW's sleep state machine (backend/app/services/sdk/sleep_service.py): a sample more than sleep_end_gap_minutes = 120
# (backend/app/config.py) away from the current session's window starts a new session; with no session open, only these
# stages start one (SLEEP_START_STATES: not awake / unknown).
SESSION_GAP = timedelta(minutes=120)
SESSION_START = frozenset({"in_bed", "sleeping", "light", "deep", "rem"})
# ... and when a session is finished (_calculate_final_metrics): with core / deep / REM stages, the in-bed and "asleep"
# wrappers are dropped; with only "asleep", in bed is dropped; with only in bed (older devices, the iPhone's bedtime),
# in bed counts as asleep. Stages are then taken in start order and a stage starting before the previous one ended is
# cut to start there.
DETAILED_STAGES = frozenset({"light", "deep", "rem"})
ASLEEP_STAGES = frozenset({"sleeping", "light", "deep", "rem"})

# Sleep of a dirty day d (a stage sample starting on d changed): the nights waking on d and d + 1 (common.sessionize puts
# a night on the day of wake − 4 h), rebuilt from every stage sample starting between d − 1 and d + 3 (local midnight),
# which holds the whole of any night that wakes on those two days.
SLEEP_DAYS_BEFORE = 1
SLEEP_DAYS_AFTER = 3

# hk-derive-run: stop taking new days after this many seconds (the rest stays queued for the next run; sync-refresh-run
# and the per-minute job must stay short)
TIME_BUDGET_S = 240
# ... and before a score recompute (sync/refresh.run_scores, possibly in the API process): a shorter budget
REFRESH_TIME_BUDGET_S = 60


# ---------------------------------------------------------------- S4: shadow comparison (rawdata/shadow.py, §4)
# Operational thresholds, not health rules: how close the two sync paths must be to count as the same data. Sources:
# docs/apple-sync-engine.md §4 (the table maintainers agreed on); the cumulative tolerance is the error of OW's end-time
# guess (importers/ow.py header: "a few percent, mostly low", CUMULATIVE_CAP) - inside it a difference is `method`.
SHADOW_CLOSED_AFTER = timedelta(hours=36)   # a day is compared once it ended this long ago (late uploads, OW's 2-day look-back)
SHADOW_DAYS = 7                             # the nightly job re-checks the last N closed days
SHADOW_AVG_REL = 0.001                      # non-cumulative daily value (mean / min / max): ≤ 0.1 % = ok
SHADOW_SUM_REL = 0.01                       # cumulative daily value: ≤ 1 % = ok
SHADOW_SUM_METHOD_REL = 0.06                # ... up to 6 % and the app higher or lower: explained by OW's end-time guess (`method`)
SHADOW_COUNT_EXPLAINED_REL = 0.05           # non-cumulative: same mean within tolerance but the counts differ ≤ 5 % (`ow_same_ts`)
SHADOW_SLEEP_MIN = 1.0                      # sleep: start / end / asleep / each stage within 1 minute = ok
SHADOW_SLEEP_EXPLAINED_MIN = 20.0           # ... within 20 minutes with several sources that night: `source_priority`
SHADOW_WORKOUT_MIN = 1.0                    # workouts: duration within 1 minute
SHADOW_WORKOUT_KCAL_REL = 0.02              # total energy within 2 %
SHADOW_WORKOUT_HR = 1.0                     # average / maximum heart rate within 1 bpm
SHADOW_SCORE_POINTS = 1.0                   # recovery / sleep score (0–100) within 1 point, strain (0–21) within 0.1
SHADOW_STRAIN_POINTS = 0.1
SHADOW_SCORE_EXPLAINED = 5.0                # a larger score difference is explained only if an input of that day is
# Reason codes (verdict explained or not): shown in the report and the API as codes; the panel / report translate them
SHADOW_REASONS = ("method", "ow_same_ts", "source_priority", "late_sample", "active_kcal_method", "input_diff",
                  "missing_app", "missing_ow", "value")

# Cut-over gates per user (rawdata/cutover.py, §5.1): the comparison was clean long enough and covers what OW had
CUTOVER_CLEAN_DAYS = 7                      # consecutive closed days without mismatch / missing_app
CUTOVER_COVERAGE = 0.95                     # share of OW's daily items (metric-days) the app also delivered
CUTOVER_MIN_APP_VERSION = "1.0.8"           # the first app with the engine (A1); older apps keep OW
CUTOVER_APP_SEEN = timedelta(days=7)        # ... and it uploaded engine batches within this many days
CUTOVER_REDERIVE_DAYS = 90                  # days re-derived in the app mode at the switch (the first sync's range)
CUTOVER_CHECK_DAYS = 14                     # score consistency window before / after the switch
CUTOVER_SCORE_MEAN = 2.0                    # mean |Δ recovery| / |Δ sleep score| over the window (points) ...
CUTOVER_SCORE_MAX = 8.0                     # ... and the largest single-day difference; strain in 0–21 units:
CUTOVER_STRAIN_MEAN = 0.5
CUTOVER_STRAIN_MAX = 2.0

# S6: Open Wearables' history kept locally (rawdata/ow_archive.py): sleep stages pulled per 60-day window
ARCHIVE_WINDOW = timedelta(days=60)
