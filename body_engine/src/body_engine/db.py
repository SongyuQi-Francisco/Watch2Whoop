"""Database connection and migrations. Migrations are SQL files under migrations/, ordered by number, each applied once.
DATA_STEPS: one-time data moves that need Python (they read existing rows and write new ones), each run once after the
SQL migration it depends on and recorded in schema_migrations under its own name."""
from importlib import resources

import psycopg
from psycopg.rows import dict_row

from .config import settings


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.dsn, row_factory=dict_row)


def _diet_constraints_data(conn: psycopg.Connection) -> None:
    from .nutrition import constraints
    constraints.carry_over(conn)


def _memory_topics_data(conn: psycopg.Connection) -> None:
    from .assistant import memory
    memory.backfill_topics(conn)


# (name, the SQL migration it needs, function); names sort after their SQL file
DATA_STEPS = [
    ("450_diet_constraints_data", "450_diet_constraints.sql", _diet_constraints_data),
    ("660_memory_hygiene_data", "660_memory_hygiene.sql", _memory_topics_data),
]


def migrate(conn: psycopg.Connection) -> list[str]:
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                      name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    files = sorted(f for f in resources.files("body_engine.migrations").iterdir() if f.name.endswith(".sql"))
    applied = []
    for f in files:
        if f.name in done:
            continue
        with conn.transaction():
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
        applied.append(f.name)
        done.add(f.name)
    for name, needs, fn in DATA_STEPS:
        if name in done or needs not in done:
            continue
        with conn.transaction():
            fn(conn)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
        applied.append(name)
    return applied
