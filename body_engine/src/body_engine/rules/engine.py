"""Run rules and write reminders (idempotent).

- Re-running on the same day: one row per (date, rule, subject); updated only if the content changed, status (new / seen / dismissed) untouched.
- Old reminders that no longer fire are deleted (reminders are derived data); but when a rule errors while reading data, its earlier reminders are kept.
- Sticky reminders (weight increase advice, banned exercises, calorie adjustment): if the same subject was "dismissed" before, the new day's reminder is dismissed too.
"""
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from . import hydration, nutrition, params as P, progression, safety, sleep_goal
from .base import Reminder, RuleResult, jsonable
from .context import Ctx, load

RULES = [
    (progression.KEY, progression.evaluate),
    (nutrition.CALORIES, nutrition.calorie_adjust),
    (nutrition.FUEL, nutrition.pre_workout_fuel),
    (nutrition.CAPS, nutrition.frequency_caps),
    (nutrition.SUGAR, nutrition.sugar_caps),
    (safety.EXERCISES, safety.exercise_restrictions),
    (safety.READINESS, safety.readiness),
    (sleep_goal.KEY, sleep_goal.evaluate),
    (hydration.KEY, hydration.evaluate),
]
RULE_KEYS = [k for k, _ in RULES]


def evaluate_ctx(ctx: Ctx) -> list[RuleResult]:
    out = []
    for key, fn in RULES:
        try:
            out.append(fn(ctx))
        except Exception as e:  # one rule failing does not affect the others
            out.append(RuleResult(key, "error", f"{type(e).__name__}: {e}"))
    return out


def _initial_status(conn, user_id: int, day: date, r: Reminder) -> str:
    if not r.sticky:
        return "new"
    prev = conn.execute(
        """SELECT status FROM reminders WHERE user_id = %s AND rule_key = %s AND subject = %s AND date < %s
           ORDER BY date DESC LIMIT 1""", (user_id, r.rule_key, r.subject, day)).fetchone()
    return "dismissed" if prev and prev["status"] == "dismissed" else "new"


def store(conn: psycopg.Connection, user_id: int, day: date, results: list[RuleResult]) -> dict:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    with conn.transaction():
        for res in results:
            if res.status == "error":
                continue
            seen = {}
            for r in res.reminders:
                seen[(r.rule_key, r.subject)] = r   # keep only the last one per subject
            for r in seen.values():
                row = conn.execute(
                    """INSERT INTO reminders (user_id, date, rule_key, subject, level, title, message, title_en, message_en,
                                              evidence, status, rules_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (user_id, date, rule_key, subject) DO UPDATE SET
                         level = EXCLUDED.level, title = EXCLUDED.title, message = EXCLUDED.message,
                         title_en = EXCLUDED.title_en, message_en = EXCLUDED.message_en,
                         evidence = EXCLUDED.evidence, rules_version = EXCLUDED.rules_version, updated_at = now()
                       WHERE (reminders.level, reminders.title, reminders.message, reminders.title_en, reminders.message_en,
                              reminders.evidence, reminders.rules_version)
                             IS DISTINCT FROM (EXCLUDED.level, EXCLUDED.title, EXCLUDED.message, EXCLUDED.title_en,
                                               EXCLUDED.message_en, EXCLUDED.evidence, EXCLUDED.rules_version)
                       RETURNING (xmax = 0) AS inserted""",
                    (user_id, day, r.rule_key, r.subject, r.level, r.title, r.message, r.title_en or None,
                     r.message_en or None, Jsonb(jsonable(r.evidence)), _initial_status(conn, user_id, day, r),
                     P.RULES_VERSION)).fetchone()
                counts["unchanged" if row is None else "inserted" if row["inserted"] else "updated"] += 1
            subjects = [s for (_, s) in seen]
            counts["deleted"] += conn.execute(
                "DELETE FROM reminders WHERE user_id = %s AND date = %s AND rule_key = %s AND NOT (subject = ANY(%s))",
                (user_id, day, res.rule_key, subjects)).rowcount
        conn.execute(
            """INSERT INTO rule_runs (user_id, date, rules_version, results) VALUES (%s, %s, %s, %s)
               ON CONFLICT (user_id, date) DO UPDATE SET ran_at = now(), rules_version = EXCLUDED.rules_version,
                 results = EXCLUDED.results""",
            (user_id, day, P.RULES_VERSION, Jsonb([r.summary() for r in results])))
    return counts


def run(conn: psycopg.Connection, user_id: int, day: date | None = None, client=None) -> dict:
    """body-engine evaluate-rules: read data -> run all rules -> write reminders / rule_runs."""
    ctx = load(conn, user_id, day, client)
    results = evaluate_ctx(ctx)
    counts = store(conn, user_id, ctx.day, results)
    return {"user_id": user_id, "date": ctx.day, "rules_version": P.RULES_VERSION, "sources_failed": ctx.errors,
            "sources_missing": ctx.missing, **counts, "rules": [r.summary() for r in results],
            "reminders": [{"rule_key": r.rule_key, "level": r.level, "title": r.title} for res in results
                          for r in res.reminders]}


def format_report(res: dict) -> str:
    icon = {"triggered": "●", "not_triggered": "○", "insufficient_data": "?", "error": "✗"}
    lines = [f"{res['date']} 用户 {res['user_id']}（规则 {res['rules_version']}）：新增 {res['inserted']}、更新 {res['updated']}、"
             f"不变 {res['unchanged']}、删除 {res['deleted']}"]
    for k, v in {**res["sources_failed"], **res["sources_missing"]}.items():
        lines.append(f"  ! 数据来源 {k}：{v}")
    for r in res["rules"]:
        lines.append(f"  {icon[r['status']]} {r['rule_key']:<22} {r['status']:<17} {r['note']}")
    for r in res["reminders"]:
        lines.append(f"    [{r['level']}] {r['rule_key']}: {r['title']}")
    return "\n".join(lines)
