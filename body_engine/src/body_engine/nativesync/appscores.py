"""Scores the iOS app computed with the JS bundle (PUT /v1/users/me/scores/app, #340 N2 item 3).

Upload: {"algo_version", "bundle_version", "days": [{"date", "row": {daily_scores columns}}]}.
- Only rows of the server's own algorithm version (scores/params.ALGO_VERSION) are taken; another version is answered
  "stale_algo" and nothing is stored (the server keeps scoring; the app updates its bundle from /v1/algo/manifest).
- Spot check: for the first upload of a bundle version and at most every CHECK_EVERY_HOURS, the server recomputes the
  newest recent uploaded day in Python and compares field by field (params.CHECK_TOLERANCE). Every check is kept in
  app_score_checks. A difference beyond the tolerance: the server's row is stored for that day, nothing else of the upload,
  the user's app scoring is marked untrusted (the hourly job scores them again) and a recompute of the uploaded range is
  queued. The next passing check trusts the app again.
- Stored rows go to daily_scores with origin = app (+ bundle_version): the AI, MCP, the Today page and every reader keep
  reading daily_scores, so they always see what is stored, whoever computed it.

The server's part afterwards (plan()): while a user's app is "active" (uploaded within APP_ACTIVE_DAYS, with the
server's algorithm version, trusted, not forced back by an admin) the hourly and nightly jobs do no full recompute for
them; they only score days whose inputs changed more than DIRTY_GRACE_MINUTES ago and that the app has not uploaded
since (store.score_dirty_days). `body-engine app-scores server --user-id N` gives the scoring back to the server.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..i18n import L
from ..scores import engine as E, params as SP
from . import params as P, store

log = logging.getLogger(__name__)
NUMERIC = [c for c in E.COLUMNS if c not in ("hrv_kind", "flags", "details", "algo_version")]


# ---------------------------------------------------------------- state
def state(conn: psycopg.Connection, uid: int) -> dict | None:
    return conn.execute("SELECT * FROM app_score_state WHERE user_id = %s", (uid,)).fetchone()


def active(conn: psycopg.Connection, uid: int) -> bool:
    """True while the app scores this user (see the module doc)."""
    s = state(conn, uid)
    return bool(s and not s["server_forced"] and s["trusted"] and s["algo_version"] == SP.ALGO_VERSION
                and s["last_upload_at"] > datetime.now(s["last_upload_at"].tzinfo) - timedelta(days=P.APP_ACTIVE_DAYS))


def plan(conn: psycopg.Connection, uid: int, today: date | None = None) -> dict:
    """What a scheduled server recompute should do for this user: {"mode": "server"} = as always, or
    {"mode": "app", "days": [...]} = the app scores; recompute only these days (possibly none)."""
    if not active(conn, uid):
        return {"mode": "server"}
    if today is None:
        tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
        today = datetime.now(tz).date()
    return {"mode": "app", "days": store.score_dirty_days(conn, uid, today - timedelta(days=P.DIRTY_LOOKBACK_DAYS),
                                                         P.DIRTY_GRACE_MINUTES)}


def set_server_forced(conn: psycopg.Connection, uid: int, forced: bool) -> dict:
    """Admin: the server scores this user whatever the app uploads (forced) or the app may again. Commits."""
    conn.execute("""INSERT INTO app_score_state (user_id, algo_version, server_forced, uploads) VALUES (%s, '', %s, 0)
                    ON CONFLICT (user_id) DO UPDATE SET server_forced = EXCLUDED.server_forced, updated_at = now()""",
                 (uid, forced))
    conn.commit()
    return status(conn, uid)


def status(conn: psycopg.Connection, uid: int) -> dict:
    s = state(conn, uid)
    checks = conn.execute("""SELECT date, ok, diffs, bundle_version, checked_at FROM app_score_checks WHERE user_id = %s
                             ORDER BY checked_at DESC LIMIT 5""", (uid,)).fetchall()
    return {"user_id": uid, "active": active(conn, uid), "server_algo_version": SP.ALGO_VERSION,
            "state": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in (s or {}).items()} or None,
            "recent_checks": [{**c, "date": c["date"].isoformat(), "checked_at": c["checked_at"].isoformat()} for c in checks],
            "plan": plan(conn, uid) if s else {"mode": "server"}}


# ---------------------------------------------------------------- checking
def _num(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError
    return v


def clean_row(row: dict, algo_version: str) -> dict:
    """The uploaded row as a daily_scores row (unknown fields rejected, numbers checked)."""
    extra = set(row) - set(E.COLUMNS)
    if extra:
        raise ValueError(L(f"未知字段：{sorted(extra)}", f"Unknown fields: {sorted(extra)}"))
    if row.get("algo_version") not in (None, algo_version):
        raise ValueError(L("行里的 algo_version 和请求不一致", "The row's algo_version differs from the request's"))
    out = {c: None for c in E.COLUMNS}
    for c in NUMERIC:
        try:
            out[c] = _num(row.get(c))
        except ValueError:
            raise ValueError(L(f"{c} 不是数字", f"{c} is not a number")) from None
    if row.get("hrv_kind") not in (None, "rmssd", "sdnn"):
        raise ValueError(L("hrv_kind 只能是 rmssd / sdnn", "hrv_kind must be rmssd or sdnn"))
    for c in ("flags", "details"):
        if row.get(c) is not None and not isinstance(row[c], dict):
            raise ValueError(L(f"{c} 应该是对象", f"{c} must be an object"))
    out.update(hrv_kind=row.get("hrv_kind"), flags=row.get("flags"), details=row.get("details"), algo_version=algo_version)
    return out


def diff(app: dict, server: dict) -> dict:
    """field -> {app, server} for the fields beyond the tolerance (one side missing counts)."""
    out = {}
    for f, tol in P.CHECK_TOLERANCE.items():
        a, s = app.get(f), server.get(f)
        if a is None and s is None:
            continue
        if a is None or s is None or abs(a - s) > tol + 1e-9:
            out[f] = {"app": a, "server": s}
    if (app.get("hrv_kind") or None) != (server.get("hrv_kind") or None) and (app.get("hrv_kind") or server.get("hrv_kind")):
        out["hrv_kind"] = {"app": app.get("hrv_kind"), "server": server.get("hrv_kind")}
    return out


def server_rows(conn: psycopg.Connection, uid: int, days: list[date]) -> dict[date, dict]:
    """The server's own rows for these days (Python, nothing written). Tests replace this."""
    from ..importers import shortcut
    from ..ow_client import OWClient, OWError
    origin = E.scoring_origin(conn, uid)
    if origin is None:
        return {}
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
    try:
        client = OWClient()
    except OWError:
        client = None
    end = max(days)
    rows, _ = E.compute(conn, client, uid, min(days), end, origin, tz, shortcut.user_archive_dir(uid), ow_cache="use")
    want = set(days)
    return {r["date"]: r for r in rows if r["date"] in want}


