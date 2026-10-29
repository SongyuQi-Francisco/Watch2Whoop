"""`body-engine upgrade send`: tell everyone still on an old client to upgrade (R2).

Per user, once per (stage, block date) (table upgrade_notice_sends, migration 1070): an admin notice (a one-time sheet on
the Today page, with the store / TestFlight link as its button) + a notification through notify() (in-app list, and a push
where the person allows it: flag `notifications`, category 「系统消息」, quiet hours). Running it again sends nothing new.

Who: active accounts that agreed to the consent (the owner always), for whom the flag `force_upgrade` is on, except those
whose phone already reported the target version (sync_device_state / push_devices app_version; --include-upgraded sends to
them as well). --dry-run lists who would get what and writes nothing.

Stages: notice (from the start of the notice window) and last-day (from the day before the block); `auto` (the default)
picks the one that is due for each person in their own time zone, so the command can run every hour once the dates are set.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from ..notices import store as notices
from ..notify import notify
from ..rollout import flags
from . import params as P, policy

STAGE_AUTO = "auto"


def message(stage: str, version: str, block_day: str | None) -> dict:
    """zh / en texts of the notice and the push for one stage."""
    if stage == "last-day":
        return {"title": "明天起旧版本停用", "title_en": "Older versions stop working tomorrow",
                "body": f"请今天升级到 Watch2Whoop {version}。数据都在，AI 连接不受影响。",
                "body_en": f"Please update to Watch2Whoop {version} today. Your data is safe and AI connections keep working."}
    if block_day:
        return {"title": f"请升级到 Watch2Whoop {version}", "title_en": f"Please update to Watch2Whoop {version}",
                "body": f"{block_day} 起旧版本停用。数据都在，AI 连接不受影响。",
                "body_en": f"Older versions stop working on {block_day}. Your data is safe and AI connections keep working."}
    return {"title": f"请升级到 Watch2Whoop {version}", "title_en": f"Please update to Watch2Whoop {version}",
            "body": "数据都在，AI 连接不受影响。", "body_en": "Your data is safe and AI connections keep working."}


def due_stage(notice_at: datetime | None, block_at: datetime | None, now: datetime) -> str | None:
    """The stage due at now (None outside the notice window or after the block)."""
    if block_at is not None and now >= block_at:
        return None
    if block_at is not None and now >= block_at - timedelta(days=1):
        return "last-day"
    if notice_at is not None and now >= notice_at:
        return "notice"
    return None


def _upgraded(conn: psycopg.Connection, user_id: int, spec: str) -> bool:
    versions = [r["v"] for r in conn.execute(
        """SELECT app_version AS v FROM sync_device_state WHERE user_id = %(u)s AND app_version IS NOT NULL
           UNION ALL SELECT app_version FROM push_devices WHERE user_id = %(u)s AND app_version IS NOT NULL""",
        {"u": user_id}).fetchall()]
    return spec != P.NONE and any(policy.meets(v.split("+")[0], v.partition("+")[2] or None, spec) for v in versions)


def run(conn: psycopg.Connection, stage: str = STAGE_AUTO, user_id: int | None = None, dry_run: bool = False,
        include_upgraded: bool = False, now: datetime | None = None) -> dict:
    if stage != STAGE_AUTO and stage not in P.STAGES:
        raise ValueError(f"stage must be {STAGE_AUTO} or one of {', '.join(P.STAGES)}")
    pol = policy.load_fresh(conn)
    if not pol.block_from and not pol.notice_from:
        raise ValueError("no upgrade dates yet: body-engine upgrade set --block-from YYYY-MM-DD first")
    if not pol.url:
        raise ValueError(f"no upgrade link yet: body-engine upgrade set --url https://… (or {P.URL_ENV})")
    if stage == "last-day" and not pol.block_from:
        raise ValueError("last-day needs --block-from")
    now = now or datetime.now(timezone.utc)
    version = policy.target_version(pol)
    spec = pol.mins.get("ios-native") or P.NONE
    users = conn.execute(
        """SELECT id, timezone, role, consent_at, status FROM users WHERE (%s::int IS NULL OR id = %s) ORDER BY id""",
        (user_id, user_id)).fetchall()
    out, counts = [], {}
    for u in users:
        res = {"user_id": u["id"]}
        tz = policy._tz(u["timezone"])
        notice_at, block_at = policy.window(pol, tz)
        st = due_stage(notice_at, block_at, now) if stage == STAGE_AUTO else stage
        block_day = block_at.astimezone(tz).date().isoformat() if block_at else None
        status = None
        if u["status"] != "active":
            status = "inactive"
        elif u["role"] != "owner" and u["consent_at"] is None:
            status = "consent"
        elif not flags.enabled(conn, u["id"], P.FLAG):
            status = "flag_off"
        elif st is None:
            status = "not_due"
        elif not include_upgraded and _upgraded(conn, u["id"], spec):
            status = "upgraded"
        elif conn.execute("SELECT 1 FROM upgrade_notice_sends WHERE user_id = %s AND stage = %s AND block_from = %s",
                          (u["id"], st, pol.block_from or "")).fetchone():
            status = "already"
        res["stage"] = st
        if status is None and dry_run:
            status = "would_send"
        if status is None:
            status = _send(conn, u["id"], st, pol, version, block_day, block_at, now)
        res["status"] = status
        counts[status] = counts.get(status, 0) + 1
        if status not in ("not_due", "inactive"):
            out.append(res)
    return {"dry_run": dry_run, "stage": stage, "version": version, "block_from": pol.block_from, "url": pol.url,
            "counts": counts, "users": out}


def _send(conn: psycopg.Connection, uid: int, stage: str, pol: policy.Policy, version: str, block_day: str | None,
          block_at: datetime | None, now: datetime) -> str:
    m = message(stage, version, block_day)
    # the sheet is pointless once the page itself shows the full-screen upgrade: it expires a day after the block
    days = max(1, min(365, (block_at - now).days + 2)) if block_at else 30
    with conn.transaction():
        fresh = conn.execute(
            """INSERT INTO upgrade_notice_sends (user_id, stage, block_from) VALUES (%s, %s, %s)
               ON CONFLICT DO NOTHING RETURNING user_id""", (uid, stage, pol.block_from or "")).fetchone()
        if not fresh:
            return "already"
        n = notices.send(conn, uid, m["title"], m["body"], pol.url, "去升级", None, days, m["title_en"], m["body_en"], "Update")
        sent = notify(conn, uid, P.NOTIFY_KIND, {"zh": m["title"], "en": m["title_en"]}, {"zh": m["body"], "en": m["body_en"]},
                      url=P.NOTIFY_URL, data={"stage": stage, "version": version}, now=now)
        conn.execute("""UPDATE upgrade_notice_sends SET notice_id = %s, notification_id = %s, push_status = %s
                        WHERE user_id = %s AND stage = %s AND block_from = %s""",
                     (n["id"], sent["id"], sent["push"], uid, stage, pol.block_from or ""))
    conn.commit()
    return "sent"
