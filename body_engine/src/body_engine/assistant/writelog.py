"""Log of every AI write (table ai_write_log, migration 265) and rollback of one logged change (issue #30).

The generic part (capture / record / list / rollback dispatch) lives in the datalayer framework (datalayer/writelog.py,
issue #116); this module is Watch2Whoop's write log under the old import path and names, plus the rollback handler of
every kind that can be rolled back.
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


# ---------------------------------------------------------------- rollback handlers, one per kind
# Imports are inside the handlers: these modules import this one (they call record).
def _sparky(conn, user_id, row, client=None):
    from . import sparky_write
    return sparky_write.rollback(conn, user_id, row["kind"], row["action"], row["before"], row["after"], client=client)


def _merge(conn, user_id, row, client=None):   # food entries merged into one (assistant/food_combine.py): split them again
    from . import food_combine
    return food_combine.split(conn, user_id, int(row["target"]), client=client, log=False)


def _template(conn, user_id, row, client=None):   # a template made from logged entries: remove it
    n = conn.execute("UPDATE meal_templates SET active = FALSE, updated_at = now() WHERE id = %s AND user_id = %s AND active",
                     (int(row["target"]), user_id)).rowcount
    return {"template_removed": int(row["target"]) if n else None}


def _workout(conn, user_id, row, client=None):
    from ..training import overrides
    return overrides.restore(conn, user_id, int(row["target"]), row["before"], row["after"])


def _lab(conn, user_id, row, client=None):
    from ..records import lab_catalog
    return lab_catalog.rollback(conn, user_id, row["action"], row["before"], row["after"])


def _water(conn, user_id, row, client=None):
    from ..water import store as water_store
    return water_store.rollback(conn, user_id, row["action"], row["before"], row["after"])


def _pantry(conn, user_id, row, client=None):   # what a logged meal took from the pantry goes back; the food entry stays (issue #104)
    from ..grocery import pantry_auto
    back = pantry_auto.restore(conn, user_id, row["target"])
    return {"pantry_restored": back["restored"], **({"pantry_not_restored": back["not_restored"]} if back["not_restored"] else {})}


def _profile(conn, user_id, row, client=None):   # a profile card item the AI created / replaced (issue #113): the old value comes back
    from . import memory
    return memory.rollback_profile(conn, user_id, row["target"], row["before"], row["after"])


def _observation(conn, user_id, row, client=None):
    from . import memory
    return memory.rollback_observation(conn, user_id, int(row["target"]), row["before"], row["after"])


def _memory_state(conn, user_id, row, client=None):   # archive / restore / confirm of a memory entry (issue #113 phase 2)
    from . import memory_review
    return memory_review.rollback_state(conn, user_id, row["target"], row["before"], row["after"])


for _kind in ("food", "session", "sets", "weight"):
    LOG.register_rollback(_kind, _sparky)
# merge: split it again; template: remove the template made from entries; observation: a rewrite of a note (issue #113)
LOG.register_rollback("merge", _merge, only_action="merge")
LOG.register_rollback("template", _template, only_action="create")
LOG.register_rollback("workout", _workout)
LOG.register_rollback("lab", _lab)
LOG.register_rollback("water", _water)
LOG.register_rollback("pantry", _pantry)
LOG.register_rollback("profile", _profile)
LOG.register_rollback("observation", _observation, only_action="update")
LOG.register_rollback("memory_state", _memory_state)   # logged as an update; any status change can be undone

ROLLBACK_KINDS = LOG.kinds


# ---------------------------------------------------------------- old names
capture = LOG.capture
record = LOG.record
record_generic = LOG.record_generic
list_log = LOG.list_log
_can_roll = LOG.can_roll


def rollback(conn: psycopg.Connection, user_id: int, log_id: int, client=None) -> dict:
    return LOG.rollback(conn, user_id, log_id, client=client)
