"""Kind `reminders` of package M6 (rules; docs/ios-native-2.0.md sections 3.2 and 4): the rule reminders the phone evaluated.

Rows of one day = what rules/engine.evaluate_data answers for that day, flattened:
- one run row {role: "run", now, rules: [{rule_key, status, note, reminders, details?}]}: the evaluation time (the water
  reminder depends on it) and every rule's summary, as rule_runs keeps it;
- one row per reminder {role: "reminder", rule_key, subject, level, title, message, title_en, message_en, evidence,
  sticky, status}: engine.reminder_rows (rules that errored are left out, so `store` keeps their old reminders) plus the
  phone's own status of it (null, or seen / dismissed when the user changed it on the phone).
The phone builds them with the bundle's rulesLocal (src/36_rules.js) = rules/context.assemble + engine.evaluate_data on its
own tables (scores, sleep, body mass, journal) and the raw inputs document GET /v1/users/me/rules/inputs.

Spot check (section 3.4): the server evaluates the day again with rules/context.load_raw (at the phone's `now`) and
engine.evaluate_data; the set of (rule_key, subject) and every level exact, texts exact, the evidence and the rules'
summaries exact (both sides round their numbers to 3 decimals before they are kept: rules/base.jsonable). The phone's status
is not compared. A day's inputs can change after the phone evaluated it (a food entry logged later the same day): the check
compares the newest uploaded day before today only.
Stored (primary only): rules/engine.store with the phone's rows (same idempotent upsert, sticky dismissals, old reminders of
errored rules kept, rule_runs written), then origin 'app' + bundle_version (migration 1050); a status the user changed on
the phone only moves forward (new -> seen -> dismissed), never back over a change made elsewhere (the AI's dismiss_reminder,
another device). Status changes of server rows stay ordinary writes: PATCH /reminders/{id} through the offline queue.
Pull: record kind `reminders` of the `records` stream (whole-day replace), so the phone sees statuses changed elsewhere and a
reinstalled phone gets its reminders back.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import psycopg

from ...i18n import L
from ...rules import context, engine
from .. import bodies

KIND = "reminders"
PACKAGE = "rules"
ALGO_KEY = "rules"
# section 3.4: the set of (rule_key, level) exact, texts exact, payload numbers 1e-6 — the payload (evidence, the rules'
# details) is rounded to 3 decimals on both sides before it is kept (rules/base.jsonable), so it is compared exactly
TOLERANCE: dict = {}
LEVELS = ("info", "warning", "alert")
STATUSES = ("triggered", "not_triggered", "insufficient_data", "error")
PHONE_STATUSES = (None, "new", "seen", "dismissed")
RANK = {"new": 0, "seen": 1, "dismissed": 2}
MAX_TEXT = 4000              # characters of one title / message (the longest rule text is ~600)
MAX_JSON = 60_000            # characters of one row's evidence / one run's summaries

RECORD_KIND = "reminders"
RECORD_TABLE = "reminders"
RECORD_DAYCOL = "date"
record_body = bodies._simple("reminders", "date", "rule_key, subject")


def _text(v, field: str, nullable: bool = False) -> str | None:
    if v is None and nullable:
        return None
    if not isinstance(v, str) or len(v) > MAX_TEXT:
        raise ValueError(L(f"{field} 应该是文字", f"{field} must be text"))
    return v


def _json(v, field: str, kind: type) -> object:
    if not isinstance(v, kind) or len(json.dumps(v, ensure_ascii=False)) > MAX_JSON:
        raise ValueError(L(f"{field} 格式不对", f"Bad {field}"))
    return v


def validate(day: date, rows: list[dict]) -> list[dict]:
    runs = [r for r in rows if r.get("role") == "run"]
    if len(runs) != 1 or any(r.get("role") not in ("run", "reminder") for r in rows):
        raise ValueError(L("一天要有一行 run 和若干行 reminder", "One run row and reminder rows per day"))
    run = runs[0]
    now = run.get("now")
    if now is not None:
        try:
            datetime.fromisoformat(now)
        except (TypeError, ValueError):
            raise ValueError(L("now 不是时间", "now is not a time")) from None
    rules = _json(run.get("rules"), "rules", list)
    for s in rules:
        if not isinstance(s, dict) or s.get("rule_key") not in engine.RULE_KEYS or s.get("status") not in STATUSES:
            raise ValueError(L("规则结果格式不对", "Bad rule result"))
    out = [{"role": "run", "now": now, "rules": rules}]
    seen = set()
    for r in rows:
        if r.get("role") != "reminder":
            continue
        if r.get("rule_key") not in engine.RULE_KEYS or r.get("level") not in LEVELS or r.get("status") not in PHONE_STATUSES:
            raise ValueError(L("提醒格式不对", "Bad reminder"))
        key = (r["rule_key"], _text(r.get("subject") or "", "subject"))
        if key in seen:
            raise ValueError(L("同一条提醒出现了两次", "The same reminder twice"))
        seen.add(key)
        out.append({"role": "reminder", "rule_key": r["rule_key"], "subject": key[1], "level": r["level"],
                    "title": _text(r.get("title"), "title"), "message": _text(r.get("message"), "message"),
                    "title_en": _text(r.get("title_en") or "", "title_en"),
                    "message_en": _text(r.get("message_en") or "", "message_en"),
                    "evidence": _json(r.get("evidence") or {}, "evidence", dict), "sticky": bool(r.get("sticky")),
                    "status": r.get("status")})
    return out


def row_key(row: dict) -> tuple:
    return ("run",) if row["role"] == "run" else ("reminder", row["rule_key"], row["subject"])


def rows_of(result: dict, now: str | None, status: dict | None = None) -> list[dict]:
    """engine.evaluate_data's answer -> the day's rows (status: the phone's, by (rule_key, subject))."""
    out = [{"role": "run", "now": now, "rules": result["rules"]}]
    for r in result["reminders"]:
        out.append({"role": "reminder", **r, "status": (status or {}).get((r["rule_key"], r["subject"]))})
    return out


def recompute(conn: psycopg.Connection, uid: int, day: date, uploaded: dict[date, list[dict]]) -> list[dict] | None:
    app = uploaded.get(day) or []
    run = next((r for r in app if r["role"] == "run"), None)
    now = run["now"] if run else None
    raw = context.load_raw(conn, uid, day, None, datetime.fromisoformat(now) if now else None)
    result = engine.evaluate_data(context.assemble(raw))
    status = {(r["rule_key"], r["subject"]): r["status"] for r in app if r["role"] == "reminder"}
    return validate(day, rows_of(json.loads(json.dumps(result)), now, status))


def write(conn: psycopg.Connection, uid: int, days: dict[date, list[dict]], bundle_version: str | None) -> int:
    n = 0
    for d, rows in days.items():
        run = next(r for r in rows if r["role"] == "run")
        reminders = [r for r in rows if r["role"] == "reminder"]
        engine.store(conn, uid, d, engine.results_from(run["rules"], reminders))
        conn.execute("UPDATE reminders SET origin = 'app', bundle_version = %s WHERE user_id = %s AND date = %s",
                     (bundle_version, uid, d))
        for r in reminders:
            if r["status"] in ("seen", "dismissed"):
                conn.execute(
                    """UPDATE reminders SET status = %s, status_changed_at = now()
                       WHERE user_id = %s AND date = %s AND rule_key = %s AND subject = %s
                       AND array_position(ARRAY['new','seen','dismissed'], status) < %s""",
                    (r["status"], uid, d, r["rule_key"], r["subject"], RANK[r["status"]] + 1))
        n += len(reminders)
    return n
