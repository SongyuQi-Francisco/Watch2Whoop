"""Kind `workouts` of package M1c (docs/ios-native-2.0.md sections 3.2 and 4): the phone's own workouts, enriched.

Rows of one day = the app's workouts (its HKWorkouts, source 'app') that started that local day, in start order, as
training/importer.workout_days answers (the Python twin of src/31_workouts.js `workoutDays`, the sync mode `app`):
{external_id (HealthKit UUID), type, name_zh, local_date, start, end (UTC seconds), duration_min, active_kcal, total_kcal,
 distance_km, avg_hr, max_hr, zone_offset, device, origin, excluded, dup_of ("source:external_id" of the copy it
 duplicates), trimp, zone1_min … zone5_min, hr_samples, hr_max_used, hr_recovery_bpm, intensity, flags,
 hrr60, hrr_peak, hrr_60s, hrr_valid, hrr_reason (cardio/hrr.hrr60)}.

The spot check recomputes the day with workout_days from what the server has: the app's HKWorkouts (hk_samples) of the
day and the days around it, the other sources' workouts (duplicates), the user's corrections (workout_overrides,
workout_type_rules), the uploaded heart rate around them (rawdata/samples, watch first), the day's HRmax (daily_scores)
and the birth date. Stored (primary only): the enrichment columns of the server's row of the same workout
(source 'app', external_id) and its workout_hrr row, computed_by 'app' + bundle_version (migration 1010); the server's own
import of the workout itself (rawdata/derive) is unchanged. The `records` pull's workout bodies carry `hrr`.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ...i18n import L
from ...rawdata import derive
from ...training import importer as TI, params as TP

KIND = "workouts"
PACKAGE = "workouts"
ALGO_KEY = "workouts"
# docs/ios-native-2.0.md section 3.4: TRIMP 0.1 (its stored rounding), each zone ±0.1 min (stored rounding), heart-rate
# recovery 0.5 bpm (HRR and hrr60 / its peak / its 60-s value: one decimal each, interpolated between samples); HRmax 1 bpm
# (the day's scores, scores' own CHECK_TOLERANCE); start / end 1 s and the minutes / energy 0.01 (rounded to two / one
# decimals on both sides). dup_of, intensity, type, excluded, flags (texts) exact.
TOLERANCE = {"trimp": 0.1, **{f"zone{i}_min": 0.1 for i in range(1, 6)}, "hr_recovery_bpm": 0.5, "hrr60": 0.5,
             "hrr_peak": 0.5, "hrr_60s": 0.5, "hr_max_used": 1.0, "start": 1.0, "end": 1.0, "duration_min": 0.01,
             "active_kcal": 0.1, "total_kcal": 0.1, "distance_km": 0.001}
NUMBERS = ("start", "end", "duration_min", "active_kcal", "total_kcal", "distance_km", "avg_hr", "max_hr", "trimp",
           *(f"zone{i}_min" for i in range(1, 6)), "hr_max_used", "hr_recovery_bpm", "hrr60", "hrr_peak", "hrr_60s")
TEXTS = ("external_id", "type", "name_zh", "local_date", "zone_offset", "device", "origin", "excluded", "dup_of",
         "intensity", "hrr_reason")
FIELDS = ("external_id", "type", "name_zh", "local_date", "start", "end", "duration_min", "active_kcal", "total_kcal",
          "distance_km", "avg_hr", "max_hr", "zone_offset", "device", "origin", "excluded", "dup_of", "trimp",
          "hr_samples", "hr_max_used", *(f"zone{i}_min" for i in range(1, 6)), "hr_recovery_bpm", "intensity",
          "hrr60", "hrr_peak", "hrr_60s", "hrr_valid", "hrr_reason", "flags")
HR_FLAGS = ("hr_recovery_bpm", "hr_coverage_low", "no_raw_hr", "computed")
HR_MARGIN = timedelta(minutes=5)   # heart rate read around the day's workouts (hrr60 looks 2 min before the end)


def row_key(r: dict) -> str:
    return r["external_id"]


def _num(v, field: str):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(L(f"{field} 应该是数字", f"{field} must be a number"))
    return float(v)


def validate(day: date, rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        ext = r.get("external_id")
        if not isinstance(ext, str) or not ext or len(ext) > 80 or ext in seen:
            raise ValueError(L("每次训练要有唯一的 external_id", "Every workout needs a unique external_id"))
        seen.add(ext)
        if r.get("local_date") != day.isoformat():
            raise ValueError(L("训练的 local_date 和这一天对不上", "A workout's local_date is not this day"))
        if not isinstance(r.get("type"), str) or not r["type"]:
            raise ValueError(L("训练要有类型", "Every workout needs a type"))
        row = {k: _num(r.get(k), k) for k in NUMBERS}
        if row["start"] is None or row["end"] is None or row["end"] < row["start"] or row["duration_min"] is None:
            raise ValueError(L("训练要有开始、结束时间和时长", "Every workout needs a start, an end and a duration"))
        for k in TEXTS:
            v = r.get(k)
            if v is not None and (not isinstance(v, str) or len(v) > 500):
                raise ValueError(L(f"{k} 格式不对", f"{k} has the wrong type"))
            row[k] = v
        if row["intensity"] is not None and row["intensity"] not in TP.INTENSITY_LEVELS:
            raise ValueError(L("intensity 不对", "Unknown intensity"))
        n = r.get("hr_samples")
        if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
            raise ValueError(L("hr_samples 应该是非负整数", "hr_samples must be a non-negative integer"))
        if r.get("hrr_valid") is not None and not isinstance(r["hrr_valid"], bool):
            raise ValueError(L("hrr_valid 应该是 true / false", "hrr_valid must be a boolean"))
        if not isinstance(r.get("flags") or {}, dict):
            raise ValueError(L("flags 应该是对象", "flags must be an object"))
        row.update(hr_samples=n, hrr_valid=r.get("hrr_valid"), flags=r.get("flags") or {})
        out.append({k: row[k] for k in FIELDS})
    return sorted(out, key=lambda x: (x["start"], x["external_id"]))


# ---------------------------------------------------------------- the Python twin of one day
def _tz(conn, uid: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])


def day_src(conn: psycopg.Connection, uid: int, day: date) -> dict:
    """workout_days' input for one day from the server's tables (the days around it for the duplicates)."""
    from ...rawdata import samples as rx
    tz = _tz(conn, uid)
    hk = [{"uuid": r["uuid"], "start": r["start_at"].timestamp(), "end": r["end_at"].timestamp(), "value": r["value"],
           "value_text": r["value_text"], "meta": r["meta"], "tz": r["tz"], "source": r["source"],
           "source_name": r["source_name"], "device_type": r["device_type"], "device_name": r["device_name"]}
          for r in conn.execute(
              """SELECT h.hk_uuid::text AS uuid, h.start_at, h.end_at, h.value, h.value_text, h.meta, h.tz, h.source,
                        s.name AS source_name, s.device_type, s.device_name
                 FROM hk_samples h LEFT JOIN hk_sources s ON s.id = h.source_id
                 WHERE h.user_id = %s AND h.type = %s AND h.local_date BETWEEN %s AND %s ORDER BY h.start_at""",
              (uid, derive.WORKOUT, day - timedelta(days=1), day + timedelta(days=1)))]
    rows = conn.execute("""SELECT id, source, external_id, type, start_at, end_at, is_xml, device, excluded, dup_of, flags
                           FROM workouts WHERE user_id = %s AND source <> %s AND local_date BETWEEN %s AND %s""",
                        (uid, TI.APP_SOURCE, day - timedelta(days=1), day + timedelta(days=1))).fetchall()
    keys = {r["id"]: f"{r['source']}:{r['external_id']}" for r in conn.execute(
        "SELECT id, source, external_id FROM workouts WHERE user_id = %s AND id = ANY(%s)",
        (uid, [r["dup_of"] for r in rows if r["dup_of"]]))}
    others = [{"source": r["source"], "external_id": r["external_id"], "type": r["type"],
               "start": r["start_at"].timestamp(), "end": r["end_at"].timestamp(), "is_xml": r["is_xml"],
               "device": r["device"], "excluded": r["excluded"],
               "manual_dup": bool((r["flags"] or {}).get("manual_dup") and r["dup_of"]),
               "dup_of": keys.get(r["dup_of"]) if (r["flags"] or {}).get("manual_dup") else None} for r in rows]
    overrides = [{"source": r["source"], "external_id": r["external_id"], "type": r["type"], "name_zh": r["name_zh"],
                  "hidden": r["hidden"]} for r in conn.execute(
        "SELECT source, external_id, type, name_zh, hidden FROM workout_overrides WHERE user_id = %s AND source = %s",
        (uid, TI.APP_SOURCE))]
    rules = [{"weekday": r["weekday"], "start_hour": r["start_hour"], "to_type": r["to_type"], "name_zh": r["name_zh"]}
             for r in conn.execute("SELECT weekday, start_hour, to_type, name_zh FROM workout_type_rules WHERE user_id = %s "
                                   "ORDER BY id", (uid,))]
    mine = [w for w in hk if datetime.fromtimestamp(w["start"], tz).date() == day]
    hr: list = []
    if mine:
        lo = datetime.fromtimestamp(min(w["start"] for w in mine), tz) - HR_MARGIN
        hi = datetime.fromtimestamp(max(w["end"] for w in mine), tz) + HR_MARGIN
        got = rx.samples(conn, uid, ["heart_rate"], lo, hi).get("heart_rate", [])
        hr = [[s.ts.timestamp(), s.value] for s in sorted(got, key=lambda s: (s.priority, s.ts))]
    hm = conn.execute("SELECT hr_max FROM daily_scores WHERE user_id = %s AND date = %s", (uid, day)).fetchone()
    birth = conn.execute("SELECT birth_date FROM users WHERE id = %s", (uid,)).fetchone()["birth_date"]
    return {"tz": str(tz), "days": [day.isoformat()], "hk_workouts": hk, "others": others, "overrides": overrides,
            "type_rules": rules, "hr": hr, "hr_max": {day.isoformat(): hm["hr_max"]} if hm and hm["hr_max"] else {},
            "birth_date": birth.isoformat() if birth else None, "_mine": len(mine)}


def recompute(conn: psycopg.Connection, uid: int, day: date, uploaded: dict[date, list[dict]]) -> list[dict] | None:
    src = day_src(conn, uid, day)
    if not src.pop("_mine"):
        return None   # none of the phone's workouts of that day are here (yet): nothing to compare with
    return TI.workout_days(src)["days"].get(day.isoformat(), [])


# ---------------------------------------------------------------- primary: the phone's enrichment becomes the user's
def write(conn: psycopg.Connection, uid: int, days: dict[date, list[dict]], bundle_version: str | None) -> int:
    n = 0
    for d, rows in days.items():
        for r in rows:
            w = conn.execute("""SELECT id, flags FROM workouts WHERE user_id = %s AND source = %s AND external_id = %s""",
                             (uid, TI.APP_SOURCE, r["external_id"])).fetchone()
            if w is None:
                continue   # the server has not imported this workout yet; its own import enriches it
            flags = {k: v for k, v in (w["flags"] or {}).items() if k not in HR_FLAGS} | r["flags"]
            if r["hr_recovery_bpm"] is not None:
                flags["hr_recovery_bpm"] = r["hr_recovery_bpm"]
            zones = [r[f"zone{i}_min"] for i in range(1, 6)]
            dup = None
            if r["dup_of"]:
                src, _, ext = r["dup_of"].partition(":")
                hit = conn.execute("SELECT id FROM workouts WHERE user_id = %s AND source = %s AND external_id = %s",
                                   (uid, src, ext)).fetchone()
                dup = hit["id"] if hit else None
            conn.execute(
                """UPDATE workouts SET trimp = %s, hr_zone_min = %s, hr_samples = %s, hr_max_used = %s, flags = %s,
                                      dup_of = CASE WHEN %s THEN %s ELSE dup_of END, computed_by = 'app',
                                      bundle_version = %s, updated_at = now()
                   WHERE id = %s""",
                (r["trimp"], None if all(z is None for z in zones) else zones, r["hr_samples"], r["hr_max_used"],
                 Jsonb(flags), dup is not None or r["dup_of"] is None, dup, bundle_version, w["id"]))
            if r["hrr_valid"] is None:
                conn.execute("DELETE FROM workout_hrr WHERE workout_id = %s", (w["id"],))
            else:
                conn.execute(
                    """INSERT INTO workout_hrr (workout_id, user_id, local_date, end_at, peak_hr, hr_60s, hrr60, hr_max_used,
                                                valid, reason, algo, computed_by, bundle_version)
                       VALUES (%s, %s, %s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, 'app', %s)
                       ON CONFLICT (workout_id) DO UPDATE SET local_date = EXCLUDED.local_date, end_at = EXCLUDED.end_at,
                         peak_hr = EXCLUDED.peak_hr, hr_60s = EXCLUDED.hr_60s, hrr60 = EXCLUDED.hrr60,
                         hr_max_used = EXCLUDED.hr_max_used, valid = EXCLUDED.valid, reason = EXCLUDED.reason,
                         algo = EXCLUDED.algo, computed_by = 'app', bundle_version = EXCLUDED.bundle_version,
                         computed_at = now()""",
                    (w["id"], uid, d, r["end"], r["hrr_peak"], r["hrr_60s"], r["hrr60"],
                     r["hr_max_used"],
                     r["hrr_valid"], r["hrr_reason"], _hrr_algo(), bundle_version))
            n += 1
    return n


def _hrr_algo() -> str:
    from ...cardio import hrr
    return hrr.ALGO


# ---------------------------------------------------------------- pull: heart-rate recovery in the workout bodies
def extend_pull(stream: str, kind: str, conn: psycopg.Connection, uid: int, found: dict) -> None:
    """`hrr` in each item of the records stream's workout bodies: {peak_hr, hr_60s, hrr60, hr_max_used, valid, reason}."""
    if stream != "records" or kind != "workout" or not found:
        return
    ids = [it["id"] for body in found.values() for it in (body or {}).get("items") or [] if it.get("id") is not None]
    if not ids:
        return
    by = {r["workout_id"]: {"peak_hr": r["peak_hr"], "hr_60s": r["hr_60s"], "hrr60": r["hrr60"],
                            "hr_max_used": r["hr_max_used"], "valid": r["valid"], "reason": r["reason"]}
          for r in conn.execute("""SELECT workout_id, peak_hr, hr_60s, hrr60, hr_max_used, valid, reason FROM workout_hrr
                                   WHERE user_id = %s AND workout_id = ANY(%s)""", (uid, ids))}
    for body in found.values():
        for it in (body or {}).get("items") or []:
            if it.get("id") in by:
                it["hrr"] = by[it["id"]]
