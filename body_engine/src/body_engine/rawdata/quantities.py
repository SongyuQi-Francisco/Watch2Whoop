"""HealthKit quantity samples uploaded by the app (POST /v1/ingest/healthkit `quantities[]`, registry rawdata/hk_types.py).

Each group of a batch is {type, unit, items: [{uuid, start, end, value, source}]}; the writer is chosen per type from the
registry (WRITERS, by `HkType.agg`), not per batch, so a new type needs no code here.
- Stored as raw samples in hk_samples, deduplicated by HealthKit UUID (a re-sent sample is skipped; HealthKit samples never
  change, a changed value is a new sample with a new UUID).
- deleted[] (UUIDs HealthKit reports as deleted) removes them here too.
- Every (type, local day) that gained or lost a sample is made again from all its stored samples, with the Apple Health
  export import's definitions (importers/apple_zip.daily_rows): `sum` = the largest single-source total of the day, `avg` =
  mean / min / max of all samples. The value goes to daily_metric_sources with source 'app', and resolve_daily picks the
  day's value among the sources (importers/common.py, 'app' ranks first for non-cumulative types). A day whose samples
  were all deleted loses its 'app' value (and its daily_metrics row when no other source has one).
- A unit other than the registry's (an app built with a different table) rejects the batch (IngestError): values in an
  unknown unit must never be stored as if they were in the right one.
- Samples outside the registry's plausible range are dropped (counted in `dropped`), not the batch.
Returns the counts and the earliest day that changed (the caller queues the score recompute from there).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg

from ..importers.common import resolve_daily, upsert_daily
from . import hk_types as T

SOURCE = "app"


class UnitMismatch(ValueError):
    pass


def check_groups(groups: list[dict]) -> None:
    """Every group's type is registered and sent in the registry's unit (raises ValueError with an English message)."""
    for g in groups:
        t = T.BY_HK.get(g["type"])
        if t is None:
            raise ValueError(f"unknown HealthKit type {g['type']}")
        if g["unit"] != t.unit:
            raise UnitMismatch(f"{g['type']} must be sent in {t.unit}, not {g['unit']}")


def _day_rows(conn: psycopg.Connection, user_id: int, pairs: set[tuple[str, date]]) -> tuple[list[dict], set[tuple[str, date]]]:
    """Daily rows (upsert_daily format) for these (type, day) pairs from all stored samples, and the pairs left empty."""
    if not pairs:
        return [], set()
    types = sorted({t for t, _ in pairs})
    days = sorted({d for _, d in pairs})
    per_src = conn.execute(
        """SELECT type, local_date, source, sum(value) AS s, count(*) AS n, min(value) AS mn, max(value) AS mx
           FROM hk_samples WHERE user_id = %s AND type = ANY(%s) AND local_date = ANY(%s)
           GROUP BY type, local_date, source""", (user_id, types, days)).fetchall()
    acc: dict[tuple[str, date], dict] = {}
    for r in per_src:
        k = (r["type"], r["local_date"])
        if k not in pairs:
            continue
        a = acc.setdefault(k, {"s": 0.0, "n": 0, "mn": float("inf"), "mx": float("-inf"), "best": 0.0})
        a["s"] += r["s"]
        a["n"] += r["n"]
        a["mn"] = min(a["mn"], r["mn"])
        a["mx"] = max(a["mx"], r["mx"])
        a["best"] = max(a["best"], r["s"])
    rows = []
    for (hk, d), a in acc.items():
        rows.append(WRITERS[T.BY_HK[hk].agg](T.BY_HK[hk], d, a))
    return rows, pairs - set(acc)


def _sum_row(t: T.HkType, d: date, a: dict) -> dict:
    # cumulative: the most complete single source (iPhone and watch both record some of these; never add them)
    return {"date": d, "metric": t.metric, "value": round(a["best"], 4), "unit": t.unit, "agg": "sum", "n": a["n"],
            "min_value": None, "max_value": None}


def _avg_row(t: T.HkType, d: date, a: dict) -> dict:
    return {"date": d, "metric": t.metric, "value": round(a["s"] / a["n"], 4), "unit": t.unit, "agg": "avg", "n": a["n"],
            "min_value": round(a["mn"], 4), "max_value": round(a["mx"], 4)}


# how a day is made, by HkType.agg
WRITERS = {"sum": _sum_row, "avg": _avg_row}
assert set(WRITERS) == set(T.AGGS)


def rebuild_days(conn: psycopg.Connection, user_id: int, pairs: set[tuple[str, date]], today: date | None = None) -> int:
    """Remake the 'app' daily values of these (type, day) pairs from hk_samples and re-choose the day's value. Idempotent."""
    rows, empty = _day_rows(conn, user_id, pairs)
    n = upsert_daily(conn, user_id, SOURCE, rows, today)
    if empty:
        gone = [(T.BY_HK[hk].metric, d) for hk, d in empty]
        for metric, d in gone:
            conn.execute("DELETE FROM daily_metric_sources WHERE user_id = %s AND date = %s AND metric = %s AND source = %s",
                         (user_id, d, metric, SOURCE))
        resolve_daily(conn, user_id, {d for _, d in gone}, today)
        for metric, d in gone:   # nothing left from any source: the chosen row goes too
            conn.execute("""DELETE FROM daily_metrics m WHERE m.user_id = %s AND m.date = %s AND m.metric = %s
                            AND NOT EXISTS (SELECT 1 FROM daily_metric_sources s WHERE s.user_id = m.user_id
                                            AND s.date = m.date AND s.metric = m.metric)""", (user_id, d, metric))
    return n


