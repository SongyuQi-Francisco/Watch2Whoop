# Methodology

> Every score in this library: inputs, each step with its formula, every parameter with its value and where it comes from,
> known limits, and how far each score has been validated. **We claim only what we can back up**: anything not validated
> says "v1 value, not yet validated". The scores are WHOOP-style wellness estimates based on published research and our own
> design choices, not a medical device and not a diagnosis; the project is not affiliated with WHOOP.
>
> Versions described: `params.ALGO_VERSION = v1.2-2026-10-27` (sleep, recovery, strain), body age `hs-v1.1-2026-10-17`
> (`healthspan_params`, the library default) and `hs-v1.2-2026-10-23` (`healthspan_params_v12`), stress `v1.0-2026-10-17`,
> HRV artifact filter `rr-v1`. Chinese version: Watch2Whoop [`docs/scoring-methodology.md`](../../docs/scoring-methodology.md).
> Validation plan and results (Chinese): Watch2Whoop [`docs/scoring-validation.md`](../../docs/scoring-validation.md), issue #118.

## 0. How to read this

### 0.1 Where each parameter comes from
| Label | Meaning |
|---|---|
| **Literature** | Taken from a published study or standard (references at the end; DOIs checked against Crossref). A citation supports where a number comes from; **it does not mean the score as a whole has been validated** |
| **Ported spec** | Taken over from another open-source implementation (currently only the Open Wearables 0.9.0 Sleep Score, MIT; see [NOTICE](NOTICE)) |
| **Design choice** | Our first value: the direction is supported by evidence or common sense, the number has not been fitted to data |
| **Personal calibration** | Fitted on one person's data (the project maintainer's). Breaks the rule of never hard-coding one person's data; to be replaced (section 11) |

### 0.2 What this library does and what the caller does
The library is pure functions: one step at a time, no database, no clock. Watch2Whoop's scoring engine
(`body_engine/scores/engine.py`) strings them together, and the conventions below are how it does so; another caller can do it
differently, and then the numbers are not comparable with Watch2Whoop's.
- **Dates** are the user's local dates. A sleep belongs to the day of (wake time − 4 h), its wake date. Datetimes passed to
  the library must be time-zone aware, in the user's local zone wherever clock time matters (bedtime, hour of day).
- **Personal baseline**: unless stated otherwise, the values of the 60 days **before** the day (the day itself excluded),
  per person and per metric.
- **Missing data**: a score that cannot be computed stays empty; nothing is filled with 0 or interpolated. No record is not
  the same as "did not happen".
- **Full recompute**: Watch2Whoop scores from a person's first day with a resting heart rate to today on every run, so results
  depend only on the data. Any parameter change that affects results bumps the module's algorithm version; one person's
  history never mixes two versions.
- **Parameters** live in `params.py` (sleep, recovery, strain), `healthspan_params.py` / `healthspan_params_v12.py` (body age),
  at the top of `stress.py` and of `hrv.py`. Every function takes a keyword-only `params=`; `params.override(NAME=value)`
  (likewise `healthspan_params.override`, `stress.override`, `hrv.override`) returns a modified copy for one call.

### 0.3 Inputs
Designed for the raw samples an Apple Watch writes to HealthKit: sleep stage segments, heart rate, HRV (Apple's SDNN, plus
about one minute of beat-to-beat times per reading), respiratory rate, SpO₂, wrist temperature, Apple's all-day resting heart
rate, workouts, steps; weight, waist, VO₂max; logged strength sessions. **Apple's own readiness and sleep scores are not
readable through HealthKit**, so they cannot be compared directly. Other watches' data can be passed in the same shapes, but
nothing has been checked on them.

### 0.4 How far each score is validated
| Score | Basis | Synthetic data | Real data (aggregate conclusions only) | External reference |
|---|---|---|---|---|
| Sleep score | ported spec | sensitivity | one person: sensitivity | none (no polysomnography, no WHOOP) |
| Sleep need / debt | design choices | sensitivity | one person: sensitivity | none |
| HRV from beat times | Task Force 1996 definitions | artifact filter checked against known artifacts (one weakness found, section 3) | SDNN recomputed vs Apple's SDNN r ≈ 0.9 (one person); SDNN vs RMSSD (two people) | no ECG |
| Recovery | method from the literature, parameters design choices | stability, construct validity (correlations; regression version partly passes), sensitivity, single-reading noise, synthetic population | one person: stability, sensitivity | no WHOOP |
| Strain / training load | Edwards, Tanaka, Banister; **K personal calibration** | sensitivity; synthetic population (per-person K varies more than 4-fold) | one person: sensitivity | none |
| Muscle load | design choice + Foster | none | none | none |
| Stress | design choices | none | none | none |
| Body age | literature curves | analytic sensitivity, worked examples | v1.1 vs v1.2 comparison (one person) | none |

"Synthetic data" checks that the implementation is consistent with a known, made-up truth and measures what noise costs; it is
**not** evidence of real-world validity. The pre-registered plan for multi-user and WHOOP comparisons is section 5 of the
validation document; it has not been run.

---

## 1. Sleep score (0–100)
`sleep.py` (`sleep_score`, `duration_score`, `stages_score`, `consistency_score`, `interruptions_score`, `waso`).
**A line-by-line port of the Open Wearables 0.9.0 Sleep Score** (`backend/app/algorithms/sleep.py`, `config_algorithms.py`).

**Inputs**: the night's main sleep (Watch2Whoop: the longest watch sleep per wake date; "asleep" segments without stages from
the iPhone or third-party apps are ignored), its stage segments, and the bedtimes of the latest 14 main sleeps within the previous 15 days.

