"""Trends tab data (GET /v1/users/{u}/trends): every WHOOP metric as a daily series over [end − days + 1, end], the mean of the
previous period of the same length (`prev_mean`, for "vs 上期"), recovery colours and contributors, the Strain Coach target band,
and the status cards computed as of `end` (HRV status, training status, load focus, resilience, chronotype, illness risk,
body battery, recovery time, recovery index, heart-rate recovery). Each block is authorised by its scope (shares).
Only reads tables that the scores / insights / training jobs already fill; nothing is stored here."""
import bisect
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth, queries, sparky_client
from ..i18n import tl
from ..insights import coach
from . import calc, params as P

# (key, Chinese label, unit, scope, group); English in params.EN. Groups follow WHOOP: recovery / sleep / strain / stress / long-term.
SERIES = [
    ("recovery", "恢复分", "", "scores", "recovery"),
    ("hrv", "HRV（SDNN 日均）", "ms", "metrics", "recovery"),
    ("resting_hr", "静息心率（Apple）", "bpm", "metrics", "recovery"),
    ("resp_rate", "呼吸频率", "次/分", "scores", "recovery"),
    ("spo2", "血氧", "%", "scores", "recovery"),
    ("wrist_temp", "手腕温度偏差", "°C", "scores", "recovery"),
    ("illness", "生病风险", "", "scores", "recovery"),
    ("recovery_index", "最低心率出现", "%", "scores", "recovery"),
    ("sleep_hours", "实际睡眠", "h", "scores", "sleep"),
    ("sleep_need_hours", "睡眠需求", "h", "scores", "sleep"),
    ("sleep_debt_hours", "睡眠债", "h", "scores", "sleep"),
    ("sleep_performance", "睡眠表现", "%", "scores", "sleep"),
    ("sleep_perf_whoop", "睡眠表现（综合）", "", "sleep", "sleep"),
    ("restorative_hours", "恢复性睡眠", "h", "sleep", "sleep"),
    ("restorative_pct", "恢复性睡眠占比", "%", "sleep", "sleep"),
    ("sleep_efficiency", "睡眠效率", "%", "sleep", "sleep"),
    ("sleep_score", "睡眠分", "", "scores", "sleep"),
    ("sleep_consistency", "睡眠规律性", "", "sleep", "sleep"),
    ("awakenings", "夜间醒来", "次", "sleep", "sleep"),
    ("daylight", "日照时间", "分钟", "metrics", "sleep"),
    ("strain", "负荷 strain", "", "scores", "strain"),
    ("strain_combined", "负荷（含肌肉）", "", "scores", "strain"),
    ("ctl", "CTL 体能", "", "scores", "strain"),
    ("atl", "ATL 疲劳", "", "scores", "strain"),
    ("tsb", "TSB 状态", "", "scores", "strain"),
    ("acwr", "急慢性负荷比", "", "scores", "strain"),
    ("aerobic_min", "有氧分钟", "分钟", "scores", "strain"),
    ("muscle_load", "肌肉负荷", "kg", "scores", "strain"),
    ("steps", "步数", "步", "metrics", "strain"),
    ("active_kcal", "活动消耗", "kcal", "metrics", "strain"),
    ("total_kcal", "总消耗", "kcal", "metrics", "strain"),
    ("hr_recovery", "运动后心率恢复", "bpm", "training", "strain"),
    ("stress", "压力", "", "scores", "stress"),
    ("resilience", "韧性", "", "scores", "stress"),
    ("vo2max", "VO₂max", "mL/kg·min", "metrics", "long"),
    ("weight", "体重", "kg", "weight", "long"),
    ("body_age", "身体年龄", "岁", "scores", "long"),
]
SCOPE = {k: s for k, _, _, s, _ in SERIES}
SUM_KEYS = {"aerobic_min", "steps", "active_kcal", "total_kcal", "muscle_load", "daylight"}   # period figure is a daily mean of these too

