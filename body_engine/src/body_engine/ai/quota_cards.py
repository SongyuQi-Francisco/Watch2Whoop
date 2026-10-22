"""Jarvis quota cards (issue #182, flag ai_cards, docs/ai-assistant-plan.md §9.10): cards that give back or add to a person's
monthly fallback-AI allowance (ai/fallback.py) before the 1st of next month.

- reset card: this month's used allowance goes back to 0 (spend before the card stops counting for the person);
- extend card: this month's allowance grows by amount_cny.
Allowance this month = monthly quota + extend cards used this month - fallback spend since the last reset card used this
month (fallback.allowance). A card counts in the month it is used; unused cards keep until they expire.

Entry points for other code (rewards, private plugins, a paid top-up in a private deployment): `grant(...)` and the
shorthand `reward(...)`. Both are idempotent with a key (the same key never grants twice), validated, written to the
ledger (ai_quota_ledger: every grant / use / expiry / revocation, with who did it), and send an in-app notification
(notify kind ai_card) when the flag is on for the person.
Cards never get past the global pause (DeepSeek balance below the stop line, admin switch): precheck checks that first,
and using a card is refused while Jarvis is paused so nobody wastes one. People only ever see "Jarvis" and percentages.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import quota_cards_params as P

log = logging.getLogger(__name__)
CARD_COLS = ("id, user_id, kind, amount_cny, source, reason, granted_by, grant_key, granted_at, expires_at, status, "
             "used_at, revoked_at, revoked_by, revoke_reason")


class CardError(Exception):
    """A use that cannot happen: code for the page, message in the request's language, HTTP status."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(v) -> float | None:
    return None if v is None else float(v)


