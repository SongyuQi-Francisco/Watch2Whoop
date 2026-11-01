"""Log of every AI write (table ai_write_log, migration 265) and rollback of one logged change (issue #30).

The generic part (capture / record / list / rollback dispatch) lives in the datalayer framework (datalayer/writelog.py,
issue #116); this module is Watch2Whoop's write log under the old import path and names. The rollback handler of each
kind sits in the module that writes that kind (register_rollbacks there, registered by dl_app.py).
- run_tool opens a capture for each write tool; handlers call record(...) with the before / after values of what they changed.
  A write tool that records nothing gets one generic row (kind=other, after = the arguments), which cannot be rolled back automatically.
- Outside run_tool (Today page, CLI) record() does nothing: only AI writes are logged here.
- rollback(log_id) puts the before values back (one row = one change) and logs the rollback itself; a row is rolled back at most once.
"""
import psycopg

from ..ai.datalayer_texts import MESSAGES
from ..datalayer.writelog import WriteLog
from ..datalayer.writelog import plain as _plain  # noqa: F401  (old name, assistant/food_combine.py)

LOG = WriteLog("ai_write_log", MESSAGES)


def _with_handlers() -> WriteLog:
    """LOG with every kind's handler registered, also in a process that has not loaded the tool registry yet.
    Imported here: the modules dl_app imports import this one (they call record)."""
    from .. import dl_app  # noqa: F401
    return LOG


def __getattr__(name: str):
    if name == "ROLLBACK_KINDS":
        return _with_handlers().kinds
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------- old names
capture = LOG.capture
record = LOG.record
record_generic = LOG.record_generic


def list_log(conn: psycopg.Connection, user_id: int, days: int = 14, limit: int = 30) -> list[dict]:
    return _with_handlers().list_log(conn, user_id, days, limit)


def _can_roll(kind: str, action: str) -> bool:
    return _with_handlers().can_roll(kind, action)


def rollback(conn: psycopg.Connection, user_id: int, log_id: int, client=None) -> dict:
    return _with_handlers().rollback(conn, user_id, log_id, client=client)
