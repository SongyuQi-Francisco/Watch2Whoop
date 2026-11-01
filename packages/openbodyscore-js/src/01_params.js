/* params: scoring parameters for sleep, recovery, strain. Port of packages/openbodyscore/src/openbodyscore/params.py —
keep the two in step (the golden tests compare them); durations are microseconds here (timedelta in Python).

Sources (category of each parameter: packages/openbodyscore/METHODOLOGY.md): the sleep score constants are ported from the
Open Wearables 0.9.0 Sleep Score (backend/app/algorithms/sleep.py, config_algorithms.py; MIT licence); strain uses Edwards
TRIMP zones and the Tanaka HRmax formula (208 − 0.7 × age); CTL / ATL follow Banister's fitness-fatigue model. The recovery
weights and logistic (1.6 / 0.3) and the sleep-need baseline are v1 values, not yet validated; the strain constant K = 170 was
calibrated on one person's data (see its comment). Validation: issue #118.
Every scoring function takes `params` as its last argument (default: these); OBS.params.override(changes) builds a modified copy.
*/
OBS.params = (function (U) {
  "use strict";
  var MIN = U.US_MIN, HOUR = U.US_HOUR;
  var P = {
    ALGO_VERSION: "v1.2-2026-10-27",
    // v1.1: adds muscle_load (training volume from workout records, muscle.js); sleep / recovery / strain are the same as v1.0
    // v1.2: a sleeping wrist temperature reading dated up to WRIST_TEMP_LEAD before the night's first sleep also belongs to
    //       the night; only the wrist temperature value and its flag change, the scores are the same as v1.1

    // ---------- sleep score: copied from Open Wearables 0.9.0's Sleep Score (backend/app/algorithms/sleep.py, config_algorithms.py)
    DURATION_IMPACT: 0.40, STAGES_IMPACT: 0.20, CONSISTENCY_IMPACT: 0.20, INTERRUPTIONS_IMPACT: 0.20,
    OPTIMAL_MIN_HOURS: 7.0, OPTIMAL_MAX_HOURS: 9.0,
    UNDERSLEEP_K: 1.5, UNDERSLEEP_MIDPOINT: 5.0,
    OVERSLEEP_K: 0.8, OVERSLEEP_MIDPOINT: 11.0,
    DEEP_TARGET_MIN: 90.0, REM_TARGET_MIN: 90.0,
    DEEP_WEIGHT: 0.5, REM_WEIGHT: 0.5,
    CONSISTENCY_GRACE_MIN: 15.0,
    MAX_LATE_PENALTY_WINDOW_MIN: 105.0,
    MAX_EARLY_PENALTY_WINDOW_MIN: 105.0,
    MAX_EARLY_PENALTY_POINTS: 20.0,
    CONSISTENCY_WINDOW_NIGHTS: 14,
    INTERRUPTION_DURATION_POINTS: 80.0, INTERRUPTION_FREQUENCY_POINTS: 20.0,
    FREQ_SCORE_FRACTIONS: [1.0, 1.0, 0.75, 0.5, 0.0],
    INTERRUPTIONS_GRACE_MIN: 20.0,
    MAX_INTERRUPTION_PENALTY_WINDOW_MIN: 70.0,
    SIGNIFICANT_WAKE_MIN: 5.0,

    // ---------- sleep need / sleep debt
    SLEEP_NEED_BASELINE_MIN: 480,          // baseline 8h (to be calibrated with personal data later)
    DEBT_NIGHTS: 14,                       // the last 14 nights
    DEBT_DECAY: 0.75,                      // weight of the shortfall k nights ago is 0.75^(k-1), normalized over nights with data
    DEBT_CAP_MIN: 120,                     // sleep debt adds at most 2h to sleep need
    STRAIN_NEED_FROM: 7.0,                 // the part of the previous day's strain above 7 ...
    STRAIN_NEED_MIN_PER_POINT: 3.0,        // ... adds 3 minutes per point (strain 21 -> +42 minutes)

    // ---------- recovery
    HRV_KIND: "sdnn",                      // "sdnn" (Apple's value) or "rmssd" (from the beat-to-beat series); chosen per user
                                           // by the server (body_engine scores/hrv_kind.py, issue #117), never mixed
    RMSSD_COVERAGE_DAYS: 28,               // enough beat-to-beat data to switch a user to rmssd (#117), v1 design choice
    RMSSD_MIN_NIGHTS: 21,
    RMSSD_MAX_GAP_DAYS: 3,
    RMSSD_NIGHT_HOURS: [18, 12],
    BASELINE_DAYS: 60,
    MIN_BASELINE_NIGHTS: 14,               // the recovery score needs a full 14 nights of HRV
    RHR_WINDOW: 5 * MIN,                   // that night's resting heart rate = lowest 5-minute mean during sleep
    HR_HOLD_CAP: 10 * MIN,                 // a heart-rate sample counts until the next one or 10 minutes at most
    RECOVERY_WEIGHTS: { hrv: 0.60, rhr: 0.20, sleep_performance: 0.15, resp_rate: 0.05 },
    RECOVERY_SIGNS: { hrv: 1, rhr: -1, sleep_performance: 1, resp_rate: -1 },
    SD_FLOOR: { hrv: 0.05, rhr: 1.0, sleep_performance: 0.05, resp_rate: 0.5 },   // floor for baseline standard deviation
    Z_CLIP: 3.0,
    RECOVERY_SLOPE: 1.6,                   // recovery = 100 / (1 + e^-(slope × weighted z + offset))
    RECOVERY_OFFSET: 0.3,                  // weighted z = 0 (equal to baseline) -> 57; +0.5 -> 75, −0.5 -> 38
    ILLNESS_RHR_DELTA: 5.0,                // illness warning: that night's resting heart rate > baseline + 5 ...
    ILLNESS_HRV_RATIO: 0.85,               // ... and HRV < 0.85 × baseline
    WRIST_TEMP_DELTA: 0.6,                 // wrist temperature 0.6°C or more above (or below) the 60-day baseline median -> flag
    MIN_TEMP_BASELINE: 5,
    // Which night a sleeping wrist temperature reading belongs to (Apple dates it from the start of the sleep schedule):
    // within WRIST_TEMP_PAD of the night; else up to WRIST_TEMP_LEAD before its start, never before the end (+ pad) of the
    // previous main sleep. Data matching, not a health threshold.
    WRIST_TEMP_PAD: 2 * HOUR,
    WRIST_TEMP_LEAD: 8 * HOUR,
    SPO2_LOW_MEDIAN: 94.0,                 // that night's median SpO2 < 94% -> flag
    SPO2_LOW_READING: 90.0, SPO2_LOW_COUNT: 2,   // or >= 2 readings < 90%

    // ---------- strain
    HR_SAMPLE_CAP: 5 * MIN,                // a heart-rate sample counts until the next one, 5 minutes at most
    HRMAX_PERCENTILE: 99.5,
    HRMAX_WINDOW_DAYS: 365,
    EDWARDS_ZONES: [[0.90, 5], [0.80, 4], [0.70, 3], [0.60, 2], [0.50, 1]],   // (%HRmax lower bound, weight)
    STRAIN_MAX: 21.0,
    STRAIN_K: 170.0,                       // strain = 21 × (1 − e^(−TRIMP/K)); K puts a typical training day (TRIMP ≈ 145) at 12
    MIN_HR_HOURS: 12,                      // fewer than 12 hours with heart rate counts as insufficient coverage:
    MIN_PARTIAL_TRIMP: 40.0,               // ... but a workout was recorded (TRIMP >= 40): score and flag "may be low"
    CTL_DAYS: 42, ATL_DAYS: 7,
    LOAD_SEED_DAYS: 14                     // initial CTL / ATL = mean TRIMP of the first 14 days with data
  };
  P.override = function (changes) { var o = U.override(P, changes, "params"); return o; };
  return P;
})(OBS.util);
