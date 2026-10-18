"""Read scores: shared by the HTTP API and the AI assistant."""
from datetime import date

import psycopg

FIELDS = """date, sleep_score, sleep_performance, sleep_actual_min, sleep_need_min, sleep_debt_min,
            recovery, hrv_kind, hrv_night_ln, round(exp(hrv_night_ln)::numeric, 1)::float AS hrv_night_ms,
            rhr_night, resp_rate_night, strain, trimp, hr_max, muscle_load, ctl, atl, tsb,
            flags, algo_version"""


def scores(conn: psycopg.Connection, user_id: int, start: date, end: date, details: bool = False) -> list[dict]:
    sql = f"SELECT {FIELDS}{', details' if details else ''} FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date"
    return conn.execute(sql, (user_id, start, end)).fetchall()
