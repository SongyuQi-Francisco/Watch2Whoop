"""One weigh-in with its conditions: the weight goes through assistant/sparky_write.log_weight (idempotent, conflict-aware),
the conditions into weight_context. Shared by POST /weight (the Today page) and the AI's log_weight tool. Nothing is written
for the conditions while the weight is in conflict (the caller asks the user first)."""
from datetime import date, datetime

import psycopg

from ..assistant import sparky_write
from . import store


def log_weight(conn: psycopg.Connection, user_id: int, a: dict, *, context_tags=None, note=None, measured_at: datetime | None = None) -> dict:
    """a = the sparky_write.log_weight arguments. context_tags / note None = leave the stored conditions alone."""
    if context_tags is not None:   # reject bad tags before anything is written
        store.clean_tags(context_tags)
    if note is not None:
        store.clean_note(note)
    res = sparky_write.log_weight(conn, user_id, a)
    if not res.get("conflicts"):
        store.save(conn, user_id, date.fromisoformat(a["date"]), tags=context_tags, note=note, measured_at=measured_at)
    return res
