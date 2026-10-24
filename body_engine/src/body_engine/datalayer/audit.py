"""Audit of every tool call, failed ones too (issue #116, docs/datalayer-design.md section 3.4).

AuditSink is the hook; PgAudit writes one row per call into a Postgres table the application owns (Watch2Whoop:
assistant_audit). `extra` holds the columns only some channels fill (the in-page chat's provider_id / model); when
none of them is set they are left out of the INSERT, so a database from before those columns were added still works."""
from typing import Protocol

from psycopg import sql
from psycopg.types.json import Jsonb


class AuditSink(Protocol):
    def record(self, conn, viewer_id: int, channel: str, tool: str, args: dict, ok: bool, error: str | None,
               **extra) -> None: ...


class PgAudit:
    """Columns: user_id, channel, tool, args (jsonb), ok, error, then every `extra_columns` entry that is not None."""

    def __init__(self, table: str, extra_columns: tuple[str, ...] = ()):
        self.table = table
        self.extra_columns = extra_columns

    def record(self, conn, viewer_id, channel, tool, args, ok, error, **extra) -> None:
        cols = ["user_id", "channel", "tool", "args", "ok", "error"]
        vals = [viewer_id, channel, tool, Jsonb(args), ok, error]
        if any(extra.get(c) is not None for c in self.extra_columns):
            cols += self.extra_columns
            vals += [extra.get(c) for c in self.extra_columns]
        conn.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(self.table), sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join([sql.Placeholder()] * len(cols))), vals)
