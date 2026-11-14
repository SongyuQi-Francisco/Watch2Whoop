"""Per-day training session counts for the Trends document (UI 2.0.1 weekly face), counted exactly like the weekly report's
training block (insights/reports.training_block): `values` = sessions in the training log (what the report calls
training.sessions), `workouts` = kept watch / manual workouts (the report's training.workouts). Server only (the phone's own
Trends twin, trends/series.compute, does not read the training log); needs the `training` scope."""
from __future__ import annotations

from datetime import date, timedelta

import psycopg

from .. import auth, sparky_client
from ..i18n import L


def add(conn: psycopg.Connection, viewer_id: int, owner_id: int, doc: dict) -> None:
    """Adds doc["training_sessions"] (or a reason under doc["unavailable"]) to a trends/series.compute document."""
    if not auth.can_view(conn, viewer_id, owner_id, "training"):
        doc["unavailable"]["training_sessions"] = L("没有授权查看 training", "No permission for training")
        return
    dates: list[date] = doc["dates"]
    lo, end, start = doc["prev_start"], doc["end"], doc["start"]
    log, error = None, None
    try:
        log = sparky_client.training_log(sparky_client.reader_for_user(conn, owner_id), lo, end)
    except Exception as e:  # noqa: BLE001 the training log being unreadable affects only this block (as in the weekly report)
        error = sparky_client.public_error(e) if isinstance(e, sparky_client.SparkyError) else type(e).__name__
    by_day: dict[date, int] = {}
    for s in log or []:
        d = date.fromisoformat(str(s["date"])[:10])
        by_day[d] = by_day.get(d, 0) + 1
    wk: dict[date, int] = {r["local_date"]: r["n"] for r in conn.execute(
        """SELECT local_date, count(*) AS n FROM workouts WHERE user_id = %s AND dup_of IS NULL AND excluded IS NULL
           AND local_date BETWEEN %s AND %s GROUP BY local_date""", (owner_id, lo, end))}
    prev = [lo + timedelta(days=k) for k in range((start - lo).days)]
    doc["training_sessions"] = {
        "values": None if log is None else [by_day.get(d, 0) for d in dates],
        "total": None if log is None else sum(by_day.get(d, 0) for d in dates),
        "prev_total": None if log is None else sum(by_day.get(d, 0) for d in prev),
        "workouts": [wk.get(d, 0) for d in dates],
        "workouts_total": sum(wk.get(d, 0) for d in dates),
        "error": error,
    }
