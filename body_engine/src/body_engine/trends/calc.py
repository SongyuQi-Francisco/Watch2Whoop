"""Trends tab analyses as pure functions (no DB): HRV status, training status + load focus, aerobic minutes, resilience,
WHOOP-style sleep performance, combined strain, illness risk, chronotype, body battery, recovery time, recovery index,
heart-rate recovery. Inputs are plain dicts keyed by date; see params.py for the thresholds and their sources."""
import math
import statistics
from datetime import date, datetime, timedelta

from . import params as P

ByDate = dict[date, float]


def _vals(by: ByDate, start: date, end: date) -> list[float]:
    return [v for d, v in by.items() if start <= d <= end and v is not None]


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _band(xs: list[float]) -> tuple[float, float] | None:
    if len(xs) < P.MIN_BASELINE_DAYS:
        return None
    return statistics.fmean(xs), statistics.pstdev(xs) or 1e-6


# ---------------------------------------------------------------- HRV status
def hrv_status(hrv_ms: ByDate, end: date) -> dict | None:
    """7-day mean of ln HRV vs the band of the 60 days before that window. status: balanced / high / low / poor."""
    w0 = end - timedelta(days=P.HRV_STATUS_WINDOW - 1)
    week = [math.log(v) for v in _vals(hrv_ms, w0, end) if v > 0]
    base = _band([math.log(v) for v in _vals(hrv_ms, w0 - timedelta(days=P.BASELINE_DAYS), w0 - timedelta(days=1)) if v > 0])
    if len(week) < P.HRV_STATUS_MIN_DAYS or base is None:
        return None
    m, sd = base
    avg = statistics.fmean(week)
    lo, hi = m - P.HRV_BAND_SD * sd, m + P.HRV_BAND_SD * sd
    status = "poor" if avg < m - P.HRV_POOR_SD * sd else "low" if avg < lo else "high" if avg > hi else "balanced"
    return {"status": status, "label": {"balanced": "平衡", "high": "偏高", "low": "偏低", "poor": "很低"}[status],
            "avg7_ms": round(math.exp(avg), 1), "band_ms": [round(math.exp(lo), 1), round(math.exp(hi), 1)],
            "baseline_ms": round(math.exp(m), 1), "nights": len(week)}


# ---------------------------------------------------------------- training status + load focus
def rolling_mean(by: ByDate, end: date, days: int, fill_zero: bool = True) -> float | None:
    """Mean over the last `days` days; days without a value count as 0 when fill_zero (rest days carry no load)."""
    xs = [by.get(end - timedelta(days=k)) for k in range(days)]
    xs = [x if x is not None else (0.0 if fill_zero else None) for x in xs]
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def acwr(trimp: ByDate, end: date) -> float | None:
    acute, chronic = rolling_mean(trimp, end, P.ACUTE_DAYS), rolling_mean(trimp, end, P.CHRONIC_DAYS)
    if not chronic:
        return None
    return round(acute / chronic, 2)


