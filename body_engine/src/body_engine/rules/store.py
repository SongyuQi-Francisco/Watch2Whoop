"""Read reminders, change status: shared by the HTTP API and the AI assistant."""
from datetime import date

import psycopg

STATUSES = ("new", "seen", "dismissed")
FIELDS = "id, date, rule_key, subject, level, title, message, status, rules_version, created_at, updated_at, status_changed_at"


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
    return conn.execute(q, args).fetchall()


def rule_runs(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    return conn.execute("SELECT date, ran_at, rules_version, results FROM rule_runs WHERE user_id = %s AND date BETWEEN %s AND %s"
                        " ORDER BY date DESC", (user_id, start, end)).fetchall()


def set_status(conn: psycopg.Connection, user_id: int, reminder_id: int, status: str) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"status 只能是 {' / '.join(STATUSES)}")
    return conn.execute(
        f"""UPDATE reminders SET status = %s, status_changed_at = now()
            WHERE id = %s AND user_id = %s RETURNING {FIELDS}""", (status, reminder_id, user_id)).fetchone()
