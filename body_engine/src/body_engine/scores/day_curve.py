"""The strain curve of one day: cumulative strain at the end of every local hour (Today L1 strain sheet, 2.0.1).

The server's scoring run (engine.compute) stores it in daily_scores.details.load.hourly_strain for every day that has a
strain, after score_days (which stays the JS bundle's twin: tests/test_js_golden.py). It reuses the strain algorithm
itself: the same raw heart-rate samples, the same minutes per sample (strain.sample_minutes over the whole timeline), the
same Edwards zones of the day's stored HRmax. The cumulative TRIMP gives the curve's shape; the curve is then mapped
through the day's own strain, so its last point is exactly the stored strain of the day (`strain` column):

    value[h] = strain(T × trimp_until_end_of_hour_h / trimp_of_the_day),   T = strain⁻¹(the day's strain)

Days without raw heart rate have no strain and get no curve (nothing is made up). Rows the iOS app computed itself
(origin = app, nativesync/appscores.py) carry the app's details, so they have no curve either until the server scores
the day again. Index h = the hour [h:00, h+1:00) in the user's time zone; a day in progress ends at the current hour.
"""
import math
from datetime import date, datetime

from . import strain as L
from . import params as P

HOURS = 24


def cumulative_trimp(samples: list[tuple[datetime, float]], minutes: list[float], hr_max: float, tz) -> list[float]:
    """TRIMP accumulated up to the end of each local hour 0–23 of one day's samples (time order). The zones add up in
    the same order as strain.edwards_trimp, so the last value is that day's TRIMP for the same samples and HRmax."""
    zones = [0.0] * 5
    out: list[float] = []
    total = lambda: sum(m * (i + 1) for i, m in enumerate(zones))   # noqa: E731  edwards_trimp's own sum
    for (t, hr), m in zip(samples, minutes):
        hour = t.astimezone(tz).hour
        while len(out) < hour:   # hours before this sample are closed (a repeated DST hour stays in its bucket)
            out.append(total())
        w = L.zone_weight(hr, hr_max)
        if w:
            zones[w - 1] += m
    while len(out) < HOURS:
        out.append(total())
    return out


def inverse_strain(value: float) -> float:
    """The TRIMP whose strain is `value` (strain = MAX × (1 − e^(−TRIMP / K)))."""
    frac = min(value, P.STRAIN_MAX - 1e-9) / P.STRAIN_MAX
    return -P.STRAIN_K * math.log(1 - frac)


def hourly_strain(cum: list[float], day_strain: float, hours: int = HOURS) -> list[float]:
    """The curve from cumulative_trimp's output: the first `hours` values, the last of them the day's strain exactly."""
    if not cum or hours <= 0:
        return []
    hours = min(hours, len(cum))
    total = cum[-1]
    if total <= 0:
        out = [0.0] * hours
    else:
        t = inverse_strain(day_strain)
        out = [round(L.strain(t * c / total), 2) for c in cum[:hours]]
    out[-1] = day_strain
    return out


def attach(rows: list[dict], hr: list[tuple[datetime, float]], tz, now: datetime) -> None:
    """Add details.load.hourly_strain to every row with a strain (in place). hr: the scoring inputs' raw heart rate
    (Inputs.hr, time order); now: the user's local now (today's curve ends at the current hour)."""
    want = {r["date"]: r for r in rows if r.get("strain") is not None and r.get("hr_max")}
    if not want or not hr:
        return
    times = [t for t, _ in hr]
    minutes = L.sample_minutes(times)
    by_day: dict[date, list[int]] = {}
    for i, t in enumerate(times):
        d = t.astimezone(tz).date()
        if d in want:
            by_day.setdefault(d, []).append(i)
    today = now.date()
    for d, row in want.items():
        idx = by_day.get(d)
        if not idx:
            continue
        cum = cumulative_trimp([hr[i] for i in idx], [minutes[i] for i in idx], float(row["hr_max"]), tz)
        hours = now.hour + 1 if d == today else HOURS
        if row.get("details") is None:
            row["details"] = {}
        row["details"].setdefault("load", {})["hourly_strain"] = hourly_strain(cum, float(row["strain"]), hours)
