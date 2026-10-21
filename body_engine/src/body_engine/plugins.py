"""Optional server plugins, loaded from BODY_PLUGIN_DIR. None by default: without the variable nothing here does anything.

A plugin is a Python package directory <BODY_PLUGIN_DIR>/<name>/ (name: lower-case letters, digits, _) whose __init__.py may define:
- `router`: a fastapi.APIRouter, included in the app as is (the plugin picks its own paths and authorization; reuse
  records.api.current_user / get_conn);
- `JOBS`: a list of Job: run inside the API process, either every `every_s` seconds or once a day at `daily_at` ("HH:MM")
  in `tz`. Each run gets its own pooled connection (committed when it returns) and holds a Postgres advisory lock, so two
  processes never run the same job at once. Jobs must be idempotent: a daily job may run again after a restart;
- `page_scripts(conn, user) -> list[str]` and `page_styles(conn, user) -> list[str]`: files under <name>/static/ that the
  Today page loads for this signed-in user (user = the users row, or None when not signed in). Only listed files are
  served, at /app/plugins/<name>/<file>; /app/plugins/manifest.json lists them for the current user;
- migrations/*.sql: applied by `body-engine migrate` after the core migrations, in file-name order, recorded in
  schema_migrations as "<name>/<file>" (so their numbering never collides with the core migrations).
Plugins are trusted code installed by the deployer. `body-engine plugins list | run <plugin> <job>` for admins.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

log = logging.getLogger(__name__)
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TICK_S = 30
MIME = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


@dataclass
class Job:
    name: str
    fn: Callable[[psycopg.Connection], object]
    every_s: int | None = None
    daily_at: str | None = None          # "HH:MM"
    tz: str = "UTC"


@dataclass
class Plugin:
    name: str
    path: Path
    module: object
    jobs: list[Job] = field(default_factory=list)

    def files(self, conn, user, kind: str) -> list[str]:
        fn = getattr(self.module, f"page_{kind}", None)
        return [f for f in (fn(conn, user) if fn else []) if _safe_file(f)]


def _safe_file(f: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*", f)) and ".." not in f


def plugin_dir() -> Path | None:
    d = os.environ.get("BODY_PLUGIN_DIR", "").strip()
    return Path(d) if d else None


@lru_cache(maxsize=1)
def load() -> tuple[Plugin, ...]:
    root = plugin_dir()
    if root is None or not root.is_dir():
        if root is not None:
            log.warning("BODY_PLUGIN_DIR %s is not a directory: no plugins", root)
        return ()
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()):
        if not NAME_RE.match(d.name):
            log.warning("plugin directory %s skipped: name must match %s", d, NAME_RE.pattern)
            continue
        mod_name = f"body_plugins.{d.name}"
        spec = importlib.util.spec_from_file_location(mod_name, d / "__init__.py", submodule_search_locations=[str(d)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        out.append(Plugin(d.name, d, module, list(getattr(module, "JOBS", []))))
        log.info("plugin %s loaded", d.name)
    return tuple(out)


def reset() -> None:
    """Forget loaded plugins (tests; a process normally loads them once)."""
    for p in load():
        sys.modules.pop(f"body_plugins.{p.name}", None)
    load.cache_clear()


# ---------------------------------------------------------------- migrations
def migrate(conn: psycopg.Connection) -> list[str]:
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for p in load():
        for f in sorted((p.path / "migrations").glob("*.sql")):
            name = f"{p.name}/{f.name}"
            if name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
            applied.append(name)
    return applied


# ---------------------------------------------------------------- HTTP: routers, page files
def _user(request: Request, conn) -> dict | None:
    from .web import session
    sid = request.cookies.get(session.COOKIE)
    return session.user_for_session(conn, sid) if sid else None


def manifest(conn, user) -> dict:
    out = {"scripts": [], "styles": []}
    for p in load():
        for kind in ("scripts", "styles"):
            out[kind] += [f"/app/plugins/{p.name}/{f}" for f in p.files(conn, user, kind)]
    return out


def install(app: FastAPI) -> None:
    """Call before the /app static mount, so /app/plugins/… is matched first."""
    for p in load():
        if getattr(p.module, "router", None) is not None:
            app.include_router(p.module.router)

    @app.get("/app/plugins/manifest.json", include_in_schema=False)
    def plugin_manifest(request: Request):
        if not load():
            return JSONResponse({"scripts": [], "styles": []})
        from . import api
        with api.pool.connection() as conn:
            return JSONResponse(manifest(conn, _user(request, conn)))

    @app.get("/app/plugins/{name}/{file:path}", include_in_schema=False)
    def plugin_file(name: str, file: str, request: Request):
        p = next((x for x in load() if x.name == name), None)
        if p is None or not _safe_file(file):
            raise HTTPException(404)
        from . import api
        with api.pool.connection() as conn:
            user = _user(request, conn)
            allowed = set(p.files(conn, user, "scripts")) | set(p.files(conn, user, "styles"))
        path = (p.path / "static" / file).resolve()
        if file not in allowed or not path.is_file() or (p.path / "static").resolve() not in path.parents:
            raise HTTPException(404)
        return FileResponse(path, media_type=MIME.get(path.suffix, "application/octet-stream"))


# ---------------------------------------------------------------- jobs
def _lock_key(plugin: str, job: str) -> int:
    return int.from_bytes(hashlib.sha256(f"plugin-job:{plugin}:{job}".encode()).digest()[:8], "big", signed=True)


def run_job(plugin: Plugin, job: Job, conn: psycopg.Connection | None = None) -> object:
    """Run one job now with an advisory lock; returns its result, or None when another process holds the lock."""
    def go(c):
        if not c.execute("SELECT pg_try_advisory_lock(%s) AS ok", (_lock_key(plugin.name, job.name),)).fetchone()["ok"]:
            return None
        try:
            res = job.fn(c)
            c.commit()
            return res
        except Exception:
            c.rollback()
            raise
        finally:
            c.execute("SELECT pg_advisory_unlock(%s)", (_lock_key(plugin.name, job.name),))
            c.commit()
    if conn is not None:
        return go(conn)
    from . import api
    with api.pool.connection() as c:
        return go(c)


def due(job: Job, now: datetime, last: datetime | None) -> bool:
    """Whether a job should run at `now` (aware) given its last start in this process."""
    if job.every_s:
        return last is None or (now - last).total_seconds() >= job.every_s
    if job.daily_at:
        local = now.astimezone(ZoneInfo(job.tz))
        hh, mm = (int(x) for x in job.daily_at.split(":"))
        if (local.hour, local.minute) < (hh, mm):
            return False
        return last is None or last.astimezone(ZoneInfo(job.tz)).date() < local.date()
    return False


async def _loop(stop: asyncio.Event) -> None:
    import anyio
    last: dict[tuple[str, str], datetime] = {}
    while not stop.is_set():
        now = datetime.now().astimezone()
        for p in load():
            for j in p.jobs:
                k = (p.name, j.name)
                if due(j, now, last.get(k)):
                    last[k] = now
                    try:
                        await anyio.to_thread.run_sync(run_job, p, j)
                    except Exception:
                        log.exception("plugin job %s/%s failed", p.name, j.name)
        try:
            await asyncio.wait_for(stop.wait(), TICK_S)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def scheduler():
    """Runs plugin jobs while the API is up; does nothing when no plugin has jobs."""
    if not any(p.jobs for p in load()):
        yield
        return
    stop = asyncio.Event()
    task = asyncio.create_task(_loop(stop))
    try:
        yield
    finally:
        stop.set()
        await task