**Steps**
1. Duration *D* (net sleep hours *h*): 7 ≤ *h* ≤ 9 → 100; *h* < 7 → `100 × (1 + e^(k₁(7−m₁))) / (1 + e^(k₁(h−m₁)))` with
   k₁ = −1.5, m₁ = 5; *h* > 9 → the same form with k₂ = 0.8, m₂ = 11, anchored at 9, floor 50.
2. Stages *St* = 0.5 × min(deep / 90, 1) × 100 + 0.5 × min(REM / 90, 1) × 100.
3. Consistency *C*: difference Δ between tonight's bedtime and the median of the 14 bedtimes (hours past noon, continuous
   over midnight); late by more than 15 min starts losing points, all 100 lost over another 105 min; going to bed early loses
   at most 20. No main sleep in the previous 15 days → 0, per the spec.
4. Interruptions *I*: night awake time (excluding awake time before the first and after the last asleep segment) worth 80
   points, no deduction within 20 min, all lost over another 70 min; awakenings (> 5 min) worth 20 points, fractions
   `(1, 1, 0.75, 0.5, 0)` for 0, 1, 2, 3, ≥ 4. **One difference from Open Wearables**: adjacent awake segments are merged into one awakening.
5. Sleep score = 0.40 *D* + 0.20 *St* + 0.20 *C* + 0.20 *I* (truncated to an integer).

| Parameter | Value | Label |
|---|---|---|
| Weights | 0.40 / 0.20 / 0.20 / 0.20 | Ported spec |
| Full-marks duration | 7–9 h | Ported spec (matches the NSF 2015 and AASM/SRS 2015 adult recommendations of 7–9 h / at least 7 h) |
| Duration sigmoids | k 1.5 / 0.8, midpoints 5 h / 11 h, oversleep floor 50 | Ported spec |
| Deep / REM targets | 90 min each, 50% each | Ported spec |
| Consistency | grace 15 min, window 105 min, early cap 20, 14 nights | Ported spec |
| Interruptions | grace 20 min, window 70 min, > 5 min per awakening | Ported spec |
| Merge adjacent awake segments | — | Design choice |

**Known limits**
- Weights and thresholds are Open Wearables'; we have not validated them, and the spec itself publishes no validation.
- The stage component depends entirely on the watch's sleep staging. Compared with polysomnography, PPG staging separates wake
  and sleep with high accuracy, but four-class agreement (wake, light, deep, REM) is only moderate (Fonseca 2017: κ ≈ 0.42);
  a 90-minute deep-sleep target is high for older adults.
- Consistency is strict for irregular sleepers (often 0) by design of the spec.
- There is no "in bed" data, so sleep latency is usually empty and not scored.
- Sensitivity (synthetic, ±20% per parameter): the sleep score moves 0.1–1.7 points; the 7 h lower bound and the deep-sleep
  target matter most.

## 2. Sleep need, sleep debt, sleep performance
`sleep.py` (`sleep_debt`, `sleep_need`, `performance`).

- Actual sleep = main sleep + naps after the previous main sleep (at most 24 h back) and before this one (the caller adds them up).
- Sleep debt = weighted mean of (baseline need − actual sleep) over the previous 14 nights, weights 0.75^(k−1) (k = 1 is last
  night), normalised over nights with data; oversleeping offsets, the result is ≥ 0.
- Sleep need = baseline 8 h + min(debt, 2 h) + 3 min per strain point above 7 the previous day (strain 21 → +42 min); none if
  that strain is missing.
- Sleep performance = min(actual / need, 1); feeds recovery.

| Parameter | Value | Label |
|---|---|---|
| Baseline need | 480 min | Design choice (inside the NSF / AASM 7–9 h range) — v1 value, not calibrated |
| Window / decay | 14 nights, 0.75 | Design choice — v1 value, not yet validated |
| Debt cap | 120 min | Design choice — v1 value, not yet validated |
| Strain add-on | 3 min per point above 7 | Design choice — v1 value, not yet validated |

**Known limits**
- Everyone gets the same 8 h baseline although sleep need varies a lot between people; WHOOP says it personalises sleep
  need, we do not yet.
- Because sleep need rises with the previous day's strain and sleep performance feeds recovery, recovery falls a little after a
  hard day **by construction**. On synthetic data this path is about a tenth of the modelled load effect; validating "load
  lowers recovery" with the recovery score itself is therefore partly circular (validation 2.4 / 3.5).

## 3. HRV from beat-to-beat times
`hrv.py` (`rr_intervals`, `clean`, `analyze`, `beats_from_clock`; `rr-v1`). For each background HRV reading the watch stores
about one minute of beat times (`HKHeartbeatSeriesSample`), 1–4 times a night.

1. RRᵢ = tᵢ − tᵢ₋₁ (ms); a beat preceded by a gap (`precededByGap`) or a backwards time is invalid.
2. Artifacts: RR outside 300–2000 ms, or more than 25% away from the series median RR → invalid (ectopic beats, motion).
3. RMSSD = √mean((RRᵢ − RRᵢ₋₁)²) over pairs of adjacent valid intervals, at least 20 pairs; SDNN = population SD of the valid intervals.

| Parameter | Value | Label |
|---|---|---|
| RMSSD / SDNN definitions | — | Literature (Task Force ESC/NASPE 1996) |
| Valid RR range | 300–2000 ms | Design choice (30–200 bpm) |
| Deviation from median | 25% | Design choice (relative-deviation rules are common practice, review in Peltola 2012; not fitted) |
| Minimum successive differences | 20 | Design choice |

**Checks done**
- SDNN recomputed from the beat times correlates about 0.9 with Apple's own SDNN (one person's readings), so parsing and
  artifact handling have no systematic error.
