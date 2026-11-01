"""What the datalayer framework asks of a database, for SQLite: about 80 lines, and the same few things for any other one.
A connection with savepoints (Db), an audit (datalayer.AuditSink) and a write log (datalayer.WriteLogStore). All of it on
the tool's own connection, so a failed tool's write log rows go with its savepoint and its audit row stays."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

TABLES = """
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, user_id INTEGER, channel TEXT, tool TEXT, args TEXT, ok INTEGER,
                                  error TEXT);
CREATE TABLE IF NOT EXISTS write_log (id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, user_id INTEGER,
                                      channel TEXT, tool TEXT, kind TEXT, action TEXT, target TEXT, before TEXT, after TEXT,
                                      rolled_back_at TEXT, rolled_back_by INTEGER);
"""


def create_tables(path, application_tables: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(TABLES + application_tables)
    c.close()


class Db:
    """A connection as the framework needs it: a context manager that commits (rolls back after an error), commit(), and
    transaction(), which is a savepoint. A psycopg connection has all of that as it is."""

    def __init__(self, path):
        self._c = sqlite3.connect(path, isolation_level=None)   # transactions by hand: savepoints need one open
        self._c.row_factory = sqlite3.Row
        self.execute = self._c.execute
        self.execute("BEGIN IMMEDIATE")

    def __enter__(self):
        return self

    def __exit__(self, error, *_):
        self.execute("ROLLBACK" if error else "COMMIT")
        self._c.close()

    def commit(self):
        self.execute("COMMIT")
        self.execute("BEGIN IMMEDIATE")

    @contextmanager
    def transaction(self):
        self.execute("SAVEPOINT tool")
        try:
            yield
        except BaseException:
            self.execute("ROLLBACK TO tool")
            raise
        finally:
            self.execute("RELEASE tool")


class Audit:
    """datalayer.AuditSink: one row per tool call, failed ones too."""

    def record(self, conn, viewer_id, channel, tool, args, ok, error, **extra):
        conn.execute("INSERT INTO audit (user_id, channel, tool, args, ok, error) VALUES (?,?,?,?,?,?)",
                     (viewer_id, channel, tool, json.dumps(args), ok, error))


def _log_row(row):
    return row and {**dict(row), "before": json.loads(row["before"]), "after": json.loads(row["after"]),
                    "created_at": datetime.fromisoformat(row["created_at"]),
                    "rolled_back_at": row["rolled_back_at"] and datetime.fromisoformat(row["rolled_back_at"])}


class WriteRows:
    """datalayer.WriteLogStore."""

    def insert(self, conn, user_id, channel, tool, kind, action, target, before, after):
        return conn.execute("""INSERT INTO write_log (user_id, channel, tool, kind, action, target, before, after)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (user_id, channel, tool, kind, action, target, json.dumps(before), json.dumps(after))).lastrowid

    def recent(self, conn, user_id, days, limit):
        return [_log_row(r) for r in conn.execute("""SELECT * FROM write_log WHERE user_id = ? AND created_at >= datetime('now', ?)
                                                     ORDER BY id DESC LIMIT ?""", (user_id, f"-{days} days", limit))]

    def row_for_update(self, conn, user_id, log_id):
        return _log_row(conn.execute("SELECT * FROM write_log WHERE id = ? AND user_id = ?", (log_id, user_id)).fetchone())

    def mark_rolled_back(self, conn, log_id, by):
        conn.execute("UPDATE write_log SET rolled_back_at = CURRENT_TIMESTAMP, rolled_back_by = ? WHERE id = ?", (by, log_id))
