"""Today's strain target (Strain Coach), sleep coach (Sleep Coach), health monitor (Health Monitor). All computed on demand from daily_scores, not stored in tables."""
import math
import statistics
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import is_en
from ..scores import params as SP
from ..scores import sleep as S

# ---------------------------------------------------------------- today's strain target
# WHOOP's Strain Coach gives the day's target strain by recovery score; the ranges are first-version values (strain calibrated to our K=170, median on strength-training days about 12).
STRAIN_TARGETS = {"green": (14.0, 18.0), "yellow": (10.0, 14.0), "red": (0.0, 10.0)}
RECOVERY_ZONES = ((67, "green"), (34, "yellow"), (0, "red"))       # consistent with the Today page / rules
TSB_FATIGUED = -30.0          # yesterday's TSB < −30 (fatigue high): shift the whole target range down by 2
TSB_SHIFT = 2.0
ILLNESS_TARGET = (0.0, 8.0)   # illness warning: light activity only


def recovery_zone(rec: int | None) -> str | None:
    if rec is None:
        return None
    return next(z for lo, z in RECOVERY_ZONES if rec >= lo)


def strain_target(recovery: int | None, tsb_yesterday: float | None, illness: bool) -> dict:
    zone = recovery_zone(recovery)
    reasons = []
    if illness:
        lo, hi = ILLNESS_TARGET
        reasons.append("有生病提示，只做轻活动")
    elif zone is None:
        lo, hi = STRAIN_TARGETS["yellow"]
        reasons.append("今天没有恢复分，按「黄」给中等目标")
    else:
        lo, hi = STRAIN_TARGETS[zone]
        reasons.append(f"恢复 {recovery}（{ {'green': '绿', 'yellow': '黄', 'red': '红'}[zone]}）")
    if tsb_yesterday is not None and tsb_yesterday < TSB_FATIGUED and not illness:
        lo, hi = max(0.0, lo - TSB_SHIFT), max(0.0, hi - TSB_SHIFT)
        reasons.append(f"近期疲劳累积（TSB {tsb_yesterday:.0f}），区间下调 {TSB_SHIFT:g}")
    return {"zone": zone, "target_low": lo, "target_high": hi, "reasons": reasons}


def strain_coach(conn: psycopg.Connection, user_id: int, d: date) -> dict:
    rows = {r["date"]: r for r in conn.execute(
        "SELECT date, recovery, strain, tsb, flags FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s",
        (user_id, d - timedelta(days=1), d))}
    today, yday = rows.get(d), rows.get(d - timedelta(days=1))
    illness = bool(today and "illness_warning" in (today["flags"] or {}))
    tgt = strain_target(today["recovery"] if today else None, yday["tsb"] if yday else None, illness)
    cur = today["strain"] if today else None
    status = None
    if cur is not None:
        status = "below" if cur < tgt["target_low"] else "in_range" if cur <= tgt["target_high"] else "above"
    missing = None
    if cur is None:
        missing = (today["flags"] or {}).get("no_raw_hr") if today else "今天还没计算评分"
    return {"date": d, "recovery": today["recovery"] if today else None, **tgt, "current_strain": cur,
            # progress = completed / range lower bound (against the upper bound when the lower bound is 0); >= 1 = reached the range
            "progress": None if cur is None else round(cur / (tgt["target_low"] or tgt["target_high"]), 2),
            "remaining_to_low": None if cur is None else round(max(0.0, tgt["target_low"] - cur), 1),
            "status": status, "current_missing_reason": missing}


# ---------------------------------------------------------------- sleep coach
LATENCY_DEFAULT_MIN = 15      # reserve 15 minutes when there is no sleep-latency data (healthy adults usually 10–20 minutes)


def _hhmm(t: datetime) -> str:
    return t.strftime("%H:%M")


def sleep_need_tonight(actual_recent: list[float | None], strain_today: float | None) -> dict:
    """actual_recent[0] = last night's (the sleep ending today's wake-up) actual sleep; same formula as the sleep need in scoring."""
    debt = S.sleep_debt(actual_recent)
    need = S.sleep_need(debt, strain_today)
    return {**need, "debt": debt}


def _goal_view(conn, user_id: int) -> dict | None:
    """Bedtime goal of the rules session (goals/ module); returns None if not merged / no goal set."""
    try:
        from ..goals import store as goal_store   # optional dependency: exists only once the bedtime goal module is merged
    except ImportError:
        return None
    try:
        v = goal_store.view(conn, user_id)
    except Exception:   # table not yet created, etc.: treat as no goal
        return None
    return v if v.get("tonight_target_bedtime") else None


