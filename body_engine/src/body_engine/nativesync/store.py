"""The per-user change log (table sync_changes, migration 970). Rows are written by triggers, never by Python.

- data_version(): the user's data version, max(seq) of their changes; grows with every import, score, record or AI write.
- versions(): the current version of some keys (what a pull reports and what base_version is compared with).
- days_changed_here(): the days this transaction changed (the healthkit upload answers with them as dirty_days).
- compact(): keep only the newest row per key (run by the hourly batch).
"""
from datetime import date

import psycopg

from . import params as P


def data_version(conn: psycopg.Connection, user_id: int) -> int:
    r = conn.execute("SELECT coalesce(max(seq), 0) AS v FROM sync_changes WHERE user_id = %s", (user_id,)).fetchone()
    return int(r["v"])


def versions(conn: psycopg.Connection, user_id: int, stream: str, keys: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """{(kind, key): version} for these keys of one stream; a key never changed since the log exists is missing (= 0)."""
    if not keys:
        return {}
    rows = conn.execute("""SELECT kind, key, max(seq) AS v FROM sync_changes
                           WHERE user_id = %s AND stream = %s AND (kind, key) IN (SELECT * FROM unnest(%s::text[], %s::text[]))
                           GROUP BY kind, key""",
                        (user_id, stream, [k for k, _ in keys], [k for _, k in keys])).fetchall()
    return {(r["kind"], r["key"]): int(r["v"]) for r in rows}


def version(conn: psycopg.Connection, user_id: int, stream: str, kind: str, key: str) -> int:
    return versions(conn, user_id, stream, [(kind, key)]).get((kind, key), 0)


def days_changed_here(conn: psycopg.Connection, user_id: int) -> list[str]:
    """Local days whose data this (still open) transaction changed, any stream: what the app should pull again."""
    rows = conn.execute("""SELECT DISTINCT day FROM sync_changes
                           WHERE user_id = %s AND day IS NOT NULL AND xid = pg_current_xact_id_if_assigned()
                           ORDER BY day""", (user_id,)).fetchall()
    return [r["day"].isoformat() for r in rows]


def compact(conn: psycopg.Connection, user_id: int | None = None) -> int:
    """Delete rows superseded by a newer row of the same key. Commits. Returns rows removed."""
    n = conn.execute("""DELETE FROM sync_changes c USING sync_changes d
                        WHERE (%(u)s::int IS NULL OR c.user_id = %(u)s) AND d.user_id = c.user_id AND d.stream = c.stream
                          AND d.kind = c.kind AND d.key = c.key AND d.seq > c.seq""", {"u": user_id}).rowcount
    n += conn.execute("DELETE FROM client_writes WHERE created_at < now() - make_interval(days => %s)",
                      (P.CLIENT_WRITE_KEEP_DAYS,)).rowcount
    conn.commit()
    return n


def score_dirty_days(conn: psycopg.Connection, user_id: int, since: date, grace_minutes: int) -> list[date]:
    """Days >= since whose score inputs changed after the day's stored score was computed (or that have no score) and
    longer ago than grace_minutes: the app had its chance to recompute them and did not."""
    rows = conn.execute(
        """SELECT c.day FROM sync_changes c
           LEFT JOIN daily_scores s ON s.user_id = c.user_id AND s.date = c.day
           WHERE c.user_id = %(u)s AND c.day >= %(since)s
             AND (c.stream = ANY(%(streams)s) OR (c.stream = 'records' AND c.kind = ANY(%(kinds)s)))
           GROUP BY c.day, s.computed_at
           HAVING max(c.changed_at) < now() - make_interval(mins => %(grace)s)
              AND (s.computed_at IS NULL OR max(c.changed_at) > s.computed_at)
           ORDER BY c.day""",
        {"u": user_id, "since": since, "streams": list(P.SCORE_INPUT_STREAMS), "kinds": list(P.SCORE_INPUT_KINDS),
         "grace": grace_minutes}).fetchall()
    return [r["day"] for r in rows]
