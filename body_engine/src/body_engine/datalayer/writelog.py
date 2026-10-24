"""Log of every AI write and rollback of one logged change (moved from the generic part of assistant/writelog.py,
issue #30 / #116, docs/datalayer-design.md section 3.4).

- The executor opens a capture for each write tool; handlers call record(...) with the before / after values of what
  they changed. A write tool that records nothing gets one generic row (kind=other, after = the arguments), which
  cannot be rolled back automatically.
- Outside a capture (the application's own pages, its command line) record() does nothing: only AI writes are logged.
- rollback(log_id) hands the row to the handler registered for its kind, which puts the before values back (one row =
  one change), and logs the rollback itself; a row is rolled back at most once.
The table belongs to the application (Watch2Whoop: ai_write_log, migration 265); its columns are id, created_at,
user_id, channel, tool, kind, action, target, before, after, rolled_back_at, rolled_back_by."""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Callable

from psycopg import sql
from psycopg.types.json import Jsonb

from .messages import Messages


def plain(x):
    """JSON-safe copy (dates, Decimals -> text / numbers)."""
    return None if x is None else json.loads(json.dumps(x, default=str, ensure_ascii=False))


class WriteLog:
    def __init__(self, table: str, messages: Messages):
        self.table = table
        self.messages = messages
        self._ctx: ContextVar[dict | None] = ContextVar(table, default=None)
        self._rollbacks: dict[str, tuple[Callable, str | None]] = {}

    def register_rollback(self, kind: str, fn: Callable, only_action: str | None = None) -> None:
        """fn(conn, user_id, row, **kw) -> result puts the row's before values back. only_action: the one action of
        this kind that can be rolled back (None = every action but a rollback)."""
        self._rollbacks[kind] = (fn, only_action)

    @property
    def kinds(self) -> frozenset:
        return frozenset(self._rollbacks)

    def can_roll(self, kind: str, action: str) -> bool:
        if kind not in self._rollbacks or action == "rollback":
            return False
        only = self._rollbacks[kind][1]
        return only is None or only == action

    @contextmanager
    def capture(self, user_id: int, channel: str, tool: str):
        token = self._ctx.set({"user_id": user_id, "channel": channel, "tool": tool, "ids": []})
        try:
            yield self._ctx.get()
        finally:
            self._ctx.reset(token)

    def active(self) -> bool:
        return self._ctx.get() is not None

    def record(self, conn, kind: str, action: str, target, before=None, after=None) -> int | None:
        c = self._ctx.get()
        if c is None:
            return None
        row = conn.execute(sql.SQL("""INSERT INTO {} (user_id, channel, tool, kind, action, target, before, after)
                                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""").format(sql.Identifier(self.table)),
                           (c["user_id"], c["channel"], c["tool"], kind, action, None if target is None else str(target),
                            Jsonb(plain(before)) if before is not None else None,
                            Jsonb(plain(after)) if after is not None else None)).fetchone()
        c["ids"].append(row["id"])
        return row["id"]

    def record_generic(self, conn, args: dict, result) -> None:
        """Fallback for write tools without a specific record: what was asked and what came back (no rollback)."""
        res = plain(result)
        if len(json.dumps(res, ensure_ascii=False)) > 4000:
            res = {"truncated": True}
        self.record(conn, "other", "other", None, None, {"args": {k: v for k, v in args.items() if not k.startswith("_")},
                                                         "result": res})

    def list_log(self, conn, user_id: int, days: int = 14, limit: int = 30) -> list[dict]:
        rows = conn.execute(sql.SQL("""SELECT id, created_at, tool, kind, action, target, before, after, rolled_back_at
                                       FROM {} WHERE user_id = %s AND created_at >= now() - %s
                                       ORDER BY id DESC LIMIT %s""").format(sql.Identifier(self.table)),
                            (user_id, timedelta(days=days), limit)).fetchall()
        return [{"id": r["id"], "at": f"{r['created_at']:%Y-%m-%d %H:%M}", "tool": r["tool"], "kind": r["kind"],
                 "action": r["action"], "target": r["target"], "before": r["before"], "after": r["after"],
                 "rollback": None if not self.can_roll(r["kind"], r["action"])
                 else ("done" if r["rolled_back_at"] else "ok")} for r in rows]

    def rollback(self, conn, user_id: int, log_id: int, **kw) -> dict:
        """kw is passed on to the kind's handler (Watch2Whoop: client, a SparkyFitness client for tests)."""
        m, table = self.messages, sql.Identifier(self.table)
        row = conn.execute(sql.SQL("SELECT * FROM {} WHERE id = %s AND user_id = %s FOR UPDATE").format(table),
                           (log_id, user_id)).fetchone()
        if row is None:
            raise ValueError(m.no_write_log_row.format(id=log_id))
        if row["rolled_back_at"]:
            raise ValueError(m.already_rolled_back.format(at=f"{row['rolled_back_at']:%Y-%m-%d %H:%M}"))
        if not self.can_roll(row["kind"], row["action"]):
            raise ValueError(m.cannot_roll_back)
        result = self._rollbacks[row["kind"]][0](conn, user_id, row, **kw)
        new_id = self.record(conn, row["kind"], "rollback", row["target"], row["after"], row["before"])
        conn.execute(sql.SQL("UPDATE {} SET rolled_back_at = now(), rolled_back_by = %s WHERE id = %s").format(table),
                     (new_id, log_id))
        return {"rolled_back": log_id, "tool": row["tool"], "kind": row["kind"], "result": result}
