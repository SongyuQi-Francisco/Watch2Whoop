"""The weekly what's-new message: who is due, what it says, and the idempotent send.

`run(conn)` is called every hour (deploy/ops/ops.py) and is cheap: users whose local clock is not in their send slot are
passed over without a query. A user is due from Sunday 19:00 in their own time zone (params.SEND_TIME) for CATCHUP_HOURS.
One row of whats_new_sends per user and slot (the local Sunday) makes it at most one message per week, however often this runs.

What it says: the entries dated after the previous message's `through` day (a new account: after the day it was made; the
first message of anyone: at most params.LOOKBACK_DAYS back) up to today. None -> no message and no row, so the next run looks
again. Sent through notify() as category `updates`; a person who switched that category off gets nothing (a row says so,
so the next message starts after this week). Members who have not agreed to the consent text yet are skipped.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..notify import core as notify_core, notify
from ..profile import consent
from . import entries as E, params as P


def slot_of(local: datetime) -> datetime:
    """The latest send slot (Sunday SEND_TIME, wall-clock in `local`'s zone) at or before `local`."""
    day = local.date() - timedelta(days=(local.weekday() - P.SEND_WEEKDAY) % 7)
    slot = datetime.combine(day, P.SEND_TIME, tzinfo=local.tzinfo)
    return slot if slot <= local else slot - timedelta(days=7)


def due_slot(now: datetime, tz: ZoneInfo) -> tuple[date, date] | None:
    """(slot day, today) in the user's zone when `now` is inside the send window, else None."""
    local = now.astimezone(tz)
    slot = slot_of(local)
    if local - slot >= timedelta(hours=P.CATCHUP_HOURS):
        return None
    return slot.date(), local.date()


def message(items: list[E.Entry]) -> tuple[dict, dict]:
    """(title, body) as {zh, en} for the entries to announce (newest first; the first one is named)."""
    n = len(items)
    first = items[0]
    zh, en = first.zh.rstrip("。"), first.en.rstrip(".")
    title = {"zh": "本周更新", "en": "What's new this week"}
    if n == 1:
        return title, {"zh": first.zh, "en": first.en}
    return title, {"zh": f"{zh}，等 {n} 项", "en": f"{en}, {n} updates in all"}


def cursor(conn: psycopg.Connection, user_id: int, created: datetime, tz: ZoneInfo, today: date) -> date:
    """Entries must be dated after this day."""
    last = conn.execute("SELECT max(through) AS d FROM whats_new_sends WHERE user_id = %s", (user_id,)).fetchone()["d"]
    return max(d for d in (last, created.astimezone(tz).date(), today - timedelta(days=P.LOOKBACK_DAYS)) if d is not None)


def run(conn: psycopg.Connection, user_id: int | None = None, now: datetime | None = None, dry_run: bool = False,
        entries: tuple[E.Entry, ...] | None = None) -> list[dict]:
    """One result per user that was in its send window (others are not listed): status sent / would_send / off / empty /
    already / consent / inactive, with the entry count and the first line."""
    now = now or datetime.now(timezone.utc)
    users = conn.execute(
        """SELECT id, timezone, created_at, role, consent_at, status FROM users WHERE (%s::int IS NULL OR id = %s) ORDER BY id""",
        (user_id, user_id)).fetchall()
    out = []
    for u in users:
        try:
            tz = ZoneInfo(u["timezone"] or "Australia/Sydney")
        except Exception:  # noqa: BLE001 - a bad zone name must not stop everyone else
            tz = ZoneInfo("Australia/Sydney")
        window = due_slot(now, tz)
        if window is None:
            continue
        slot_day, today = window
        res = {"user_id": u["id"], "week_start": slot_day.isoformat()}
        if u["status"] != "active":
            out.append({**res, "status": "inactive"})
            continue
        if u["role"] != "owner" and u["consent_at"] is None:
            out.append({**res, "status": "consent"})
            continue
        if conn.execute("SELECT 1 FROM whats_new_sends WHERE user_id = %s AND week_start = %s", (u["id"], slot_day)).fetchone():
            out.append({**res, "status": "already"})
            continue
        items = E.between(cursor(conn, u["id"], u["created_at"], tz, today), today, entries)
        if not items:
            out.append({**res, "status": "empty"})
            continue
        title, body = message(items)
        res |= {"items": len(items), "first": items[0].zh}
        if dry_run:
            out.append({**res, "status": "would_send"})
            continue
        on = notify_core.settings(conn, u["id"])["categories"].get("updates", True)
        with conn.transaction():
            fresh = conn.execute(
                """INSERT INTO whats_new_sends (user_id, week_start, through, items, outcome) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, week_start) DO NOTHING RETURNING user_id""",
                (u["id"], slot_day, today, len(items), "sent" if on else "off")).fetchone()
            if fresh and on:
                sent = notify(conn, u["id"], P.KIND, title, body, url=P.URL, data={"items": len(items), "week": slot_day.isoformat()},
                              now=now)
                conn.execute("UPDATE whats_new_sends SET notification_id = %s WHERE user_id = %s AND week_start = %s",
                             (sent["id"], u["id"], slot_day))
        conn.commit()
        out.append({**res, "status": ("sent" if on else "off") if fresh else "already"})
    return out
