"""Body age and pace of aging (matching WHOOP Age / Healthspan). Pure functions; parameters and literature sources are in healthspan_params.py.

1. Each metric is averaged over the last 180 days (the last 30 days carry day weight ×2); metrics with too few data days are not counted (written to insufficient).
2. Each metric is converted by the literature's dose-response curve into an all-cause mortality hazard ratio HR (relative to the literature's reference level).
3. Δ years = ln(HR) / ln2 × 8 (Gompertz: adult mortality roughly doubles every 8 years), capped at ±4 years per item;
   the sum × 0.6 (items are correlated, to avoid double counting), total Δ capped at ±10 years. Body age = chronological age + total Δ.
4. Pace of aging: one Δ per week, first a 4-week moving average, then regressed against time (years) over the last 26 weeks (all of them if fewer, >= 8 weeks):
   pace = 1 + dΔ/dt (= slope of body age), limited to −1 ~ 3.
"""
import math
from datetime import date, timedelta

from . import healthspan_params as H


def weighted_mean(values: dict[date, float], as_of: date) -> tuple[float | None, int]:
    """values: date -> value (only those within the window are used). Returns (weighted mean, number of data days)."""
    lo = as_of - timedelta(days=H.WINDOW_DAYS - 1)
    recent = as_of - timedelta(days=H.RECENT_DAYS - 1)
    num = den = 0.0
    n = 0
    for d, v in values.items():
        if lo <= d <= as_of and v is not None:
            w = H.RECENT_WEIGHT if d >= recent else 1.0
            num += w * v
            den += w
            n += 1
    return (num / den if den else None), n


