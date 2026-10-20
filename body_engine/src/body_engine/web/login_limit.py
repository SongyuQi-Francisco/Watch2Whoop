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

class Limiter:
    """A sliding-window event counter per key (IP, e-mail, …): retry_after() says how long to wait once `max_events` events
    happened within `window` seconds. The module-level functions below are the shared login-failure limiter; sign-up uses
    more instances (account/api.py: e-mails sent per address / per IP)."""

    def __init__(self, max_events: int, window: int):
        self.max_events, self.window = max_events, window
        self._lock = threading.Lock()
        self._events: dict[str, deque] = {}
        _ALL.append(self)

    def _prune(self, q: deque, now: float) -> None:
        while q and q[0] <= now - self.window:
            q.popleft()

    def retry_after(self, key: str, now: float | None = None) -> int:
        """0 if this key may go ahead now, otherwise how many seconds it still has to wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._events.get(key)
            if not q:
                return 0
            self._prune(q, now)
            if len(q) < self.max_events:
                return 0
            return max(1, int(q[0] + self.window - now) + 1)

    def record(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._events.get(key)
            if q is None:
                if len(self._events) >= MAX_TRACKED_IPS:
                    self._events.pop(next(iter(self._events)))
                q = self._events[key] = deque()
            self._prune(q, now)
            q.append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


_ALL: list[Limiter] = []


class _LoginFailures(Limiter):
    """The shared login-failure limiter: reads MAX_FAILURES / WINDOW_SECONDS at call time (tests patch them)."""
    max_events = property(lambda self: MAX_FAILURES, lambda self, v: None)
    window = property(lambda self: WINDOW_SECONDS, lambda self, v: None)


_failures = _LoginFailures(MAX_FAILURES, WINDOW_SECONDS)


def retry_after(ip: str, now: float | None = None) -> int:
    """Whether this IP may try now: returns 0 if yes, otherwise how many seconds it still has to wait."""
    return _failures.retry_after(ip, now)


def record_failure(ip: str, now: float | None = None) -> None:
    _failures.record(ip, now)


def record_success(ip: str) -> None:
    _failures.clear(ip)


def reset() -> None:
    """For tests: clears every limiter."""
    for limiter in _ALL:
        limiter.reset()
