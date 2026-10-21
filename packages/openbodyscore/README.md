# openbodyscore

Recovery, sleep, strain, stress and body-age scores from wearable data (heart rate, HRV, sleep stages, steps,
workout logs). Pure Python functions, standard library only, every parameter documented with its source and
overridable per call. Licence: [Apache-2.0](LICENSE).

> **Not affiliated with WHOOP.** This project is not affiliated with, endorsed by or derived from WHOOP, Inc. or any
> other wearable maker; WHOOP is a trademark of WHOOP, Inc. The scores are independent re-implementations from public
> literature and open-source code (see [Methodology](#methodology)). **Not a medical device**: the numbers are for
> personal insight, not diagnosis.

Status: extracted from [Watch2Whoop](../../README.md)'s Body Engine (issue #115), where it scores real users' data
every day. Not published on PyPI yet and the name is a working name; the API may still change. The plan is in
[`docs/oss-extraction-plan.md`](../../docs/oss-extraction-plan.md).

## Install

From a checkout (Python ≥ 3.12, no third-party dependencies):

```sh
pip install ./packages/openbodyscore          # or: uv pip install ./packages/openbodyscore
cd packages/openbodyscore && uv run pytest     # the package's own tests
```

## Minimal example

A night of heart-rate and HRV readings in, a recovery score and what it was built from out:

```python
import math
from datetime import datetime, timedelta, timezone

from openbodyscore import params, recovery

tz = timezone(timedelta(hours=10))
bed, wake = datetime(2026, 8, 1, 23, 0, tzinfo=tz), datetime(2026, 8, 2, 7, 0, tzinfo=tz)
hr = [(bed + timedelta(minutes=5 * i), 55.0 + i % 7) for i in range(96)]       # (time, bpm)
hrv = [(bed + timedelta(hours=h), 48.0) for h in (1, 3, 5)]                     # (time, SDNN ms)

hrv_ln, n = recovery.night_hrv_ln(hrv, bed, wake)          # median ln HRV of the night
rhr = recovery.lowest_window_mean(hr, bed, wake)            # lowest 5-minute mean heart rate
history = [math.log(42 + i % 5) for i in range(30)]         # earlier nights' ln HRV (your personal baseline)
mean, sd = recovery.baseline(history)
z = {"hrv": recovery.zscore(hrv_ln, mean, sd, params.SD_FLOOR["hrv"])}
score, weighted_z = recovery.recovery_score(z)              # 0–100, and the weighted z it came from
print(score, round(weighted_z, 2), rhr)                     # -> 96 1.75 55.0

# the same with one parameter changed for this call only
score_flat, _ = recovery.recovery_score(z, params=params.override(RECOVERY_SLOPE=1.0))
```

The other scores follow the same pattern: `sleep.sleep_score(...)`, `strain.edwards_trimp(...)` + `strain.strain(...)`,
`stress.score_day(...)`, `healthspan.hr_*(...)` + `healthspan.years_from_hr(...)`, `hrv.analyze(beat_times)`.
Each function's docstring states its inputs and units; datetimes must be time-zone aware and in the user's local zone
where a function looks at clock time (bedtime, hour of day).

## Methodology

Parameters and their sources live in [`params.py`](src/openbodyscore/params.py) (sleep, recovery, strain),
[`healthspan_params.py`](src/openbodyscore/healthspan_params.py) (body age), and at the top of
[`stress.py`](src/openbodyscore/stress.py) and [`hrv.py`](src/openbodyscore/hrv.py). Values marked as first-version
design choices are not fitted to population data and are meant to be calibrated.

| Score | How it is computed | Main sources |
|---|---|---|
| **Sleep score** 0–100 | duration 40% (full marks 7–9 h, sigmoid penalties outside), stages 20% (deep and REM vs 90-minute targets), consistency 20% (bedtime vs the median of the last 14 nights), interruptions 20% (wake time after sleep onset and wake-ups > 5 min) | Port of the Open Wearables 0.9.0 Sleep Score (MIT, see [NOTICE](NOTICE)) |
| **Sleep need / debt** | 8 h baseline + sleep debt (decay-weighted shortfall of the last 14 nights, capped at 2 h) + 3 min per strain point above 7 the day before; performance = actual / need | first-version design choice |
| **Recovery** 0–100 | z-scores of ln HRV (weight 0.60), resting heart rate (0.20, lower is better), sleep performance (0.15) and respiratory rate (0.05) against a 60-day personal baseline (≥ 14 nights), clipped at ±3, mapped by a logistic `100 / (1 + e^-(1.6 z + 0.3))`; flags for possible illness (RHR > baseline + 5 and HRV < 85%), wrist temperature (±0.6 °C) and SpO₂ (median < 94% or ≥ 2 readings < 90%) | first-version design choice; resting HR as the lowest 5-minute mean during sleep |
| **Strain** 0–21 | Edwards TRIMP (minutes in 50/60/70/80/90% HRmax zones × weights 1–5) → `21 × (1 − e^(−TRIMP / 170))`; HRmax = max(observed 99.5th percentile, 208 − 0.7 × age) | Edwards 1993; Tanaka et al. 2001 |
| **Training load** | CTL / ATL = 42 / 7-day exponentially weighted TRIMP, TSB = CTL − ATL | Banister impulse-response model |
| **Muscle load** | volume (reps × kg) of working sets of strength exercises, session RPE × minutes kept separately | Foster session-RPE |
| **Stress** 0–3 | heart rate while awake, not in a workout and not walking, per hour vs a robust 14-day calm baseline (median, 1.4826 × MAD); daytime HRV mixed in at 30%; score = clip(0.5 + z, 0, 3), bands low / medium (≥ 1) / high (≥ 2) | Rousseeuw & Croux 1993 for the MAD constant; rest first-version design choice |
| **Body age, pace of aging** | each metric (sleep duration and regularity, steps, MVPA, strength training, VO₂max, resting HR, BMI / waist-to-height) → all-cause mortality hazard ratio from published dose-response curves, relative to a typical adult → years via the Gompertz 8-year doubling time, capped and shrunk for correlation; pace = slope of body age over 26 weeks | Yin 2017, Windred 2024, Paluch 2022, Lee 2022, Momma 2022, Kodama 2009, Kaminsky 2022 (FRIEND), Jensen 2013, Global BMI Mortality Collaboration 2016, Jayedi 2020, Kirkwood 2015 — full citations in `healthspan_params.py` |
| **HRV from beat times** | RR intervals with gap, range (300–2000 ms) and ±25%-of-median filters; RMSSD needs ≥ 20 successive differences | Task Force of the ESC / NASPE 1996 |

Each score module has an `ALGO_VERSION` (`params.ALGO_VERSION`, `healthspan_params.ALGO_VERSION`, `hrv.ALGO`,
`stress.STRESS_VERSION`) that changes whenever a result would change; store it with your scores.

## Tests

`tests/test_golden.py` runs every public function on fixed synthetic data (`tests/golden_cases.py`, a made-up person,
60 days) and compares with `tests/golden/scores.json`, which was generated from the code before it was extracted:
any change of a number fails the test. An intentional change bumps the algorithm version and regenerates the file.

## Licence

Apache-2.0, see [LICENSE](LICENSE). The sleep score is ported from Open Wearables (MIT); its licence notice is in
[NOTICE](NOTICE). The rest of the Watch2Whoop repository stays under AGPL-3.0.