- **Synthetic beat series with known artifacts** (validation 2.5 / 3.6; four HRV levels, missed / extra / premature beats at
  2% and 5% of beats): missed and extra beats and clearly premature beats are removed; at 2% artifacts the median RMSSD
  error is below 1% at every level, against 12–895% without the median filter, and no genuine interval is dropped without
  artifacts. **Weakness found**: a mildly premature beat (less than 25% early) passes the filter and can inflate that
  reading's RMSSD several-fold (at 2% artifacts the 95th percentile of the error is about +400% at low HRV and +90% at
  normal HRV); a 20% threshold has a smaller 95th percentile at every level and drops only 0.3% of genuine intervals at
  very high HRV. **Not changed** (it would change
  results; see section 11). It does not affect any score today: recovery uses Apple's SDNN by default.
- **Not compared with ECG ground truth.**
- **Resolution**: beat times in `export.xml` have 10 ms resolution. Rounding biases RMSSD **upwards**, approximately
  √(RMSSD² + 50 ms²) (the rounding error has variance 0.01² / 12 s², and successive differences of intervals multiply it by
  6): about +25% at a true RMSSD near 10 ms, +2.5% near 30 ms, below 1% above 70 ms (synthetic check, validation 3.6).
  Beat times read directly through HealthKit are double precision. A person whose history mixes the two sources can have a
  shifted RMSSD baseline.

**Known limits**
- Apple decides when readings happen; third parties cannot trigger them, and no algorithm adds readings.
- Ultra-short windows: against 4–5-minute reference recordings, RMSSD is already valid from 10 s, while SDNN needs at least 30 s
  or several 10 s recordings (Munoz 2015; review in Shaffer 2017). Ultra-short windows favour RMSSD.

## 4. Recovery (0–100)
`recovery.py` (`night_hrv_ln`, `lowest_window`, `baseline`, `zscore`, `recovery_score`, `illness_warning`, `wrist_temp_flag`, `spo2_flag`).

**Inputs** (within the main sleep window)
- Night HRV: median of ln of the readings. Apple's SDNN by default (`HRV_KIND = "sdnn"`). Watch2Whoop chooses SDNN or RMSSD
  per user: RMSSD from beat times only for a user switched on for it **and** with RMSSD on at least 21 of the last 28 nights,
  the newest at most 3 days old (`RMSSD_*`, design choices, not validated). One kind per person over the whole history; an
  RMSSD user's night without beat data has no HRV and no recovery score (SDNN never fills in).
- Night resting heart rate: the **lowest 5-minute mean** while asleep. Asleep, the watch records heart rate every 4–5 min, so
  each reading counts until the next (at most 10 min), time-weighted; a window covered less than half does not count; if none
  qualifies, the lowest single reading. Without sleeping heart rate, Watch2Whoop uses Apple's all-day resting heart rate
  **against its own baseline** (it runs a few bpm above the sleeping minimum).
- Night respiratory rate: median of the readings. Sleep performance: section 2.

**Steps**
1. Each input against its 60-day baseline: z = (x − mean) / max(SD, floor), clipped to ±3; resting heart rate and respiratory
   rate sign-flipped.
2. Weighted z̄ = Σ wₖ zₖ / Σ wₖ over the inputs present (**HRV is required**).
3. Recovery = 100 / (1 + e^−(1.6 z̄ + 0.3)), rounded. At baseline 57; z̄ = +0.5 → 75, −0.5 → 38.
4. Zones: ≥ 67 green, 34–66 yellow, < 34 red.
5. No score until the previous 60 days hold 14 nights of HRV.
6. Flags only, not part of the score: **illness warning** (resting heart rate > baseline + 5 bpm and HRV < 0.85 × baseline),
   wrist temperature ≥ 0.6 °C from its 60-day median (≥ 5 nights of baseline), night SpO₂ median < 94% or ≥ 2 readings < 90%.

