"""Workout import: Open Wearables workouts (/events/workouts) -> workouts table; the Shortcut's Workout rows are in shortcut_workouts.py, sharing the database writes and dedup here.

- Each source is stored as-is, unique key (user_id, source, external_id), re-imports only update; multiple copies of one workout
  (the export.xml import in OW + the live watch sync; OW + Shortcut) are marked with dup_of, keeping the highest-priority one (refresh_duplicates).
- OW's XML import: energy is stored in export.xml's unit (kJ in Australia) but labelled kcal -> converted; no distance, device name is just Watch.
- Heart rate, TRIMP, active energy: computed from raw OW samples (heart rate, active energy, basal energy) over the workout window (enrich):
  - TRIMP uses the same set as the scoring module: Edwards five zones (50/60/70/80/90% HRmax, weights 1–5), HRmax from that day's daily_scores.hr_max
    (208 − 0.7 × age if absent); a heart-rate sample counts until the next one or 5 minutes at most, up to the workout end. Windows with heart-rate coverage under 60% get no TRIMP.
  - OW's calories_kcal is total energy (active + basal, checked against samples: the 2026-09-04 strength workout 302.9 = active 213.3 + basal 90.5).
    Active energy: when active + basal samples in the window sum to >= 85% of total energy, split by sample ratio; otherwise (samples not fully synced) use total energy − estimated basal energy, flagged active_kcal_estimated.
- Times are always converted to the user's time zone; local_date = the start day (a workout crossing midnight counts for its start day).
- OW incremental: import-ow re-pulls whole workouts within the days pulled each time, and anything already deleted in OW is deleted here too (only source=ow within the pulled range).
- The app's own sync engine (source=app, docs/apple-sync-engine.md §3.2.4): parse_app turns its HKWorkouts (hk_samples) into the same
  rows (rawdata/derive.py writes them); outside the app sync mode they are stored with excluded = 'shadow'.
"""
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..importers import ow as ow_imp
from ..scores import strain as L
from . import params as P

KJ_PER_KCAL = 4.184
APP_SOURCE = "app"     # the app's own sync engine (rawdata/derive.py)
SHADOW = "shadow"      # excluded value of the engine's workouts outside the app sync mode (docs/apple-sync-engine.md D4)
FIELDS = ["origin", "is_xml", "device", "type", "name_zh", "local_date", "start_at", "end_at", "duration_min",
          "active_kcal", "total_kcal", "distance_km", "avg_hr", "max_hr", "zone_offset", "excluded"]


def type_name(t: str) -> str:
    return P.TYPE_NAMES.get(t, t.replace("_", " "))


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz)


def _base_row(tz: ZoneInfo, start: datetime, end: datetime) -> dict:
    flags = {}
    excluded = None
    if end - start > P.MAX_DURATION:
        excluded = "implausible_duration"
        flags["implausible_duration"] = f"时长 {(end - start).total_seconds() / 3600:.0f} 小时，像是没结束的记录，不计入"
    return {"local_date": _local(start, tz).date(), "start_at": start, "end_at": end,
            "duration_min": round((end - start).total_seconds() / 60, 2), "excluded": excluded, "flags": flags}


# ---------------------------------------------------------------- OW
def parse_ow(raw: dict, tz: ZoneInfo, xml_energy_unit: str = P.XML_ENERGY_UNIT) -> dict:
    src = raw.get("source") or {}
    xml = src.get("source") == ow_imp.XML_SOURCE
    start, end = datetime.fromisoformat(raw["start_time"]), datetime.fromisoformat(raw["end_time"])
    row = _base_row(tz, start, end)
    if raw.get("duration_seconds") and not row["excluded"]:
        # OW's duration_seconds is HealthKit's workout duration (paused time excluded), more accurate than end − start
        row["duration_min"] = round(raw["duration_seconds"] / 60, 2)
    kcal = raw.get("calories_kcal")
    if kcal is not None and xml and xml_energy_unit == "kJ":
        kcal /= KJ_PER_KCAL
    t = raw.get("type") or "other"
    if t in P.GENERIC_TYPES and raw.get("name"):
        # OW only knew "other": its label often still carries the HealthKit activity type (e.g. HKWorkoutActivityTypeBasketball)
        from .shortcut_workouts import shortcut_type
        if (m := shortcut_type(raw["name"])) in P.TYPE_NAMES:
            t = m
    row.update(source="ow", external_id=raw["id"], origin=src.get("source"), is_xml=xml,
               device=src.get("device_name") or src.get("device"), type=t, name_zh=type_name(t),
               total_kcal=None if kcal is None else round(kcal, 1), active_kcal=None,
               distance_km=None if raw.get("distance_meters") is None else round(raw["distance_meters"] / 1000, 3),
               avg_hr=raw.get("avg_heart_rate_bpm"), max_hr=raw.get("max_heart_rate_bpm"),
               zone_offset=raw.get("zone_offset"), device_type=src.get("device_type"))
    return row


