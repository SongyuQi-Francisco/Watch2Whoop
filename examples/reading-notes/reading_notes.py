"""Reading notes: a personal data layer on the datalayer framework, for a kind of data that has nothing to do with health.

Any AI client reads and writes one person's reading notes over MCP. This file only says what is specific to the data: the
tables, the tools and who shares what. The framework does the rest: who is calling, read-only callers, writes only for
oneself, asking before a delete, an audit row per call, a log of every AI write with rollback, tools loaded on demand.

    python reading_notes.py stdio --user 1      # one process, one person (local AI clients)
    python reading_notes.py http --port 8000    # many people at http://127.0.0.1:8000/mcp, a Bearer token per request

The demo accounts are in TABLES. A real application hashes its tokens and puts TLS in front. See README.md; what the
framework asks of a database is in sqlite_store.py.
"""
import argparse
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Route

from body_engine.datalayer import Caller, PlaybookSource, Registry, Tool, ToolsetConfig, WriteLog, nullable, schema, toolsets
from body_engine.datalayer.mcp import http as mcp_http
from body_engine.datalayer.mcp import server as mcp_server
from body_engine.datalayer.mcp import stdio as mcp_stdio
from sqlite_store import Audit, Db, WriteRows, create_tables

# ---------------------------------------------------------------- the data
TABLES = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, token TEXT UNIQUE, can_write INTEGER);
CREATE TABLE IF NOT EXISTS shares (owner_id INTEGER, viewer_id INTEGER, scope TEXT, PRIMARY KEY (owner_id, viewer_id, scope));
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user_id INTEGER, book TEXT, text TEXT);
INSERT OR IGNORE INTO users VALUES (1, 'Ann', 'ann-demo', 1), (2, 'Ben', 'ben-demo', 1), (3, 'Cai', 'cai-demo-read-only', 0);
INSERT OR IGNORE INTO shares VALUES (2, 1, 'notes');   -- Ben lets Ann read his notes, and nobody else
INSERT OR IGNORE INTO notes VALUES (1, 2, 'Example Book', 'Example note: the second chapter is the best one.');
"""


def init_db(path) -> None:
    create_tables(path, TABLES)


class Shares:
    """datalayer.Policy: your own notes, and notes someone shared with you."""

    def can_view(self, conn, viewer_id, owner_id, scope):
        return viewer_id == owner_id or conn.execute(
            "SELECT 1 FROM shares WHERE owner_id = ? AND viewer_id = ? AND scope = ?", (owner_id, viewer_id, scope)).fetchone() is not None


# ---------------------------------------------------------------- tools: handler(ctx, args), the owner already checked
def _note(conn, user_id, note_id):
    row = conn.execute("SELECT id, book, text FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)).fetchone()
    if row is None:
        raise ValueError(f"No note {note_id}")
    return dict(row)


def list_books(ctx, a):
    return [dict(r) for r in ctx.conn.execute(
        "SELECT book, count(*) AS notes FROM notes WHERE user_id = ? GROUP BY book ORDER BY book", (ctx.owner_id,))]


def get_notes(ctx, a):
    return [dict(r) for r in ctx.conn.execute(
        "SELECT id, book, text FROM notes WHERE user_id = ? AND book = coalesce(?, book) ORDER BY id", (ctx.owner_id, a["book"]))]


def add_note(ctx, a):
    note_id = ctx.conn.execute("INSERT INTO notes (user_id, book, text) VALUES (?,?,?)",
                               (ctx.owner_id, a["book"], a["text"])).lastrowid
    ctx.log("note", "create", note_id, None, {"book": a["book"], "text": a["text"]})   # before / after, for the rollback
    return {"id": note_id}


def edit_note(ctx, a):
    before = _note(ctx.conn, ctx.owner_id, a["note_id"])
    ctx.conn.execute("UPDATE notes SET text = ? WHERE id = ?", (a["text"], a["note_id"]))
    ctx.log("note", "update", a["note_id"], before, {**before, "text": a["text"]})
    return {"id": a["note_id"]}


def delete_note(ctx, a):
    before = _note(ctx.conn, ctx.owner_id, a["note_id"])
    ctx.conn.execute("DELETE FROM notes WHERE id = ?", (a["note_id"],))
    ctx.log("note", "delete", a["note_id"], before)
    return {"deleted": a["note_id"]}


def undo_note(conn, user_id, row):
    """Puts a logged change of kind "note" back (reg.rollback below)."""
    conn.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (row["target"], user_id))
    if b := row["before"]:
        conn.execute("INSERT INTO notes (id, user_id, book, text) VALUES (?,?,?,?)", (b["id"], user_id, b["book"], b["text"]))
    return {"note": row["target"]}


def get_write_log(ctx, a):
    return ctx.writelog.list_log(ctx.conn, ctx.viewer_id)


def rollback_write(ctx, a):
    return ctx.writelog.rollback(ctx.conn, ctx.viewer_id, a["log_id"])


def get_playbook(ctx, a):
    return {"name": a["name"], "text": PLAYBOOKS.text(a["name"])}


USER = {"type": "string", "description": "A user id who shared their notes with you; leave out for your own"}
TEXT, ID = {"type": "string"}, {"type": "integer"}
TOOLS = [
    Tool("list_books", "Books with notes, and how many.", schema({"user": USER}), list_books, scope="notes", group="notes"),
    Tool("get_notes", "Notes on one book, or all.", schema({"user": USER, "book": nullable("string")}), get_notes,
         scope="notes", group="notes"),
    Tool("add_note", "Add a note on a book.", schema({"book": TEXT, "text": TEXT}), add_note, scope="notes", group="notes",
         writes=True),
    Tool("edit_note", "Change a note's text.", schema({"note_id": ID, "text": TEXT}), edit_note, scope="notes", group="notes",
         writes=True),
    Tool("delete_note", "Delete a note.", schema({"note_id": ID}), delete_note, scope="notes", group="notes", writes=True,
         confirm=True),
    Tool("get_write_log", "What the AI changed lately; rollback = ok can be undone.", schema({}), get_write_log, scope="",
         group="history"),
    Tool("rollback_write", "Undo one change of the write log.", schema({"log_id": ID}), rollback_write, scope="",
         group="history", writes=True, confirm=True),
    Tool("get_playbook", "Step-by-step instructions: book_review.", schema({"name": TEXT}), get_playbook, scope="",
         group="guides"),
]

# ---------------------------------------------------------------- the application, assembled in one place
GROUPS = {"notes": "Books and notes", "history": "What the AI changed, and undoing it", "guides": "Step-by-step instructions"}
SCOPES = {"notes": "Books and notes; what one reader can share with another"}
OPTIONAL = {"user": "me"}    # `user` may be left out: the MCP schema does not require it and the executor fills this in
META = toolsets.meta_tools(GROUPS)
PLAYBOOKS = PlaybookSource(Path(__file__).parent / "playbooks", ("book_review",), tool_re=re.compile(r"`([a-z]+_[a-z_]+)`"),
                           find_line="A tool that is not in your list is reached with find_tools.")
RULES = "Notes are the reader's own words: quote them, never invent or improve them."
INSTRUCTIONS = ("The user's reading notes. list_books and get_notes read them, add_note writes; say what you will write first. "
                "Other tools: find_tools. Instructions for a book review: prompt book_review.")


def toolset(confirm):
    return ToolsetConfig(core=("list_books", "get_notes", "add_note"), groups=GROUPS, confirm=confirm, meta=META,
                         optional_defaults=OPTIONAL)


reg = Registry()     # English texts; pass your own Messages for another language
reg.tools(TOOLS)
reg.rollback("note", undo_note)
APP = reg.freeze(policy=Shares(), audit=Audit(), writelog=WriteLog(WriteRows()), toolset=toolset, optional_defaults=OPTIONAL)
RESOURCES = [*mcp_server.playbook_resources(PLAYBOOKS), mcp_server.scopes_resource(SCOPES),
             mcp_server.rules_resource(lambda: RULES)]


def build_server(identity_of, path, resources=False):
    return mcp_server.build_server("reading-notes", f"{INSTRUCTIONS}\n\n{RULES}", identity_of, executor=APP.executor,
                                   tools=APP.tools, toolset=APP.toolset, connect_fn=lambda: Db(path),
                                   prompts=mcp_server.playbook_prompts(PLAYBOOKS), resources=RESOURCES if resources else ())


def http_app(path, resources=False) -> Starlette:
    def authenticate(token):
        with Db(path) as conn:
            user = conn.execute("SELECT id, can_write FROM users WHERE token = ?", (token,)).fetchone()
        return user and Caller(user["id"], "mcp:http", bool(user["can_write"]))

    mcp = mcp_http.McpHttpApp(lambda identity_of: build_server(identity_of, path, resources), authenticate)
    return Starlette(routes=[Route("/mcp", mcp, methods=["GET", "POST", "DELETE"])], lifespan=lambda app: mcp.lifespan())


def main() -> None:
    p = argparse.ArgumentParser(description="Reading notes over MCP")
    p.add_argument("transport", choices=("stdio", "http"))
    p.add_argument("--db", default="reading_notes.db")
    p.add_argument("--user", type=int, default=1, help="stdio: who this process is")
    p.add_argument("--port", type=int, default=8000, help="http: the port on 127.0.0.1")
    p.add_argument("--resources", action="store_true", help="also offer MCP resources (off by default)")
    a = p.parse_args()
    init_db(a.db)
    if a.transport == "stdio":
        me = Caller(a.user, "mcp:stdio")
        mcp_stdio.run(build_server(lambda ctx: me, a.db, a.resources))
    else:
        import uvicorn
        uvicorn.run(http_app(a.db, a.resources), host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
