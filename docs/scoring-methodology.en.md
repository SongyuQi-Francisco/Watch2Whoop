# Scoring methodology

> Issue #118. This English text becomes the scoring library's `METHODOLOGY.md` (`packages/openbodyscore/METHODOLOGY.md` once
> #115 / #123 is merged); the Chinese version is [scoring-methodology.md](scoring-methodology.md). Validation plan and results:
> [scoring-validation.md](scoring-validation.md) (Chinese). This describes the code as it is (`ALGO_VERSION = v1.1-2026-10-16`, body age
> `hs-v1.1-2026-10-17`, stress `v1.0-2026-10-17`, HRV artifact filter `rr-v1`). **These are WHOOP-style wellness estimates, not a medical
> diagnosis, and the project is not affiliated with WHOOP.**

## 0. How to read this

### 0.1 Where each parameter comes from
Every parameter carries one of four labels. **We claim only what we can back up**; anything not validated says "v1 value, not yet validated".

| Label | Meaning |
|---|---|
| **Literature** | Taken from a published study or standard (references at the end; DOIs checked against Crossref on 2026-10-22). A citation supports where a number comes from; **it does not mean the score as a whole has been validated** |
| **Ported spec** | Taken over from another open-source implementation (currently only the Open Wearables 0.9.0 Sleep Score, MIT) |
| **Design choice** | Our first value: the direction is supported by evidence or common sense, the number has not been fitted to data |
| **Personal calibration** | Fitted on one person's data (the project owner). Breaks the rule of never hard-coding one person's data; to be replaced (#118, calibration) |

### 0.2 Conventions
- **Dates** are the user's local dates (`users.timezone`). A sleep belongs to the day of (wake time − 4 h), its wake date.
- **Personal baseline**: unless stated otherwise, the values of the 60 days **before** the day (the day itself excluded), per person and per metric.
- **Missing data**: a score that cannot be computed stays empty; nothing is filled with 0 or interpolated. The reason goes into
  `daily_scores.flags`, intermediate values into `details`. No record is not the same as "did not happen".
- **Full recompute**: every run scores from the person's start (first day with a resting heart rate, i.e. first day wearing the watch) to today,
  so results depend only on the data. Any parameter change that affects results bumps `ALGO_VERSION` and recomputes the whole range;
  one person's history never mixes two versions.
- All thresholds live in parameter modules (`scores/params.py`; body age `insights/healthspan_params.py`; stress at the top of
  `insights/stress.py`; HRV artifact filter at the top of `rawdata/hrv.py`) and can be overridden per call.

### 0.3 Inputs
Raw samples an Apple Watch writes to HealthKit (via the iOS app / Open Wearables, the iOS Shortcut, or the full Health export): sleep stage
segments, heart rate, HRV (Apple's SDNN, plus about one minute of beat-to-beat times per reading), respiratory rate, SpO₂, wrist temperature,
Apple's all-day resting heart rate, workouts, steps; weight, waist, VO₂max; strength sessions from SparkyFitness. **Apple's own readiness and sleep
scores are not readable through HealthKit**, so they cannot be compared directly.

---

## 1. Sleep score (0–100)
Code `scores/sleep.py`. **A line-by-line port of the Open Wearables 0.9.0 Sleep Score** (`backend/app/algorithms/sleep.py`, `config_algorithms.py`).

**Inputs**: the night's main sleep (the longest watch sleep per wake date; "asleep" segments without stages from the iPhone or third-party apps are
ignored), its stage segments, and the bedtimes of the latest 14 main sleeps within the previous 15 days.

**Steps**
1. Duration *D* (net sleep hours *h*): 7 ≤ *h* ≤ 9 → 100; *h* < 7 → `100 × (1 + e^(k₁(7−m₁))) / (1 + e^(k₁(h−m₁)))` with k₁ = −1.5, m₁ = 5;
   *h* > 9 → the same form with k₂ = 0.8, m₂ = 11, anchored at 9, floor 50.
2. Stages *St* = 0.5 × min(deep / 90, 1) × 100 + 0.5 × min(REM / 90, 1) × 100.
3. Consistency *C*: difference Δ between tonight's bedtime and the median of the 14 bedtimes (hours past noon, continuous over midnight); late by
   more than 15 min starts losing points, all 100 lost over another 105 min; going to bed early loses at most 20. No main sleep in the previous 15
   days → 0, per the spec.
4. Interruptions *I*: night awake time (excluding awake time before the first and after the last asleep segment) worth 80 points, no deduction
   within 20 min, all lost over another 70 min; awakenings (> 5 min) worth 20 points, fractions `(1, 1, 0.75, 0.5, 0)` for 0, 1, 2, 3, ≥ 4.
   **One difference from Open Wearables**: adjacent awake segments are merged into one awakening.
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
- The stage component depends entirely on the watch's sleep staging. Compared with polysomnography, PPG staging separates wake and sleep with
  high accuracy, but four-class agreement (wake, light, deep, REM) is only moderate (Fonseca 2017: κ ≈ 0.42); a 90-minute deep-sleep target is high
  for older adults.
- Consistency is strict for irregular sleepers (often 0) by design of the spec.
- There is no "in bed" data, so sleep latency is usually empty and not scored.

## 2. Sleep need, sleep debt, sleep performance
Code `scores/sleep.py`.

- Actual sleep = main sleep + naps after the previous main sleep (at most 24 h back) and before this one.
- Sleep debt = weighted mean of (baseline need − actual sleep) over the previous 14 nights, weights 0.75^(k−1) (k = 1 is last night), normalised over
  nights with data; oversleeping offsets, the result is ≥ 0.
- Sleep need = baseline 8 h + min(debt, 2 h) + 3 min per strain point above 7 the previous day (strain 21 → +42 min); none if that strain is missing.
- Sleep performance = min(actual / need, 1); feeds recovery.

| Parameter | Value | Label |
|---|---|---|
| Baseline need | 480 min | Design choice (inside the NSF / AASM 7–9 h range) — v1 value, not calibrated |
| Window / decay | 14 nights, 0.75 | Design choice — v1 value, not yet validated |
| Debt cap | 120 min | Design choice — v1 value, not yet validated |
| Strain add-on | 3 min per point above 7 | Design choice — v1 value, not yet validated |

**Known limits**: everyone gets the same 8 h baseline although sleep need varies a lot between people; WHOOP says it personalises sleep need, we do not yet.

## 3. HRV from beat-to-beat times
Code `rawdata/hrv.py` (`rr-v1`). For each background HRV reading the watch stores about one minute of beat times (`HKHeartbeatSeriesSample`),
1–4 times a night.

1. RRᵢ = tᵢ − tᵢ₋₁ (ms); a beat preceded by a gap (`precededByGap`) or a backwards time is invalid.
2. Artifacts: RR outside 300–2000 ms, or more than 25% away from the series median RR → invalid (ectopic beats, motion).
3. RMSSD = √mean((RRᵢ − RRᵢ₋₁)²) over pairs of adjacent valid intervals, at least 20 pairs; SDNN = population SD of the valid intervals.

| Parameter | Value | Label |
|---|---|---|
| RMSSD / SDNN definitions | — | Literature (Task Force ESC/NASPE 1996) |
| Valid RR range | 300–2000 ms | Design choice (30–200 bpm) |
| Deviation from median | 25% | Design choice (20–30% is common practice; not fitted) |
| Minimum successive differences | 20 | Design choice |

**Check done**: SDNN recomputed from the same beat times correlates about 0.9 with Apple's own SDNN (`docs/raw-data-plan.md` §1), so parsing and
artifact handling have no systematic error. **Not compared with ECG ground truth.** Beat times in `export.xml` have 10 ms resolution, so RMSSD
from the export carries about ±1–2 ms quantisation error; the app reads double-precision seconds.

**Known limits**
- Apple decides when readings happen; third parties cannot trigger them, and no algorithm adds readings.
- Ultra-short windows: against 4–5-minute reference recordings, RMSSD is already valid from 10 s, while SDNN needs at least 30 s or several 10 s
  recordings (Munoz 2015; review in Shaffer 2017). Ultra-short windows favour RMSSD — one reason for #117.

## 4. Recovery (0–100)
Code `scores/recovery.py`, `scores/engine.py`.

**Inputs** (within the main sleep window)
- Night HRV: median of ln of the readings. Currently Apple's SDNN (`HRV_KIND = "sdnn"`); RMSSD from beat times is #117.
- Night resting heart rate: the **lowest 5-minute mean** while asleep. Asleep, the watch records heart rate every 4–5 min, so each reading counts
  until the next (at most 10 min), time-weighted; a window covered less than half does not count; if none qualifies, the lowest single reading.
  Without sleeping heart rate, Apple's all-day resting heart rate is used **against its own baseline** (it runs a few bpm above the sleeping minimum).
- Night respiratory rate: median of the readings. Sleep performance: section 2.

**Steps**
1. Each input against its 60-day baseline: z = (x − mean) / max(SD, floor), clipped to ±3; resting heart rate and respiratory rate sign-flipped.
2. Weighted z̄ = Σ wₖ zₖ / Σ wₖ over the inputs present (**HRV is required**).
3. Recovery = 100 / (1 + e^−(1.6 z̄ + 0.3)), rounded. At baseline 57; z̄ = +0.5 → 75, −0.5 → 38.
4. Zones: ≥ 67 green, 34–66 yellow, < 34 red.
5. No score until the previous 60 days hold 14 nights of HRV.
6. Flags only, not part of the score: **illness warning** (resting heart rate > baseline + 5 bpm and HRV < 0.85 × baseline), wrist temperature
   ≥ 0.6 °C from its 60-day median (≥ 5 nights of baseline), night SpO₂ median < 94% or ≥ 2 readings < 90%.

| Parameter | Value | Label |
|---|---|---|
| Method: z-score of ln HRV (and resting HR) against a rolling personal baseline | — | Literature (common practice in HRV-guided training: Kiviniemi 2007, Plews 2012 / 2013, Buchheit 2014). **Those studies use morning or overnight ln RMSSD**; the current SDNN input does not match them exactly |
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

**Known limits**
- **Only 1–4 HRV readings a night** (WHOOP measures continuously while you sleep). A single reading is noisy: one high SDNN reading can push a
  bad night above 90. On synthetic data, raising single-reading noise from 0.15 to 0.45 (ln scale) lowers the correlation of recovery with the true
  state from 0.82 to 0.70 (validation, section 3.3).
- Weights and logistic parameters are not fitted; score distributions may differ a lot between people.
- The baseline follows the person: slow improvement or decline is absorbed, recovery only says "relative to the last 60 days".
- The illness warning also fires on very low non-illness days in synthetic data (validation, section 3.2); treat it as a hint.

## 5. Strain (0–21) and training load
Code `scores/strain.py`.

1. HRmax = max(208 − 0.7 × age, 99.5th percentile of all heart-rate readings in the previous 365 days); without a birth date, the observed value only.
2. A heart-rate reading counts until the next one, at most 5 min (normally every 2–5 min, about every 5 s in workouts); the last one counts half the cap.
3. Edwards TRIMP = Σ minutes per zone × weight: ≥ 50 / 60 / 70 / 80 / 90% HRmax → weights 1–5, over the **whole local day**, not only workouts.
4. Strain = 21 × (1 − e^(−TRIMP / K)), K = 170.
5. Coverage: fewer than 12 hours with heart rate → scored and flagged "may be low" if a workout was recorded (TRIMP ≥ 40), otherwise empty and kept
   out of CTL / ATL.
6. CTL / ATL: exponentially weighted TRIMP with time constants 42 / 7 days (α = 1 − e^(−1/τ)), TSB = CTL − ATL; seeded with the mean TRIMP of the first
   14 days with data; days without TRIMP carry the previous value.

| Parameter | Value | Label |
|---|---|---|
| Edwards zones and weights | 50–90% HRmax, 1–5 | Literature (Edwards 1993, *The Heart Rate Monitor Book*; a book, no DOI) |
| HRmax formula | 208 − 0.7 × age | Literature (Tanaka 2001) |
| HRmax = max(formula, observed 99.5th percentile) | 365 days | Design choice |
| CTL / ATL / TSB | 42 / 7 days | Literature (Banister's fitness-fatigue model, Morton 1990; 42 / 7 are the conventional time constants of training software) |
| Saturating 0–21 curve | 1 − e^(−x/K) | Design choice (WHOOP describes strain as a non-linear 0–21 scale without publishing a formula) |
| **K** | **170** | **Personal calibration**: the owner's median TRIMP on 20 training days mapped to 12. To be replaced |
| Heart-rate hold cap | 5 min | Design choice |
| Coverage rules | 12 h, TRIMP ≥ 40 | Design choice |
| CTL / ATL seed | first 14 days with data | Design choice |

**Known limits**
- Heart rate underestimates strength training (hence muscle load, section 6).
- The whole day counts, so sitting with an elevated heart rate (stress, caffeine, heat) adds a little TRIMP.
- Readings are mostly at rest, so the observed 99.5th percentile is usually below the formula, which therefore nearly always wins; Tanaka's formula
  has an individual error of about ±10 bpm, shifting all five zones.
- K comes from one person; distributions differ between people and sports. Sensitivity analysis: K ± 20% → strain changes by about 1 point on average.

## 6. Muscle load
Code `scores/muscle.py`.

- `muscle_load` = Σ reps × weight (kg) over the day's working sets of strength exercises, as recorded (one dumbbell, per side for unilateral
  exercises); warm-up sets excluded, bodyweight sets count as sets only. A relative measure, comparable for the same person over time, **not a true total**.
- With RPE and duration, session RPE = mean RPE × minutes (Foster 2001), kept separate from volume.
- No session record → empty.

| Parameter | Value | Label |
|---|---|---|
| Volume = reps × weight | — | Design choice (common volume measure) |
| Session RPE | mean RPE × minutes | Literature (Foster 2001) |

**Known limits**: volumes of different exercises are not comparable (squat vs lateral raise); muscle load feeds neither strain nor recovery.

## 7. Stress (0–3)
Code `insights/stress.py`.

1. Only heart-rate readings while awake (not in any sleep segment), outside workouts, with < 20 steps from 5 min before to 2 min after (a stand-in for acceleration).
2. Calm baseline = median of such readings over the previous 14 days; spread = 1.4826 × MAD (≥ 3 bpm); no baseline below 50 readings.
3. Per hour with ≥ 3 readings: z = (median heart rate − baseline) / spread; with a daytime HRV reading in that hour, z = 0.7 × heart-rate z + 0.3 × HRV z
   (HRV baseline = mean / SD of night ln HRV over 60 days; below baseline counts as stress).
4. Score = clip(0.5 + z, 0, 3): < 1 low, 1–2 medium, ≥ 2 high; the day = mean of scored hours.

| Parameter | Value | Label |
|---|---|---|
| 1.4826 × MAD | — | Literature (consistency constant of the MAD under normality, Rousseeuw & Croux 1993) |
| Everything else (14 days, 50 readings, 3 bpm, 20 steps, 5 / 2 min, 3 readings an hour, 0.7 / 0.3, offset 0.5, bands 1 / 2) | — | Design choice — v1 value, not yet validated |

**Known limits**: without acceleration, still-but-busy (driving, typing) cannot be told from relaxed; caffeine, heat and dehydration read as stress;
never compared with perceived stress or cortisol.

## 8. Body age and pace of aging
Code `insights/healthspan.py`; parameters with full citations in `insights/healthspan_params.py` (each checked against the paper's tables, 2026-10-17).

1. Each item is the mean of the last 180 days with the last 30 days weighted × 2; at least 30 days of data (regularity: 30 pairs of adjacent nights;
   VO₂max: ≥ 2 readings in 180 days, the latest within 120; weight / waist: one within 90 days).
2. Each item → all-cause mortality hazard ratio from a published dose-response curve (ln HR interpolated linearly between knots, flat beyond the
   ends), **divided by the HR at an anchor (a "typical adult")**.
3. Years = ln(HR) / ln 2 × 8 (Gompertz: adult mortality doubles about every 8 years), each item capped at ±4; the sum × 0.6 and capped at ±10.
   Body age = chronological age + sum.
4. Pace of aging: one snapshot a week; Δ smoothed with a 4-week moving average, least squares over the last 26 weekly points,
   pace = 1 + dΔ/dt, clipped to −1 … 3; at least 8 weeks.

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
- Each curve is an **association** from cohort studies, not a causal effect; their populations (mostly middle-aged and older adults in Europe and
  North America), adjustments and exposure measures (mostly questionnaires) differ from watch data.
- Items are correlated (steps, exercise, VO₂max, resting heart rate); the × 0.6 shrinkage is crude, not a multivariable model. The total means more than any item.
- Sensitivity is analytic: when nothing is capped, the years are proportional to the doubling time and to the 0.6 factor, so ±20% on either moves
  the gap between body age and chronological age by ±20%.
- No external validation (e.g. against WHOOP Age or epigenetic clocks).

## 9. Features derived from these scores (brief)
- **Strain target** (`insights/coach.py`): green 14–18, yellow 10–14, red 0–10; yesterday's TSB < −30 shifts down by 2; illness warning 0–8. Ranges fit
  strain with K = 170; design choice.
- **Sleep coach**: tonight's need = section 2 plus a latency buffer (recent median or 15 min).
- **Health monitor**: "60-day mean ± 2 SD" ranges for resting heart rate, HRV (ln scale), respiration, SpO₂, wrist temperature, ≥ 14 nights; design choice.
- **Heart-rate recovery HRR60** (`cardio/hrr.py`): peak in the last 2 min of a workout − heart rate 60 s after; valid only if the peak ≥ 75% HRmax; design choice.

## 10. Apple and WHOOP, side by side

| | Apple | WHOOP (public material) | This library |
|---|---|---|---|
| HRV metric | HealthKit `heartRateVariabilitySDNN` (SDNN, ms), 1-minute background readings, 1–4 a night | RMSSD (developer API field `hrv_rmssd_milli`), from continuous optical heart rate during sleep (windowing and weighting not formally documented) | Apple's SDNN now; RMSSD from beat times available (#117) |
| Recovery / readiness | not in HealthKit | 0–100%, inputs HRV, resting HR, sleep, respiratory rate; three colours | section 4: same inputs, our own weights |
| Strain | none (activity rings, workload estimates) | 0–21, non-linear, cardiovascular load, formula not published | Edwards TRIMP → saturating 0–21 curve, K to be recalibrated |
| Sleep | not in HealthKit | sleep performance = actual / need; need adjusts to strain and debt | Open Wearables sleep score + WHOOP-style need / performance |
| Body age | none | WHOOP Age (approach described, weights not published) | literature curves + Gompertz conversion (section 8) |

**No side-by-side comparison with WHOOP devices has been done**; the WHOOP column comes only from its public documentation.

## 11. Known limits, summarised
1. Back-calculation and spot checks exist for one person over a little more than a year; **no multi-user validation, no comparison with WHOOP,
   Oura or ECG** (plan in the validation document).
2. Recovery weights, the logistic and the sleep-need baseline are not fitted; strain's K is one person's calibration.
3. HRV readings are sparse (Apple decides); single-reading noise is the largest error source in recovery.
4. Strain and stress have no accelerometer data; steps and workout records stand in.
5. Body-age curves are cohort associations, not individual causal effects.
6. All scores are wellness estimates, not for diagnosis or treatment decisions.

## 12. References
DOIs checked against Crossref on 2026-10-22 (title, authors, journal, year, volume, pages). Body-age sources: see also `insights/healthspan_params.py`.

- Task Force of the European Society of Cardiology and the North American Society of Pacing and Electrophysiology. Heart rate variability: standards of measurement, physiological interpretation and clinical use. *Circulation* 1996;93:1043–1065. doi:10.1161/01.CIR.93.5.1043
- Shaffer F, Ginsberg JP. An overview of heart rate variability metrics and norms. *Front Public Health* 2017;5:258. doi:10.3389/fpubh.2017.00258
- Munoz ML et al. Validity of (ultra-)short recordings for heart rate variability measurements. *PLoS One* 2015;10:e0138921. doi:10.1371/journal.pone.0138921
- Hernando D et al. Validation of the Apple Watch for heart rate variability measurements during relax and mental stress in healthy subjects. *Sensors* 2018;18:2619. doi:10.3390/s18082619
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
- Ported spec: Open Wearables 0.9.0, `backend/app/algorithms/sleep.py`, `config_algorithms.py` (MIT; `vendor/open-wearables` in this repository)
- Public documentation: WHOOP Developer Platform, "Recovery" (developer.whoop.com/docs/developing/user-data/recovery; fields `hrv_rmssd_milli`,
  `resting_heart_rate`); Apple HealthKit `HKQuantityTypeIdentifier.heartRateVariabilitySDNN`, `HKHeartbeatSeriesSample` (developer.apple.com)