# ---------------------------------------------------------------- the app's sync engine (hk_samples)
def _zone_offset(at: datetime, tz: ZoneInfo) -> str:
    off = at.astimezone(tz).utcoffset() or timedelta(0)
    sign, minutes = ("-" if off < timedelta(0) else "+"), abs(int(off.total_seconds())) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def parse_app(r: dict, tz: ZoneInfo, shadow: bool) -> dict:
    """One HKWorkout the app's sync engine uploaded (an hk_samples row joined with its hk_sources row: value = duration in
    minutes without pauses, value_text = activity name, meta = {activity, stats, events, meta}) -> a workouts row, with
    the same definitions as parse_ow: the OW type of that activity (P.hk_workout_type), HealthKit's duration, total energy
    = active + basal (OW's calories_kcal). Active energy comes straight from the workout's statistics (the OW import has
    to split it from samples, enrich). shadow: stored with excluded = 'shadow' (not shown, not in any total)."""
    meta = r.get("meta") or {}
    stats = meta.get("stats") or {}
    row = _base_row(tz, r["start_at"], r["end_at"])
    if r["value"] and not row["excluded"]:
        row["duration_min"] = round(r["value"], 2)   # HealthKit's duration (paused time excluded), like OW's duration_seconds
    active, basal = stats.get("active_kcal"), stats.get("basal_kcal")
    total = None if active is None and basal is None else (active or 0.0) + (basal or 0.0)
    t = P.hk_workout_type(meta.get("activity"), r.get("value_text"))
    zone = r.get("tz")
    try:
        ztz = ZoneInfo(zone) if zone else tz
    except (ValueError, KeyError):
        ztz = tz
    dist = stats.get("distance_m")
    row.update(source=APP_SOURCE, external_id=str(r["uuid"]), origin=r.get("source_name") or r.get("source") or None,
               is_xml=False, device=r.get("device_name") or r.get("source_name") or r.get("source") or None, type=t,
               name_zh=type_name(t), total_kcal=None if total is None else round(total, 1),
               active_kcal=None if active is None else round(active, 1),
               distance_km=None if dist is None else round(dist / 1000, 3),
               avg_hr=stats.get("avg_hr"), max_hr=stats.get("max_hr"),
               zone_offset=_zone_offset(r["start_at"], ztz), device_type=r.get("device_type") or None)
    if shadow:
        row["excluded"] = SHADOW
    return row