BASELINE_DAYS = 60
BASELINE_MIN_N = 14
LOOKBACK = 90   # extra days before the previous period, for baselines (60), ACWR (28), muscle median (90)
METRICS = ["resting_heart_rate", "heart_rate_variability_sdnn", "vo2_max", "step_count", "active_energy_burned",
           "basal_energy_burned", "time_in_daylight", "oxygen_saturation", "respiratory_rate", "apple_sleeping_wrist_temperature"]
# The pure part of this module (compute, baseline_band, rolling, hr_zone_summary) and web/views month_days / sleep_nights,
# insights/sleep_detail, insights/fitness run on the phone as packages/openbodyscore-js/src/33_series.js (M3). Bump with
# any change of their output; the JS bundle carries the same string (algo/versions.py, tests/test_js_golden_series.py).
SERIES_VERSION = "series-1"


def baseline_band(by_date: dict[date, float], dates: list[date]) -> dict:
    """Personal baseline for each day: mean and ±1 SD of the previous 60 days (excluding the day itself); not produced with fewer than 14 days."""
    mean, lo, hi = [], [], []
    items = [(k, v) for k, v in by_date.items() if v is not None]
    keys = [k for k, _ in items]
    ordered = all(a < b for a, b in zip(keys, keys[1:]))   # rows come by date: slice the window instead of scanning all
    for d in dates:
        start = d - timedelta(days=BASELINE_DAYS)
        xs = ([v for _, v in items[bisect.bisect_left(keys, start):bisect.bisect_left(keys, d)]] if ordered
              else [v for k, v in items if start <= k < d])
        if len(xs) < BASELINE_MIN_N:
            mean.append(None); lo.append(None); hi.append(None)
            continue
        m = sum(xs) / len(xs)
        sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
        mean.append(round(m, 2)); lo.append(round(m - sd, 2)); hi.append(round(m + sd, 2))
    return {"mean": mean, "lo": lo, "hi": hi}


def rolling(values: list, window: int = 7, min_n: int = 3) -> list:
    out = []
    for i in range(len(values)):
        w = [v for v in values[max(0, i - window + 1):i + 1] if v is not None]
        out.append(round(sum(w) / len(w), 2) if len(w) >= min_n else None)
    return out


def weights(conn, owner_id: int, start: date, end: date) -> dict[date, float]:
    """Manual weigh-ins from SparkyFitness take priority; days without one use Body Engine's body_mass (same as the rule engine)."""
    out = {r["date"]: r["value"] for r in conn.execute(
        """SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'body_mass' AND unit = 'kg'
           AND date BETWEEN %s AND %s""", (owner_id, start, end))}
    try:
        client = sparky_client.reader_for_user(conn, owner_id)
        for r in sparky_client.weight(client, start, end):
            if r.get("weight_kg") is not None:
                out[date.fromisoformat(r["date"])] = r["weight_kg"]
    except sparky_client.SparkyError:
        if not out:
            raise
    return out


def hr_zone_summary(by_date: dict, dates: list[date]) -> dict | None:
    """Heart-rate zone distribution: total minutes per zone over the period + totals per week (starting Monday)."""
    total, weeks = [0.0] * 5, {}
    for d in dates:
        z = by_date.get(d)
        if not z:
            continue
        wk = d - timedelta(days=d.weekday())
        weeks.setdefault(wk, [0.0] * 5)
        for i in range(5):
            total[i] += z[i] or 0
            weeks[wk][i] += z[i] or 0
    if not weeks:
        return None
    return {"labels": ["50–60%", "60–70%", "70–80%", "80–90%", "90–100%"], "total_min": [round(x) for x in total],
            "weeks": [{"week": wk, "minutes": [round(x) for x in v]} for wk, v in sorted(weeks.items())]}


def _local(ts, tz: ZoneInfo) -> datetime | None:
    if not ts:
        return None
    t = ts if isinstance(ts, datetime) else datetime.fromisoformat(ts)
    return t.astimezone(tz)


def _metric_map(rows, metric: str, scale: float = 1.0) -> dict[date, float]:
    return {r["date"]: r["value"] * scale for r in rows if r["metric"] == metric and r["value"] is not None}


def window(end: date, days: int) -> tuple[date, date, date]:
    """(start, prev_start, lo): the range, the previous period of the same length, and the first day read (baselines)."""
    start = end - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)
    return start, prev_start, prev_start - timedelta(days=LOOKBACK)


