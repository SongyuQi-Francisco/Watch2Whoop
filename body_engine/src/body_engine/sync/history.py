"""Apple Health read range ("近 90 天" / "全部历史") and the full-history backfill (replaces the manual export.zip upload).

The app reads HealthKit itself through the Open Wearables SDK: 90 days by default, or the whole history (newest first,
in batches, resumable). It reports its choice and progress here (report(), via the Today page in its WebView).
- days90: nothing to do on the server; the regular import (hourly / on open) covers it.
- all: while the phone uploads, status 'uploading' (upload_percent from the app). When the app says the history is in,
  status 'queued' and `body-engine history-backfill-run` (outside the API: Ali's per-minute imports job) takes over:
  1. 'importing': importers/ow.run for the user's whole OW history, WINDOW_DAYS at a time from the earliest day,
     resumable (import_cursor) and within a time budget per run (TIME_BUDGET, the next run continues);
  2. 'scoring': the whole-history score recompute (compute-scores from the start + insights + rules);
  3. 'done'. Any error: 'error' (shown as not running; the next report / run retries).
status() gives the Today page 「历史数据导入中 x%」: upload 0–70 %, import 70–95 %, scoring 95–99 %.
Idempotent everywhere (one row per user, ow.run and the score engine are idempotent). Users without consent are skipped.
"""
import logging
import time as _time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

log = logging.getLogger(__name__)

SCOPES = ("days90", "all")
WINDOW_DAYS = 30                       # OW import window (same as importers/ow.WINDOW): bounded memory per step
TIME_BUDGET = 240                      # seconds per history-backfill-run before it stops and lets the next run continue
STALE_UPLOAD = timedelta(days=2)       # an upload the app stopped reporting on: no longer shown as running
LOCK_NS = 0x4849                       # advisory lock namespace ("HI")
ACTIVE = ("uploading", "queued", "importing", "scoring")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(conn, user_id: int) -> dict:
    return conn.execute("SELECT * FROM health_history WHERE user_id = %s", (user_id,)).fetchone() or {}


def percent(row: dict) -> int | None:
    st = row.get("status")
    if st == "uploading":
        return min(70, round((row.get("upload_percent") or 0) * 0.7))
    if st == "queued":
        return 70
    if st == "importing":
        frm, until, cur = row.get("import_from"), row.get("import_until"), row.get("import_cursor")
        if frm and until and cur:
            total = max((until - frm).days + 1, 1)
            return 70 + min(25, round(25 * min(max((cur - frm).days, 0), total) / total))
        return 70
    if st == "scoring":
        return 97
    if st == "done":
        return 100
    return None


def status(conn: psycopg.Connection, user_id: int) -> dict:
    row = _row(conn, user_id)
    st = row.get("status") or "idle"
    active = st in ACTIVE and not (st == "uploading" and row["updated_at"] < _now() - STALE_UPLOAD)
    return {"scope": row.get("scope") or "days90", "status": st, "active": active, "percent": percent(row) if active or st == "done" else None,
            "upload_percent": row.get("upload_percent") or 0, "sent": row.get("sent_count") or 0,
            "requested_at": row.get("requested_at"), "finished_at": row.get("finished_at"),
            "updated_at": row.get("updated_at"), "error": row.get("error")}