def slope_per(by: ByDate, end: date, days: int) -> float | None:
    """Least-squares slope over the last `days` days, expressed as change per `days` days (None with < 3 points)."""
    pts = [((d - end).days, v) for d, v in by.items() if end - timedelta(days=days) < d <= end and v is not None]
    if len(pts) < 3:
        return None
    mx, my = statistics.fmean(p[0] for p in pts), statistics.fmean(p[1] for p in pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    if not den:
        return None
    return round(sum((x - mx) * (y - my) for x, y in pts) / den * days, 2)


TRAINING_STATUS = {"overreaching": "过度", "detraining": "去适应", "recovery": "恢复中", "strained": "疲劳",
                   "productive": "有效", "unproductive": "低效", "maintaining": "保持"}


def training_status(trimp: ByDate, ctl: ByDate, vo2: ByDate, hrv: dict | None, end: date) -> dict | None:
    days_with = sum(1 for k in range(P.CHRONIC_DAYS) if trimp.get(end - timedelta(days=k)) is not None)
    if days_with < P.MIN_TRIMP_DAYS:
        return None
    ratio = acwr(trimp, end)
    if ratio is None:
        return None
    ctl_now, ctl_then = ctl.get(end), ctl.get(end - timedelta(days=P.CHRONIC_DAYS))
    vo2_slope = slope_per(vo2, end, P.VO2_SLOPE_DAYS)
    if ratio > P.ACWR_HIGH:
        status = "overreaching"
    elif ratio < P.ACWR_LOW:
        dropped = ctl_now is not None and ctl_then and ctl_now < ctl_then * P.DETRAIN_CTL_DROP
        status = "detraining" if ratio < P.DETRAIN_ACWR and dropped else "recovery"
    elif hrv and hrv["status"] in ("low", "poor"):
        status = "strained"
    elif vo2_slope is not None and vo2_slope >= P.VO2_SLOPE_UP:
        status = "productive"
    elif vo2_slope is not None and vo2_slope <= -P.VO2_SLOPE_UP:
        status = "unproductive"
    else:
        status = "maintaining"
    return {"status": status, "label": TRAINING_STATUS[status], "acwr": ratio,
            "acute": round(rolling_mean(trimp, end, P.ACUTE_DAYS), 1), "chronic": round(rolling_mean(trimp, end, P.CHRONIC_DAYS), 1),
            "vo2_change_60d": vo2_slope, "trimp_days": days_with}


def load_focus(zone_minutes: dict[date, list[float]], end: date, days: int = P.CHRONIC_DAYS) -> dict | None:
    """Share of TRIMP (zone minutes × zone weight 1–5) per zone group over the last `days` days."""
    tot = [0.0] * 5
    for k in range(days):
        z = zone_minutes.get(end - timedelta(days=k))
        for i in range(5):
            tot[i] += ((z or [0] * 5)[i] or 0) * (i + 1)
    s = sum(tot)
    if not s:
        return None
    return {"groups": [{"key": k, "label": lbl, "trimp": round(sum(tot[i] for i in idx)),
                        "pct": round(100 * sum(tot[i] for i in idx) / s)} for k, lbl, idx in P.LOAD_FOCUS], "days": days}


# ---------------------------------------------------------------- aerobic minutes per week
def aerobic_minutes(zones: list[float] | None) -> float | None:
    if not zones:
        return None
    return sum(zones[i] or 0 for i in P.AZM_MODERATE_ZONES) + 2 * sum(zones[i] or 0 for i in P.AZM_VIGOROUS_ZONES)


def weekly_sum(by: ByDate, dates: list[date]) -> list[dict]:
    """Totals per week starting Monday (only weeks that touch `dates`)."""
    weeks: dict[date, float] = {}
    for d in dates:
        wk = d - timedelta(days=d.weekday())
        weeks.setdefault(wk, 0.0)
        weeks[wk] += by.get(d) or 0
    return [{"week": wk, "value": round(v, 1)} for wk, v in sorted(weeks.items())]


# ---------------------------------------------------------------- resilience
def resilience(recovery: ByDate, sleep_perf: ByDate, stress: ByDate, end: date) -> dict | None:
    """Capacity (recovery and sleep sufficiency) minus half the daytime stress load over the last 14 days."""
    start = end - timedelta(days=P.RESILIENCE_DAYS - 1)
    rec = _vals(recovery, start, end)
    if len(rec) < P.RESILIENCE_MIN_DAYS:
        return None
    sp = [min(1.0, v) for v in _vals(sleep_perf, start, end)]
    st = _vals(stress, start, end)
    capacity = 0.6 * statistics.fmean(rec) / 100 + 0.4 * (statistics.fmean(sp) if sp else statistics.fmean(rec) / 100)
    load = statistics.fmean(st) / 3 if st else 0.0
    balance = capacity - 0.5 * load
    key, label = next((k, lbl) for cut, k, lbl in P.RESILIENCE_LEVELS if balance >= cut)
    return {"level": key, "label": label, "balance": round(balance, 3), "capacity": round(capacity, 3),
            "stress_load": round(load, 3) if st else None, "days": len(rec)}


# ---------------------------------------------------------------- WHOOP-style sleep performance
def sleep_performance_whoop(sufficiency: float | None, consistency: float | None, efficiency: float | None) -> float | None:
    """0–100: hours vs needed (capped at 100 %), consistency 0–100, efficiency 0–1; missing parts drop out and the weights renormalise."""
    if sufficiency is None:
        return None
    parts = {"sufficiency": min(1.0, sufficiency) * 100, "consistency": consistency,
             "efficiency": None if efficiency is None else min(1.0, efficiency) * 100}
    w = {k: P.SLEEP_PERF_WEIGHTS[k] for k, v in parts.items() if v is not None}
    return round(sum(parts[k] * w[k] for k in w) / sum(w.values()), 1)


# ---------------------------------------------------------------- combined strain (optional view)
def strain_from_trimp(trimp: float) -> float:
    return 21 * (1 - math.exp(-trimp / P.STRAIN_K))


def combined_strain(trimp: float | None, muscle_load: float | None, median_muscle: float | None) -> float | None:
    if trimp is None and not muscle_load:
        return None
    extra = P.MUSCLE_TRIMP_AT_MEDIAN * muscle_load / median_muscle if muscle_load and median_muscle else 0.0
    return round(strain_from_trimp((trimp or 0) + extra), 1)


# ---------------------------------------------------------------- illness risk
def illness_risk(z: dict | None, temp_dev: float | None, spo2: float | None) -> dict | None:
    """z = recovery z-scores signed so that positive is good (scores details.recovery.z). Risk 0–100 from the adverse signs."""
    if not z and temp_dev is None:
        return None
    signs = sum(max(0.0, -(z or {}).get(k, 0.0) - P.ILLNESS_DEAD_Z) * w for k, w in P.ILLNESS_WEIGHTS.items())
    if temp_dev is not None and temp_dev >= P.ILLNESS_TEMP_C:
        signs += 1.0
    if spo2 is not None and spo2 < P.ILLNESS_SPO2:
        signs += 0.5
    score = round(100 * (1 - math.exp(-signs / 2)))
    key, label = next((k, lbl) for cut, k, lbl in P.ILLNESS_LEVELS if score >= cut)
    return {"score": score, "level": key, "label": label}


# ---------------------------------------------------------------- chronotype
def _mid_min(bed: datetime, wake: datetime) -> float:
    """Mid-sleep as minutes after the midnight before waking (can be negative when mid-sleep is before midnight)."""
    mid = bed + (wake - bed) / 2
    midnight = datetime.combine(wake.date(), datetime.min.time(), wake.tzinfo)
    return (mid - midnight).total_seconds() / 60


def chronotype(nights: list[tuple[datetime, datetime]]) -> dict | None:
    """nights = (bedtime, wake time) in local time. MCTQ: MSFsc = mid-sleep on free days − half of (free − work day sleep)."""
    if len(nights) < P.CHRONO_MIN_NIGHTS:
        return None
    free = [n for n in nights if n[1].weekday() in P.CHRONO_FREE_WAKE_DAYS]
    work = [n for n in nights if n[1].weekday() not in P.CHRONO_FREE_WAKE_DAYS]
    use = free if len(free) >= 3 else nights
    msf = statistics.median(_mid_min(b, w) for b, w in use)
    if len(free) >= 3 and work:
        sd_f = statistics.fmean((w - b).total_seconds() / 60 for b, w in free)
        sd_w = statistics.fmean((w - b).total_seconds() / 60 for b, w in work)
        if sd_f > sd_w:
            msf -= (sd_f - sd_w) / 2
    key, label = next((k, lbl) for cut, k, lbl in P.CHRONO_BINS if msf < cut)
    m = int(round(msf)) % 1440
    return {"type": key, "label": label, "mid_sleep": f"{m // 60:02d}:{m % 60:02d}", "free_days": len(free) >= 3,
            "nights": len(nights)}


# ---------------------------------------------------------------- body battery (estimate)
def body_battery(recovery: int | None, sleep_perf: float | None, wake_hour: int | None,
                 stress_hours: list[dict], strain: float | None, until_hour: int = 23) -> dict | None:
    """Hourly estimate from wake-up to `until_hour`: starts at 20 + 75 × recovery %, drains 2 per awake hour plus stress
    (medium 2 / high 4 per hour) and strain spread over the waking hours. Rough: Apple Watch has no continuous daytime HRV."""
    charge = recovery / 100 if recovery is not None else (min(1.0, sleep_perf) if sleep_perf is not None else None)
    if charge is None:
        return None
    wake = 7 if wake_hour is None else max(0, min(16, wake_hour))
    until = max(wake, min(23, until_hour))
    level = {h["hour"]: h.get("level") for h in stress_hours or [] if h.get("score") is not None}
    hours = max(1, 23 - wake)
    v = P.BB_WAKE_BASE + P.BB_WAKE_SPAN * charge
    pts = [{"hour": wake, "v": round(v)}]
    for h in range(wake, until):
        v -= P.BB_DRAIN_PER_HOUR + P.BB_STRESS_DRAIN.get(level.get(h) or "low", 0) + P.BB_STRAIN_DRAIN * (strain or 0) / hours
        v = max(5.0, v)
        pts.append({"hour": h + 1, "v": round(v)})
    return {"wake": pts[0]["v"], "current": pts[-1]["v"], "low": min(p["v"] for p in pts), "points": pts, "estimate": True}


# ---------------------------------------------------------------- recovery time (estimate)
def recovery_time(strain: ByDate, tsb: ByDate, zone_today: str | None, day: date) -> dict | None:
    """Hours still needed as of 07:00 on `day`, from the hardest of the last 4 days (strain above 8)."""
    best = None
    as_of = datetime.combine(day, datetime.min.time()) + timedelta(hours=P.RT_AS_OF_HOUR)
    for k in range(1, 5):
        d = day - timedelta(days=k)
        s = strain.get(d)
        if s is None or s <= P.RT_STRAIN_FREE:
            continue
        need = (s - P.RT_STRAIN_FREE) * P.RT_HOURS_PER_STRAIN * (1 + max(0.0, -(tsb.get(d) or 0)) / P.RT_TSB_FATIGUE)
        need = min(P.RT_MAX_HOURS, need * P.RT_ZONE_FACTOR.get(zone_today or "yellow", 1.0))
        left = need - (as_of - (datetime.combine(d, datetime.min.time()) + timedelta(hours=P.RT_WORKOUT_HOUR))).total_seconds() / 3600
        if best is None or left > best["hours"]:
            best = {"hours": max(0, round(left)), "from_date": d, "from_strain": round(s, 1), "estimate": True}
    if best is None and any(strain.get(day - timedelta(days=k)) is not None for k in range(1, 5)):
        return {"hours": 0, "from_date": None, "from_strain": None, "estimate": True}
    return best


# ---------------------------------------------------------------- recovery index / heart-rate recovery
def recovery_index(low_offset_min: float | None, asleep_min: float | None) -> dict | None:
    """When the lowest 5-minute heart rate came, as a share of the night (Oura Recovery Index)."""
    if low_offset_min is None or not asleep_min:
        return None
    frac = max(0.0, min(1.0, low_offset_min / asleep_min))
    key = "good" if frac < P.RI_GOOD else "fair" if frac < P.RI_LATE else "late"
    return {"pct": round(100 * frac), "level": key, "label": {"good": "前半夜", "fair": "后半夜", "late": "很晚"}[key]}


def hr_recovery(end_hr: float | None, after_hr: float | None) -> float | None:
    """Drop in bpm from the last minute of exercise to one minute after it."""
    if end_hr is None or after_hr is None:
        return None
    return round(end_hr - after_hr, 1)


def hr_recovery_level(bpm: float | None) -> str | None:
    if bpm is None:
        return None
    return "low" if bpm <= P.HRR_LOW else "fair" if bpm < P.HRR_GOOD else "good"
