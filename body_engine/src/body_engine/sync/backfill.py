"""History top-up (「补全历史数据」): how much watch history the server has, and a card that asks for more on a fixed rhythm.

The app's first sync reads only the last 14 days (app 1.0.8) so the new data arrives fast. The scores want more: a personal
baseline of 60 days and at least 14 nights of HRV (openbodyscore/params.py), and we ask for 90 days (sync/params.py). This module
answers three questions for the Today page (the card), the sync status panel and the AI briefing:

1. coverage(): days with watch data in the last BACKFILL_TARGET_DAYS days, whichever way the data came (Open Wearables, the app's
   own sync engine, the Shortcut, an Apple Health export): heart rate / HRV / sleep from daily_metrics and sleep_sessions, which
   already hold the resolved values of every source. `impact` says what a short history costs the scores:
   baseline = fewer than MIN_BASELINE_NIGHTS HRV nights in the baseline window (no recovery score yet),
   thin     = fewer days than the baseline window (the baseline works but is not steady).
2. the reminder rhythm (decide(), pure): the card opens 1, 3 and 7 days after the first sync (local dates), once each, then at
   most weekly. A reminder is a local day: the card stays up that day (the page reports it with mark_seen()), then waits for
   the next date. Stops for good when the person says "don't remind me" (set_muted) and when the target is reached
   (completed_at); pauses BACKFILL_EXHAUSTED_FOR when a whole-history top-up finished and the target is still not reached
   (exhausted_at: the phone may have no more). While a
   top-up runs the card shows its progress and does not count as a reminder. State: table history_backfill (migration 840).
3. request(): the card's button. Creates the forced re-sync job (resync.py: the phone re-reads Apple Health from N days ago, the
   server imports and rescores that range) and remembers that the card asked, so the app may use /v1/ingest/resync for a while even
   where the sync_center flag is off. The app does the phone part when the page opens watch2whoop://backfill?days=N (app 1.0.8+,
   Watch2WhoopFeatures.backfill; docs/sync-onboarding-app-requirements.md section 10). Older apps are sent to their sync page
   (read range → all history; the sync center's re-sync where it exists), progress from sync/history.py.

Progress seen by the server (progress()): the re-sync job (phone 0-60 %, import, scoring), the app's read-range backfill
(history.py) and an admin / API export import (history_imports). Own data only.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import history, params as P, resync

log = logging.getLogger(__name__)

IMPACTS = ("baseline", "thin")
# remind.state: progress = a top-up is running; error = it failed; due = the card is up (today is a reminder day);
# later = waiting for the next date; muted / complete = stopped for good; exhausted = paused; none = no watch data yet;
# consent = not agreed
STATES = ("consent", "progress", "complete", "none", "exhausted", "error", "muted", "due", "later")
SHOWN = ("progress", "error", "due")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tz(conn: psycopg.Connection, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


def _row(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM history_backfill WHERE user_id = %s", (user_id,)).fetchone()


# ---------------------------------------------------------------- coverage
def coverage(conn: psycopg.Connection, user_id: int, today: date) -> dict:
    """Days with watch data in the last BACKFILL_TARGET_DAYS days (today included) and the baseline window's share of them."""
    start = today - timedelta(days=P.BACKFILL_TARGET_DAYS - 1)
    base_from = today - timedelta(days=P.BASELINE_DAYS - 1)
    heart, hrv = set(), set()
    for r in conn.execute("""SELECT DISTINCT date, metric FROM daily_metrics
                             WHERE user_id = %(u)s AND date BETWEEN %(a)s AND %(b)s AND metric = ANY(%(m)s)""",
                          {"u": user_id, "a": start, "b": today,
                           "m": [*P.BACKFILL_HEART_METRICS, *P.BACKFILL_HRV_METRICS]}):
        (heart if r["metric"] in P.BACKFILL_HEART_METRICS else hrv).add(r["date"])
    sleep = {r["wake_date"] for r in conn.execute(
        "SELECT DISTINCT wake_date FROM sleep_sessions WHERE user_id = %s AND wake_date BETWEEN %s AND %s",
        (user_id, start, today))}
    every = heart | hrv | sleep
    return {"days": len(every), "heart_days": len(heart), "hrv_nights": len(hrv), "sleep_nights": len(sleep),
            "baseline_days": sum(1 for d in every if d >= base_from),
            "baseline_hrv_nights": sum(1 for d in hrv if d >= base_from)}


