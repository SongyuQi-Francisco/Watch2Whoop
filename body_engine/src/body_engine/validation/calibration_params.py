"""Thresholds of the calibration decision rules (docs/scoring-calibration.md section 5, issue #118).

Every value here is a design choice, not a literature value: the rules make a decision explicit and repeatable, they do not
make it objective. None of these change a score; they only decide whether a candidate parameter may be shown to the
maintainer as eligible. A change after real data has been analysed has to be marked as such in the document.
"""
# Splitting and uncertainty (section 3)
DEV_SHARE = 2 / 3             # first 2/3 of the paired nights = development window, the rest = hold-out
BLOCK_DAYS = 14               # bootstrap block: two weekly training cycles, longer than the short-range autocorrelation
BOOTSTRAP = 2000
SEED = 118
CI = 0.95

# Rule A: the replay must reproduce the stored scores before any what-if is believed
REPLAY_WITHIN_1 = 0.95        # share of days within ±1 recovery point
REPLAY_MAX_POINTS = 3         # no day further than this
NIGHT_LN_TOL = 0.005          # nightly ln HRV tolerance ...
NIGHT_LN_SHARE = 0.99         # ... on this share of nights

# Rule B: enough data
MIN_PAIRED_NIGHTS = 90
MIN_HOLDOUT_NIGHTS = 30
MIN_ERA_NIGHTS = 30           # the newest source era is checked separately when it has at least this many

# Rules C–G
COVERAGE_MIN = 0.98           # candidate keeps this share of the current recovery days
NO_HARM_MARGIN = 0.05         # a primary metric may not drop further than this (same as scoring-validation R6 for lag-1)
PRACTICAL_MAE = 1.0           # below both of these a change is not worth a new algorithm version
PRACTICAL_ZONE = 0.02
CHURN_ZONE = 0.25             # above this share of changed colours, two primary metrics must improve

# D3: artifact threshold
READING_LOSS_MAX = 0.02       # usable RMSSD readings may drop by at most 2 percentage points
OVERFILTER_MAX = 0.05         # median per-reading ln RMSSD may not fall by more than this

# D4: recovery logistic
ZONE_SHARE_BAND = (0.15, 0.60)   # green, yellow and red each cover this share of days
ZBAR_DRIFT_MAX = 0.30         # the mean weighted z stays within ±this
FLIP_MARGIN = 0.02            # noise flip rate of a replacement mapping may be at most this much worse
ROLLING_NIGHTS = 90           # rolling per-person mapping: trailing nights ...
ROLLING_MIN = 30              # ... and the minimum before it replaces the fixed mapping
ROLLING_SLOPE = (0.5, 4.0)
SLOPE_FACTORS = (0.8, 1.0, 1.2)

# D5: recovery weights
MIN_HRR_PAIRS = 15            # same-day workout heart-rate recovery is a criterion only with at least this many pairs
MIN_WEIGHT_WINS = 2           # hold-out criteria whose interval must lie entirely above 0

# D6: strain K
TRAINING_TRIMP = 40.0         # a training day (scoring-validation 2.6)
TRAINING_STRAIN_BAND = (8.0, 16.0)   # median strain of training days
SATURATION_STRAIN = 19.0      # the top of the scale, where strain stops telling days apart ...
SATURATION_MAX = 0.05         # ... reached on at most this share of days
REF_TRIMP = 180.0             # reference session: 60 minutes entirely at 70–80% HRmax (Edwards weight 3)
REF_STRAIN = 14.0             # the strain it is given if K has to be redefined (K = 180 / ln 3 ≈ 164)
OLD_K_TARGET = 12.0           # the old definition: a median training day maps to 12

# D7: sleep need (report only)
CALM_DEBT_MAX = 30.0
CALM_STRAIN_MAX = 7.0

# D8: mixed beat sources
MIXED_MEDIAN_MAX = 0.05       # median |Δ nightly ln RMSSD| between exact and 10 ms beat times
MIXED_SHIFT_SD_MAX = 0.25     # mean signed shift as a share of the baseline SD
EXPORT_STEP_S = 0.01          # export.xml beat time resolution
