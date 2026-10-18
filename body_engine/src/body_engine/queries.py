"""Data-reading functions. API routes and AI assistant tools both call these, so both see the same data under the same authorization."""
from datetime import date

import psycopg


def daily_metrics(conn: psycopg.Connection, user_id: int, start: date, end: date,
                  metrics: list[str] | None = None) -> list[dict]:
    sql = """SELECT date, metric, value, unit, agg, n, min_value, max_value, source, flags
             FROM daily_metrics WHERE user_id = %s AND date BETWEEN %s AND %s"""
    params: list = [user_id, start, end]
    if metrics:
        sql += " AND metric = ANY(%s)"
        params.append(metrics)
    return conn.execute(sql + " ORDER BY date, metric", params).fetchall()


def metric_names(conn: psycopg.Connection, user_id: int) -> list[dict]:
    return conn.execute(
        """SELECT metric, unit, agg, count(*) AS days, min(date) AS first, max(date) AS last
           FROM daily_metrics WHERE user_id = %s GROUP BY metric, unit, agg ORDER BY metric""",
        (user_id,)).fetchall()


def sleep_sessions(conn: psycopg.Connection, user_id: int, start: date, end: date,
                   main_only: bool = False) -> list[dict]:
    sql = """SELECT wake_date, start_at, end_at, asleep_min, core_min, deep_min, rem_min, awake_min,
                    unspecified_min, is_main, source, device
             FROM sleep_sessions WHERE user_id = %s AND wake_date BETWEEN %s AND %s"""
    if main_only:
        sql += " AND is_main"
    return conn.execute(sql + " ORDER BY start_at", (user_id, start, end)).fetchall()


def profile(conn: psycopg.Connection, user_id: int) -> dict:
    row = conn.execute("""SELECT id, display_name, birth_date, sex, height_cm, timezone FROM users WHERE id = %s""",
                       (user_id,)).fetchone()
    if row and row["birth_date"]:
        today = date.today()
        b = row["birth_date"]
        row["age"] = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return row


def events(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    return conn.execute(
        """SELECT e.id, e.occurred_at, e.time_precision, e.category, e.body_part, e.severity, e.title, e.note,
                  e.resolved_on
           FROM events e JOIN users u ON u.id = e.user_id
           WHERE e.user_id = %s AND (e.occurred_at AT TIME ZONE u.timezone)::date BETWEEN %s AND %s
           ORDER BY e.occurred_at""", (user_id, start, end)).fetchall()


def add_event(conn: psycopg.Connection, user_id: int, occurred_at: str, title: str, category: str | None,
              body_part: str | None, severity: int | None, note: str | None) -> dict:
    """An occurred_at without a timezone is interpreted in the user's own timezone."""
    return conn.execute(
        """INSERT INTO events (user_id, occurred_at, category, body_part, severity, title, note)
           SELECT %s, CASE WHEN %s::text ~ '([+-]\\d{2}:?\\d{2}|Z)$' THEN %s::timestamptz
                           ELSE (%s::timestamp AT TIME ZONE u.timezone) END,
                  %s, %s, %s, %s, %s
           FROM users u WHERE u.id = %s
           RETURNING id, occurred_at, title""",
        (user_id, occurred_at, occurred_at, occurred_at, category, body_part, severity, title, note,
         user_id)).fetchone()
