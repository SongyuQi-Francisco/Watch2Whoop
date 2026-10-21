# openbodyscore

Open-source, evidence-based WHOOP-style scores — not affiliated with WHOOP.

Recovery, sleep, strain, stress and body-age scores from wearable data (heart rate, HRV, sleep stages, steps,
workout logs). Pure Python functions, standard library only, every parameter documented with its source and
overridable per call. Licence: [Apache-2.0](LICENSE).

> **Not affiliated with WHOOP.** This project is not affiliated with, endorsed by or derived from WHOOP, Inc. or any
> other wearable maker; WHOOP is a trademark of WHOOP, Inc. "WHOOP-style" only describes the kind of score; the scores are
> independent implementations from public literature, open-source code and our own design choices (see [Methodology](#methodology)). **Not a medical device**: the numbers are for
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

Every score, its inputs and where each parameter comes from — **literature**, **ported spec**, **design choice** or
**personal calibration** — are listed in [METHODOLOGY.md](METHODOLOGY.md) (a skeleton for now; the full text and the
validation studies are Watch2Whoop issue #118). In short: the sleep score is ported from Open Wearables 0.9.0, strain
uses Edwards TRIMP and the Tanaka HRmax formula, HRV from beat times follows the ESC / NASPE Task Force (1996), and body
age uses published dose-response curves (DOIs in [`healthspan_params.py`](src/openbodyscore/healthspan_params.py)).
The recovery and stress weights, the sleep-need baseline and several thresholds are **v1 values, not yet validated**,
and the strain constant K = 170 was calibrated on one person's data. Nothing has been compared with a WHOOP device yet.

Parameters live in [`params.py`](src/openbodyscore/params.py), [`healthspan_params.py`](src/openbodyscore/healthspan_params.py)
and at the top of [`stress.py`](src/openbodyscore/stress.py) and [`hrv.py`](src/openbodyscore/hrv.py). Each score module
has an algorithm version (`params.ALGO_VERSION`, `healthspan_params.ALGO_VERSION`, `hrv.ALGO`, `stress.STRESS_VERSION`)
that changes whenever a result would change; store it with your scores.

## Tests

`tests/test_golden.py` runs every public function on fixed synthetic data (`tests/golden_cases.py`, a made-up person,
60 days) and compares with `tests/golden/scores.json`, which was generated from the code before it was extracted:
any change of a number fails the test. An intentional change bumps the algorithm version and regenerates the file.

## Licence

Apache-2.0, see [LICENSE](LICENSE). The sleep score is ported from Open Wearables (MIT); its licence notice is in
[NOTICE](NOTICE). The rest of the Watch2Whoop repository stays under AGPL-3.0.
