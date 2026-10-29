"""Kind `daily` of package M1 (derive; docs/ios-native-2.0.md sections 3.2 and 4): the day's values the phone derived.

Rows of one day (PUT /v1/users/me/derived/app), one per (role, metric):
- role "source": the app's own value of a metric (daily_metric_sources, source 'app'): rawdata/derive.daily_values for
  the sync engine's types, derive.phase1_rows for the phase-1 types;
- role "chosen": the day's value among all sources (daily_metrics, importers/common.resolve with the incomplete rules and
  SOURCE_PRIORITY, the app's values taking part as in the sync mode `app`).
Fields: role, metric, source, value, unit, agg (sum / avg), n, min_value, max_value, flags (object).

The spot check recomputes the day with rawdata/derive.derive_days (the Python twin of the phone's src/30_derive.js) from
what the server has: the uploaded HealthKit rows (hk_samples, hk_stats), the other sources' rows (daily_metric_sources),
the app's earlier rows (this upload's, else the server's), the day's workouts. Stored (primary only): the 'app' rows of the
day replace the server's, the chosen rows go to daily_metrics, origin 'app' + bundle_version (migration 1000).
The pull's `daily` bodies get `sources` (extend_pull): every source's row of the day, so the phone can choose itself.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ...i18n import L
from ...importers import common
from ...rawdata import derive

KIND = "daily"
PACKAGE = "derive"
ALGO_KEY = "derive"
# Both sides run the same algorithm (goldens within 1e-9), so a difference means different inputs. Averages: 1e-4
# relative (docs/ios-native-2.0.md section 3.4); sums use the same relative bound (a 10,000-step day: 1 step), which is
# within the contract's 0.5 unit for every day total below 5,000 units. Everything else (n, source, flags, unit) exact.
TOLERANCE = {"value": ("rel", 1e-4), "min_value": ("rel", 1e-4), "max_value": ("rel", 1e-4)}
ROLES = ("source", "chosen")
FIELDS = ("role", "metric", "source", "value", "unit", "agg", "n", "min_value", "max_value", "flags")


def row_key(r: dict) -> tuple:
    return (r["role"], r["metric"])


def _num(v, field: str, nullable: bool = False):
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(L(f"{field} 应该是数字", f"{field} must be a number"))
    return float(v)


def validate(day: date, rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        role, metric, source = r.get("role"), r.get("metric"), r.get("source")
        if role not in ROLES or not isinstance(metric, str) or not metric or len(metric) > 80:
            raise ValueError(L("每一行要有 role（source / chosen）和 metric", "Every row needs a role (source / chosen) and a metric"))
        if not isinstance(source, str) or not source or len(source) > 40 or (role == "source" and source != common.APP_SOURCE):
            raise ValueError(L("来源不对：role=source 的行只能是 app", "Bad source: rows with role=source must be 'app'"))
        if r.get("agg") not in ("sum", "avg"):
            raise ValueError(L("agg 只能是 sum 或 avg", "agg must be sum or avg"))
        n = r.get("n")
        if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
            raise ValueError(L("n 应该是非负整数", "n must be a non-negative integer"))
        unit, flags = r.get("unit"), r.get("flags") or {}
        if (unit is not None and not isinstance(unit, str)) or not isinstance(flags, dict):
            raise ValueError(L("unit / flags 格式不对", "unit / flags have the wrong type"))
        if (role, metric) in seen:
            raise ValueError(L(f"{metric} 重复了", f"{metric} is in the day twice"))
        seen.add((role, metric))
        out.append({"role": role, "metric": metric, "source": source, "value": _num(r.get("value"), "value"),
                    "unit": unit, "agg": r["agg"], "n": n, "min_value": _num(r.get("min_value"), "min_value", True),
                    "max_value": _num(r.get("max_value"), "max_value", True), "flags": flags})
    return out


# ---------------------------------------------------------------- the Python twin of one day
def _tz(conn, uid: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])


def _midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tz)


def source_row(r: dict) -> dict:
    return {"date": r["date"].isoformat(), "metric": r["metric"], "source": r["source"], "value": r["value"],
            "unit": r["unit"], "agg": r["agg"], "n": r["n"], "min_value": r["min_value"], "max_value": r["max_value"],
            "flags": r["flags"] or {}}


def day_src(conn: psycopg.Connection, uid: int, day: date, uploaded: dict[date, list[dict]] | None = None) -> dict:
    """derive.derive_days' input for one day, from the server's tables (stored units)."""
    tz = _tz(conn, uid)
    samples = [{"uuid": str(r["hk_uuid"]), "type": r["type"], "start": r["start_at"], "end": r["end_at"], "value": r["value"],
                "source": r["source"] or "", "source_id": r["source_id"], "device_type": r["device_type"],
                "device_name": r["device_name"]}
               for r in conn.execute(
                   """SELECT h.hk_uuid, h.type, h.start_at, h.end_at, h.value, h.source, h.source_id, s.device_type,
                             s.device_name
                      FROM hk_samples h LEFT JOIN hk_sources s ON s.id = h.source_id
                      WHERE h.user_id = %s AND h.local_date = %s""", (uid, day))]
    buckets = [{"type": r["type"], "start": r["bucket_start"], "value": r["value"]} for r in conn.execute(
        "SELECT type, bucket_start, value FROM hk_stats WHERE user_id = %s AND bucket_start >= %s AND bucket_start < %s",
        (uid, _midnight(day, tz), _midnight(day + timedelta(days=1), tz)))]
    lo = day - timedelta(days=common.BASAL_REF_DAYS)
    rows = conn.execute("""SELECT date, metric, source, value, unit, agg, n, min_value, max_value, flags FROM daily_metric_sources
                           WHERE user_id = %s AND date >= %s AND date <= %s""", (uid, lo, day)).fetchall()
    uploaded = uploaded or {}
    up_app = [{**r, "date": d.isoformat()} for d, rs in uploaded.items() if lo <= d < day
              for r in rs if r["role"] == "source"]
    history = up_app + [source_row(r) for r in rows if r["source"] == common.APP_SOURCE and r["date"] < day
                        and r["date"] not in uploaded]
    kcal = conn.execute("""SELECT sum(active_kcal) AS kcal FROM workouts WHERE user_id = %s AND local_date = %s
                           AND dup_of IS NULL AND excluded IS NULL AND active_kcal IS NOT NULL""", (uid, day)).fetchone()["kcal"]
    return {"tz": str(tz), "today": datetime.now(tz).date().isoformat(), "days": [day.isoformat()], "wake_dates": [],
            "units": "stored", "samples": samples, "buckets": buckets,
            "other": [source_row(r) for r in rows if r["source"] != common.APP_SOURCE], "app_history": history,
            "workout_kcal": {day.isoformat(): kcal} if kcal is not None else {}}


def rows_of(res: dict, day: date) -> list[dict]:
    """derive_days' answer for one day as upload rows."""
    got = res["daily"].get(day.isoformat()) or {"sources": [], "chosen": []}
    return ([{"role": "source", **{k: r[k] for k in FIELDS if k != "role"}} for r in got["sources"]]
            + [{"role": "chosen", **{k: r[k] for k in FIELDS if k != "role"}} for r in got["chosen"]])