def interp_log_hr(x: float, knots: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation of ln(HR); beyond the ends the value is held at the endpoint (no extrapolation). knots = [(x, HR)] ascending by x."""
    if x <= knots[0][0]:
        return knots[0][1]
    if x >= knots[-1][0]:
        return knots[-1][1]
    for (x0, h0), (x1, h1) in zip(knots, knots[1:]):
        if x0 <= x <= x1:
            f = (x - x0) / (x1 - x0)
            return math.exp(math.log(h0) + f * (math.log(h1) - math.log(h0)))
    raise AssertionError("unreachable")


def _rel(x: float, knots, anchor: float) -> float:
    """Hazard ratio relative to the anchor."""
    return interp_log_hr(x, knots) / interp_log_hr(anchor, knots)


def self_report_hours(measured: float) -> float:
    a, b = H.SLEEP_SELF_REPORT
    return a + b * measured


def hr_sleep_duration(hours_measured: float) -> float:
    """U-shaped sleep duration: the watch-measured duration is first converted to the "self-report" scale (the literature uses questionnaires), nadir 7h = anchor, each side has its own RR per hour."""
    h = self_report_hours(hours_measured)
    if h < H.SLEEP_NADIR_H:
        return H.SLEEP_RR_PER_H_SHORT ** (H.SLEEP_NADIR_H - h)
    return H.SLEEP_RR_PER_H_LONG ** (h - H.SLEEP_NADIR_H)


def hr_regularity(sri: float) -> float:
    return _rel(sri, H.SRI_KNOTS, H.SRI_ANCHOR)


def hr_steps(steps: float, age: float) -> float:
    if age < 60:
        return _rel(steps, H.STEPS_KNOTS_UNDER60, H.STEPS_ANCHOR_UNDER60)
    return _rel(steps, H.STEPS_KNOTS_60PLUS, H.STEPS_ANCHOR_60PLUS)


def hr_mvpa(moderate_equiv_min_week: float) -> float:
    return _rel(moderate_equiv_min_week, H.MVPA_KNOTS, H.MVPA_ANCHOR)


def hr_strength(min_week: float) -> float:
    return _rel(min_week, H.STRENGTH_KNOTS, H.STRENGTH_ANCHOR)


def _by_age(table: dict, age: float):
    """Linear interpolation at age-band midpoints (the value can be a number or a list of the same length)."""
    pts = sorted(table.items())
    if age <= pts[0][0]:
        return pts[0][1]
    if age >= pts[-1][0]:
        return pts[-1][1]
    for (a0, v0), (a1, v1) in zip(pts, pts[1:]):
        if a0 <= age <= a1:
            f = (age - a0) / (a1 - a0)
            if isinstance(v0, list):
                return [x0 + f * (x1 - x0) for x0, x1 in zip(v0, v1)]
            return v0 + f * (v1 - v0)


def vo2_norm(age: float, sex: str) -> float | None:
    """Same-age same-sex median (FRIEND 2022 treadmill norms)."""
    table = H.VO2_MEDIAN.get(sex)
    return _by_age(table, age) if table else None


def vo2_percentile(vo2: float, age: float, sex: str) -> float | None:
    """Roughly which percentile among the same age and sex (linear interpolation between deciles, clamped to 5 / 95 at the ends)."""
    table = H.VO2_DECILES.get(sex)
    if not table:
        return None
    dec = _by_age(table, age)
    if vo2 <= dec[0]:
        return 5.0
    if vo2 >= dec[-1]:
        return 95.0
    for i in range(len(dec) - 1):
        if dec[i] <= vo2 <= dec[i + 1]:
            return round(10 * (i + 1) + 10 * (vo2 - dec[i]) / (dec[i + 1] - dec[i]), 1)
    return None


def hr_vo2(vo2: float, norm: float) -> float:
    """Risk × 0.87 per 1 MET (3.5 mL/kg/min) above the norm (Kodama 2009); the gap is limited to ±3 MET."""
    met = max(-H.VO2_MAX_DELTA_MET, min(H.VO2_MAX_DELTA_MET, (vo2 - norm) / 3.5))
    return H.VO2_RR_PER_MET ** met


def hr_rhr(rhr: float) -> float:
    """× 1.16 per 10 bpm higher (Jensen 2013), flat below 45 bpm; anchor 65."""
    return H.RHR_RR_PER_10 ** ((max(rhr, H.RHR_FLOOR) - H.RHR_ANCHOR) / 10)


def hr_bmi(bmi: float) -> float:
    return _rel(bmi, H.BMI_KNOTS, H.BMI_ANCHOR)


def hr_whtr(whtr: float) -> float:
    return _rel(whtr, H.WHTR_KNOTS, H.WHTR_ANCHOR)


def years_from_hr(hr: float) -> float:
    y = math.log(hr) / math.log(2) * H.MORTALITY_DOUBLING_YEARS
    return max(-H.ITEM_CAP_YEARS, min(H.ITEM_CAP_YEARS, y))


def combine(items: list[dict]) -> float:
    total = H.SHRINKAGE * sum(i["years"] for i in items)
    return max(-H.TOTAL_CAP_YEARS, min(H.TOTAL_CAP_YEARS, total))


def chrono_age(birth: date, d: date) -> float:
    return (d - birth).days / 365.2425


def smooth(history: list[tuple[date, float | None]], weeks: int = H.PACE_SMOOTH_WEEKS) -> list[tuple[date, float]]:
    """Each weekly point = the mean of its Δ and those of the preceding weeks (weeks in total; as_of in (d − weeks weeks, d]); null values skipped."""
    pts = [(d, v) for d, v in history if v is not None]
    out = []
    for d, _ in pts:
        xs = [v for d2, v in pts if d - timedelta(weeks=weeks) < d2 <= d]
        out.append((d, sum(xs) / len(xs)))
    return out


def pace(history: list[tuple[date, float | None]], as_of: date) -> dict:
    """history = [(as_of, Δ years)] (sorted by time, only those <= as_of). Returns {pace, pace_raw, window_weeks}.
    Δ first gets a 4-week moving average, then least squares over the last 26 weeks (all of them if fewer than 26, a value only at >= 8 weeks): pace = 1 + dΔ/dt, clipped to −1 ~ 3.
    Body age = chronological age + Δ, so 1 + dΔ/dt is the slope of body age against time."""
    none = {"pace": None, "pace_raw": None, "window_weeks": None}
    sm = [(d, v) for d, v in smooth([(d, v) for d, v in history if d <= as_of]) if d <= as_of]
    pts = sm[-H.PACE_WEEKS:]   # the last 26 weekly points (the current week's point is today, less than 7 days after last Sunday; counted by points, not days)
    if len(pts) < H.PACE_MIN_WEEKS:
        return none
    xs = [(d - as_of).days / 365.2425 for d, _ in pts]
    ys = [v for _, v in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return none
    raw = 1 + sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return {"pace": round(max(H.PACE_RANGE[0], min(H.PACE_RANGE[1], raw)), 2), "pace_raw": round(raw, 2),
            "window_weeks": len(pts)}


def tip(key: str, years: float, value: float | None = None) -> str:
    if key == "sleep_duration":
        if abs(years) < 0.25 or value is None:
            return H.TIPS["sleep_ok"]
        return H.TIPS["sleep_short"] if self_report_hours(value) < H.SLEEP_NADIR_H else H.TIPS["sleep_long"]
    good, bad = H.TIPS[key]
    return good if years <= 0 else bad
