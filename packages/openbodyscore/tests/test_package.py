"""The package stands alone and parameters can be overridden per call."""
import importlib
import math
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from openbodyscore import healthspan, healthspan_params, hrv, params, recovery, sleep, strain, stress

TZ = timezone(timedelta(hours=10))


def test_no_heavy_dependencies():
    """Importing the package loads no database, web or Body Engine module (checked in a fresh interpreter)."""
    code = ("import sys, openbodyscore; bad = [m for m in sys.modules if m.split('.')[0] in "
            "('psycopg', 'fastapi', 'body_engine', 'pydantic', 'starlette')]; print(bad); sys.exit(bool(bad))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_module_imports():
    for name in ("healthspan", "healthspan_params", "hrv", "muscle", "params", "recovery", "sleep", "strain", "stress"):
        importlib.import_module(f"openbodyscore.{name}")


def test_override_defaults_unchanged_and_applied():
    z = {"hrv": 0.5, "rhr": -0.2}
    assert recovery.recovery_score(z) == recovery.recovery_score(z, params=params.override())
    steeper = recovery.recovery_score(z, params=params.override(RECOVERY_SLOPE=3.0))
    assert steeper[0] > recovery.recovery_score(z)[0] and steeper[1] == recovery.recovery_score(z)[1]
    assert params.RECOVERY_SLOPE == 1.6   # the module itself is untouched


def test_override_reaches_nested_calls():
    bed = datetime(2026, 8, 1, 23, 0, tzinfo=TZ)
    base = sleep.sleep_score(420, 60, 60, bed, [bed], 10, [])
    p = params.override(DEEP_TARGET_MIN=60.0, REM_TARGET_MIN=60.0)
    assert base["stages"] == 66 and sleep.sleep_score(420, 60, 60, bed, [bed], 10, [], params=p)["stages"] == 100
    samples = [(bed + timedelta(minutes=i), 150.0) for i in range(30)]
    minutes = strain.sample_minutes([t for t, _ in samples])
    assert strain.edwards_trimp(samples, minutes, 190, params=params.override(EDWARDS_ZONES=((0.5, 1),)))[0] < \
        strain.edwards_trimp(samples, minutes, 190)[0]
    assert strain.sample_minutes([bed, bed + timedelta(hours=1)], params=params.override(
        HR_SAMPLE_CAP=timedelta(minutes=10))) == [10.0, 5.0]
    assert healthspan.hr_rhr(75, params=healthspan_params.override(RHR_ANCHOR=75.0)) == 1.0
    assert hrv.analyze([i * 0.8 for i in range(40)], params=hrv.override(MIN_VALID_DIFFS=50)).rmssd is None
    assert stress.band(1.5, params=stress.override(BANDS=((1.2, "high"), (0.0, "low")))) == "high"


def test_override_rejects_unknown_names():
    with pytest.raises(KeyError, match="RECOVERY_SLOPPE"):
        params.override(RECOVERY_SLOPPE=1.0)


def test_minimal_example_from_readme():
    """The README's example: a night of heart rate and HRV in, a recovery score and its parts out."""
    night = datetime(2026, 8, 1, 23, 0, tzinfo=TZ)
    hr = [(night + timedelta(minutes=5 * i), 55.0 + (i % 7)) for i in range(90)]
    hrv_ms = [(night + timedelta(hours=h), 48.0) for h in (1, 3, 5)]
    ln, n = recovery.night_hrv_ln(hrv_ms, night, night + timedelta(hours=8))
    rhr = recovery.lowest_window_mean(hr, night, night + timedelta(hours=8))
    hist_ln = [math.log(42 + i % 5) for i in range(30)]
    mean, sd = recovery.baseline(hist_ln)
    score, weighted = recovery.recovery_score({"hrv": recovery.zscore(ln, mean, sd, params.SD_FLOOR["hrv"])})
    assert n == 3 and rhr == 55.0 and 0 <= score <= 100 and weighted > 0
    assert strain.age_on(date(1990, 1, 1), date(2026, 1, 1)) == 36


def test_readme_example_runs_and_prints_what_it_says(capsys):
    import re
    from pathlib import Path
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    code = re.search(r"```python\n(.*?)```", readme, re.S).group(1)
    exec(compile(code, "README.md", "exec"), {})
    expected = re.search(r"# -> (.+)", code).group(1).strip()
    assert capsys.readouterr().out.strip() == expected