def sleep_coach(conn: psycopg.Connection, user_id: int, d: date, tz: ZoneInfo, goal: dict | None = "auto") -> dict:
    """d = today (fall asleep tonight, wake up tomorrow)."""
    rows = conn.execute("""SELECT date, sleep_actual_min, strain, details->'sleep' AS s FROM daily_scores
                           WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date DESC""",
                        (user_id, d - timedelta(days=SP.DEBT_NIGHTS - 1), d)).fetchall()
    by = {r["date"]: r for r in rows}
    actual = [by[d - timedelta(days=k)]["sleep_actual_min"] if d - timedelta(days=k) in by else None
              for k in range(SP.DEBT_NIGHTS)]
    strain_today = by[d]["strain"] if d in by else None
    need = sleep_need_tonight(actual, strain_today)
    latencies = [r["s"].get("latency_min") for r in rows if r["s"] and r["s"].get("latency_min") is not None]
    latency = statistics.median(latencies) if len(latencies) >= 3 else LATENCY_DEFAULT_MIN
    wakes = [datetime.fromisoformat(r["s"]["wake_time"]) for r in rows[:7] if r["s"] and r["s"].get("wake_time")]
    if goal == "auto":
        goal = _goal_view(conn, user_id)
    out = {"date": d, "need_min": round(need["need"]), "need_parts": {k: round(need[k]) for k in ("baseline", "from_debt",
           "from_strain")}, "sleep_debt_min": round(need["debt"]), "strain_today": strain_today,
           "latency_buffer_min": round(latency), "wake_time": None, "wake_source": None,
           "bedtime_for_need": None, "goal_bedtime": None, "recommended_bedtime": None, "source": None,
           "shortfall_min": None, "notes": []}
    tomorrow = d + timedelta(days=1)

    def at(hm: str, base: date) -> datetime:
        h, m = map(int, hm.split(":"))
        return datetime.combine(base, time(h, m), tz)

    goal_bed = None
    if goal:
        gh = int(goal["tonight_target_bedtime"][:2])
        goal_bed = at(goal["tonight_target_bedtime"], d if gh >= 12 else tomorrow)
        out.update(goal_bedtime=goal["tonight_target_bedtime"], recommended_bedtime=goal["tonight_target_bedtime"],
                   source="goal")
    if goal and goal.get("target_wake"):            # the goal fixes a wake time: wake time is fixed, check whether sleeping on target is enough
        wake, out["wake_source"] = at(goal["target_wake"], tomorrow), "goal"
    elif goal:                                       # only bedtime set: wake = target bedtime + latency + need
        wake, out["wake_source"] = goal_bed + timedelta(minutes=latency + need["need"]), "goal_plus_need"
    elif wakes:
        m = int(statistics.median(sorted(w.hour * 60 + w.minute for w in wakes)))
        wake, out["wake_source"] = at(f"{m // 60:02d}:{m % 60:02d}", tomorrow), "recent_median"
    else:
        out["notes"].append("没有最近的起床时间，也没设目标起床时间，给不出几点睡")
        return out
    if wake.hour >= 18 and out["wake_source"] != "goal_plus_need":   # a "wake-up" after evening is actually today's
        wake -= timedelta(days=1)
    out["wake_time"] = _hhmm(wake)
    for_need = wake - timedelta(minutes=need["need"] + latency)
    out["bedtime_for_need"] = _hhmm(for_need)
    if goal_bed is None:
        out.update(recommended_bedtime=_hhmm(for_need), source="need")
    elif out["wake_source"] == "goal":
        short = (goal_bed - for_need).total_seconds() / 60
        if short > 0:
            out["shortfall_min"] = round(short)
            out["notes"].append(f"按入睡目标 {out['goal_bedtime']} 睡、{out['wake_time']} 起，比需求少 {round(short)} 分钟："
                                "先守住逐步提前的目标，不够的可以晚点起或下午小睡 ≤ 20 分钟补")
        else:
            out["notes"].append(f"入睡目标比睡够所需的 {out['bedtime_for_need']} 还早，按目标睡就够")
    else:
        out["notes"].append(f"按入睡目标 {out['goal_bedtime']} 睡，要睡够 {round(need['need'] / 60, 1)} 小时，"
                            f"建议 {out['wake_time']} 起")
    return out


