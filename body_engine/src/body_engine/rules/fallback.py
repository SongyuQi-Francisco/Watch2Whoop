"""2.0.0 S9 (nativesync/primary.py): reminders of a user whose phone evaluates the rules (package `rules` primary).

The stored reminders are then the phone's uploads (kind `reminders`). When today has no rule run yet (the phone has not
uploaded today's evaluation), the app-serving readers (GET /reminders, the Today document) evaluate today on demand with
the Python twin: nothing is stored (no id: a status change needs the stored row, which comes with the phone's upload),
the fallback is recorded. Everyone else: exactly store.list_reminders.
"""
from datetime import date

import psycopg

from .. import queries
from . import store


def list_reminders(conn: psycopg.Connection, user_id: int, start: date, end: date, statuses: list[str] | None = None,
                   rule_keys: list[str] | None = None, evidence: bool = True) -> list[dict]:
    rows = store.list_reminders(conn, user_id, start, end, statuses, rule_keys, evidence)
    today = queries.local_today(conn, user_id)
    if not start <= today <= end or (statuses and "new" not in statuses):
        return rows
    if conn.execute("SELECT 1 FROM rule_runs WHERE user_id = %s AND date = %s", (user_id, today)).fetchone():
        return rows
    from ..nativesync import primary
    if not primary.server_primary(conn, user_id, "rules"):
        return rows
    from . import engine, params as P
    results = engine.evaluate_ctx(engine.load(conn, user_id, today, None))
    primary.fallback(conn, user_id, "rules", "reminders", today)
    have = {(r["rule_key"], r["subject"]) for r in rows if r["date"] == today}
    extra = []
    for r in engine.reminder_rows(results):
        if (r["rule_key"], r["subject"]) in have or (rule_keys and r["rule_key"] not in rule_keys):
            continue
        row = {"id": None, "date": today, "rule_key": r["rule_key"], "subject": r["subject"], "level": r["level"],
               "title": r["title"], "message": r["message"], "title_en": r["title_en"] or None,
               "message_en": r["message_en"] or None, "status": "new", "rules_version": P.RULES_VERSION,
               "created_at": None, "updated_at": None, "status_changed_at": None, "computed_on_demand": True}
        if evidence:
            row["evidence"] = r["evidence"]
        extra.append(store._lang(row))
    order = {"alert": 0, "warning": 1, "info": 2}
    extra.sort(key=lambda r: (order.get(r["level"], 3), r["rule_key"], r["subject"]))
    return extra + rows if extra else rows
