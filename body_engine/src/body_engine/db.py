"""Database connection and migrations. Migrations are SQL files under migrations/, ordered by number, each applied once."""
from importlib import resources

import psycopg
from psycopg.rows import dict_row

from .config import settings


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.dsn, row_factory=dict_row)


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
    return applied