# ---------------------------------------------------------------- write to the database
def upsert(conn: psycopg.Connection, user_id: int, rows: list[dict]) -> dict:
    """Insert or update by (user_id, source, external_id); returns {inserted, updated, unchanged, ids}.
    User overrides / type rules (overrides.py) are applied first, so a re-import never undoes a correction.
    The flags set at import time (implausible duration, energy from the Shortcut) are rewritten each time; the results of heart-rate / energy computation (enrich) are kept."""
    from . import overrides
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    ids = []
    ov = overrides.load(conn, user_id)
    for r in rows:
        r = overrides.apply(dict(r), ov)   # the user's corrections (type, name, hidden) and type rules win over the import
        old = conn.execute("SELECT id, " + ", ".join(FIELDS) + ", flags FROM workouts "
                           "WHERE user_id = %s AND source = %s AND external_id = %s",
                           (user_id, r["source"], r["external_id"])).fetchone()
        import_flags = dict(r["flags"])
        if old is None:
            new = conn.execute(
                f"""INSERT INTO workouts (user_id, source, external_id, {", ".join(FIELDS)}, flags)
                    VALUES (%s, %s, %s, {", ".join(["%s"] * len(FIELDS))}, %s) RETURNING id""",
                (user_id, r["source"], r["external_id"], *[r[k] for k in FIELDS], Jsonb(import_flags))).fetchone()
            counts["inserted"] += 1
            ids.append(new["id"])
            continue
        ids.append(old["id"])
        kept = {k: v for k, v in (old["flags"] or {}).items() if k not in IMPORT_FLAGS}
        # active_kcal: for OW it is computed by enrich and empty at import time, so it must not be overwritten with null
        values = {k: r[k] for k in FIELDS if not (k == "active_kcal" and r["source"] == "ow")}
        changed = any(_differs(old[k], v) for k, v in values.items()) or kept | import_flags != old["flags"]
        if not changed:
            counts["unchanged"] += 1
            continue
        if any(_differs(old[k], values[k]) for k in ("start_at", "end_at")):
            kept.pop("computed", None)   # the window changed, heart rate must be recomputed
        conn.execute(f"""UPDATE workouts SET {", ".join(f"{k} = %s" for k in values)}, flags = %s, updated_at = now()
                         WHERE id = %s""", (*values.values(), Jsonb(kept | import_flags), old["id"]))
        counts["updated"] += 1
    return {**counts, "ids": ids}


IMPORT_FLAGS = {"implausible_duration", "energy_from_shortcut"}


def _differs(a, b) -> bool:
    if a is None or b is None:
        return a is not b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) > 1e-6
    return a != b


def _rank(w: dict, ranks: dict = P.SOURCE_RANK) -> tuple:
    dev = (w.get("device") or "").lower()
    device = 0 if "watch" in dev else 1 if ("iphone" in dev or "phone" in dev) else 2
    # a copy with a real activity type wins over one the source only knew as generic (e.g. a Shortcut workout inferred
    # from exercise minutes + heart rate vs. the OW / Apple workout that arrives later)
    return (1 if w["type"] in P.GENERIC_TYPES else 0, ranks.get(w["source"], 9), 1 if w["is_xml"] else 0,
            device, w["id"])


def _is_dup(a: dict, b: dict) -> bool:
    if abs(a["start_at"] - b["start_at"]) <= P.DUP_SAME_TIME and abs(a["end_at"] - b["end_at"]) <= P.DUP_SAME_TIME:
        return True
    overlap = (min(a["end_at"], b["end_at"]) - max(a["start_at"], b["start_at"])).total_seconds()
    shorter = min((a["end_at"] - a["start_at"]).total_seconds(), (b["end_at"] - b["start_at"]).total_seconds())
    same_type = a["type"] == b["type"] or a["type"] in P.GENERIC_TYPES or b["type"] in P.GENERIC_TYPES
    if "hevy" in (a.get("source"), b.get("source")):   # a Hevy log is "strength" whatever the watch called that workout
        same_type = same_type or (a["type"] in P.STRENGTH_TYPES and b["type"] in P.STRENGTH_TYPES)
    return same_type and shorter > 0 and overlap / shorter >= P.DUP_OVERLAP


