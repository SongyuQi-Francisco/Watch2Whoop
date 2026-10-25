"""Run right after compute-scores: body age (recompute all weekly snapshots each time, a few seconds), stress (last N days). A failure in one does not affect the others."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from . import healthspan_store, stress


def run(conn: psycopg.Connection, client, user_id: int, stress_days: int = stress.DEFAULT_DAYS, sparky=None,
        ow_cache: str | None = None) -> dict:
    """ow_cache: how the stress score reads the OW samples (importers/ow_cache.py), None = all from OW."""
    out = {}
    try:
        rows = healthspan_store.compute(conn, user_id, sparky=sparky)
        out["healthspan_weeks"] = healthspan_store.write(conn, user_id, rows)
        if rows:
            out["body_age"], out["pace_of_aging"] = rows[-1]["body_age"], rows[-1]["pace_of_aging"]
    except Exception as e:
        out["healthspan_error"] = f"{type(e).__name__}: {e}"
    try:
        tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
        end = datetime.now(tz).date()
        rows = stress.compute(conn, client, user_id, end - timedelta(days=stress_days - 1), end, ow_cache=ow_cache)
        out["stress_days"] = stress.write(conn, user_id, rows)
        out["stress_days_with_score"] = sum(r["score"] is not None for r in rows)
    except Exception as e:
        out["stress_error"] = f"{type(e).__name__}: {e}"
    return out
