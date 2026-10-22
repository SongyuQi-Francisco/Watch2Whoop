# Methodology (skeleton)

> **Status: skeleton.** This file lists every score and labels where each parameter comes from. The full text (inputs,
> formulas, every parameter, known limits) and the validation studies are being written in Watch2Whoop issue #118
> and must be finished before any public release. Only sources already cited in the code are named here; anything
> not validated says so. Nothing here has been compared with a WHOOP device or validated on more than one user yet.

Parameter categories:
- **Literature**: a value taken from a published study (citation in the code, with DOI where the code has one).
- **Ported spec**: taken over from another open-source implementation's specification.
- **Design choice**: chosen by us; a reasonable first value, not fitted to data.
- **Personal calibration**: fitted on one person's data (the project owner). To be replaced (issue #118).

"v1 value, not yet validated" marks every design choice and personal calibration below.

## Sleep score (`sleep.py`, parameters in `params.py`)
- **Ported spec**: Open Wearables 0.9.0 Sleep Score (`backend/app/algorithms/sleep.py`, `config_algorithms.py`, MIT;
  notice in [NOTICE](NOTICE)): weights duration 40% / stages 20% / consistency 20% / interruptions 20%, the duration
  sigmoids, deep and REM targets, consistency grace and penalty windows, interruption points. Not independently validated.
- **Design choice**: one difference from Open Wearables — adjacent awake segments are merged into one wake-up (`waso`).
- Known limits: _to be written (#118)._

## Sleep need, sleep debt, sleep performance (`sleep.py`)
- **Design choice** — v1 value, not yet validated: 8 h baseline need, 14-night decay-weighted debt (0.75 per night),
  debt capped at 2 h, +3 min per strain point above 7 the day before.
- Known limits: _to be written (#118)._

## Recovery (`recovery.py`)
- Method: z-scores of nightly ln HRV and resting heart rate against a 60-day personal baseline (a common approach in
  the HRV-guided training literature; specific citations to be added in #118 only after they are verified).
- **Design choice** — v1 value, not yet validated: weights HRV 0.60 / resting HR 0.20 / sleep performance 0.15 /
  respiratory rate 0.05; logistic slope 1.6 and offset 0.3; baseline length and minimum nights; SD floors; z clip ±3;
  illness flag thresholds (RHR > baseline + 5 and HRV < 85%); wrist temperature ±0.6 °C; SpO₂ flags (median < 94%,
  ≥ 2 readings < 90%); resting HR as the lowest 5-minute mean during sleep.
- HRV input: Apple's SDNN by default (`HRV_KIND = "sdnn"`). Watch2Whoop picks SDNN or RMSSD per user (issue #117); the
  thresholds for "enough beat-to-beat data" (`RMSSD_COVERAGE_DAYS`, `RMSSD_MIN_NIGHTS`, `RMSSD_MAX_GAP_DAYS`,
  `RMSSD_NIGHT_HOURS`) are a **design choice** — v1 value, not yet validated. One kind per person, never mixed.
- Known limits: _to be written (#118)._

## HRV from beat-to-beat times (`hrv.py`)
- **Literature**: RMSSD / SDNN definitions and artifact handling follow the Task Force of the ESC / NASPE (1996).
- **Design choice** — v1 value, not yet validated: RR range 300–2000 ms, ±25% of the series median, ≥ 20 successive
  differences for RMSSD.
- Check done so far: SDNN recomputed this way correlates with Apple's own SDNN (r = 0.90 on 1,928 readings of one
  person, Watch2Whoop `docs/raw-data-plan.md` §1).

## Strain and training load (`strain.py`)
- **Literature**: Edwards TRIMP (five heart-rate zones at 50/60/70/80/90% HRmax, weights 1–5); HRmax formula of Tanaka
  (208 − 0.7 × age); CTL / ATL / TSB after Banister's fitness-fatigue model (42 / 7-day time constants).
- **Design choice** — v1 value, not yet validated: HRmax = max(formula, observed 99.5th percentile over 365 days);
  heart-rate sample hold of 5 min; coverage rules (12 h of heart rate, TRIMP ≥ 40); 14-day seed for CTL / ATL.
- **Personal calibration**: K = 170 in `strain = 21 × (1 − e^(−TRIMP / K))`, calibrated on a single person's training days (one maintainer's).
  Does not meet the project rule of never hard-coding one person's data; to be replaced (#118).
- Known limits: _to be written (#118)._

## Muscle load (`muscle.py`)
- **Design choice**: training volume = reps × kg of working strength sets (a relative measure).
- Session RPE load (average RPE × minutes) follows Foster's session-RPE method, kept separate from volume.
- Known limits: _to be written (#118)._

## Stress (`stress.py`)
- **Design choice** — v1 value, not yet validated: calm baseline = median of awake, still heart rate over 14 days with
  spread 1.4826 × MAD (floor 3 bpm); still = no workout and < 20 steps from 5 min before to 2 min after; ≥ 3 readings
  per hour; heart rate 0.7 / HRV 0.3; score = clip(0.5 + z, 0, 3); bands low / medium (≥ 1) / high (≥ 2).
- Known limits: _to be written (#118)._

## Body age and pace of aging (`healthspan.py`, parameters and full citations in `healthspan_params.py`)
- **Literature** (dose-response curves of all-cause mortality, each with its DOI in `healthspan_params.py`):
  sleep duration — Yin et al., J Am Heart Assoc 2017; self-report conversion — Lauderdale et al., Epidemiology 2008;
  sleep regularity — Windred et al., Sleep 2024; steps — Paluch et al., Lancet Public Health 2022; moderate-to-vigorous
  activity — Lee et al., Circulation 2022; strength training — Momma et al., Br J Sports Med 2022; VO₂max — Kodama et
  al., JAMA 2009, norms from FRIEND (Kaminsky et al., Mayo Clin Proc 2022); resting heart rate — Jensen et al., Heart
  2013; BMI — Global BMI Mortality Collaboration, Lancet 2016; waist-to-height ratio — Jayedi et al., BMJ 2020;
  years from hazard ratio — Gompertz doubling time of about 8 years (Kirkwood, Philos Trans R Soc B 2015).
- **Design choice** — v1 value, not yet validated: anchors ("typical adult") per item; 180-day window with the last 30
  days weighted ×2 and ≥ 30 days of data; item cap ±4 years; shrinkage × 0.6 for correlated items; total cap ±10
  years; pace of aging over 26 weeks with a 4-week moving average.
- Known limits (more to be written, #118):
  - The items take whatever the caller measured or recorded. Strength minutes in particular are only the logged
    sessions: missing records read as fewer minutes. With the anchor at 0 minutes this only removes "younger" credit,
    it never adds years. Watch2Whoop counts its sources per day and labels an item whose records look incomplete
    (issue #210); the curve and the conversion here do not change.

## Validation done so far
- One person's 415-day back-calculation, spot-checked against their diary (fatigue periods, low-blood-sugar days).
- `hrv-compare` correlation (SDNN vs RMSSD, one person).
- **Not done**: comparison with WHOOP devices, multi-user validation, sensitivity analysis. Plan: issue #118.
