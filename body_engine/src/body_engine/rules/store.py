"""Read reminders, change status: shared by the HTTP API and the AI assistant.
title / message come back in the request language (issue #42): an English request (Today page, X-Body-Lang: en) gets the
title_en / message_en columns, or the Chinese when they are NULL (rows from before migration 310, until the next evaluate-rules).
AI tools run in zh, so they always see the Chinese and no extra fields."""
from datetime import date

import psycopg

from ..i18n import is_en

STATUSES = ("new", "seen", "dismissed")
FIELDS = ("id, date, rule_key, subject, level, title, message, title_en, message_en, status, rules_version, created_at, "
          "updated_at, status_changed_at")


def _lang(row: dict | None) -> dict | None:
    """Drop the _en columns; in an English request use them for title / message."""
    if row is None:
        return None
    t, m = row.pop("title_en", None), row.pop("message_en", None)
    if is_en():
        row["title"], row["message"] = t or row["title"], m or row["message"]
    return row


def list_reminders(conn: psycopg.Connection, user_id: int, start: date, end: date,
                   statuses: list[str] | None = None, rule_keys: list[str] | None = None,
                   evidence: bool = True) -> list[dict]:
    q = f"SELECT {FIELDS}{', evidence' if evidence else ''} FROM reminders WHERE user_id = %s AND date BETWEEN %s AND %s"
    args: list = [user_id, start, end]
    if statuses:
        q += " AND status = ANY(%s)"
        args.append(statuses)
    if rule_keys:
        q += " AND rule_key = ANY(%s)"
        args.append(rule_keys)
    q += " ORDER BY date DESC, array_position(ARRAY['alert','warning','info'], level), rule_key, subject"
    return [_lang(r) for r in conn.execute(q, args).fetchall()]


def rule_runs(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    return conn.execute("SELECT date, ran_at, rules_version, results FROM rule_runs WHERE user_id = %s AND date BETWEEN %s AND %s"
                        " ORDER BY date DESC", (user_id, start, end)).fetchall()


def set_status(conn: psycopg.Connection, user_id: int, reminder_id: int, status: str) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"status 只能是 {' / '.join(STATUSES)}")
    return _lang(conn.execute(
        f"""UPDATE reminders SET status = %s, status_changed_at = now()
            WHERE id = %s AND user_id = %s RETURNING {FIELDS}""", (status, reminder_id, user_id)).fetchone())