def report(conn: psycopg.Connection, user_id: int, scope: str, upload_percent: int | None = None,
           sent: int | None = None, upload_done: bool = False) -> dict:
    """The app's choice and progress. Choosing 'all' starts the upload phase; upload_done hands over to the backfill."""
    if scope not in SCOPES:
        raise ValueError(f"scope 只能是 {' / '.join(SCOPES)}")
    conn.execute("INSERT INTO health_history (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    row = conn.execute("SELECT * FROM health_history WHERE user_id = %s FOR UPDATE", (user_id,)).fetchone()
    sets: dict = {"scope": scope}
    if scope == "all":
        st = row["status"]
        if row["scope"] != "all" or st == "idle" or (st == "error" and not upload_done):
            # newly chosen (or retried after an error): the phone reads the whole history now
            sets.update(status="uploading", requested_at=_now(), finished_at=None, error=None, upload_percent=0)
            st = "uploading"
        elif st == "error" and upload_done:
            # the backfill failed: retry from where it stopped
            sets.update(status="importing" if row["import_cursor"] else "queued", error=None)
        if st == "uploading":
            if upload_percent is not None:
                sets["upload_percent"] = max(0, min(100, int(upload_percent)))
            if sent is not None:
                sets["sent_count"] = max(0, int(sent))
            if upload_done:
                sets.update(status="queued", upload_percent=100, import_cursor=None, import_from=None, import_until=None)
    cols = ", ".join(f"{k} = %s" for k in sets)
    conn.execute(f"UPDATE health_history SET {cols}, updated_at = now() WHERE user_id = %s", (*sets.values(), user_id))
    conn.commit()
    return status(conn, user_id)


def _set(conn, user_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(f"UPDATE health_history SET {cols}, updated_at = now() WHERE user_id = %s", (*fields.values(), user_id))
    conn.commit()


def _score(conn, user_id: int, client) -> dict:
    """The whole-history recompute: compute-scores from the start (+ insights) and the rules, as the nightly job does."""
    from ..importers import shortcut
    from ..insights import jobs as insights_jobs
    from ..rules import engine as rules_engine
    from ..scores import engine as scores_engine
    res = scores_engine.run(conn, client, user_id, None, None, shortcut.user_archive_dir(user_id))
    res["insights"] = insights_jobs.run(conn, client, user_id)
    rules_engine.run(conn, user_id, None)
    return res


def _first_day(conn, user_id: int, client) -> date | None:
    from ..importers import ow
    row = conn.execute("SELECT ow_user_id, timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    first = ow._first_sample_time(client, row["ow_user_id"])
    return first.astimezone(ZoneInfo(row["timezone"])).date() if first else None


def run_user(conn: psycopg.Connection, user_id: int, client=None, importer=None, scorer=None,
             budget: float = TIME_BUDGET, clock=_time.monotonic) -> dict:
    """One step of the backfill for one user, within `budget` seconds. Returns what was done."""
    from ..importers import ow
    from ..ow_client import OWClient
    client = client or OWClient()
    importer = importer or (lambda c, cl, uid, s, e: ow.run(c, cl, uid, s, e))
    t0 = clock()
    row = _row(conn, user_id)
    try:
        if row["status"] == "queued":
            tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
            first = _first_day(conn, user_id, client)
            until = datetime.now(tz).date()
            if first is None:   # nothing in OW (yet): straight to scoring, which is cheap then
                _set(conn, user_id, status="scoring")
            else:
                _set(conn, user_id, status="importing", import_from=first, import_until=until, import_cursor=first)
            row = _row(conn, user_id)
        windows = 0
        while row["status"] == "importing" and clock() - t0 < budget:
            cur, until = row["import_cursor"], row["import_until"]
            if cur > until:
                _set(conn, user_id, status="scoring")
                row = _row(conn, user_id)
                break
            end = min(cur + timedelta(days=WINDOW_DAYS - 1), until)
            importer(conn, client, user_id, cur, end)
            _set(conn, user_id, import_cursor=end + timedelta(days=1))
            windows += 1
            row = _row(conn, user_id)
            if row["import_cursor"] > row["import_until"]:
                _set(conn, user_id, status="scoring")
                row = _row(conn, user_id)
        if row["status"] == "scoring" and clock() - t0 < budget:
            (scorer or _score)(conn, user_id, client)
            _set(conn, user_id, status="done", finished_at=_now(), error=None)
            row = _row(conn, user_id)
        return {"user_id": user_id, "status": row["status"], "windows": windows, "percent": percent(row)}
    except Exception as e:  # noqa: BLE001  shown as not running; the next run retries from the saved cursor
        conn.rollback()
        log.warning("history backfill failed for user %s: %s", user_id, e)
        _set(conn, user_id, status="error", error=str(e)[:300])
        return {"user_id": user_id, "status": "error", "error": str(e)}


def run_queued(conn: psycopg.Connection, user_id: int | None = None, **kw) -> list[dict]:
    """`body-engine history-backfill-run`: one budgeted step for every user whose backfill is queued / running.
    Nothing to do = no OW calls at all. An 'error' row is retried when the app reports again."""
    from ..profile import consent
    rows = conn.execute("SELECT user_id FROM health_history WHERE status IN ('queued', 'importing', 'scoring') "
                        "ORDER BY requested_at").fetchall()
    out = []
    for r in rows:
        uid = r["user_id"]
        if user_id is not None and uid != user_id:
            continue
        if consent.gate(conn, uid):
            continue
        got = conn.execute("SELECT pg_try_advisory_lock(%s, %s) AS ok", (LOCK_NS, uid)).fetchone()["ok"]
        if not got:
            continue
        try:
            out.append(run_user(conn, uid, **kw))
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NS, uid))
            conn.commit()
    return out