# ---------------------------------------------------------------- health monitor
MONITOR_DAYS = 60
MONITOR_MIN_NIGHTS = 14
MONITOR_K = 2.0               # normal range = baseline mean ± 2 × standard deviation (not below the half-band widths below)
MONITOR = {   # key: (label, unit, getter, minimum half-band)
    "rhr": ("静息心率（睡眠期最低）", "bpm", lambda r: r["rhr_value"], 3.0),
    "hrv": ("HRV（SDNN，夜间）", "ms", lambda r: math.exp(r["hrv_night_ln"]) if r["hrv_night_ln"] is not None else None, None),
    "resp_rate": ("呼吸频率（夜间）", "次/分", lambda r: r["resp_rate_night"], 1.0),
    "spo2": ("血氧（夜间中位数）", "%", lambda r: (r["rec"] or {}).get("spo2_night"), 2.0),
    "wrist_temp": ("手腕温度（夜间）", "°C", lambda r: (r["rec"] or {}).get("wrist_temp"), 0.5),
}


MONITOR_EN = {  # key: (English label, unit) for the Today page in English (issue #42)
    "rhr": ("Resting HR (lowest during sleep)", "bpm"),
    "hrv": ("HRV (SDNN, night)", "ms"),
    "resp_rate": ("Respiratory rate (night)", "/min"),
    "spo2": ("Blood oxygen (night median)", "%"),
    "wrist_temp": ("Wrist temperature (night)", "°C"),
}
RHR_APPLE_LABEL = ("静息心率（Apple 全天，最近一晚没有睡眠期原始心率）", "Resting HR (Apple, all day; no raw sleep HR last night)")


def monitor_range(values: list[float], key: str) -> tuple[float, float, float] | None:
    """Returns (lower, upper, center). HRV's symmetric range is computed on the ln scale and converted back to ms."""
    if len(values) < MONITOR_MIN_NIGHTS:
        return None
    if key == "hrv":
        ln = [math.log(v) for v in values]
        m, sd = statistics.fmean(ln), statistics.stdev(ln)
        half = max(MONITOR_K * sd, 0.15)
        return math.exp(m - half), math.exp(m + half), math.exp(m)
    m, sd = statistics.fmean(values), statistics.stdev(values)
    half = max(MONITOR_K * sd, MONITOR[key][3])
    return m - half, m + half, m


def health_monitor(conn: psycopg.Connection, user_id: int, d: date) -> dict:
    rows = conn.execute("""SELECT s.date, s.rhr_night, s.hrv_night_ln, s.resp_rate_night, s.details->'recovery' AS rec,
                                  s.flags ? 'rhr_from_daily' AS rhr_daily, m.value AS apple_rhr
                           FROM daily_scores s LEFT JOIN daily_metrics m ON m.user_id = s.user_id AND m.date = s.date
                                AND m.metric = 'resting_heart_rate'
                           WHERE s.user_id = %s AND s.date BETWEEN %s AND %s ORDER BY s.date""",
                        (user_id, d - timedelta(days=MONITOR_DAYS), d)).fetchall()
    latest_any = next((r for r in reversed(rows) if r["rhr_night"] is not None or r["hrv_night_ln"] is not None), None)
    # the lowest sleep heart rate and Apple's all-day resting heart rate differ by a few bpm and cannot be compared mixed: when the latest night is a fallback all-day value, use the all-day value against its own baseline for the whole item
    rhr_apple = bool(latest_any and latest_any["rhr_daily"])
    for r in rows:
        r["rhr_value"] = r["apple_rhr"] if rhr_apple else (None if r["rhr_daily"] else r["rhr_night"])
    latest = latest_any
    metrics = []
    for key, (label, unit, get, _) in MONITOR.items():
        if is_en():
            label, unit = MONITOR_EN[key]
        if key == "rhr" and rhr_apple:
            label = RHR_APPLE_LABEL[is_en()]
        cur = get(latest) if latest else None
        hist = [v for r in rows if latest is None or r["date"] < latest["date"] if (v := get(r)) is not None]
        rng = monitor_range(hist, key)
        status = None
        if cur is not None and rng is not None:
            status = "low" if cur < rng[0] else "high" if cur > rng[1] else "in_range"
        metrics.append({"key": key, "label": label, "unit": unit, "value": None if cur is None else round(cur, 2),
                        "date": latest["date"] if latest and cur is not None else None,
                        "low": None if rng is None else round(rng[0], 2), "high": None if rng is None else round(rng[1], 2),
                        "baseline": None if rng is None else round(rng[2], 2), "baseline_nights": len(hist),
                        "status": status,
                        "missing_reason": None if rng is not None and cur is not None else
                        ("最近一晚没有这项读数" if cur is None else f"前 {MONITOR_DAYS} 天只有 {len(hist)} 晚（要 ≥ {MONITOR_MIN_NIGHTS}）")})
    out_of_range = [m["key"] for m in metrics if m["status"] in ("low", "high")]
    return {"date": latest["date"] if latest else None, "metrics": metrics, "out_of_range": out_of_range,
            "all_in_range": None if all(m["status"] is None for m in metrics) else not out_of_range}