def refresh_duplicates(conn: psycopg.Connection, user_id: int, mode: str | None = None) -> dict:
    """Recompute dup_of for all of this user's workouts: from highest priority down, one that duplicates an already-kept workout points to it. Returns {kept, duplicates, changed}.
    Runs after every import, whichever copy arrived first. A dup_of set by hand (flags.manual_dup) is never changed.
    mode: the user's sync mode (rawdata/engine.mode), looked up only when the app's sync engine has workouts here: in the
    app mode its copies rank first (P.SOURCE_RANK_APP), otherwise they are excluded ('shadow') and take no part."""
    rows = conn.execute("""SELECT id, source, is_xml, device, type, start_at, end_at, dup_of, excluded, flags FROM workouts
                           WHERE user_id = %s""", (user_id,)).fetchall()
    ranks = P.SOURCE_RANK
    if any(w["source"] == APP_SOURCE for w in rows):
        if mode is None:
            from ..rawdata import engine
            mode = engine.mode(conn, user_id)
        if mode == "app":
            ranks = P.SOURCE_RANK_APP
    kept: list[dict] = []
    target: dict[int, int | None] = {}
    manual = {w["id"]: w["dup_of"] for w in rows if (w["flags"] or {}).get("manual_dup") and w["dup_of"]}
    for w in sorted(rows, key=lambda w: _rank(w, ranks)):
        if w["id"] in manual:
            target[w["id"]] = manual[w["id"]]
            continue
        if w["excluded"] and w["excluded"] != "user_hidden":   # hidden by the user: still holds its copies as duplicates
            target[w["id"]] = None
            continue
        hit = next((k for k in kept if k["start_at"] < w["end_at"] + P.DUP_SAME_TIME
                    and w["start_at"] < k["end_at"] + P.DUP_SAME_TIME and _is_dup(k, w)), None)
        target[w["id"]] = hit["id"] if hit else None
        if hit is None:
            kept.append(w)
    changed = [(t, w["id"]) for w in rows if (t := target[w["id"]]) != w["dup_of"]]
    if changed:
        with conn.cursor() as cur:
            cur.executemany("UPDATE workouts SET dup_of = %s, updated_at = now() WHERE id = %s", changed)
    return {"kept": len(kept), "duplicates": sum(1 for t in target.values() if t), "changed": len(changed)}


# ---------------------------------------------------------------- heart rate, TRIMP, active energy
def session_trimp(hr: list[tuple[datetime, float]], start: datetime, end: datetime, hr_max: float,
                  cap: timedelta = P.HR_CAP) -> dict:
    """Edwards TRIMP over the workout window [start, end]. hr is sorted by time and deduplicated.
    Each heart-rate sample counts until the next one (at most cap), not beyond the workout end. Returns {trimp, zones, covered_min, samples}."""
    xs = [(t, v) for t, v in hr if start <= t <= end]
    zones = [0.0] * 5
    covered = 0.0
    for i, (t, v) in enumerate(xs):
        nxt = xs[i + 1][0] if i + 1 < len(xs) else end
        minutes = max(0.0, (min(nxt, t + cap, end) - t).total_seconds() / 60)
        covered += minutes
        w = L.zone_weight(v, hr_max)
        if w:
            zones[w - 1] += minutes
    return {"trimp": sum(m * (i + 1) for i, m in enumerate(zones)), "zones": zones, "covered_min": covered,
            "samples": len(xs)}


def avg_hr_trimp(avg_hr: float, minutes: float, hr_max: float) -> float:
    """Estimate TRIMP from the average heart rate only: minutes × weight of the zone of the average heart rate (coarse: fluctuations within the zone are invisible)."""
    return minutes * L.zone_weight(avg_hr, hr_max)


def hr_max_for(conn: psycopg.Connection, user_id: int, day: date) -> float | None:
    r = conn.execute("SELECT hr_max FROM daily_scores WHERE user_id = %s AND date = %s", (user_id, day)).fetchone()
    if r and r["hr_max"]:
        return float(r["hr_max"])
    u = conn.execute("SELECT birth_date FROM users WHERE id = %s", (user_id,)).fetchone()
    if u and u["birth_date"]:
        return L.hrmax_formula(L.age_on(u["birth_date"], day))
    return None