| Parameter | Value | Label |
|---|---|---|
| Method: z-score of ln HRV (and resting HR) against a rolling personal baseline | — | Literature (common practice in HRV-guided training: Kiviniemi 2007, Plews 2012 / 2013, Buchheit 2014). **Those studies use morning or overnight ln RMSSD**; the default SDNN input does not match them exactly |
| Inputs: HRV, resting HR, sleep, respiratory rate | — | Design choice, the same inputs WHOOP describes publicly (its developer API recovery object has `hrv_rmssd_milli`, `resting_heart_rate`) |
| Weights | HRV 0.60, resting HR 0.20, sleep performance 0.15, respiratory rate 0.05 | Design choice — v1 value, **never fitted** |
| Logistic | slope 1.6, offset 0.3 | Design choice — v1 value, never fitted |
| Zone thresholds | 67 / 34 | Design choice (matches WHOOP's three-colour bands; WHOOP publishes no formal definition) |
| Baseline | 60 days, at least 14 nights | Design choice |
| SD floors | ln HRV 0.05, heart rate 1, performance 0.05, respiration 0.5 | Design choice |
| z clip | ±3 | Design choice |
| Resting HR window | lowest 5 min, a reading counts at most 10 min | Design choice |
| Illness warning | +5 bpm and HRV < 85% | Design choice. The direction is supported: resting heart rate rises before influenza-like illness and COVID-19 symptoms (Radin 2020, Mishra 2020), as does respiratory rate (Miller 2020); **the thresholds are not validated** |
| Wrist temperature / SpO₂ flags | 0.6 °C; 94% / 90% × 2 | Design choice — not yet validated |
| Wrist temperature reading of a night | within ±2 h of the night; else up to 8 h before it, not before the previous main sleep's end + 2 h | Data matching (v1.2): Apple dates the reading from the start of the sleep schedule, not from the first sleep the watch detects |

**What synthetic data shows** (validation section 3; a made-up person whose hidden daily state drives HRV, resting heart rate
and breathing, sampled the way an Apple Watch samples)
- Recovery tracks the hidden state better than HRV alone (r 0.75 vs 0.69 at the default noise), because resting heart rate and
  sleep performance offset single-reading noise. Single-reading HRV noise is the largest error source: raising it from 0.15
  to 0.45 (ln scale) lowers that correlation from 0.82 to 0.70.
- Sensitivity, each parameter ±20%: the logistic slope and the baseline length matter most (2–3 points on average, but 7–9% of
  days change colour); each of the four weights moves recovery by at most about 1 point.
- Regression version of construct validity (strain of day D and sleep hours → day D+1; OLS + Newey-West + Benjamini-Hochberg):
  without weekday control the load effect is found in 88% of synthetic persons for recovery and resting heart rate (54% for
  ln HRV, which is noisy) but false positives without any effect are 12–14%; with weekday control false positives are 4–6% and
  power drops to 36–50%. **Partly passes** the pre-set criteria (power ≥ 80%, false positives ≤ 10%).
- Synthetic population (40 persons with different HRV levels, coupling, noise, readings a night, ages, training volumes): with
  the fixed parameters the share of green days ranges 33–46% and red 15–30%, nearly unrelated to absolute HRV or resting
  heart-rate level (|Spearman ρ| ≤ 0.25) — the per-person baseline does its job — and driven mostly by how consistently the
  inputs follow the hidden state.

**Known limits**
- **Only 1–4 HRV readings a night** (WHOOP measures continuously while you sleep). A single reading is noisy: one high SDNN
  reading can push a bad night above 90.
- Weights and logistic parameters are not fitted; real score distributions may differ between people more than in the synthetic population.
- The baseline follows the person: slow improvement or decline is absorbed, recovery only says "relative to the last 60 days".
- The illness warning also fires on very low non-illness days in synthetic data; treat it as a hint.

## 5. Strain (0–21) and training load
`strain.py` (`hrmax_formula`, `hrmax`, `sample_minutes`, `zone_weight`, `edwards_trimp`, `strain`, `training_load`).

1. HRmax = max(208 − 0.7 × age, 99.5th percentile of all heart-rate readings in the previous 365 days); without a birth date,
   the observed value only.
2. A heart-rate reading counts until the next one, at most 5 min (normally every 2–5 min, about every 5 s in workouts); the
   last one counts half the cap.
3. Edwards TRIMP = Σ minutes per zone × weight: ≥ 50 / 60 / 70 / 80 / 90% HRmax → weights 1–5, over the **whole local day**,
   not only workouts.
4. Strain = 21 × (1 − e^(−TRIMP / K)), K = 170.
5. Coverage (Watch2Whoop's engine): fewer than 12 hours with heart rate → scored and flagged "may be low" if a workout was
   recorded (TRIMP ≥ 40), otherwise empty and kept out of CTL / ATL.
6. CTL / ATL: exponentially weighted TRIMP with time constants 42 / 7 days (α = 1 − e^(−1/τ)), TSB = CTL − ATL; seeded with the
   mean TRIMP of the first 14 days with data; days without TRIMP carry the previous value.

| Parameter | Value | Label |
|---|---|---|
| Edwards zones and weights | 50–90% HRmax, 1–5 | Literature (Edwards 1993, *The Heart Rate Monitor Book*; a book, no DOI) |
| HRmax formula | 208 − 0.7 × age | Literature (Tanaka 2001) |
| HRmax = max(formula, observed 99.5th percentile) | 365 days | Design choice |
| CTL / ATL / TSB | 42 / 7 days | Literature (Banister's fitness-fatigue model, Morton 1990; 42 / 7 are the conventional time constants of training software) |
| Saturating 0–21 curve | 1 − e^(−x/K) | Design choice (WHOOP describes strain as a non-linear 0–21 scale without publishing a formula) |
| **K** | **170** | **Personal calibration**: one person's median TRIMP on 20 training days mapped to 12. To be replaced (section 11) |
| Heart-rate hold cap | 5 min | Design choice |
| Coverage rules | 12 h, TRIMP ≥ 40 | Design choice |
| CTL / ATL seed | first 14 days with data | Design choice |

**Known limits**
- Heart rate underestimates strength training (hence muscle load, section 6).
- The whole day counts, so sitting with an elevated heart rate (stress, caffeine, heat) adds a little TRIMP.
- Readings are mostly at rest, so the observed 99.5th percentile is usually below the formula, which therefore nearly always
  wins; Tanaka's formula has an individual error of about ±10 bpm, shifting all five zones.
- Sensitivity (synthetic): K ± 20% → strain changes by about 1 point on average; the zone bounds ± 5% change it by 0.7–1.0.
- K comes from one person. In the synthetic population, calibrating K on each person's own training days gives values from 85
  to 370. Strain is meant to be an absolute scale (the same cardiovascular load gives comparable strain for different people),
  so **K should not be calibrated per person**; the options are a multi-user value or a reference-session definition that
  depends on nobody's data (for example: 60 minutes entirely at 70–80% HRmax = Edwards TRIMP 180, which K = 170 maps to 13.7).

## 6. Muscle load
`muscle.py` (`daily_muscle_load`).

- `muscle_load` = Σ reps × weight (kg) over the day's working sets of strength exercises, as recorded (one dumbbell, per side
  for unilateral exercises); warm-up sets excluded, bodyweight sets count as sets only. A relative measure, comparable for the
  same person over time, **not a true total**.
- With RPE and duration, session RPE = mean RPE × minutes (Foster 2001), kept separate from volume.
- No session record → empty.

| Parameter | Value | Label |
|---|---|---|
| Volume = reps × weight | — | Design choice (common volume measure) |
| Session RPE | mean RPE × minutes | Literature (Foster 2001) |

**Known limits**: volumes of different exercises are not comparable (squat vs lateral raise); muscle load feeds neither
strain nor recovery. Not validated in any way.

## 7. Stress (0–3)
`stress.py` (`calm_samples`, `robust_baseline`, `score_day`, `band`).

1. Only heart-rate readings while awake (not in any sleep segment), outside workouts, with < 20 steps from 5 min before to 2 min
   after (a stand-in for acceleration).
2. Calm baseline = median of such readings over the previous 14 days; spread = 1.4826 × MAD (≥ 3 bpm); no baseline below 50 readings.
3. Per hour with ≥ 3 readings: z = (median heart rate − baseline) / spread; with a daytime HRV reading in that hour,
   z = 0.7 × heart-rate z + 0.3 × HRV z (HRV baseline = mean / SD of night ln HRV over 60 days; below baseline counts as stress).
4. Score = clip(0.5 + z, 0, 3): < 1 low, 1–2 medium, ≥ 2 high; the day = mean of scored hours.

| Parameter | Value | Label |
|---|---|---|
| 1.4826 × MAD | — | Literature (consistency constant of the MAD under normality, Rousseeuw & Croux 1993) |
| Everything else (14 days, 50 readings, 3 bpm, 20 steps, 5 / 2 min, 3 readings an hour, 0.7 / 0.3, offset 0.5, bands 1 / 2) | — | Design choice — v1 value, not yet validated |

**Known limits**: without acceleration, still-but-busy (driving, typing) cannot be told from relaxed; caffeine, heat and
dehydration read as stress; never compared with perceived stress or cortisol, not checked on synthetic data either.

## 8. Body age and pace of aging
`healthspan.py`; parameters with full citations in `healthspan_params.py` (each checked against the paper's tables).
**This library's default is v1.1** (`healthspan_params`); v1.2 (`healthspan_params_v12`, section 8.1) changes only the activity
part. Watch2Whoop runs v1.2 in production since 2026-10-23.

1. Each item is the mean of the last 180 days with the last 30 days weighted × 2 (`weighted_mean`); at least 30 days of data
   (regularity: 30 pairs of adjacent nights; VO₂max: ≥ 2 readings in 180 days, the latest within 120; weight / waist: one within 90 days).
2. Each item → all-cause mortality hazard ratio from a published dose-response curve (`hr_*`; ln HR interpolated linearly
   between knots, flat beyond the ends), **divided by the HR at an anchor (a "typical adult")**.
3. Years = ln(HR) / ln 2 × 8 (`years_from_hr`; Gompertz: adult mortality doubles about every 8 years), each item capped at ±4;
   the sum × 0.6 and capped at ±10 (`combine`). Body age = chronological age + sum.
4. Pace of aging (`smooth`, `pace`): one snapshot a week; Δ smoothed with a 4-week moving average, least squares over the last
   26 weekly points, pace = 1 + dΔ/dt, clipped to −1 … 3; at least 8 weeks.

| Item | Curve (literature) | Anchor (design choice) |
|---|---|---|
| Sleep duration | U-shaped, nadir 7 h, RR 1.06 per hour shorter, 1.13 per hour longer (Yin 2017); watch time converted to self-report first: self-report ≈ 3.365 + 0.567 × measured (Lauderdale 2008) | self-reported 7 h |
| Sleep regularity, SRI (Phillips 2017) | quintile HR 1 / 0.80 / 0.75 / 0.72 / 0.70 (Windred 2024) | 81 (cohort median) |
| Daily steps | quartile HR by age < 60 / ≥ 60 (Paluch 2022) | cohort median of the age group |
| Weekly moderate-to-vigorous minutes | moderate-intensity bracket HRs, vigorous counted × 2 (Lee 2022) | 150 min (guideline minimum) |
| Weekly strength training | J-shaped, lowest 0.83 at 40 min (Momma 2022); 0.92 at 140 min read off the figure (inferred) | 0 |
| VO₂max | RR 0.87 per MET higher (Kodama 2009), at most ±3 MET | same-age same-sex FRIEND median (Kaminsky 2022) |
| Resting heart rate | HR 1.16 per 10 bpm (Jensen 2013), flat below 45 bpm (Zhang 2016) | 65 bpm (Quer 2020) |
| BMI / waist-to-height | Global BMI Mortality Collaboration 2016 / Jayedi 2020 | 25 / 0.50 |
| Years from HR | mortality doubling time 8 years (Kirkwood 2015) | — |

| Combination parameter | Value | Label |
|---|---|---|
| Anchors | table above | Design choice |
| Window, last 30 days × 2, ≥ 30 days | — | Design choice |
| Item ±4 years, sum × 0.6, ±10 years | — | Design choice — v1 value, not yet validated |
| Pace: 26 weeks, 4-week smoothing, −1 … 3 | — | Design choice |

**Known limits**
- Each curve is an **association** from cohort studies, not a causal effect; their populations (mostly middle-aged and older
  adults in Europe and North America), adjustments and exposure measures (mostly questionnaires) differ from watch data.
- Items are correlated (steps, exercise, VO₂max, resting heart rate); the × 0.6 shrinkage is crude, not a multivariable model.
  The total means more than any item.
- Sensitivity is analytic: when nothing is capped, the years are proportional to the doubling time and to the 0.6 factor, so
  ±20% on either moves the gap between body age and chronological age by ±20%.
- No external validation (e.g. against WHOOP Age or epigenetic clocks).
- **Strength training counts only what the caller passes in.** Missing records read as fewer minutes; with the anchor at 0
  minutes this only removes "younger" credit, it never adds years. Watch2Whoop counts its sources per day (the largest per
  day, without a duration 3 minutes per set) and labels an item whose records look incomplete; the curve does not change.

### 8.1 v1.2: activity as one domain (`healthspan_params_v12`, `hs-v1.2-2026-10-23`)
Anything not set in `healthspan_params_v12.py` is v1.1's. Watch2Whoop switched after its maintainer reviewed a v1.1 / v1.2
comparison, with both options at their defaults: strength is not converted into volume, intensity gets no separate credit; the
× 0.6 shrinkage is unchanged.

**Why**
1. v1.1 scored steps, moderate-to-vigorous activity (MVPA) and strength training as three items, each capped at ±4 years, then
   × 0.6. Lee 2022's MVPA already includes walking and weightlifting and steps are mostly walking, so the same activity counted up to three times.
2. The under-60 step curve (Paluch 2022 quartiles) falls from HR 1.00 to 0.59 between 4849 and 7245 steps — its least precise
   segment (CI 0.39–0.88) and the one most open to reverse causation (people who walk little include the ill). The larger
   Ding 2025 meta-analysis is almost flat between 5000 and 10000 steps.
3. Strength training, swimming, cycling and rowing give the wrist few steps, so people who train a lot but walk little lost out on steps.

**Computation (activity only)**
1. Volume = mean wrist steps × wrist factor (1.0) + mean step equivalents of exercise without steps (`step_equivalents`).
   Conversion: minutes × the steps per minute of a walk / run at the same MET (3 MET ≈ 100 steps/min, 6 MET ≈ 130, linear
   between, no more above 6 MET; yoga, pilates, stretching < 3 MET count 0). Only swimming, cycling and rowing machine are
   converted (general-intensity Compendium MET each); walking, running, basketball, tennis, hiking, stairs and elliptical
   already produce steps and are not added again.
2. HR_volume = the step curve (under 60: Ding 2025; 60+: still Paluch 2022), relative to the age group's anchor.
3. HR_activity = HR_volume × HR_strength (Momma 2022's J curve, unchanged) (`hr_activity`). Strength is **not** also converted to steps.
4. Activity years = ln(HR_activity)/ln 2 × 8, capped at ±4 **for the whole domain**; the other items are unchanged, the sum is
   still × 0.6 and capped at ±10.
5. Heart-rate MVPA is a reference only, not counted: its walking is in the steps, its exercise in the conversion. Its
   definition changed too (`moderate_equiv_minutes`): strength, swimming, cycling and rowing count at their MET (moderate × 1,
   ≥ 6 MET × 2, as in questionnaires), everything else by heart-rate zone.

**Why MVPA read low (the v1.1 definition)**: v1.1 counts all-day heart rate ≥ 60% HRmax. Most of a strength session (rests
between sets) sits at 50–60% HRmax, so only a small part of a session counts; and at a given %HRmax resistance exercise uses
less oxygen than running (Collins 1991), so "by heart rate" is neither the absolute intensity questionnaires use nor oxygen
uptake. Apple's exercise minutes are not a fix: the ring starts at about 30% VO₂ reserve, below the 40% moderate threshold
(Abt 2018), so they read high.

| Parameter | Value | Label |
|---|---|---|
| Step curve under 60 | vs 2000 steps: 3000 0.77, 4000 0.64, 5000 0.57, 6000 0.54, 7000 0.53, 8000–10000 0.52, 11000 0.49, 12000 0.45, flat beyond (Ding 2025 Table 2) | Literature |
| Anchor under 60 | 7803 steps (Paluch 2022, median of the under-60 cohorts, as printed) | Literature (its use as anchor: design choice) |
| Step curve and anchor 60+ | unchanged (Paluch 2022, 6167 steps) | Literature / design choice |
| Wrist factor | 1.0: no consistent bias of the Apple Watch vs waist / hip research devices at group level (Breteler 2019 about 4–5% fewer, Spartano 2024 near-identical medians); a raw accelerometer moved to the wrist counts ~2500 more (Tudor-Locke 2015), but that is not Apple's algorithm | Literature |
| Cadence ↔ MET | 100 steps/min ≈ 3 MET, 130 ≈ 6 MET (CADENCE-Adults 2019 / 2020); linear between, flat above 6 MET | Literature / design choice |
| MET per workout type | swimming 5.8 [18240], cycling 7.0 [01014], indoor cycling 6.8 [01200], rowing machine 5.0 [02071], strength 3.5 [02054] (2024 Compendium) | Literature |
| Which types are converted | swimming, cycling, rowing machine | Design choice |
| Strength only on its own curve (`STRENGTH_IN_VOLUME = False`) | Momma 2022's association is the part beyond aerobic activity (all included studies considered aerobic or other activity; GRADE very low); converting it to steps as well would count it twice. Lee 2022's way (weightlifting = moderate activity) is kept as an alternative for comparison | Design choice |
| Volume × strength multiplied | step studies are not adjusted for strength training; the product is an approximation | Design choice |
| Domain cap | ±4 years (v1.1's item cap; activity can no longer reach ±12) | Design choice — v1 value, not yet validated |
| Personal calibration | none | — |

**Synthetic examples** (made-up people, activity only, years before × 0.6)

| Example | v1.1 (steps / MVPA / strength = sum) | v1.2 activity domain |
|---|---|---|
| 25, 6000 steps, MVPA 80 min/week, strength 40 min/week | +4.0 / +0.7 / −2.1 = +2.6 | −1.8 (volume +0.4, strength −2.1) |
| 25, 6000 steps, swims 3 × 45 min a week | +4.0 / +0.1 / 0 = +4.1 | 0.0 (swimming ≈ 2500 steps/day) |
| 25, 3000 steps, no exercise | +4.0 / +2.0 / 0 = +6.0 | +4.0 (+4.5 before the cap) |
| 35, 10500 steps, MVPA 350, strength 120 | −0.8 / −1.2 / −1.2 = −3.2 | −1.6 (volume −0.4, strength −1.2) |
| 70, 5000 steps, MVPA 100 | +1.5 / +0.5 / 0 = +2.0 | +1.5 |

**Known limits of v1.2**
- Very active people get less credit: v1.1's MVPA curve kept rewarding up to 600 min/week, v1.2 no longer rewards intensity
  on its own (Lee 2019 and Saint-Maurice 2020 found little extra association for cadence / intensity once steps are adjusted
  for; del Pozo Cruz 2022 found some). No study was found with MVPA minutes and steps in the same model.
- Swimming / cycling not recorded as a workout is not converted.
- Ding 2025 has no sensitivity analysis excluding early deaths; its curve is flatter than Paluch's, in the direction of Hansen
  2020's and Lee 2019's estimates after excluding early deaths, but that is indirect evidence.
- The ±4-year domain cap, multiplying volume and strength, and converting only three workout types are design choices, not validated.

## 9. Apple and WHOOP, side by side

| | Apple | WHOOP (public material) | This library |
|---|---|---|---|
| HRV metric | HealthKit `heartRateVariabilitySDNN` (SDNN, ms), 1-minute background readings, 1–4 a night | RMSSD (developer API field `hrv_rmssd_milli`), from continuous optical heart rate during sleep (windowing and weighting not formally documented) | Apple's SDNN by default; RMSSD from beat times (`hrv.py`) |
| Recovery / readiness | not in HealthKit | 0–100%, inputs HRV, resting HR, sleep, respiratory rate; three colours | section 4: same inputs, our own weights |
| Strain | none (activity rings, workload estimates) | 0–21, non-linear, cardiovascular load, formula not published | Edwards TRIMP → saturating 0–21 curve, K to be recalibrated |
| Sleep | not in HealthKit | sleep performance = actual / need; need adjusts to strain and debt | Open Wearables sleep score + WHOOP-style need / performance |
| Body age | none | WHOOP Age (approach described, weights not published) | literature curves + Gompertz conversion (section 8) |

**No side-by-side comparison with WHOOP devices has been done**; the WHOOP column comes only from its public documentation.

## 10. Known limits, summarised
1. Real-data checks exist for one person over a little more than a year (two people for the HRV-kind comparison); **no
   multi-user validation, no comparison with WHOOP, Oura, ECG or polysomnography**.
2. Recovery weights, the logistic and the sleep-need baseline are not fitted; strain's K is one person's calibration.
3. HRV readings are sparse (Apple decides); single-reading noise is the largest error source in recovery.
4. Strain and stress have no accelerometer data; steps and workout records stand in.
5. Body-age curves are cohort associations, not individual causal effects.
6. All scores are wellness estimates based on published research, not for diagnosis or treatment decisions.

## 11. Open questions (found during validation, not changed)
Changing any of these changes results, so each needs a before / after comparison reviewed by the maintainer, a new algorithm
version and new golden files (Watch2Whoop issue #118).
1. **Strain K** (section 5): replace the one-person calibration with a multi-user value or a reference-session definition; not per person.
2. **Recovery calibration** (section 4): fit the logistic slope / offset on multi-user data (the weights matter little), or
   calibrate per person; the target (e.g. WHOOP's recovery, if a comparison study happens) has to be chosen first.
3. **Artifact threshold** (section 3): 25% lets mildly premature beats through; 20%, or a successive-difference rule
   (e.g. Lipponen & Tarvainen 2019), did better on synthetic series. Matters once RMSSD is used for recovery.
4. **Export resolution** (section 3): RMSSD from 10 ms beat times is biased upwards; mixing sources in one baseline needs a check.
5. **Sleep-need baseline** (section 2): one 8 h value for everyone.

## 12. References
DOIs checked against Crossref on 2026-10-22 (and 2026-10-24 for Peltola 2012 and Lipponen 2019): title, authors, journal,
year, volume, pages. Body-age sources: see also `healthspan_params.py` and `healthspan_params_v12.py`.

- Task Force of the European Society of Cardiology and the North American Society of Pacing and Electrophysiology. Heart rate variability: standards of measurement, physiological interpretation and clinical use. *Circulation* 1996;93:1043–1065. doi:10.1161/01.CIR.93.5.1043
- Shaffer F, Ginsberg JP. An overview of heart rate variability metrics and norms. *Front Public Health* 2017;5:258. doi:10.3389/fpubh.2017.00258
- Munoz ML et al. Validity of (ultra-)short recordings for heart rate variability measurements. *PLoS One* 2015;10:e0138921. doi:10.1371/journal.pone.0138921
- Hernando D et al. Validation of the Apple Watch for heart rate variability measurements during relax and mental stress in healthy subjects. *Sensors* 2018;18:2619. doi:10.3390/s18082619
- Peltola MA. Role of editing of R–R intervals in the analysis of heart rate variability. *Front Physiol* 2012;3:148. doi:10.3389/fphys.2012.00148
- Lipponen JA, Tarvainen MP. A robust algorithm for heart rate variability time series artefact correction using novel beat classification. *J Med Eng Technol* 2019;43:173–181. doi:10.1080/03091902.2019.1640306
- Kiviniemi AM et al. Endurance training guided individually by daily heart rate variability measurements. *Eur J Appl Physiol* 2007;101:743–751. doi:10.1007/s00421-007-0552-2
- Plews DJ et al. Heart rate variability in elite triathletes, is variation in variability the key to effective training? A case comparison. *Eur J Appl Physiol* 2012;112:3729–3741. doi:10.1007/s00421-012-2354-4
- Plews DJ et al. Training adaptation and heart rate variability in elite endurance athletes: opening the door to effective monitoring. *Sports Med* 2013;43:773–781. doi:10.1007/s40279-013-0071-8
- Buchheit M. Monitoring training status with HR measures: do all roads lead to Rome? *Front Physiol* 2014;5:73. doi:10.3389/fphys.2014.00073
- Radin JM et al. Harnessing wearable device data to improve state-level real-time surveillance of influenza-like illness in the USA. *Lancet Digit Health* 2020;2:e85–e93. doi:10.1016/S2589-7500(19)30222-5
- Mishra T et al. Pre-symptomatic detection of COVID-19 from smartwatch data. *Nat Biomed Eng* 2020;4:1208–1220. doi:10.1038/s41551-020-00640-6
- Miller DJ et al. Analyzing changes in respiratory rate to predict the risk of COVID-19 infection. *PLoS One* 2020;15:e0243693. doi:10.1371/journal.pone.0243693
- Tanaka H, Monahan KD, Seals DR. Age-predicted maximal heart rate revisited. *J Am Coll Cardiol* 2001;37:153–156. doi:10.1016/S0735-1097(00)01054-8
- Edwards S. *The Heart Rate Monitor Book.* Sacramento: Fleet Feet Press; 1993. (book, no DOI)
- Morton RH, Fitz-Clarke JR, Banister EW. Modeling human performance in running. *J Appl Physiol* 1990;69:1171–1177. doi:10.1152/jappl.1990.69.3.1171
- Foster C et al. A new approach to monitoring exercise training. *J Strength Cond Res* 2001;15:109–115. doi:10.1519/00124278-200102000-00019
- Hirshkowitz M et al. National Sleep Foundation's sleep time duration recommendations: methodology and results summary. *Sleep Health* 2015;1:40–43. doi:10.1016/j.sleh.2014.12.010
- Watson NF et al. Recommended amount of sleep for a healthy adult: a joint consensus statement of the AASM and SRS. *J Clin Sleep Med* 2015;11:591–592. doi:10.5664/jcsm.4758
- Fonseca P et al. Validation of photoplethysmography-based sleep staging compared with polysomnography in healthy middle-aged adults. *Sleep* 2017;40:zsx097. doi:10.1093/sleep/zsx097
- Rousseeuw PJ, Croux C. Alternatives to the median absolute deviation. *J Am Stat Assoc* 1993;88:1273–1283. doi:10.1080/01621459.1993.10476408
- Body age: Yin 2017 (doi:10.1161/JAHA.117.005947), Lauderdale 2008 (doi:10.1097/EDE.0b013e318187a7b0), Phillips 2017 (doi:10.1038/s41598-017-03171-4),
  Windred 2024 (doi:10.1093/sleep/zsad253), Paluch 2022 (doi:10.1016/S2468-2667(21)00302-9), Lee 2022 (doi:10.1161/CIRCULATIONAHA.121.058162),
  Momma 2022 (doi:10.1136/bjsports-2021-105061), Kodama 2009 (doi:10.1001/jama.2009.681), Kaminsky 2022 (doi:10.1016/j.mayocp.2021.08.020),
  Jensen 2013 (doi:10.1136/heartjnl-2012-303375), Zhang 2016 (doi:10.1503/cmaj.150535), Quer 2020 (doi:10.1371/journal.pone.0227709),
  Global BMI Mortality Collaboration 2016 (doi:10.1016/S0140-6736(16)30175-1), Jayedi 2020 (doi:10.1136/bmj.m3324), Kirkwood 2015 (doi:10.1098/rstb.2014.0379)
- Body age v1.2: Ding 2025 (doi:10.1016/S2468-2667(25)00164-1), Hansen 2020 (doi:10.1111/sms.13726), Lee IM 2019 (doi:10.1001/jamainternmed.2019.0899),
  Saint-Maurice 2020 (doi:10.1001/jama.2020.1382), del Pozo Cruz 2022 (doi:10.1001/jamainternmed.2022.4000), Tudor-Locke 2015 (doi:10.1249/MSS.0000000000000476),
  Breteler 2019 (doi:10.2196/11489), Spartano 2024 (doi:10.2196/54631), Tudor-Locke 2011 (doi:10.1186/1479-5868-8-79), CADENCE-Adults 2019 (doi:10.1186/s12966-019-0769-6) / 2020 (doi:10.1186/s12966-020-01045-z),
  Herrmann 2024 Compendium (doi:10.1016/j.jshs.2023.10.010), Collins 1991 (doi:10.1249/00005768-199105000-00018), Abt 2018 (doi:10.2196/cardio.8574)
- Ported spec: Open Wearables 0.9.0, `backend/app/algorithms/sleep.py`, `config_algorithms.py` (MIT; notice in [NOTICE](NOTICE))
- Public documentation: WHOOP Developer Platform, "Recovery" (developer.whoop.com/docs/developing/user-data/recovery; fields
  `hrv_rmssd_milli`, `resting_heart_rate`); Apple HealthKit `HKQuantityTypeIdentifier.heartRateVariabilitySDNN`,
  `HKHeartbeatSeriesSample` (developer.apple.com)
