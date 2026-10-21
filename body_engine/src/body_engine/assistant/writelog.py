"""Log of every AI write (table ai_write_log, migration 265) and rollback of one logged change (issue #30).

- run_tool opens a capture for each write tool; handlers call record(...) with the before / after values of what they changed.
  A write tool that records nothing gets one generic row (kind=other, after = the arguments), which cannot be rolled back automatically.
- Outside run_tool (Today page, CLI) record() does nothing: only AI writes are logged here.
- rollback(log_id) puts the before values back (one row = one change) and logs the rollback itself; a row is rolled back at most once.
"""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb

_ctx: ContextVar[dict | None] = ContextVar("ai_write_log", default=None)
ROLLBACK_KINDS = {"food", "session", "sets", "weight", "workout", "lab", "pantry"}


def _plain(x):
    """JSON-safe copy (dates, Decimals -> text / numbers)."""
    return None if x is None else json.loads(json.dumps(x, default=str, ensure_ascii=False))


@contextmanager
def capture(user_id: int, channel: str, tool: str):
    token = _ctx.set({"user_id": user_id, "channel": channel, "tool": tool, "ids": []})
    try:
        yield _ctx.get()
    finally:
        _ctx.reset(token)


def record(conn: psycopg.Connection, kind: str, action: str, target, before=None, after=None) -> int | None:
    c = _ctx.get()
    if c is None:
        return None
    row = conn.execute("""INSERT INTO ai_write_log (user_id, channel, tool, kind, action, target, before, after)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                       (c["user_id"], c["channel"], c["tool"], kind, action, None if target is None else str(target),
                        Jsonb(_plain(before)) if before is not None else None,
                        Jsonb(_plain(after)) if after is not None else None)).fetchone()
    c["ids"].append(row["id"])
    return row["id"]


def record_generic(conn, args: dict, result) -> None:
    """Fallback for write tools without a specific record: what was asked and what came back (no rollback)."""
    res = _plain(result)
    if len(json.dumps(res, ensure_ascii=False)) > 4000:
        res = {"truncated": True}
    record(conn, "other", "other", None, None, {"args": {k: v for k, v in args.items() if not k.startswith("_")},
                                                "result": res})


def list_log(conn, user_id: int, days: int = 14, limit: int = 30) -> list[dict]:
    rows = conn.execute("""SELECT id, created_at, tool, kind, action, target, before, after, rolled_back_at
                           FROM ai_write_log WHERE user_id = %s AND created_at >= now() - %s
                           ORDER BY id DESC LIMIT %s""", (user_id, timedelta(days=days), limit)).fetchall()
    return [{"id": r["id"], "at": f"{r['created_at']:%Y-%m-%d %H:%M}", "tool": r["tool"], "kind": r["kind"],
             "action": r["action"], "target": r["target"], "before": r["before"], "after": r["after"],
             "rollback": None if r["kind"] not in ROLLBACK_KINDS or r["action"] == "rollback"
             else ("done" if r["rolled_back_at"] else "ok")} for r in rows]


# ---------------------------------------------------------------- rollback
def rollback(conn: psycopg.Connection, user_id: int, log_id: int, client=None) -> dict:
    row = conn.execute("SELECT * FROM ai_write_log WHERE id = %s AND user_id = %s FOR UPDATE",
                       (log_id, user_id)).fetchone()
    if row is None:
        raise ValueError(f"没有这条操作日志：{log_id}（或不是你的）")
    if row["rolled_back_at"]:
        raise ValueError(f"这条已经在 {row['rolled_back_at']:%Y-%m-%d %H:%M} 回滚过了")
    if row["action"] == "rollback" or row["kind"] not in ROLLBACK_KINDS:
        raise ValueError("这条没有可自动回滚的前值；用对应的工具手动改回")
    from . import sparky_write
    from ..records import lab_catalog
    from ..training import overrides
    kind, before, after = row["kind"], row["before"], row["after"]
    if kind in ("food", "session", "sets", "weight"):
        result = sparky_write.rollback(conn, user_id, kind, row["action"], before, after, client=client)
    elif kind == "workout":
        result = overrides.restore(conn, user_id, int(row["target"]), before, after)
    elif kind == "pantry":   # what a logged meal took from the pantry goes back; the food entry stays (issue #104)
        from ..grocery import pantry_auto
        back = pantry_auto.restore(conn, user_id, row["target"])
        result = {"pantry_restored": back["restored"], **({"pantry_not_restored": back["not_restored"]} if back["not_restored"] else {})}
    else:
        result = lab_catalog.rollback(conn, user_id, row["action"], before, after)
    new_id = record(conn, kind, "rollback", row["target"], after, before) if _ctx.get() else None
    conn.execute("UPDATE ai_write_log SET rolled_back_at = now(), rolled_back_by = %s WHERE id = %s", (new_id, log_id))
    return {"rolled_back": log_id, "tool": row["tool"], "kind": kind, "result": result}

