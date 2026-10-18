"""Failure-count limit for web login (/v1/web/login): per client IP, reject early (429) after too many failures in the window, without hitting the database.

On the public host (body.example.com) nginx already rate-limits requests and fail2ban bans IPs flooding 401s; this is the last application-level line:
even if the config in front is broken, one IP cannot try tokens forever. The token itself is 32 random bytes so brute force is unrealistic;
this limit mainly stops scanners from hammering the login endpoint and filling up the database and logs.

- The client IP is request.client.host. Body Engine only binds 127.0.0.1, so nginx / tailscale serve is always in front;
  on Ali uvicorn trusts X-Forwarded-For via FORWARDED_ALLOW_IPS=*, and nginx overwrites that header with $remote_addr (not appends), so clients cannot forge it.
- Kept in process memory only (single uvicorn process): cleared on restart, which is fine; with multiple processes each counts separately, so the effective limit is multiplied by N.
- A successful login clears that IP's failure record.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

MAX_FAILURES = int(os.environ.get("BODY_LOGIN_MAX_FAILURES", "10"))
WINDOW_SECONDS = int(os.environ.get("BODY_LOGIN_WINDOW_SECONDS", "900"))
MAX_TRACKED_IPS = 10_000   # Keep many distinct IPs from blowing up memory: drop the oldest when over the cap

_lock = threading.Lock()
_failures: dict[str, deque] = {}


def _prune(q: deque, now: float) -> None:
    while q and q[0] <= now - WINDOW_SECONDS:
        q.popleft()


def retry_after(ip: str, now: float | None = None) -> int:
    """Whether this IP may try now: returns 0 if yes, otherwise how many seconds it still has to wait."""
    now = time.monotonic() if now is None else now
    with _lock:
        q = _failures.get(ip)
        if not q:
            return 0
        _prune(q, now)
        if len(q) < MAX_FAILURES:
            return 0
        return max(1, int(q[0] + WINDOW_SECONDS - now) + 1)


def record_failure(ip: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _lock:
        q = _failures.get(ip)
        if q is None:
            if len(_failures) >= MAX_TRACKED_IPS:
                _failures.pop(next(iter(_failures)))
            q = _failures[ip] = deque()
        _prune(q, now)
        q.append(now)


def record_success(ip: str) -> None:
    with _lock:
        _failures.pop(ip, None)


def reset() -> None:
    """For tests."""
    with _lock:
        _failures.clear()
