"""Apple sync engine, step S3: the two sync paths of one user, day by day, side by side (docs/apple-sync-engine.md §4) -
what the shadow comparison (step S4: verdicts, reasons, the nightly job, the diff table) is built on, and what a
maintainer looks at before it exists. Reads only; works in every sync mode.

For each local day:
- daily: every metric with a value from Open Wearables (daily_metric_sources source 'ow') or from the app (source 'app',
  derived by rawdata/derive.py or the phase-1 direct upload): value / min / max / count of each, app − ow and the
  relative difference, and the source daily_metrics chose;
- sleep: the OW nights stored in sleep_sessions (source 'ow'; while the app mode puts the app's night there, the OW one
  is replaced) and the app's nights computed now from the stage samples (rawdata/derive.nights, as the app mode writes
  them), by wake date;
- workouts: the OW and app rows of workouts that day (app ones are excluded = 'shadow' outside the app mode), with
  type, times, energy and heart rate.
Health values: own data only (the API) or the deployer's terminal (`body-engine sync-shadow-day`), never into git.
"""
from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import psycopg

from . import derive as DV
from . import engine as EN
from . import hk_types as T

MAX_DAYS = 31
SLEEP_KEYS = ("asleep_min", "core_min", "deep_min", "rem_min", "awake_min", "unspecified_min")
WORKOUT_COLS = ("source", "external_id", "type", "start_at", "end_at", "duration_min", "total_kcal", "active_kcal",
                "distance_km", "avg_hr", "max_hr", "trimp", "excluded", "dup_of")


def _num(x):
    return None if x is None else round(float(x), 4)


def _daily(conn, user_id: int, lo: date, hi: date) -> dict[date, list[dict]]:
    chosen = {(r["date"], r["metric"]): r["source"] for r in conn.execute(
        "SELECT date, metric, source FROM daily_metrics WHERE user_id = %s AND date BETWEEN %s AND %s", (user_id, lo, hi))}
    by: dict[tuple, dict] = {}
    for r in conn.execute("""SELECT date, metric, source, value, unit, agg, n, min_value, max_value FROM daily_metric_sources
                             WHERE user_id = %s AND date BETWEEN %s AND %s AND source IN ('ow', 'app')
                             ORDER BY date, metric, source""", (user_id, lo, hi)):
        row = by.setdefault((r["date"], r["metric"]), {"metric": r["metric"], "unit": r["unit"], "agg": r["agg"],
                                                       "ow": None, "app": None})
        row[r["source"]] = {"value": _num(r["value"]), "min": _num(r["min_value"]), "max": _num(r["max_value"]), "n": r["n"]}
    direct = {t.metric for t in T.HK_TYPES}
    out: dict[date, list[dict]] = {}
    for (d, metric), row in by.items():
        ow, app = (row["ow"] or {}).get("value"), (row["app"] or {}).get("value")
        diff = None if ow is None or app is None else round(app - ow, 4)
        out.setdefault(d, []).append({**row, "diff": diff, "rel": None if diff is None or not ow else round(diff / abs(ow), 5),
                                      "chosen": chosen.get((d, metric)), "direct": metric in direct})
    return out


def _night(n, tz: ZoneInfo) -> dict:
    return {"start_at": n["start_at"].astimezone(tz), "end_at": n["end_at"].astimezone(tz), "device": n["device"],
            **{k: n[k] for k in SLEEP_KEYS}}


def _sleep(conn, user_id: int, days: list[date], tz: ZoneInfo) -> dict[date, dict]:
    out = {d: {"ow": [], "app": []} for d in days}
    for r in conn.execute("""SELECT wake_date, start_at, end_at, device, is_main, asleep_min, core_min, deep_min, rem_min,
                                    awake_min, unspecified_min FROM sleep_sessions
                             WHERE user_id = %s AND source = 'ow' AND wake_date = ANY(%s) ORDER BY wake_date, start_at""",
                          (user_id, days)):
        out[r["wake_date"]]["ow"].append({**_night(r, tz), "is_main": r["is_main"]})
    nights = sorted(DV.nights(conn, user_id, set(days), tz), key=lambda n: n.start)
    for d in days:
        mine = [n for n in nights if n.wake_date == d]
        main = max(mine, key=lambda n: n.asleep_min, default=None)
        for n in mine:
            m = n.minutes
            out[d]["app"].append({"start_at": n.start.astimezone(tz), "end_at": n.end.astimezone(tz), "device": n.device, "is_main": n is main,
                                  "asleep_min": n.asleep_min, **{f"{k}_min": round(m[k]) for k in
                                                                 ("core", "deep", "rem", "awake", "unspecified")}})
    return out