def _check_due(s: dict | None, bundle_version: str | None) -> bool:
    if s is None or s["last_check_at"] is None or s["bundle_version"] != bundle_version or not s["trusted"]:
        return True
    return s["last_check_at"] < datetime.now(s["last_check_at"].tzinfo) - timedelta(hours=P.CHECK_EVERY_HOURS)


def _check_day(rows: dict[date, dict], today: date) -> date | None:
    recent = sorted((d for d in rows if d >= today - timedelta(days=P.CHECK_WITHIN_DAYS) and d < today), reverse=True)
    with_scores = [d for d in recent if rows[d].get("recovery") is not None or rows[d].get("sleep_score") is not None]
    return (with_scores or recent or [None])[0]


# ---------------------------------------------------------------- upload
def upload(conn: psycopg.Connection, uid: int, algo_version: str, bundle_version: str | None, days: list[dict],
           force_check: bool = False) -> dict:
    """Store the app's rows (see the module doc). Commits. Raises ValueError for a malformed upload."""
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
    today = datetime.now(tz).date()
    rows: dict[date, dict] = {}
    for item in days:
        d = item["date"]
        if d > today:
            raise ValueError(L(f"{d} 是未来的日期", f"{d} is in the future"))
        rows[d] = {**clean_row(item["row"], algo_version), "date": d}
    if algo_version != SP.ALGO_VERSION:
        return {"status": "stale_algo", "stored": 0, "server_algo_version": SP.ALGO_VERSION, "check": None,
                "app_scoring": active(conn, uid)}
    s = state(conn, uid)
    check = None
    stored_rows = rows
    day = _check_day(rows, today) if (force_check or _check_due(s, bundle_version)) else None
    if day is not None:
        server = server_rows(conn, uid, [day]).get(day)
        diffs = diff(rows[day], server) if server is not None else {}
        ok = server is None or not diffs
        conn.execute("""INSERT INTO app_score_checks (user_id, date, algo_version, bundle_version, ok, diffs)
                        VALUES (%s, %s, %s, %s, %s, %s)""",
                     (uid, day, algo_version, bundle_version, ok, Jsonb(diffs)))
        check = {"date": day.isoformat(), "ok": ok, "diffs": diffs, "compared": server is not None}
        if not ok:   # the server wins: its row for the checked day, nothing else of this upload
            log.warning("app scores of user %s (bundle %s) differ on %s: %s", uid, bundle_version, day, diffs)
            E.write(conn, uid, [server], day, day)
            stored_rows = {}
    if stored_rows:
        lo, hi = min(stored_rows), max(stored_rows)
        E.write(conn, uid, list(stored_rows.values()), lo, hi, only=set(stored_rows), origin="app",
                bundle_version=bundle_version)
    ok_now = check is None or check["ok"]
    conn.execute(
        """INSERT INTO app_score_state AS s (user_id, algo_version, bundle_version, last_upload_at, uploads, days_stored,
                                             trusted, last_check_at, last_check_ok)
           VALUES (%(u)s, %(a)s, %(b)s, now(), 1, %(n)s, %(ok)s, %(ca)s, %(cok)s)
           ON CONFLICT (user_id) DO UPDATE SET algo_version = EXCLUDED.algo_version, bundle_version = EXCLUDED.bundle_version,
             last_upload_at = now(), uploads = s.uploads + 1, days_stored = s.days_stored + EXCLUDED.days_stored,
             trusted = CASE WHEN %(checked)s THEN EXCLUDED.trusted ELSE s.trusted END,
             last_check_at = coalesce(EXCLUDED.last_check_at, s.last_check_at),
             last_check_ok = coalesce(EXCLUDED.last_check_ok, s.last_check_ok), updated_at = now()""",
        {"u": uid, "a": algo_version, "b": bundle_version, "n": len(stored_rows), "ok": ok_now,
         "ca": datetime.now(tz) if check else None, "cok": check["ok"] if check else None, "checked": check is not None})
    conn.commit()
    if not ok_now and rows:
        from ..sync import refresh
        refresh.queue_scores(conn, uid, min(rows))
    return {"status": "ok" if ok_now else "mismatch", "stored": len(stored_rows), "check": check,
            "server_algo_version": SP.ALGO_VERSION, "app_scoring": active(conn, uid),
            "data_version": store.data_version(conn, uid)}