def impact(cov: dict) -> str | None:
    """What a short history costs the scores: 'baseline' (too few HRV nights: no recovery score), 'thin', or None."""
    if cov["baseline_hrv_nights"] < P.MIN_BASELINE_NIGHTS:
        return "baseline"
    if cov["baseline_days"] < P.BASELINE_DAYS * P.BACKFILL_BASELINE_MIN_FILL:
        return "thin"
    return None


def impact_label(code: str | None) -> str | None:
    return {"baseline": L("恢复分基线还在建立", "Recovery baseline still building"),
            "thin": L("恢复分基线还不够稳", "Recovery baseline not yet steady")}.get(code) if code else None


# ---------------------------------------------------------------- the rhythm (pure)
def next_due(anchor: date, last_shown: date | None) -> date:
    """The local date the next reminder opens: the first of anchor + 1 / 3 / 7 days that is after the last reminder, then a
    week after the last one."""
    for off in P.BACKFILL_REMIND_AFTER_DAYS:
        d = anchor + timedelta(days=off)
        if last_shown is None or d > last_shown:
            return d
    return last_shown + timedelta(days=P.BACKFILL_REMIND_EVERY_DAYS)


def decide(*, consent_ok: bool, days: int, target: int, anchor: date | None, last_shown: date | None, today: date,
           muted: bool, completed: bool, exhausted: bool, active: bool, failed: bool) -> tuple[str, date | None]:
    """(remind.state, the date the next reminder opens or None)."""
    if not consent_ok:
        return "consent", None
    if active:
        return "progress", None
    if completed or days >= target:
        return "complete", None
    if days <= 0 or anchor is None:
        return "none", None
    if exhausted:
        return "exhausted", None
    if failed:
        return "error", None
    if muted:
        return "muted", None
    if last_shown == today:
        return "due", next_due(anchor, last_shown)
    nxt = next_due(anchor, last_shown)
    return ("due" if today >= nxt else "later"), nxt


# ---------------------------------------------------------------- progress
def _import_progress(conn: psycopg.Connection, user_id: int) -> int | None:
    """An export.zip import in progress (history/store.py), as a percent; None when none runs."""
    j = conn.execute("""SELECT status, size_bytes, received_bytes, progress FROM history_imports
                        WHERE user_id = %s AND status IN ('uploading', 'queued', 'processing')
                        ORDER BY id DESC LIMIT 1""", (user_id,)).fetchone()
    if j is None:
        return None
    if j["status"] == "uploading":
        return round(40 * j["received_bytes"] / max(j["size_bytes"], 1))
    return 40 + round(55 * (j["progress"] or 0)) if j["status"] == "processing" else 40


def progress(conn: psycopg.Connection, user_id: int, row: dict | None, now: datetime) -> dict:
    """{active, percent, via, error}: a top-up the server can see. A re-sync job whose phone part never reported (the app was
    not opened, or did not understand the link) counts as failed after BACKFILL_PHONE_STALL, not as running."""
    rs = resync._row(conn, user_id)
    asked = row["backfill_requested_at"] if row else None
    if rs.get("status") == resync.PHONE and rs["updated_at"] < now - P.BACKFILL_PHONE_STALL and not rs.get("phone_percent"):
        stalled = asked is not None and asked > now - P.BACKFILL_ERROR_SHOWN
        return {"active": False, "percent": None, "via": "resync", "error": stalled}
    if rs and resync._running(rs, now):
        return {"active": True, "percent": resync.percent(rs), "via": "resync", "error": False}
    h = history.status(conn, user_id)
    if h["active"]:
        return {"active": True, "percent": h["percent"], "via": "range", "error": False}
    pct = _import_progress(conn, user_id)
    if pct is not None:
        return {"active": True, "percent": pct, "via": "import", "error": False}
    failed = rs.get("status") == "error" and asked is not None and asked > now - P.BACKFILL_ERROR_SHOWN
    return {"active": False, "percent": None, "via": None, "error": bool(failed)}


