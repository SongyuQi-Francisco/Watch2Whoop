"""My Today on the AI-first page (#279, design sections 4–5): which cards, in what order, and why each one is there.

Order: what the user pinned (in their order) -> their focus -> today's findings -> what any AI used for them in the last 30 days ->
the old page's order. Cards the user took off never come back by themselves. The page draws at most MAX_MODULES of them that
have something to show; the rest are in 全部功能. Only the user's own choices are stored (ui_modules, ui_state)."""
from datetime import date, datetime, timedelta, timezone

import psycopg

from . import findings as F
from . import params as P


def state(conn: psycopg.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT focus, overview_seen_at FROM ui_state WHERE user_id = %s", (user_id,)).fetchone()
    return {"focus": list(row["focus"]) if row else [], "overview_seen_at": row["overview_seen_at"] if row else None}


def set_focus(conn: psycopg.Connection, user_id: int, focus: list[str]) -> None:
    clean = [f for f in dict.fromkeys(focus) if f in P.FOCUS]
    conn.execute("""INSERT INTO ui_state (user_id, focus) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET focus = EXCLUDED.focus, updated_at = now()""", (user_id, clean))


def overview_seen(conn: psycopg.Connection, user_id: int) -> None:
    conn.execute("""INSERT INTO ui_state (user_id, overview_seen_at) VALUES (%s, now())
                    ON CONFLICT (user_id) DO UPDATE SET overview_seen_at = COALESCE(ui_state.overview_seen_at, now()), updated_at = now()""",
                 (user_id,))


def set_module(conn: psycopg.Connection, user_id: int, key: str, choice: str) -> None:
    """choice: pinned (goes first, after the cards pinned before it), hidden (off my Today), auto (back to the rules)."""
    if key not in P.MODULES:
        raise ValueError(f"unknown module {key!r}")
    if choice == "auto":
        conn.execute("DELETE FROM ui_modules WHERE user_id = %s AND module_key = %s", (user_id, key))
        return
    if choice not in ("pinned", "hidden"):
        raise ValueError(f"unknown choice {choice!r}")
    order = conn.execute("SELECT COALESCE(max(pin_order), 0) + 1 AS n FROM ui_modules WHERE user_id = %s AND state = 'pinned'",
                         (user_id,)).fetchone()["n"] if choice == "pinned" else 0
    conn.execute("""INSERT INTO ui_modules (user_id, module_key, state, pin_order) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, module_key) DO UPDATE SET state = EXCLUDED.state, pin_order = EXCLUDED.pin_order, updated_at = now()""",
                 (user_id, key, choice, order))


def used_modules(conn: psycopg.Connection, user_id: int, now: datetime | None = None) -> list[str]:
    """Cards whose tools an AI called successfully for the user lately (any channel: in-page chat, MCP, the butler), most recent first."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=P.USED_DAYS)
    rows = conn.execute("""SELECT tool, max(at) AS last FROM assistant_audit WHERE user_id = %s AND ok AND at > %s AND tool = ANY(%s)
                           GROUP BY tool ORDER BY last DESC""", (user_id, since, list(P.TOOLS))).fetchall()
    return list(dict.fromkeys(P.TOOLS[r["tool"]] for r in rows))


def home(conn: psycopg.Connection, user_id: int, today: date) -> dict:
    """{focus, modules: [{key, reason}] in order (every candidate; the page shows the first MAX_MODULES that have content),
    hidden, overview (null once seen: the first screen's findings and what the history covers), focus_options, max_modules}."""
    st = state(conn, user_id)
    chosen = conn.execute("SELECT module_key, state, pin_order FROM ui_modules WHERE user_id = %s ORDER BY pin_order, module_key",
                          (user_id,)).fetchall()
    hidden = {r["module_key"] for r in chosen if r["state"] == "hidden"}
    found = F.findings(conn, user_id, today)
    out, seen = [], set()

    def add(key, reason):
        if key in P.MODULES and key not in seen and key not in hidden:
            out.append({"key": key, "reason": reason})
            seen.add(key)

    for r in chosen:
        if r["state"] == "pinned":
            add(r["module_key"], "pinned")
    for f in st["focus"]:
        for key in P.FOCUS.get(f, ()):
            add(key, "focus")
    for f in found:
        if f["module"]:
            add(f["module"], "finding")
    for key in used_modules(conn, user_id):
        add(key, "used")
    for key in P.MODULES:
        add(key, None)
    overview = None if st["overview_seen_at"] else {"findings": found[:P.OVERVIEW_FINDINGS], "stats": F.stats(conn, user_id)}
    return {"focus": st["focus"], "modules": out, "hidden": sorted(hidden), "overview": overview,
            "focus_options": list(P.FOCUS), "max_modules": P.MAX_MODULES}
