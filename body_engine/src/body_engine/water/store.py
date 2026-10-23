"""water_intake read / write (migration 540). Per user; callers check authorization and the feature flag."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import params as P


def tz_of(conn: psycopg.Connection, user_id: int) -> ZoneInfo:
    row = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    return ZoneInfo((row or {}).get("timezone") or "Australia/Sydney")


def _ml(ml) -> int:
    try:
        v = round(float(ml))
    except (TypeError, ValueError):
        raise ValueError(L("ml 要是数字", "ml must be a number"))
    if not 0 < v <= P.MAX_ML:
        raise ValueError(L(f"一次喝水在 1–{P.MAX_ML} ml 之间", f"One drink is 1–{P.MAX_ML} ml"))
    return v


def parse_at(at: str | None, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """None = now; "HH:MM" = today at that time; otherwise ISO date-time (naive = the user's time zone). Not in the future."""
    now = now or datetime.now(tz)
    if not at:
        return now
    s = at.strip()
    try:
        if len(s) <= 5 and ":" in s:
            h, m = s.split(":")
            t = datetime.combine(now.date(), time(int(h), int(m)), tz)
        else:
            t = datetime.fromisoformat(s)
            t = t.replace(tzinfo=tz) if t.tzinfo is None else t
    except ValueError:
        raise ValueError(L("at 写 HH:MM 或 YYYY-MM-DDTHH:MM", "at is HH:MM or YYYY-MM-DDTHH:MM"))
    if t > now + timedelta(minutes=5):
        raise ValueError(L("不能记未来的喝水", "Cannot log water in the future"))
    return t


def add(conn: psycopg.Connection, user_id: int, ml, source: str, at: datetime | None = None,
        client_id: str | None = None, hk_uuid: str | None = None) -> dict:
    """One drink. Idempotent on client_id / hk_uuid: a repeat returns the stored row with duplicate=True."""
    if source not in P.SOURCES:
        raise ValueError(f"source: {source}")
    tz = tz_of(conn, user_id)
    at = at or datetime.now(tz)
    row = conn.execute(
        """INSERT INTO water_intake (user_id, at, local_date, ml, source, client_id, hk_uuid)
           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
           RETURNING id, at, local_date, ml, source""",
        (user_id, at, at.astimezone(tz).date(), _ml(ml), source, client_id, hk_uuid)).fetchone()
    if row is not None:
        return {**_row(row, tz), "duplicate": False}
    key, val = ("client_id", client_id) if client_id else ("hk_uuid", hk_uuid)
    old = conn.execute(f"SELECT id, at, local_date, ml, source FROM water_intake WHERE user_id = %s AND {key} = %s",
                       (user_id, val)).fetchone()
    return {**_row(old, tz), "duplicate": True}


def _row(r: dict, tz: ZoneInfo) -> dict:
    return {"id": r["id"], "at": r["at"].astimezone(tz).isoformat(timespec="minutes"), "date": r["local_date"],
            "ml": r["ml"], "source": r["source"]}


def delete(conn: psycopg.Connection, user_id: int, entry_id: int) -> dict | None:
    r = conn.execute("""DELETE FROM water_intake WHERE user_id = %s AND id = %s
                        RETURNING id, at, local_date, ml, source, client_id, hk_uuid""", (user_id, entry_id)).fetchone()
    return None if r is None else {**_row(r, tz_of(conn, user_id)), "client_id": r["client_id"],
                                   "hk_uuid": str(r["hk_uuid"]) if r["hk_uuid"] else None}


def delete_hk(conn: psycopg.Connection, user_id: int, uuids: list[str]) -> int:
    """Samples the person deleted in Health (ingest deleted[])."""
    if not uuids:
        return 0
    return len(conn.execute("DELETE FROM water_intake WHERE user_id = %s AND hk_uuid = ANY(%s::uuid[]) RETURNING 1",
                            (user_id, uuids)).fetchall())


def totals(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[date, int]:
    return {r["local_date"]: int(r["ml"]) for r in conn.execute(
        """SELECT local_date, sum(ml) AS ml FROM water_intake WHERE user_id = %s AND local_date BETWEEN %s AND %s
           GROUP BY local_date""", (user_id, start, end))}


def goal(conn: psycopg.Connection, user_id: int, day: date) -> dict:
    """{ml, base_ml, source: profile | default, extras: [{kind, ml}]} for that day."""
    t = conn.execute("""SELECT (data->>'water_ml')::numeric AS ml FROM assistant_profile
                        WHERE user_id = %s AND key = 'diet_targets'""", (user_id,)).fetchone()
    if t and t["ml"] and t["ml"] > 0:
        base, source = int(t["ml"]), "profile"
    else:
        sex = (conn.execute("SELECT sex FROM users WHERE id = %s", (user_id,)).fetchone() or {}).get("sex")
        base, source = P.DEFAULT_GOAL_ML.get(sex or "", P.DEFAULT_GOAL_UNKNOWN_ML), "default"
    extras = []
    if conn.execute("""SELECT 1 FROM workouts WHERE user_id = %s AND local_date = %s AND dup_of IS NULL
                       AND excluded IS DISTINCT FROM 'shadow'
                       AND duration_min >= %s LIMIT 1""", (user_id, day, P.TRAINING_MIN_MINUTES)).fetchone():
        extras.append({"kind": "training", "ml": P.TRAINING_EXTRA_ML})
    if conn.execute("""SELECT 1 FROM journal_entries WHERE user_id = %s AND date = %s AND key = %s AND value > 0""",
                    (user_id, day, P.SAUNA_JOURNAL_KEY)).fetchone():
        extras.append({"kind": "sauna", "ml": P.SAUNA_EXTRA_ML})
    return {"ml": base + sum(e["ml"] for e in extras), "base_ml": base, "source": source, "extras": extras}


def day_view(conn: psycopg.Connection, user_id: int, day: date | None = None) -> dict:
    """What the Today page and the AI see for one day: total, goal, entries (newest first), the entry undo removes."""
    tz = tz_of(conn, user_id)
    day = day or datetime.now(tz).date()
    rows = conn.execute("""SELECT id, at, local_date, ml, source FROM water_intake WHERE user_id = %s AND local_date = %s
                           ORDER BY at DESC, id DESC""", (user_id, day)).fetchall()
    entries = [_row(r, tz) for r in rows]
    g = goal(conn, user_id, day)
    total = sum(e["ml"] for e in entries)
    # undo = the newest drink logged by hand / button / AI (Health samples are removed in Health itself)
    last = next((e for e in sorted(entries, key=lambda e: e["id"], reverse=True) if e["source"] != "apple_health"), None)
    return {"date": day, "total_ml": total, "goal": g, "percent": round(100 * total / g["ml"]) if g["ml"] else None,
            "entries": entries, "undo": last, "quick_ml": list(P.QUICK_ML)}


def rollback(conn: psycopg.Connection, user_id: int, action: str, before: dict | None, after: dict | None) -> dict:
    """assistant/writelog.py: undo log_water (delete the drink it added) or put back a deleted one."""
    if action == "create" and after:
        r = delete(conn, user_id, int(after["id"]))
        return {"deleted": after["id"] if r else None}
    if action == "delete" and before:
        r = add(conn, user_id, before["ml"], before["source"], datetime.fromisoformat(before["at"]),
                client_id=before.get("client_id"), hk_uuid=before.get("hk_uuid"))
        return {"restored": r["id"]}
    raise ValueError("这条喝水记录没有可回滚的内容")
