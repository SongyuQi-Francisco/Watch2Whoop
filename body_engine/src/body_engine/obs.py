"""Observability of the API process: who a response was for, and an application log that outlives the container.

- X-Body-User: every response carries the id of the signed-in user its request was authenticated as (Bearer token, AI token,
  Today page cookie), so the reverse proxy can log it next to the status and time (nginx: `$upstream_http_x_body_user` in
  log_format, `proxy_hide_header X-Body-User` so it does not leave the server). 5xx and slow requests can then be traced
  to a user; nothing else about the user is added. The auth functions call note_user(); UserHeaderMiddleware writes the
  header. It works across worker threads: the middleware puts one mutable slot per request in a context variable, and
  the threads FastAPI runs dependencies in see a copy of the context that refers to the same slot.
- install_file_log(): with BODY_LOG_DIR set, the API's own log (warnings, errors with tracebacks, uvicorn's error log)
  also goes to <dir>/engine-YYYY-MM-DD.log (one file per local day, LOG_KEEP_DAYS kept). `docker logs` is lost whenever
  the container is recreated (every deploy, every OOM restart), so without this an exception's traceback was gone.
  Access lines are not written (the reverse proxy has them, with the user now).
"""
from __future__ import annotations

import contextvars
import logging
import os
import re
import time
from pathlib import Path

HEADER = b"x-body-user"
LOG_KEEP_DAYS = int(os.environ.get("BODY_LOG_KEEP_DAYS", "14"))
_slot: contextvars.ContextVar[dict | None] = contextvars.ContextVar("body_user_slot", default=None)


def note_user(user_id: int | None) -> None:
    """Remember the authenticated user of the current request (no-op outside a request)."""
    slot = _slot.get()
    if slot is not None and user_id is not None:
        slot["user"] = int(user_id)


class UserHeaderMiddleware:
    """Pure ASGI (streaming responses and the MCP transport pass through untouched): adds X-Body-User when known."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        outer = _slot.get()   # set when this is a request run inside another one (POST /v1/web/boot, web/boot.py)
        slot: dict = {}
        token = _slot.set(slot)
        started = False

        async def send_with_user(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if "user" in slot:
                    headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != HEADER]
                    message = {**message, "headers": [*headers, (HEADER, str(slot["user"]).encode())]}
            await send(message)
        try:
            await self.app(scope, receive, send_with_user)
        except Exception:
            # An unhandled error: Starlette's outermost ServerErrorMiddleware would answer 500 without the header, so
            # answer here (same body) and re-raise for the traceback in the log
            if not started:
                await send_with_user({"type": "http.response.start", "status": 500,
                                      "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send_with_user({"type": "http.response.body", "body": b"Internal Server Error"})
            raise
        finally:
            _slot.reset(token)
            if outer is not None and "user" in slot:
                outer.setdefault("user", slot["user"])   # the bundle's own response names the user too


# ---------------------------------------------------------------- file log
class DailyFileHandler(logging.Handler):
    """<dir>/<prefix>-YYYY-MM-DD.log by local date; files older than keep_days are removed when a new day starts."""

    def __init__(self, directory: Path, prefix: str = "engine", keep_days: int = LOG_KEEP_DAYS):
        super().__init__()
        self.dir, self.prefix, self.keep_days = Path(directory), prefix, keep_days
        self.dir.mkdir(parents=True, exist_ok=True)
        self._day, self._f = None, None

    def _file(self):
        day = time.strftime("%Y-%m-%d")
        if day != self._day:
            if self._f:
                self._f.close()
            self._f = open(self.dir / f"{self.prefix}-{day}.log", "a", encoding="utf-8")
            self._day = day
            self.prune()
        return self._f

    def prune(self) -> list[str]:
        cutoff = time.time() - self.keep_days * 86400
        pat = re.compile(rf"^{re.escape(self.prefix)}-\d{{4}}-\d{{2}}-\d{{2}}\.log$")
        gone = []
        for p in self.dir.iterdir():
            if pat.match(p.name) and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                gone.append(p.name)
        return gone

    def emit(self, record):
        try:
            msg = self.format(record)
            with self.lock:
                f = self._file()
                f.write(msg + "\n")
                f.flush()
        except Exception:  # noqa: BLE001  logging must never break a request
            self.handleError(record)

    def close(self):
        with self.lock:
            if self._f:
                self._f.close()
                self._f = None
        super().close()


_installed: DailyFileHandler | None = None


def install_file_log(directory: str | None = None, level: int = logging.INFO) -> DailyFileHandler | None:
    """Add the daily file to the root logger and to uvicorn's logger (once per process). BODY_LOG_DIR unset = off."""
    global _installed
    d = directory or os.environ.get("BODY_LOG_DIR", "").strip()
    if not d or _installed is not None:
        return _installed
    h = DailyFileHandler(Path(d))
    h.setLevel(level)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [pid %(process)d] %(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    # uvicorn's "uvicorn" logger (parent of uvicorn.error: tracebacks of failed requests) does not propagate to the root
    # logger; uvicorn.access does not propagate to it either, so access lines stay out of the file
    lg = logging.getLogger("uvicorn")
    if not lg.propagate:
        lg.addHandler(h)
    for name in ("httpx", "httpcore"):   # one INFO line per outgoing request (the OW poller: every 30 s)
        if logging.getLogger(name).level == logging.NOTSET:
            logging.getLogger(name).setLevel(logging.WARNING)
    _installed = h
    return h
