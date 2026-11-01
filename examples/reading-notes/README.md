# Reading notes

A small personal data layer on the [datalayer framework](../../body_engine/src/body_engine/datalayer/README.md), for a kind
of data that has nothing to do with health. Any AI client reads and writes one person's reading notes over MCP, on SQLite,
through stdio or Streamable HTTP.

It exists to show that the framework works without Watch2Whoop: it imports only `body_engine.datalayer`, never another
`body_engine` module, and CI runs it on every pull request.

| File | What |
|---|---|
| [`reading_notes.py`](reading_notes.py) | The application: its tables, tools, sharing rule, and the one place they are assembled |
| [`sqlite_store.py`](sqlite_store.py) | What the framework asks of a database, for SQLite: a connection with savepoints, an audit, a write log |
| [`playbooks/book_review.md`](playbooks/book_review.md) | One playbook, served as an MCP prompt, by a tool and optionally as a resource |
| [`test_reading_notes.py`](test_reading_notes.py) | Starts real processes on both transports and talks to them with the MCP SDK's client |

## Run it
The framework still lives inside the Body Engine package, so use its environment (from the repository root):

```sh
cd body_engine && uv sync

# stdio: one process, one person
uv run python ../examples/reading-notes/reading_notes.py stdio --user 1

# Streamable HTTP: many people at http://127.0.0.1:8000/mcp, a Bearer token per request
uv run python ../examples/reading-notes/reading_notes.py http --port 8000

# the tests
uv run pytest ../examples/reading-notes
```

The database is `reading_notes.db` in the current directory (`--db` to change it). `--resources` also offers MCP resources,
which are off by default.

To connect an MCP client, give it one of these (Claude Code's `.mcp.json` format; the path is yours):

```json
{"mcpServers": {
  "reading-notes": {"command": "uv", "args": ["run", "--project", "/path/to/body_engine", "python",
                                              "/path/to/examples/reading-notes/reading_notes.py", "stdio", "--user", "1",
                                              "--db", "/path/to/reading_notes.db"]},
  "reading-notes-http": {"type": "http", "url": "http://127.0.0.1:8000/mcp",
                         "headers": {"Authorization": "Bearer ann-demo"}}
}}
```

## The three demo people
| User | Token | Can do |
|---|---|---|
| 1, Ann | `ann-demo` | Writes her own notes; reads Ben's, because he shared them |
| 2, Ben | `ben-demo` | Writes his own notes; has one example note; nobody shared anything with him |
| 3, Cai | `cai-demo-read-only` | A read-only token: sees no write tool and cannot reach one |

Over stdio the person is `--user`, fixed when the process starts. These tokens are for this example only: a real application
stores hashes of its tokens and puts TLS in front of the HTTP port.

## What to try
Ask your AI to add a note, then:

- **Reading someone's data needs a share.** `get_notes(user="2")` works for Ann and fails for Ben's view of her notes
  (`user="1"`). Ann still cannot write as Ben: `add_note(user="2", ...)` is refused whatever is shared.
- **Tools load on demand.** `tools/list` has the core tools, the delete and undo tools, and three meta tools. `edit_note`,
  `get_write_log` and `get_playbook` are reached with `find_tools` and `run_write_tool` / `run_read_tool`.
- **Deletes ask first.** `delete_note` and `rollback_write` are marked `confirm=True`: they are always in `tools/list`, and
  `run_write_tool` refuses them, so the approval rule your client has for that name always applies.
- **Everything an AI writes can be looked at, and undone.** `get_write_log` shows the changes with their earlier values;
  `rollback_write` puts one back, at most once.
- **Every call is on record.** The `audit` table has one row per call, failed ones too, under the real tool's name even when
  it ran through a meta tool. Look with `sqlite3 reading_notes.db "SELECT * FROM audit"`.