def load(conn: psycopg.Connection, viewer: dict, owner_id: int, end: date, days: int) -> dict:
    """Everything `compute` reads, from the database (each block only with its scope). The phone builds the same dict from
    its local tables (docs/ios-native-2.0.md M3; JS `OBS.series.compute`)."""
    start, prev_start, lo = window(end, days)
    can = {s: auth.can_view(conn, viewer["id"], owner_id, s) for s in ("scores", "metrics", "sleep", "weight", "training")}
    data: dict = {"can": can}
    if can["scores"]:
        data["scores"] = [dict(r) for r in conn.execute(
            """SELECT date, recovery, sleep_score, sleep_actual_min, sleep_need_min, sleep_debt_min, sleep_performance,
                      resp_rate_night, trimp, strain, muscle_load, ctl, atl, tsb, flags, details
               FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date""", (owner_id, lo, end))]
        data["stress"] = [dict(r) for r in conn.execute(
            "SELECT date, score, level, hours FROM stress_daily WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date",
            (owner_id, lo, end))]
        data["body_age"] = [dict(r) for r in conn.execute(
            """SELECT as_of, body_age FROM healthspan_weekly WHERE user_id = %s AND as_of BETWEEN %s AND %s
               AND body_age IS NOT NULL ORDER BY as_of""", (owner_id, prev_start, end))]
    if can["metrics"]:
        data["metrics"] = [{"date": r["date"], "metric": r["metric"], "value": r["value"]}
                           for r in queries.daily_metrics(conn, owner_id, lo, end, METRICS)]
    if can["sleep"]:
        data["sleep"] = [dict(r) for r in conn.execute(
            """SELECT DISTINCT ON (wake_date) wake_date, start_at, end_at, asleep_min,
                      extract(epoch FROM end_at - start_at) / 60 AS in_bed
               FROM sleep_sessions WHERE user_id = %s AND is_main AND wake_date BETWEEN %s AND %s
               ORDER BY wake_date, asleep_min DESC NULLS LAST""", (owner_id, lo, end))]
    if can["training"]:   # heart-rate recovery: Apple's one-minute value, else our own from the workout's raw heart rate
        data["hrr_apple"] = {r["date"]: r["value"] for r in conn.execute(
            """SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'heart_rate_recovery_one_minute'
               AND date BETWEEN %s AND %s ORDER BY date""", (owner_id, lo, end))}
        data["hrr_workouts"] = {r["local_date"]: r["v"] for r in conn.execute(
            """SELECT local_date, max((flags->>'hr_recovery_bpm')::float) AS v FROM workouts
               WHERE user_id = %s AND dup_of IS NULL AND excluded IS NULL AND flags ? 'hr_recovery_bpm'
               AND local_date BETWEEN %s AND %s GROUP BY local_date ORDER BY local_date""", (owner_id, lo, end))}
    if can["weight"]:
        try:  # 6 extra days back so the 7-day mean of the first week is complete
            data["weights"] = weights(conn, owner_id, prev_start - timedelta(days=6), end)
        except sparky_client.SparkyError as e:
            data["weights_error"] = str(e)
    return data


def build(conn: psycopg.Connection, viewer: dict, owner_id: int, end: date, days: int, today: date, tz: ZoneInfo) -> dict:
    return compute(load(conn, viewer, owner_id, end, days), end, days, today, tz)


