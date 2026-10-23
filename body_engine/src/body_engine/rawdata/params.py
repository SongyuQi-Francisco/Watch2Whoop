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
