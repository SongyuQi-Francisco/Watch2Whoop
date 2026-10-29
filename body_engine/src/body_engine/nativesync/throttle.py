"""Server pressure of the phones' history downloads (stream raw_history, history.py; params.HISTORY_*).

A history page loads the merged scoring inputs of up to two weeks (about one compute-scores of that range), and every
phone wants ~80 of them after an install or a reinstall, mostly at night while charging. Two limits, both per API
process (each uvicorn worker keeps its own; the deployment runs one or two):
- per user: one page every HISTORY_MIN_INTERVAL_S;
- per process: at most HISTORY_CONCURRENCY pages computed at the same time.
Over a limit the request is answered 429 with Retry-After (seconds) right away and nothing is computed; the phone waits
that long and asks again, or stops and resumes in its next background task. Ordinary pulls never pass through here.
"""
from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager

from . import params as P

_lock = threading.Lock()
_last: dict[int, float] = {}
_sem = threading.BoundedSemaphore(P.HISTORY_CONCURRENCY)


class SlowDown(Exception):
    def __init__(self, retry_after: int):
        super().__init__(retry_after)
        self.retry_after = retry_after


@contextmanager
def history_page(uid: int, now: float | None = None):
    """Raises SlowDown(seconds) instead of running when this user asked too recently or the process is busy."""
    t = time.monotonic() if now is None else now
    with _lock:
        wait = _last.get(uid, -math.inf) + P.HISTORY_MIN_INTERVAL_S - t
        if wait > 0:
            raise SlowDown(max(1, math.ceil(wait)))
        if not _sem.acquire(blocking=False):
            raise SlowDown(P.HISTORY_BUSY_RETRY_S)
        _last[uid] = t
        if len(_last) > 10000:   # forget users long done
            for k in [k for k, v in _last.items() if v < t - 3600]:
                del _last[k]
    try:
        yield
    finally:
        _sem.release()


def reset() -> None:
    """Tests."""
    with _lock:
        _last.clear()