def compute(data: dict, end: date, days: int, today: date, tz: ZoneInfo, now_hour: int | None = None) -> dict:
    """The Trends document from `load`'s data, no database. `now_hour`: the local hour for today's body battery (None = now).
    Missing blocks (`stress`, `body_age`, `hrr_*`, `weights`) are treated as having no rows."""
    start, prev_start, lo = window(end, days)
    dates = [start + timedelta(days=k) for k in range(days)]
    can = data["can"]
    raw: dict[str, dict[date, float]] = {}
    unavailable: dict[str, str] = {}
    extra: dict = {}
    status: dict = {}
    bands: dict[str, dict] = {}
    zones: dict[date, list] = {}
    rows: dict[date, dict] = {}

    if can["scores"]:
        rows = {r["date"]: r for r in data.get("scores") or []}
        det = {d: r["details"] or {} for d, r in rows.items()}
        sl = {d: x.get("sleep") or {} for d, x in det.items()}
        rc = {d: x.get("recovery") or {} for d, x in det.items()}
        col = lambda k, f=lambda v: v: {d: f(r[k]) for d, r in rows.items() if r[k] is not None}
        raw["recovery"] = col("recovery")
        raw["sleep_score"] = col("sleep_score")
        raw["sleep_hours"] = col("sleep_actual_min", lambda v: round(v / 60, 2))
        raw["sleep_need_hours"] = col("sleep_need_min", lambda v: round(v / 60, 2))
        raw["sleep_debt_hours"] = col("sleep_debt_min", lambda v: round(v / 60, 2))
        raw["sleep_performance"] = col("sleep_performance", lambda v: round(100 * v, 1))
        raw["resp_rate"] = col("resp_rate_night")
        raw["strain"] = col("strain")
        raw["muscle_load"] = col("muscle_load")
        for k in ("ctl", "atl", "tsb"):
            raw[k] = col(k)
        trimp = col("trimp")
        raw["sleep_consistency"] = {d: s["consistency"] for d, s in sl.items() if s.get("consistency") is not None}
        raw["awakenings"] = {d: s["awakenings"] for d, s in sl.items() if s.get("awakenings") is not None}
        raw["restorative_hours"] = {d: round(((s.get("deep_min") or 0) + (s.get("rem_min") or 0)) / 60, 2)
                                    for d, s in sl.items() if s.get("deep_min") is not None or s.get("rem_min") is not None}
        raw["restorative_pct"] = {d: round(100 * ((s.get("deep_min") or 0) + (s.get("rem_min") or 0)) / s["asleep_min"], 1)
                                  for d, s in sl.items() if (s.get("deep_min") is not None or s.get("rem_min") is not None)
                                  and s.get("asleep_min")}
        raw["sleep_perf_whoop"] = {d: v for d, r in rows.items() if (v := calc.sleep_performance_whoop(
            r["sleep_performance"], sl.get(d, {}).get("consistency"), sl.get(d, {}).get("efficiency"))) is not None}
        raw["spo2"] = {d: x["spo2_night"] for d, x in rc.items() if x.get("spo2_night") is not None}
        temps = {d: x["wrist_temp"] for d, x in rc.items() if x.get("wrist_temp") is not None}
        raw["wrist_temp"] = {}
        for d, v in temps.items():
            past = [t for k, t in temps.items() if d - timedelta(days=BASELINE_DAYS) <= k < d]
            if len(past) >= 3:
                raw["wrist_temp"][d] = round(v - statistics.fmean(past), 2)
        illness = {d: calc.illness_risk(rc.get(d, {}).get("z"), raw["wrist_temp"].get(d), raw["spo2"].get(d)) for d in rows}
        raw["illness"] = {d: x["score"] for d, x in illness.items() if x}
        ri = {d: calc.recovery_index(rc.get(d, {}).get("rhr_low_offset_min"), sl.get(d, {}).get("asleep_min")) for d in rows}
        raw["recovery_index"] = {d: x["pct"] for d, x in ri.items() if x}
        ml = sorted(v for v in raw["muscle_load"].values() if v)
        med = statistics.median(ml) if ml else None
        raw["strain_combined"] = {d: v for d, r in rows.items()
                                  if (v := calc.combined_strain(r["trimp"], r["muscle_load"], med)) is not None}
        zones = {d: (x.get("load") or {}).get("zone_minutes") for d, x in det.items()}
        raw["aerobic_min"] = {d: round(v) for d, z in zones.items() if (v := calc.aerobic_minutes(z)) is not None}
        span = [lo + timedelta(days=k) for k in range((end - lo).days + 1)]
        raw["acwr"] = {d: v for d in span if trimp.get(d) is not None and (v := calc.acwr(trimp, d)) is not None
                       and sum(1 for k in range(P.CHRONIC_DAYS) if trimp.get(d - timedelta(days=k)) is not None) >= P.MIN_TRIMP_DAYS}
        stress = {r["date"]: r for r in data.get("stress") or []}
        raw["stress"] = {d: round(r["score"], 2) for d, r in stress.items() if r["score"] is not None}
        perf_raw = {d: r["sleep_performance"] for d, r in rows.items() if r["sleep_performance"] is not None}
        raw["resilience"] = {}
        for d in [prev_start + timedelta(days=k) for k in range((end - prev_start).days + 1)]:
            res = calc.resilience(raw["recovery"], perf_raw, raw["stress"], d)
            if res:
                raw["resilience"][d] = round(100 * res["balance"])
        raw["body_age"] = {r["as_of"]: round(r["body_age"], 1) for r in data.get("body_age") or []}

        # recovery colours, contributors, and the Strain Coach target band of each day
        zone_of = {d: coach.recovery_zone(v) for d, v in raw["recovery"].items()}
        cnt = {"green": 0, "yellow": 0, "red": 0}
        for d in dates:
            if zone_of.get(d):
                cnt[zone_of[d]] += 1
        extra["recovery_zones"] = cnt
        extra["recovery_contributors"] = [rc.get(d, {}).get("z") for d in dates]
        tgt = []
        for d in dates:
            r, y = rows.get(d), rows.get(d - timedelta(days=1))
            if r is None:
                tgt.append(None)
                continue
            t = coach.strain_target(r["recovery"], y["tsb"] if y else None, "illness_warning" in (r["flags"] or {}))
            tgt.append([t["target_low"], t["target_high"]])
        hit = sum(1 for d, t in zip(dates, tgt) if t and raw["strain"].get(d) is not None and t[0] <= raw["strain"][d] <= t[1])
        extra["strain_target"] = {"bands": tgt, "in_range_days": hit,
                                  "days_with_strain": sum(1 for d in dates if raw["strain"].get(d) is not None)}
        extra["hr_zones"] = hr_zone_summary(zones, dates)
        extra["aerobic_weeks"] = [{**w, "target": P.AZM_WEEK_TARGET} for w in calc.weekly_sum(raw["aerobic_min"], dates)]

        # status cards as of `end`
        er, es = rows.get(end), stress.get(end)
        status["load_focus"] = calc.load_focus(zones, end)
        status["resilience"] = calc.resilience(raw["recovery"], perf_raw, raw["stress"], end)
        status["illness"] = illness.get(end)
        status["recovery_index"] = ri.get(end)
        status["recovery_time"] = calc.recovery_time(raw["strain"], raw["tsb"], zone_of.get(end), end)
        wake = _local(sl.get(end, {}).get("wake_time"), tz)
        status["body_battery"] = calc.body_battery(
            er["recovery"] if er else None, er["sleep_performance"] if er else None, wake.hour if wake else None,
            (es or {}).get("hours") or [], er["strain"] if er else None,
            until_hour=(datetime.now(tz).hour if now_hour is None else now_hour) if end == today else 23) if er else None
        status["strain_combined"] = None if end not in raw["strain_combined"] else {
            "value": raw["strain_combined"][end], "strain": raw["strain"].get(end), "muscle_median_kg": med}
        status["sleep_perf_whoop"] = None if end not in raw["sleep_perf_whoop"] else {
            "value": raw["sleep_perf_whoop"][end], "sufficiency": perf_raw.get(end),
            "consistency": sl.get(end, {}).get("consistency"), "efficiency": sl.get(end, {}).get("efficiency")}
    else:
        trimp = {}

    if can["metrics"]:
        m = data.get("metrics") or []
        raw["hrv"] = _metric_map(m, "heart_rate_variability_sdnn")
        raw["resting_hr"] = _metric_map(m, "resting_heart_rate")
        raw["vo2max"] = _metric_map(m, "vo2_max")
        raw["steps"] = _metric_map(m, "step_count")
        raw["active_kcal"] = _metric_map(m, "active_energy_burned")
        basal = _metric_map(m, "basal_energy_burned")
        raw["total_kcal"] = {d: v + basal[d] for d, v in raw["active_kcal"].items() if d in basal}
        raw["daylight"] = _metric_map(m, "time_in_daylight")
        if not can["scores"]:   # without the scores scope fall back to Apple's daily values
            raw["spo2"] = _metric_map(m, "oxygen_saturation")
            raw["resp_rate"] = _metric_map(m, "respiratory_rate")
        for k in ("resting_hr", "hrv"):
            bands[k] = baseline_band(raw[k], dates)
        hs = calc.hrv_status(raw["hrv"], end)
        status["hrv_status"] = hs
        if can["scores"]:
            status["training_status"] = calc.training_status(trimp, raw["ctl"], raw["vo2max"], hs, end)

    if can["sleep"]:   # sleep efficiency = asleep / in bed (main sleep); chronotype from the last 60 nights
        sess = data.get("sleep") or []
        raw["sleep_efficiency"] = {r["wake_date"]: min(100.0, round(100 * r["asleep_min"] / float(r["in_bed"]), 1))
                                   for r in sess if r["in_bed"] and r["asleep_min"]}
        status["chronotype"] = calc.chronotype([(_local(r["start_at"], tz), _local(r["end_at"], tz)) for r in sess
                                                if r["wake_date"] > end - timedelta(days=P.CHRONO_DAYS)])

    if can["training"]:   # heart-rate recovery: Apple's one-minute value when synced, else our own from the workout's raw heart rate
        hrr = dict(data.get("hrr_apple") or {})
        for d, v in (data.get("hrr_workouts") or {}).items():
            hrr.setdefault(d, v)
        raw["hr_recovery"] = hrr
        last = max((d for d in hrr if d <= end), default=None)
        status["hr_recovery"] = None if last is None else {
            "bpm": round(hrr[last], 1), "date": last, "level": calc.hr_recovery_level(hrr[last])}

    if can["weight"]:
        if data.get("weights_error") is not None:
            unavailable["weight"] = data["weights_error"]
        else:
            w = data.get("weights") or {}
            ext = [start - timedelta(days=k) for k in range(6, 0, -1)] + dates
            avg = rolling([w.get(d) for d in ext], min_n=1)[6:]
            raw["weight"] = w
            extra["weight_avg7"] = dict(zip(dates, avg))

    for key in [k for k in raw if not can.get(SCOPE.get(k), True)]:
        del raw[key]   # sleep-session details kept in the scores row (bedtime, stages, regularity) need sleep (issue #111)
    if not can["sleep"]:
        status.pop("sleep_perf_whoop", None)
    series = {}
    for key, label, unit, scope, group in SERIES:
        if key not in raw:
            unavailable.setdefault(key, f"没有授权查看 {scope}" if not can.get(scope) else "没有数据")
            continue
        values = [None if raw[key].get(d) is None else round(float(raw[key][d]), 2) for d in dates]
        avg7 = [extra["weight_avg7"][d] for d in dates] if key == "weight" else rolling(values)
        present = [v for v in values if v is not None]
        prev = [float(v) for d, v in sorted(raw[key].items()) if prev_start <= d < start and v is not None]
        series[key] = {"label": tl(label, P.EN), "unit": tl(unit, P.EN), "group": group, "values": values, "avg7": avg7, "n": len(present),
                       "min": min(present) if present else None, "max": max(present) if present else None,
                       "mean": round(sum(present) / len(present), 2) if present else None,
                       "prev_mean": round(sum(prev) / len(prev), 2) if prev else None, "prev_n": len(prev)}
        if key in bands:
            series[key]["baseline"] = bands[key]
    if "recovery" in series:
        series["recovery"]["zones"] = extra["recovery_zones"]
        series["recovery"]["contributors"] = extra["recovery_contributors"]
    if "strain" in series:
        series["strain"]["target"] = extra["strain_target"]
    return {"start": start, "end": end, "days": days, "prev_start": prev_start, "dates": dates, "series": series,
            "unavailable": unavailable, "hr_zones": extra.get("hr_zones"), "aerobic_weeks": extra.get("aerobic_weeks"),
            "status": status, "version": P.TREND_VERSION}