def _ledger(conn, user_id: int, card_id: int | None, event: str, kind: str, actor: str, amount=None,
            spent_before=None, allowance_before=None, note: str | None = None) -> None:
    conn.execute("""INSERT INTO ai_quota_ledger (user_id, card_id, event, kind, amount_cny, spent_before_cny,
                    allowance_before_cny, actor, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                 (user_id, card_id, event, kind, amount, spent_before, allowance_before, actor, note))


# ---------------------------------------------------------------- effect on this month (read by fallback.allowance)
def month_effects(conn: psycopg.Connection, user_id: int, since: datetime) -> dict:
    """Cards used since the start of the person's month: the last reset's time and the extend total."""
    r = conn.execute("""SELECT max(used_at) FILTER (WHERE kind = 'reset') AS reset_at,
                               coalesce(sum(amount_cny) FILTER (WHERE kind = 'extend'), 0) AS extended
                        FROM ai_quota_cards WHERE user_id = %s AND status = 'used' AND used_at >= %s""",
                     (user_id, since)).fetchone()
    return {"reset_at": r["reset_at"], "extended_cny": float(r["extended"])}


def _valid_unused(conn, user_id: int) -> list[dict]:
    return conn.execute(f"""SELECT {CARD_COLS} FROM ai_quota_cards WHERE user_id = %s AND status = 'unused'
                            AND (expires_at IS NULL OR expires_at > now())
                            ORDER BY expires_at NULLS LAST, id""", (user_id,)).fetchall()


def counts(conn: psycopg.Connection, user_id: int) -> dict:
    """Usable cards now: {"reset": n, "extend": n, "total": n, "extend_cny": x} (amounts are for the admin and the % math)."""
    rows = _valid_unused(conn, user_id)
    ext = [r for r in rows if r["kind"] == "extend"]
    return {"reset": len(rows) - len(ext), "extend": len(ext), "total": len(rows),
            "extend_cny": round(sum(float(r["amount_cny"]) for r in ext), 2)}


def max_extra_cny(conn: psycopg.Connection, user_id: int, allowance_cny: float) -> float:
    """The most the unused cards can still add to this person's spend this month (doctor's "本月最多还会花"): every extend
    card, then each reset card gives back a whole allowance that already includes all the extends."""
    c = counts(conn, user_id)
    return round(c["extend_cny"] + c["reset"] * (allowance_cny + c["extend_cny"]), 4)


# ---------------------------------------------------------------- expiry
def expire_due(conn: psycopg.Connection, user_id: int | None = None) -> int:
    """Unused cards past their expiry -> expired (+ ledger). Idempotent; run hourly by `ai-cards periodic` and before reads."""
    rows = conn.execute(f"""UPDATE ai_quota_cards SET status = 'expired'
                            WHERE status = 'unused' AND expires_at <= now() {'AND user_id = %s' if user_id else ''}
                            RETURNING id, user_id, kind, amount_cny""", (user_id,) if user_id else ()).fetchall()
    for r in rows:
        _ledger(conn, r["user_id"], r["id"], "expire", r["kind"], "system", r["amount_cny"])
    return len(rows)


# ---------------------------------------------------------------- grant (the public entry point)
def parse_expires(text: str | None, now: datetime | None = None) -> datetime | None | str:
    """CLI --expires: "30d", "2w", "12h", "2026-11-30" (end of that day, UTC), "never" -> None; "" / None -> "default"."""
    if not text:
        return "default"
    t = text.strip().lower()
    if t in ("never", "none", "0"):
        return None
    now = now or _now()
    units = {"h": timedelta(hours=1), "d": timedelta(days=1), "w": timedelta(weeks=1)}
    if t[-1:] in units and t[:-1].isdigit() and int(t[:-1]) > 0:
        return now + int(t[:-1]) * units[t[-1]]
    try:
        d = date.fromisoformat(t)
    except ValueError:
        raise ValueError(f"--expires: {text!r} (examples: 30d, 2w, 2026-11-30, never)")
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)


def grant(conn: psycopg.Connection, user_id: int, kind: str, *, source: str, reason: str, actor: str,
          amount_cny: float | None = None, count: int = 1, expires_at: datetime | None | str = "default",
          key: str | None = None, notify: bool = True) -> dict:
    """Give `count` cards to one person. Returns {"created": [card rows], "existing": n (already granted under `key`),
    "notified": notification id or None}.

    kind: reset | extend (extend needs amount_cny, ¥0–MAX_EXTEND_CNY); source: admin | periodic | reward | other;
    reason: shown to the person; actor: who / what grants (cli:<name>, rule:<name>, module:<name>, plugin:<name>);
    expires_at: a datetime, None = never, "default" = DEFAULT_EXPIRES_DAYS from now;
    key: idempotency key per person (the same key never grants again; with count > 1 each card gets key#i).
    Raises ValueError on bad arguments. Runs in the caller's transaction (the caller commits)."""
    if kind not in P.KINDS:
        raise ValueError(f"kind must be one of {P.KINDS}")
    if source not in P.SOURCES:
        raise ValueError(f"source must be one of {P.SOURCES}")
    if kind == "extend":
        if amount_cny is None or not 0 < float(amount_cny) <= P.MAX_EXTEND_CNY:
            raise ValueError(f"an extend card needs an amount between ¥0 and ¥{P.MAX_EXTEND_CNY:g}")
        amount_cny = round(float(amount_cny), 2)
    elif amount_cny is not None:
        raise ValueError("a reset card has no amount")
    if not 1 <= int(count) <= P.MAX_COUNT:
        raise ValueError(f"count must be 1–{P.MAX_COUNT}")
    reason = (reason or "").strip()
    if not reason or len(reason) > P.MAX_REASON:
        raise ValueError(f"reason is required (at most {P.MAX_REASON} characters)")
    actor = (actor or "").strip()
    if not actor:
        raise ValueError("actor is required (who grants the card)")
    if key is not None and not (0 < len(key) <= 120):
        raise ValueError("key must be 1–120 characters")
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise ValueError(f"no user {user_id}")
    if expires_at == "default":
        expires_at = _now() + timedelta(days=P.DEFAULT_EXPIRES_DAYS)
    created, existing = [], 0
    for i in range(int(count)):
        gk = None if key is None else (key if count == 1 else f"{key}#{i + 1}")
        row = conn.execute(f"""INSERT INTO ai_quota_cards (user_id, kind, amount_cny, source, reason, granted_by, grant_key, expires_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (user_id, grant_key) WHERE grant_key IS NOT NULL DO NOTHING
                               RETURNING {CARD_COLS}""",
                           (user_id, kind, amount_cny, source, reason, actor, gk, expires_at)).fetchone()
        if row is None:
            existing += 1
            continue
        created.append(row)
        _ledger(conn, user_id, row["id"], "grant", kind, actor, amount_cny, note=f"{source}: {reason}")
    nid = _notify_grant(conn, user_id, created) if created and notify else None
    return {"created": created, "existing": existing, "notified": nid}


def reward(conn: psycopg.Connection, user_id: int, reason: str, *, module: str, key: str, kind: str = "reset",
           amount_cny: float | None = None, count: int = 1, expires_at: datetime | None | str = "default") -> dict:
    """A reward from another module or a plugin (feedback adopted, a friend invited, …): grant(source="reward") under the
    key reward:<module>:<key>, so calling it again for the same event grants nothing. Example:
        from body_engine.ai.quota_cards import reward
        reward(conn, uid, "反馈被采纳", module="feedback", key=f"adopted:{feedback_id}")"""
    if not module or not key:
        raise ValueError("module and key are required (the key makes a reward idempotent)")
    return grant(conn, user_id, kind, source="reward", reason=reason, actor=f"module:{module}", amount_cny=amount_cny,
                 count=count, expires_at=expires_at, key=f"reward:{module}:{key}")


def _pct(conn, user_id: int, amount) -> int:
    from .fallback import quota_cny
    q = quota_cny(conn, user_id)
    return round(100 * float(amount) / q) if q else 0


def _notify_grant(conn, user_id: int, cards: list[dict]) -> int | None:
    """One in-app notification per grant (and a push when the person allows it); only when the flag is on for them.
    A notification problem never undoes the grant (savepoint)."""
    from ..notify import notify
    from ..rollout import flags
    if not flags.enabled(conn, user_id, P.FLAG):
        return None
    n, c = len(cards), cards[0]
    if c["kind"] == "reset":
        what = ("重置卡：用了以后本月 Jarvis 额度回到 100%", "Reset card: brings this month's Jarvis allowance back to 100%")
    else:
        p = _pct(conn, user_id, c["amount_cny"])
        what = (f"扩充卡：本月 Jarvis 额度 +{p}%", f"Extend card: +{p}% Jarvis allowance this month")
    exp = c["expires_at"]
    tz = (conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone() or {}).get("timezone") or "UTC"
    day = (exp - timedelta(microseconds=1)).astimezone(ZoneInfo(tz)).date().isoformat() if exp else None
    body = {"zh": f"{what[0]}。{c['reason']}" + (f"（{day} 前有效）" if day else ""),
            "en": f"{what[1]}. {c['reason']}" + (f" (valid until {day})" if day else "")}
    title = {"zh": f"你收到 {n} 张 Jarvis 额度卡" if n > 1 else "你收到一张 Jarvis 额度卡",
             "en": f"You got {n} Jarvis allowance cards" if n > 1 else "You got a Jarvis allowance card"}
    try:
        with conn.transaction():
            return notify(conn, user_id, P.NOTIFY_KIND, title, body, url=P.NOTIFY_URL,
                          data={"cards": [x["id"] for x in cards][:20]})["id"]
    except Exception:  # noqa: BLE001  the card stays granted; the person still sees it in AI settings
        log.exception("ai card notification failed (user %s)", user_id)
        return None


# ---------------------------------------------------------------- use (the person, from the Today page)
def use(conn: psycopg.Connection, user_id: int, card_id: int) -> dict:
    """Use one of your own cards. Tapping twice counts once: a card already used by you returns {"already": True} and
    changes nothing (the row is locked, so two requests at the same moment cannot both use it). Commits."""
    from . import fallback
    if expire_due(conn, user_id):
        conn.commit()
    card = conn.execute(f"SELECT {CARD_COLS} FROM ai_quota_cards WHERE id = %s AND user_id = %s FOR UPDATE",
                        (card_id, user_id)).fetchone()
    if card is None:
        raise CardError("not_found", L("没有这张卡", "No such card"), 404)
    if card["status"] == "used":
        conn.commit()
        return {"card": card, "already": True}
    if card["status"] != "unused":
        msg = (L("这张卡已经过期了", "This card has expired") if card["status"] == "expired"
               else L("这张卡已经被收回了", "This card was withdrawn"))
        raise CardError(card["status"], msg)
    reason = fallback.paused_reason(conn, refresh=False)
    if reason is not None:
        raise CardError("paused", L("Jarvis 现在暂停了，卡先留着，恢复后再用",
                                    "Jarvis is paused right now; keep the card and use it once Jarvis is back"))
    if fallback.quota_cny(conn, user_id) <= 0:
        raise CardError("no_quota", L("你的账号没有 Jarvis 额度，用不了额度卡", "Your account has no Jarvis allowance, so cards cannot be used"))
    a = fallback.allowance(conn, user_id)
    if card["kind"] == "reset" and a["spent_cny"] <= 0:
        raise CardError("nothing_to_reset", L("本月的 Jarvis 额度还没用，这张卡先留着",
                                              "You haven't used any of this month's Jarvis allowance yet; keep the card for later"))
    used = conn.execute(f"""UPDATE ai_quota_cards SET status = 'used', used_at = now() WHERE id = %s AND status = 'unused'
                            RETURNING {CARD_COLS}""", (card_id,)).fetchone()
    _ledger(conn, user_id, card_id, "use", card["kind"], f"user:{user_id}", card["amount_cny"],
            spent_before=round(a["spent_cny"], 6), allowance_before=round(a["quota_cny"], 6))
    conn.commit()
    return {"card": used, "already": False}


# ---------------------------------------------------------------- admin
def revoke(conn: psycopg.Connection, card_id: int, *, actor: str, reason: str | None = None) -> dict | None:
    """Withdraw an unused card (used ones stay: their effect on the month is history). None = not unused / not found."""
    row = conn.execute(f"""UPDATE ai_quota_cards SET status = 'revoked', revoked_at = now(), revoked_by = %s, revoke_reason = %s
                           WHERE id = %s AND status = 'unused' RETURNING {CARD_COLS}""",
                       (actor, reason, card_id)).fetchone()
    if row:
        _ledger(conn, row["user_id"], card_id, "revoke", row["kind"], actor, row["amount_cny"], note=reason)
    return row


def list_cards(conn: psycopg.Connection, user_id: int | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
    q, args = f"SELECT {CARD_COLS} FROM ai_quota_cards WHERE true", []
    if user_id is not None:
        q += " AND user_id = %s"
        args.append(user_id)
    if status:
        q += " AND status = %s"
        args.append(status)
    return conn.execute(q + " ORDER BY id DESC LIMIT %s", (*args, limit)).fetchall()


def ledger(conn: psycopg.Connection, user_id: int | None = None, limit: int = 200) -> list[dict]:
    q, args = "SELECT * FROM ai_quota_ledger", []
    if user_id is not None:
        q += " WHERE user_id = %s"
        args.append(user_id)
    return conn.execute(q + " ORDER BY id DESC LIMIT %s", (*args, limit)).fetchall()


# ---------------------------------------------------------------- the person's view (Today page)
def _local_day(ts: datetime | None, tz: str) -> str | None:
    return None if ts is None else ts.astimezone(ZoneInfo(tz)).date().isoformat()


def user_view(conn: psycopg.Connection, user_id: int) -> dict:
    """Usable cards (soonest expiry first) and the last HISTORY_DAYS of used / expired / withdrawn ones. No amounts:
    an extend card shows as a percentage of the person's monthly quota."""
    expire_due(conn, user_id)
    tz = (conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone() or {}).get("timezone") or "UTC"

    def view(c: dict) -> dict:
        exp = c["expires_at"]
        return {"id": c["id"], "kind": c["kind"], "status": c["status"], "source": c["source"], "reason": c["reason"],
                "pct": _pct(conn, user_id, c["amount_cny"]) if c["kind"] == "extend" else None,
                "granted_on": _local_day(c["granted_at"], tz),
                "expires_on": _local_day(exp - timedelta(microseconds=1), tz) if exp else None,
                "used_on": _local_day(c["used_at"], tz)}
    unused = [view(c) for c in _valid_unused(conn, user_id)]
    past = conn.execute(f"""SELECT {CARD_COLS} FROM ai_quota_cards WHERE user_id = %s AND status <> 'unused'
                            AND coalesce(used_at, revoked_at, expires_at, granted_at) > now() - make_interval(days => %s)
                            ORDER BY coalesce(used_at, revoked_at, expires_at, granted_at) DESC LIMIT 30""",
                        (user_id, P.HISTORY_DAYS)).fetchall()
    return {"cards": unused, "history": [view(c) for c in past], "counts": counts(conn, user_id)}


# ---------------------------------------------------------------- periodic rules
def set_rule(conn: psycopg.Connection, name: str, *, period: str, day: int, kind: str, reason: str, actor: str,
             amount_cny: float | None = None, count: int = 1, expires_days: int | None = P.DEFAULT_EXPIRES_DAYS,
             active_within_days: int | None = None, enabled: bool = True) -> dict:
    if kind == "extend" and (amount_cny is None or not 0 < amount_cny <= P.MAX_EXTEND_CNY):
        raise ValueError(f"an extend rule needs an amount between ¥0 and ¥{P.MAX_EXTEND_CNY:g}")
    if kind == "reset":
        amount_cny = None
    try:
        with conn.transaction():
            return conn.execute("""INSERT INTO ai_quota_card_rules (name, enabled, period, day, kind, amount_cny, count, expires_days,
                                   active_within_days, reason, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                   ON CONFLICT (name) DO UPDATE SET enabled = EXCLUDED.enabled, period = EXCLUDED.period,
                                   day = EXCLUDED.day, kind = EXCLUDED.kind, amount_cny = EXCLUDED.amount_cny, count = EXCLUDED.count,
                                   expires_days = EXCLUDED.expires_days, active_within_days = EXCLUDED.active_within_days,
                                   reason = EXCLUDED.reason, updated_by = EXCLUDED.updated_by, updated_at = now()
                                   RETURNING *""",
                                (name, enabled, period, day, kind, amount_cny, count, expires_days, active_within_days,
                                 reason, actor)).fetchone()
    except psycopg.errors.CheckViolation as e:
        raise ValueError(f"invalid rule ({e.diag.constraint_name}): name a-z0-9_-, monthly day 1–28, weekly day 1–7, "
                         "count 1–10, days 1–366")


def set_rule_enabled(conn: psycopg.Connection, name: str, enabled: bool, actor: str) -> bool:
    return conn.execute("UPDATE ai_quota_card_rules SET enabled = %s, updated_by = %s, updated_at = now() WHERE name = %s",
                        (enabled, actor, name)).rowcount > 0


def list_rules(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT * FROM ai_quota_card_rules ORDER BY name").fetchall()


def period_key(rule: dict, today: date) -> str | None:
    """The period this local day belongs to, when the rule is due in it (day reached); None = not yet due."""
    if rule["period"] == "monthly":
        return f"{today:%Y-%m}" if today.day >= rule["day"] else None
    y, w, wd = today.isocalendar()
    return f"{y}-W{w:02d}" if wd >= rule["day"] else None


def _eligible(conn, user_id: int, rule: dict) -> bool:
    from . import fallback
    from ..rollout import flags
    if not flags.enabled(conn, user_id, P.FLAG) or fallback.quota_cny(conn, user_id) <= 0:
        return False
    if rule["active_within_days"]:
        return conn.execute("""SELECT 1 FROM ai_usage WHERE user_id = %s AND kind = %s AND at > now() - make_interval(days => %s)
                               LIMIT 1""", (user_id, fallback.KIND, rule["active_within_days"])).fetchone() is not None
    return True


def run_periodic(conn: psycopg.Connection, now: datetime | None = None, dry_run: bool = False) -> dict:
    """Hourly (`body-engine ai-cards periodic`): expire due cards, then every enabled rule that is due in the person's
    current period (their local date) grants under the key rule:<name>:<period> — a period never grants twice,
    a missed run catches up later in the same period. dry_run: only list who would get cards. Commits per grant."""
    now = now or _now()
    out = {"expired": 0 if dry_run else expire_due(conn), "granted": [], "would_grant": []}
    if not dry_run:
        conn.commit()
    rules = [r for r in list_rules(conn) if r["enabled"]]
    if not rules:
        return out
    for u in conn.execute("SELECT id, timezone FROM users ORDER BY id").fetchall():
        today = now.astimezone(ZoneInfo(u["timezone"] or "UTC")).date()
        for r in rules:
            pk = period_key(r, today)
            if pk is None or not _eligible(conn, u["id"], r):
                continue
            key = f"rule:{r['name']}:{pk}"
            if dry_run:
                if conn.execute("SELECT 1 FROM ai_quota_cards WHERE user_id = %s AND (grant_key = %s OR grant_key LIKE %s)",
                                (u["id"], key, key + "#%")).fetchone() is None:
                    out["would_grant"].append({"user_id": u["id"], "rule": r["name"], "period": pk, "count": r["count"]})
                continue
            res = grant(conn, u["id"], r["kind"], source="periodic", reason=r["reason"], actor=f"rule:{r['name']}",
                        amount_cny=_num(r["amount_cny"]), count=r["count"], key=key,
                        expires_at=now + timedelta(days=r["expires_days"]) if r["expires_days"] else None)
            conn.commit()
            if res["created"]:
                out["granted"].append({"user_id": u["id"], "rule": r["name"], "period": pk, "count": len(res["created"])})
    return out