def basal_rate(conn: psycopg.Connection, user_id: int) -> float:
    """Basal metabolism (kcal/min) for estimating active energy: the median of this person's basal energy verified against samples in workouts; if none, the median daily basal energy / 1440."""
    rates = [r["v"] for r in conn.execute(
        """SELECT (flags->>'basal_kcal')::float / duration_min AS v FROM workouts
           WHERE user_id = %s AND flags ? 'basal_kcal' AND duration_min > 10""", (user_id,))]
    if len(rates) >= 3:
        return statistics.median(rates)
    r = conn.execute("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS v FROM daily_metrics
                        WHERE user_id = %s AND metric = 'basal_energy_burned' AND value > 800""", (user_id,)).fetchone()
    return r["v"] / 1440 if r and r["v"] else P.DEFAULT_BASAL_KCAL_PER_MIN


def window_samples(client, ow_user: str, start: datetime, end: datetime) -> dict[str, list]:
    """Heart-rate, active-energy and basal-energy samples within the workout window (XML copies removed), split by type."""
    by: dict[str, list] = defaultdict(list)
    for raw in client.paginate(f"/users/{ow_user}/timeseries", start_time=start.isoformat(), end_time=end.isoformat(),
                               types=["heart_rate", "active_energy", "basal_energy"], resolution="raw", limit=1000):
        if raw.get("is_daily_total"):
            continue
        code, _, s = ow_imp.parse_sample(raw)
        if start <= s.ts <= end:
            by[code].append(s)
    return {code: ow_imp.drop_xml_duplicates(xs) for code, xs in by.items()}


def session_hr_recovery(hr: list[tuple[datetime, float]], end: datetime) -> float | None:
    """Heart-rate drop one minute after the workout: last reading in the final minute − reading closest to end + 60 s (45–100 s)."""
    last = [v for t, v in hr if end - timedelta(minutes=1) <= t <= end]
    lo, hi = P.HRR_AFTER
    after = [(abs((t - end).total_seconds() - 60), v) for t, v in hr if end + lo <= t <= end + hi]
    if not last or not after:
        return None
    return round(last[-1] - min(after)[1], 1)


def enrich(conn: psycopg.Connection, client, user_id: int, w: dict, ow_user: str | None, tz: ZoneInfo) -> dict:
    """Compute a workout's TRIMP / five-zone minutes / active energy / heart-rate recovery and write back to workouts. Returns flags."""
    flags = {k: v for k, v in (w["flags"] or {}).items() if k in IMPORT_FLAGS}
    tail_end = w["end_at"] + P.HRR_TAIL   # heart rate a few minutes past the end, for heart-rate recovery only
    samples = window_samples(client, ow_user, w["start_at"], tail_end) if client is not None and ow_user else {}
    for code in ("active_energy", "basal_energy"):
        if code in samples:
            samples[code] = [x for x in samples[code] if x.ts <= w["end_at"]]
    hr_by_ts: dict[datetime, float] = {}
    for s in sorted(samples.get("heart_rate", []), key=lambda s: s.priority):
        hr_by_ts.setdefault(s.ts, s.value)
    if not hr_by_ts:  # OW has no heart rate for this window: raw heart rate from the export.zip the user uploaded (history/, export_samples)
        for r in conn.execute("""SELECT start_at, value FROM export_samples WHERE user_id = %s AND kind = 'hr'
                                 AND start_at BETWEEN %s AND %s ORDER BY start_at""",
                              (user_id, w["start_at"], tail_end)):
            hr_by_ts.setdefault(r["start_at"], r["value"])
    hr_all = sorted(hr_by_ts.items())
    hr = [x for x in hr_all if x[0] <= w["end_at"]]
    if (hrr := session_hr_recovery(hr_all, w["end_at"])) is not None:
        flags["hr_recovery_bpm"] = hrr
    hr_max = hr_max_for(conn, user_id, w["local_date"])
    upd = {"trimp": None, "hr_zone_min": None, "hr_samples": len(hr) or None, "hr_max_used": hr_max}
    if hr and hr_max:
        res = session_trimp(hr, w["start_at"], w["end_at"], hr_max)
        cover = res["covered_min"] / max(w["duration_min"], 1e-6)
        upd["hr_zone_min"] = [round(z, 1) for z in res["zones"]]
        if cover >= P.MIN_HR_COVERAGE:
            upd["trimp"] = round(res["trimp"], 1)
        else:
            flags["hr_coverage_low"] = f"心率只覆盖了训练的 {cover:.0%}，不出 TRIMP（强度按平均心率估）"
    elif not hr:
        flags["no_raw_hr"] = "训练时段没有原始心率（手表没戴 / 没同步，或 2025-08 之前没有手表）"

    # active energy (OW workouts have only total energy)
    if w["source"] == "ow":
        act = sum(ow_imp.dedupe_cumulative(samples.get("active_energy", []), tz).values())
        bas = sum(ow_imp.dedupe_cumulative(samples.get("basal_energy", []), tz).values())
        total = w["total_kcal"]
        if total and act and act + bas >= (1 - P.ENERGY_MATCH_TOL) * total:
            # samples complete: split total energy by the active / basal ratio in the samples (the sample sum is sometimes 10–20% above total energy, the ratio is still reliable)
            upd["active_kcal"] = round(total * act / (act + bas), 1)
            if bas:
                flags["basal_kcal"] = round(total * bas / (act + bas), 1)
        elif total:
            rate = basal_rate(conn, user_id)
            upd["active_kcal"] = round(max(total - rate * w["duration_min"], 0.0), 1)
            flags["active_kcal_estimated"] = (f"活动能量样本没同步全（样本 {act:.0f} + {bas:.0f} kcal，训练总能量 {total:.0f}），"
                                              f"按 总能量 − 基础代谢 {rate:.2f} kcal/分钟 估算")
        elif act:
            upd["active_kcal"] = round(act, 1)
    flags["computed"] = P.TRAINING_VERSION
    conn.execute(f"""UPDATE workouts SET {", ".join(f"{k} = %s" for k in upd)}, flags = %s, updated_at = now()
                     WHERE id = %s""", (*upd.values(), Jsonb(flags), w["id"]))
    return flags


def enrich_pending(conn: psycopg.Connection, client, user_id: int, force_ids: set[int] | None = None) -> int:
    """Compute heart rate and energy for kept workouts (not duplicates, not excluded): those not yet computed / whose algorithm version changed / in force_ids. Returns how many were computed."""
    user = conn.execute("SELECT ow_user_id, timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    tz = ZoneInfo(user["timezone"])
    rows = conn.execute("""SELECT * FROM workouts WHERE user_id = %s AND dup_of IS NULL AND excluded IS NULL
                           ORDER BY start_at""", (user_id,)).fetchall()
    todo = [w for w in rows if (w["flags"] or {}).get("computed") != P.TRAINING_VERSION or w["id"] in (force_ids or set())]
    for w in todo:
        enrich(conn, client, user_id, w, user["ow_user_id"], tz)
    return len(todo)


# ---------------------------------------------------------------- entry point
def import_ow(conn: psycopg.Connection, client, user_id: int, start: date | None = None, end: date | None = None,
              xml_energy_unit: str = P.XML_ENERGY_UNIT) -> dict:
    """Pull OW workouts in [start, end] (user's local dates, by start time). Empty start = all history. Idempotent."""
    user = conn.execute("SELECT ow_user_id, timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None or not user["ow_user_id"]:
        raise ValueError(f"用户 {user_id} 还没对应 OW 的 user：先运行 body-engine ow-link --user-id {user_id}")
    tz = ZoneInfo(user["timezone"])
    lo = datetime.combine(start, time.min, tz) if start else datetime(2000, 1, 1, tzinfo=timezone.utc)
    end = end or datetime.now(tz).date()
    # OW filters on start >= start_date and end < end_date: end is pushed back 2 days so workouts that started the day before and cross midnight are also fetched
    hi = datetime.combine(end + timedelta(days=2), time.min, tz)
    raws = list(client.paginate(f"/users/{user['ow_user_id']}/events/workouts", start_date=lo.isoformat(),
                                end_date=hi.isoformat(), limit=100))
    rows = [parse_ow(r, tz, xml_energy_unit) for r in raws]
    rows = [r for r in rows if r["local_date"] <= end]
    with conn.transaction():
        res = upsert(conn, user_id, rows)
        # workouts deleted in OW (within this pull's range and no longer appearing) are deleted here too
        gone = conn.execute(
            """DELETE FROM workouts WHERE user_id = %s AND source = 'ow' AND start_at >= %s AND end_at < %s
               AND local_date <= %s AND NOT (external_id = ANY(%s)) RETURNING id""",
            (user_id, lo, hi, end, [r["external_id"] for r in rows])).fetchall()
        dups = refresh_duplicates(conn, user_id)
    with conn.transaction():
        n = enrich_pending(conn, client, user_id, set(res["ids"]))
    return {"fetched": len(raws), "inserted": res["inserted"], "updated": res["updated"], "unchanged": res["unchanged"],
            "deleted": len(gone), "duplicates": dups["duplicates"], "kept": dups["kept"], "enriched": n,
            "excluded": sum(1 for r in rows if r["excluded"])}