def ingest(conn: psycopg.Connection, user_id: int, groups: list[dict], deleted: list[str], tz: ZoneInfo) -> dict:
    """groups: validated (rawdata/api.py QuantityGroupIn as dicts, datetimes parsed). Does not commit."""
    check_groups(groups)
    items, dropped = [], 0
    for g in groups:
        t = T.BY_HK[g["type"]]
        for it in g["items"]:
            v = float(it["value"])
            if not (t.lo <= v <= t.hi) or it["end"] < it["start"]:
                dropped += 1
                continue
            items.append((t, it, v))
    uuids = [str(it["uuid"]) for _, it, _ in items]
    known = {r["u"] for r in conn.execute(
        "SELECT hk_uuid::text AS u FROM hk_samples WHERE user_id = %s AND hk_uuid = ANY(%s::uuid[])",
        (user_id, uuids)).fetchall()} if uuids else set()
    new = [(t, it, v) for t, it, v in items if str(it["uuid"]) not in known]
    pairs: set[tuple[str, date]] = set()
    if new:
        rows = []
        for t, it, v in new:
            local = it["start"].astimezone(tz).date()
            pairs.add((t.hk, local))
            rows.append((user_id, str(it["uuid"]), t.hk, it["start"], it["end"], local, v, (it.get("source") or "")[:120]))
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO hk_samples (user_id, hk_uuid, type, start_at, end_at, local_date, value, source)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (user_id, hk_uuid) DO NOTHING""", rows)
    n_del = 0
    if deleted:
        gone = conn.execute("""DELETE FROM hk_samples WHERE user_id = %s AND hk_uuid = ANY(%s::uuid[])
                               RETURNING type, local_date""", (user_id, deleted)).fetchall()
        n_del = len(gone)
        pairs |= {(r["type"], r["local_date"]) for r in gone if r["type"] in T.BY_HK}
    rebuild_days(conn, user_id, pairs, datetime.now(tz).date())
    return {"metrics": len(new), "skipped": len(items) - len(new), "dropped": dropped, "deleted": n_del,
            "changed_from": min((d for _, d in pairs), default=None)}


def counts(conn: psycopg.Connection, user_id: int) -> dict:
    """{samples, last_at} of this user's app-read quantity samples (the sync page shows them)."""
    r = conn.execute("SELECT count(*) AS n, max(received_at) AS t FROM hk_samples WHERE user_id = %s", (user_id,)).fetchone()
    return {"samples": r["n"], "last_at": r["t"]}


def latest_by_type(conn: psycopg.Connection, user_id: int) -> dict[str, datetime]:
    """HealthKit type -> start of the newest stored sample (the sync center compares it with the phone's newest)."""
    return {r["type"]: r["t"] for r in conn.execute(
        "SELECT type, max(start_at) AS t FROM hk_samples WHERE user_id = %s GROUP BY type", (user_id,))}
