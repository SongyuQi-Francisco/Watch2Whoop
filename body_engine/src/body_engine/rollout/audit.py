"""Audit log of rollout changes (table rollout_audit): who changed which flag / cohort / experiment / setting, before and after."""
import getpass
import os

import psycopg
from psycopg.types.json import Jsonb


def actor(name: str | None = None) -> str:
    """--by, else BODY_ADMIN, else the OS user running the command."""
    if name:
        return name
    try:
        return os.environ.get("BODY_ADMIN") or getpass.getuser() or "cli"
    except Exception:  # no user name in a container
        return "cli"


def log(conn: psycopg.Connection, who: str, action: str, target: str, before: dict | None, after: dict | None) -> None:
    conn.execute("INSERT INTO rollout_audit (actor, action, target, before, after) VALUES (%s, %s, %s, %s, %s)",
                 (who, action, target, Jsonb(before) if before is not None else None,
                  Jsonb(after) if after is not None else None))


def recent(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    return conn.execute("SELECT * FROM rollout_audit ORDER BY at DESC, id DESC LIMIT %s", (limit,)).fetchall()