def _whole_history_done(conn: psycopg.Connection, user_id: int) -> bool:
    """A top-up that asked for the target or more finished (re-sync job or the read-range backfill)."""
    rs = resync._row(conn, user_id)
    if rs.get("status") == "done" and rs.get("finished_at") and (rs.get("days") is None or rs["days"] >= P.BACKFILL_TARGET_DAYS):
        return True
    h = history._row(conn, user_id)
    return bool(h.get("status") == "done" and h.get("scope") == "all" and h.get("finished_at"))


# ---------------------------------------------------------------- state
def _first_sync(conn: psycopg.Connection, user_id: int) -> datetime | None:
    """When the server first received watch data of this user: the earliest of the app's first upload, the Shortcut's first
    upload and the first watch day stored. None = nothing yet."""
    r = conn.execute("""SELECT least(
                          (SELECT min(received_at) FROM rawdata_uploads WHERE user_id = %(u)s),
                          (SELECT min(imported_at) FROM shortcut_uploads WHERE user_id = %(u)s),
                          (SELECT min(updated_at) FROM daily_metrics WHERE user_id = %(u)s AND metric = ANY(%(m)s))) AS t""",
                      {"u": user_id, "m": [*P.BACKFILL_HEART_METRICS, *P.BACKFILL_HRV_METRICS]}).fetchone()
    return r["t"]