def recompute(conn: psycopg.Connection, uid: int, day: date, uploaded: dict[date, list[dict]]) -> list[dict] | None:
    src = day_src(conn, uid, day, uploaded)
    if not src["samples"] and not src["buckets"]:
        return None   # the server has none of the phone's HealthKit rows of that day (not synced yet)
    return rows_of(derive.derive_days(src), day)


# ---------------------------------------------------------------- primary: the phone's rows become the user's
def write(conn: psycopg.Connection, uid: int, days: dict[date, list[dict]], bundle_version: str | None) -> int:
    n = 0
    for d, rows in days.items():
        conn.execute("DELETE FROM daily_metric_sources WHERE user_id = %s AND date = %s AND source = %s",
                     (uid, d, common.APP_SOURCE))
        src = [r for r in rows if r["role"] == "source"]
        chosen = [r for r in rows if r["role"] == "chosen"]
        with conn.cursor() as cur:
            if src:
                cur.executemany(
                    """INSERT INTO daily_metric_sources (user_id, date, metric, source, value, unit, agg, n, min_value,
                                                         max_value, flags, origin, bundle_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'app', %s)""",
                    [(uid, d, r["metric"], r["source"], r["value"], r["unit"], r["agg"], r["n"], r["min_value"],
                      r["max_value"], json.dumps(r["flags"], ensure_ascii=False), bundle_version) for r in src])
            if chosen:
                cur.executemany(
                    """INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, min_value, max_value, source,
                                                  flags, origin, bundle_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'app', %s)
                       ON CONFLICT (user_id, date, metric) DO UPDATE SET value = EXCLUDED.value, unit = EXCLUDED.unit,
                         agg = EXCLUDED.agg, n = EXCLUDED.n, min_value = EXCLUDED.min_value,
                         max_value = EXCLUDED.max_value, source = EXCLUDED.source, flags = EXCLUDED.flags,
                         origin = 'app', bundle_version = EXCLUDED.bundle_version, updated_at = now()""",
                    [(uid, d, r["metric"], r["value"], r["unit"], r["agg"], r["n"], r["min_value"], r["max_value"],
                      r["source"], json.dumps(r["flags"], ensure_ascii=False), bundle_version) for r in chosen])
        # a metric no source has any more loses its chosen row (as rawdata/derive._write_daily)
        conn.execute("""DELETE FROM daily_metrics m WHERE m.user_id = %s AND m.date = %s AND NOT (m.metric = ANY(%s))
                        AND NOT EXISTS (SELECT 1 FROM daily_metric_sources s WHERE s.user_id = m.user_id
                                        AND s.date = m.date AND s.metric = m.metric)""",
                     (uid, d, [r["metric"] for r in chosen]))
        n += len(rows)
    return n


# ---------------------------------------------------------------- pull: every source of the day
def extend_pull(stream: str, kind: str, conn: psycopg.Connection, uid: int, found: dict) -> None:
    """`sources` in the daily bodies: {metric: [{source, value, unit, agg, n, min, max, flags}, ...]}."""
    if stream != "daily" or not found:
        return
    for r in conn.execute("""SELECT date, metric, source, value, unit, agg, n, min_value, max_value, flags
                             FROM daily_metric_sources WHERE user_id = %s AND date = ANY(%s) ORDER BY date, metric, source""",
                          (uid, [d for d in found if d is not None])):
        body = found.get(r["date"])
        if body is None:
            continue
        body.setdefault("sources", {}).setdefault(r["metric"], []).append(
            {"source": r["source"], "value": r["value"], "unit": r["unit"], "agg": r["agg"], "n": r["n"],
             "min": r["min_value"], "max": r["max_value"], "flags": r["flags"] or {}})
