"""The saved choice of one person: theme and daily quote. No row = the defaults."""
from __future__ import annotations

import psycopg

FEATURE = "slim_mode"
THEMES = ("default", "slim")
DEFAULT = {"theme": "default", "quotes": True}


def get(conn: psycopg.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT theme, quotes FROM user_appearance WHERE user_id = %s", (user_id,)).fetchone()
    return {"theme": row["theme"], "quotes": row["quotes"]} if row else dict(DEFAULT)


def update(conn: psycopg.Connection, user_id: int, theme: str | None = None, quotes: bool | None = None) -> dict:
    """Change one's own choice; None = leave as it is. Raises ValueError for an unknown theme."""
    if theme is not None and theme not in THEMES:
        raise ValueError(f"unknown theme {theme!r}")
    cur = get(conn, user_id)
    new = {"theme": cur["theme"] if theme is None else theme, "quotes": cur["quotes"] if quotes is None else quotes}
    conn.execute("""INSERT INTO user_appearance (user_id, theme, quotes) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET theme = EXCLUDED.theme, quotes = EXCLUDED.quotes, updated_at = now()""",
                 (user_id, new["theme"], new["quotes"]))
    return new
