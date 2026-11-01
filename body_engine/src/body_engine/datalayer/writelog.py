"""Log of every AI write and rollback of one logged change (moved from the generic part of assistant/writelog.py,
issue #30 / #116, docs/datalayer-design.md section 3.4).

- The executor opens a capture for each write tool; handlers call record(...) with the before / after values of what
  they changed. A write tool that records nothing gets one generic row (kind=other, after = the arguments), which
  cannot be rolled back automatically.
- Outside a capture (the application's own pages, its command line) record() does nothing: only AI writes are logged.
- rollback(log_id) hands the row to the handler registered for its kind, which puts the before values back (one row =
  one change), and logs the rollback itself; a row is rolled back at most once.
Where the rows live is a WriteLogStore, on the same connection as the tool (a failed tool's rows go with its savepoint).
PgWriteLogStore keeps them in a Postgres table the application owns (Watch2Whoop: ai_write_log, migration 265); its columns
are id, created_at, user_id, channel, tool, kind, action, target, before, after, rolled_back_at, rolled_back_by."""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Callable, Mapping, Protocol

from psycopg import sql
from psycopg.types.json import Jsonb

from .messages import ENGLISH, Messages


def plain(x):
    """JSON-safe copy (dates, Decimals -> text / numbers)."""
    return None if x is None else json.loads(json.dumps(x, default=str, ensure_ascii=False))


class WriteLogStore(Protocol):
    """A row is a mapping with the columns above: created_at and rolled_back_at datetimes (rolled_back_at None until then),
    before and after the JSON values that were inserted, target a string or None."""

    def insert(self, conn, user_id: int, channel: str, tool: str, kind: str, action: str, target: str | None,
               before, after) -> int:
        """Add a row; its id."""
        ...

    def recent(self, conn, user_id: int, days: int, limit: int) -> list[Mapping]:
        """That user's rows of the last `days` days, newest first, at most `limit`."""
        ...

    def row_for_update(self, conn, user_id: int, log_id: int) -> Mapping | None:
        """One row of that user, locked against a second rollback where the database needs it; None = no such row."""
        ...

    def mark_rolled_back(self, conn, log_id: int, by: int | None) -> None:
        """Set rolled_back_at to now and rolled_back_by to `by` (the id of the row that logged the rollback)."""
        ...


class PgWriteLogStore:
    def __init__(self, table: str):
        self.table = table

    def insert(self, conn, user_id, channel, tool, kind, action, target, before, after) -> int:
        return conn.execute(sql.SQL("""INSERT INTO {} (user_id, channel, tool, kind, action, target, before, after)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""").format(sql.Identifier(self.table)),
                            (user_id, channel, tool, kind, action, target,
                             Jsonb(before) if before is not None else None,
                             Jsonb(after) if after is not None else None)).fetchone()["id"]

    def recent(self, conn, user_id, days, limit) -> list[Mapping]:
        return conn.execute(sql.SQL("""SELECT id, created_at, tool, kind, action, target, before, after, rolled_back_at
                                       FROM {} WHERE user_id = %s AND created_at >= now() - %s
                                       ORDER BY id DESC LIMIT %s""").format(sql.Identifier(self.table)),
                            (user_id, timedelta(days=days), limit)).fetchall()

    def row_for_update(self, conn, user_id, log_id) -> Mapping | None:
        return conn.execute(sql.SQL("SELECT * FROM {} WHERE id = %s AND user_id = %s FOR UPDATE").format(sql.Identifier(self.table)),
                            (log_id, user_id)).fetchone()

    def mark_rolled_back(self, conn, log_id, by) -> None:
        conn.execute(sql.SQL("UPDATE {} SET rolled_back_at = now(), rolled_back_by = %s WHERE id = %s").format(sql.Identifier(self.table)),
                     (by, log_id))


class WriteLog:
    def __init__(self, store: WriteLogStore | str, messages: Messages = ENGLISH):
        """store: where the rows live; a string = PgWriteLogStore on that table."""
        self.store = PgWriteLogStore(store) if isinstance(store, str) else store
        self.messages = messages
        self._ctx: ContextVar[dict | None] = ContextVar("datalayer_write_log", default=None)
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
        row_id = self.store.insert(conn, c["user_id"], c["channel"], c["tool"], kind, action,
                                   None if target is None else str(target), plain(before), plain(after))
        c["ids"].append(row_id)
        return row_id

    def record_generic(self, conn, args: dict, result) -> None:
        """Fallback for write tools without a specific record: what was asked and what came back (no rollback)."""
        res = plain(result)
        if len(json.dumps(res, ensure_ascii=False)) > 4000:
            res = {"truncated": True}
        self.record(conn, "other", "other", None, None, {"args": {k: v for k, v in args.items() if not k.startswith("_")},
                                                         "result": res})

    def list_log(self, conn, user_id: int, days: int = 14, limit: int = 30) -> list[dict]:
        rows = self.store.recent(conn, user_id, days, limit)
        return [{"id": r["id"], "at": f"{r['created_at']:%Y-%m-%d %H:%M}", "tool": r["tool"], "kind": r["kind"],
                 "action": r["action"], "target": r["target"], "before": r["before"], "after": r["after"],
                 "rollback": None if not self.can_roll(r["kind"], r["action"])
                 else ("done" if r["rolled_back_at"] else "ok")} for r in rows]

    def rollback(self, conn, user_id: int, log_id: int, **kw) -> dict:
        """kw is passed on to the kind's handler (Watch2Whoop: client, a SparkyFitness client for tests)."""
        m = self.messages
        row = self.store.row_for_update(conn, user_id, log_id)
        if row is None:
            raise ValueError(m.no_write_log_row.format(id=log_id))
        if row["rolled_back_at"]:
            raise ValueError(m.already_rolled_back.format(at=f"{row['rolled_back_at']:%Y-%m-%d %H:%M}"))
        if not self.can_roll(row["kind"], row["action"]):
            raise ValueError(m.cannot_roll_back)
        result = self._rollbacks[row["kind"]][0](conn, user_id, row, **kw)
        new_id = self.record(conn, row["kind"], "rollback", row["target"], row["after"], row["before"])
        self.store.mark_rolled_back(conn, log_id, new_id)
        return {"rolled_back": log_id, "tool": row["tool"], "kind": row["kind"], "result": result}