def _workouts(conn, user_id: int, lo: date, hi: date, tz: ZoneInfo) -> dict[date, dict]:
    def val(k, x):
        return _num(x) if isinstance(x, float) else x.astimezone(tz) if k in ("start_at", "end_at") else x
    out: dict[date, dict] = {}
    for r in conn.execute(f"""SELECT local_date, {", ".join(WORKOUT_COLS)} FROM workouts
                              WHERE user_id = %s AND local_date BETWEEN %s AND %s AND source IN ('ow', 'app')
                              ORDER BY local_date, start_at, source""", (user_id, lo, hi)):
        out.setdefault(r["local_date"], {"ow": [], "app": []})[r["source"]].append(
            {k: val(k, r[k]) for k in WORKOUT_COLS if k != "source"})
    return out


def days(conn: psycopg.Connection, user_id: int, start: date, end: date | None = None) -> dict:
    """{user_id, mode, days: [{date, daily, sleep, workouts}]} for start..end (at most MAX_DAYS days)."""
    end = end or start
    if end < start or (end - start).days >= MAX_DAYS:
        raise ValueError(f"1–{MAX_DAYS} days")
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    daily, sleep = _daily(conn, user_id, start, end), _sleep(conn, user_id, span, tz)
    workouts = _workouts(conn, user_id, start, end, tz)
    return {"user_id": user_id, "mode": EN.mode(conn, user_id), "days": [
        {"date": d, "daily": daily.get(d, []), "sleep": sleep[d], "workouts": workouts.get(d, {"ow": [], "app": []})}
        for d in span]}


# ---------------------------------------------------------------- terminal report (Chinese, like hrv-compare)
def _fmt(x) -> str:
    if x is None:
        return "—"
    return f"{x:.4g}" if isinstance(x, float) else str(x)


def markdown(res: dict) -> str:
    """Chinese Markdown with health values: the terminal or health_data/reports/ only (never git)."""
    out = [f"# 两条同步链路逐日对照（user {res['user_id']}，当前模式 {res['mode']}）", ""]
    for day in res["days"]:
        out += [f"## {day['date']}", "", "| 指标 | 口径 | OW | app | app − OW | 相对差 | 选中 |", "|---|---|---|---|---|---|---|"]
        for r in day["daily"]:
            ow, app = r["ow"] or {}, r["app"] or {}
            rel = f"{r['rel']:+.2%}" if r["rel"] is not None else "—"
            out.append(f"| {r['metric']}{'（直传）' if r['direct'] else ''} | {r['agg']} {r['unit']} | {_fmt(ow.get('value'))}"
                       f"{'' if not ow.get('n') else ' ×' + str(ow['n'])} | {_fmt(app.get('value'))}"
                       f"{'' if not app.get('n') else ' ×' + str(app['n'])} | {_fmt(r['diff'])} | {rel} | {r['chosen'] or '—'} |")
        if not day["daily"]:
            out.append("| （这天两边都没有日值） | | | | | | |")
        out += ["", "睡眠（按醒来日期）：", ""]
        for side in ("ow", "app"):
            ns = day["sleep"][side]
            out.append(f"- {side}：" + ("；".join(
                f"{n['start_at']:%H:%M}–{n['end_at']:%H:%M} 睡着 {n['asleep_min']} 分（核心 {n['core_min']} / 深 {n['deep_min']}"
                f" / REM {n['rem_min']} / 醒 {n['awake_min']}）{'主' if n.get('is_main') else ''}" for n in ns) or "无"))
        out += ["", "训练：", ""]
        for side in ("ow", "app"):
            ws = day["workouts"][side]
            out.append(f"- {side}：" + ("；".join(
                f"{w['type']} {w['start_at']:%H:%M}–{w['end_at']:%H:%M} {_fmt(w['duration_min'])} 分 总 {_fmt(w['total_kcal'])}"
                f" / 活动 {_fmt(w['active_kcal'])} kcal 心率 {_fmt(w['avg_hr'])}/{_fmt(w['max_hr'])}"
                f"{' 影子' if w['excluded'] == 'shadow' else ''}{' 副本' if w['dup_of'] else ''}" for w in ws) or "无"))
        out.append("")
    return "\n".join(out)