def _ensure(conn: psycopg.Connection, user_id: int, now: datetime) -> dict | None:
    """The user's row; created from the first sync time once watch data exists."""
    row = _row(conn, user_id)
    if row is not None:
        return row
    first = _first_sync(conn, user_id)
    if first is None:
        return None
    conn.execute("INSERT INTO history_backfill (user_id, anchor_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                 (user_id, min(first, now)))
    conn.commit()
    return _row(conn, user_id)


def _set(conn: psycopg.Connection, user_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(f"UPDATE history_backfill SET {cols}, updated_at = now() WHERE user_id = %s", (*fields.values(), user_id))
    conn.commit()


def evaluate(conn: psycopg.Connection, user_id: int, now: datetime | None = None) -> dict:
    """The whole answer for one user. Creates the row on first sight of watch data and records completion / exhaustion
    (idempotent writes); nothing else changes."""
    from ..profile import consent
    now = now or _now()
    tz = _tz(conn, user_id)
    today = now.astimezone(tz).date()
    cov = coverage(conn, user_id, today)
    row = _ensure(conn, user_id, now) if cov["days"] > 0 else _row(conn, user_id)
    target = P.BACKFILL_TARGET_DAYS
    if row is not None and cov["days"] >= target and row["completed_at"] is None:
        _set(conn, user_id, completed_at=now)
        row = _row(conn, user_id)
    if row is not None and cov["days"] < target and row["exhausted_at"] is None and row["completed_at"] is None \
            and _whole_history_done(conn, user_id):
        _set(conn, user_id, exhausted_at=now)
        row = _row(conn, user_id)
    prog = progress(conn, user_id, row, now)
    imp = impact(cov)
    last_shown = row["last_shown_at"].astimezone(tz).date() if row and row["last_shown_at"] else None
    state, nxt = decide(
        consent_ok=not consent.needs_consent(conn, user_id), days=cov["days"], target=target,
        anchor=row["anchor_at"].astimezone(tz).date() if row else None, last_shown=last_shown, today=today,
        muted=bool(row and row["muted_at"]), completed=bool(row and row["completed_at"]),
        exhausted=bool(row and row["exhausted_at"] and now - row["exhausted_at"] < P.BACKFILL_EXHAUSTED_FOR), active=prog["active"], failed=prog["error"])
    return {
        "target_days": target, "baseline_days_needed": P.BASELINE_DAYS, "hrv_nights_needed": P.MIN_BASELINE_NIGHTS,
        **cov,
        "status": "complete" if state == "complete" else "building" if imp else "partial",
        "impact": imp, "impact_label": impact_label(imp),
        "label": L("补全历史数据", "Fill in your history"),
        "progress": prog,
        "remind": {"state": state, "show": state in SHOWN, "muted": bool(row and row["muted_at"]),
                   "next_on": nxt.isoformat() if nxt and state in ("later", "due") else None,
                   "shown_count": row["shown_count"] if row else 0},
        "action": {"kind": "backfill", "days": target},
    }


def mark_seen(conn: psycopg.Connection, user_id: int, now: datetime | None = None) -> dict:
    """The page showed the card as a reminder: today counts as this reminder (once per local day; progress and error
    states are not reminders). Returns evaluate()."""
    now = now or _now()
    ev = evaluate(conn, user_id, now)
    if ev["remind"]["state"] == "due":
        tz = _tz(conn, user_id)
        row = _row(conn, user_id)
        if row["last_shown_at"] is None or row["last_shown_at"].astimezone(tz).date() != now.astimezone(tz).date():
            _set(conn, user_id, last_shown_at=now, shown_count=row["shown_count"] + 1)
            ev = evaluate(conn, user_id, now)
    return ev


def set_muted(conn: psycopg.Connection, user_id: int, muted: bool, now: datetime | None = None) -> dict:
    """「不再提醒」 (muted) / turn the reminders on again. Without a row yet (no watch data) one is made, counting from now."""
    now = now or _now()
    if _row(conn, user_id) is None:
        conn.execute("INSERT INTO history_backfill (user_id, anchor_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (user_id, _first_sync(conn, user_id) or now))
        conn.commit()
    _set(conn, user_id, muted_at=now if muted else None)
    return evaluate(conn, user_id, now)


def request(conn: psycopg.Connection, user_id: int, days: int | None = P.BACKFILL_TARGET_DAYS,
            now: datetime | None = None) -> dict:
    """The card's button: start a top-up of the last `days` days (None = all history). The re-sync job's status plus
    accepted / reason (busy | throttled | consent) and the new coverage answer. The app does the phone part."""
    now = now or _now()
    rs = resync._row(conn, user_id)
    if rs.get("status") == resync.PHONE and rs["updated_at"] < now - P.BACKFILL_PHONE_STALL and not rs.get("phone_percent"):
        resync.cancel(conn, user_id)   # an earlier ask the app never took up: ask again instead of waiting a day
    res = resync.request(conn, user_id, days)
    if res.get("accepted"):
        _ensure(conn, user_id, now)
        if _row(conn, user_id) is None:   # no watch data yet: still remember the ask (the app may open /v1/ingest/resync)
            conn.execute("INSERT INTO history_backfill (user_id, anchor_at) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, now))
            conn.commit()
        _set(conn, user_id, backfill_requested_at=now, backfill_days=days, exhausted_at=None)
    return {**res, "coverage": evaluate(conn, user_id, now)}


def allows_resync(conn: psycopg.Connection, user_id: int, now: datetime | None = None) -> bool:
    """The app may use /v1/ingest/resync for a top-up the card started, flag sync_center or not."""
    row = _row(conn, user_id)
    return bool(row and row["backfill_requested_at"] and row["backfill_requested_at"] > (now or _now()) - P.BACKFILL_RESYNC_OPEN)


# ---------------------------------------------------------------- the AI briefing
def briefing_hint(conn: psycopg.Connection, user_id: int) -> dict | None:
    """A few words for get_briefing: the history is short and the person has not stopped the reminders. None = nothing to say
    (enough history, muted, nothing to top up, no consent)."""
    ev = evaluate(conn, user_id)
    if ev["remind"]["state"] in ("complete", "none", "exhausted", "muted", "consent"):
        return None
    out = {"days": ev["days"], "of": ev["target_days"]}
    if ev["impact"]:
        out["effect"] = ev["impact_label"]
    if ev["progress"]["active"]:
        out["topping_up"] = ev["progress"]["percent"]
    else:
        out["how"] = "今天页「补全历史数据」卡片一键补全；合适时提一句就好，别反复提"
    return out
