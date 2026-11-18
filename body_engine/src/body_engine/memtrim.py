"""Hand memory the C allocator keeps after a big request back to the system (glibc malloc_trim; docs/api-memory.md).

The API answers requests in a thread pool. With glibc, memory freed after a big request mostly stays in the process:
free chunks inside the malloc arenas (MALLOC_ARENA_MAX=2 in the image) are only returned when they sit at the top of an
arena, and long-lived objects allocated in between (the exercise catalog cache, lazily imported modules) pin the rest.
So the resident size only went up, request after request, and the next big one started from there. Measured on the API
image: after a year-long histogram request and two score spot checks with the catalog loaded, 281 MB resident, 212 MB
after one malloc_trim(0). It walks the arenas and gives every free page back (madvise), a few milliseconds.

A background task (lifespan, like sync/ow_watch.py) trims every BODY_MALLOC_TRIM_S seconds (default 60, 0 = off) when
the resident size grew by at least GROWTH_MB since the last trim. Only on Linux with glibc; elsewhere nothing happens.
"""
import asyncio
import ctypes
import ctypes.util
import logging
import os
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)
GROWTH_MB = 16
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def interval_s() -> float:
    try:
        return max(0.0, float(os.environ.get("BODY_MALLOC_TRIM_S", "60")))
    except ValueError:
        return 60.0


def _libc():
    """glibc's malloc_trim, or None (macOS, musl, no libc found)."""
    if not os.path.exists("/proc/self/statm"):
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        fn = libc.malloc_trim
    except (OSError, AttributeError):
        return None
    fn.argtypes, fn.restype = [ctypes.c_size_t], ctypes.c_int
    return fn


def rss_mb() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * _PAGE / 1048576


class Trimmer:
    def __init__(self, trim=None, rss=rss_mb, growth_mb: float = GROWTH_MB):
        self.trim, self.rss, self.growth_mb = trim, rss, growth_mb
        self.after: float | None = None   # resident size right after the last trim

    def tick(self) -> float | None:
        """Trim if the process grew since the last trim. Returns the MB given back, None = skipped."""
        before = self.rss()
        if self.after is not None and before < self.after + self.growth_mb:
            return None
        self.trim(0)
        self.after = self.rss()
        freed = before - self.after
        if freed >= 32:
            log.info("malloc_trim: %.0f MB -> %.0f MB", before, self.after)
        return freed


@asynccontextmanager
async def trimmer():
    """Lifespan: the periodic trim (see the module doc); a no-op where it is off or not available."""
    every, fn = interval_s(), _libc()
    if not every or fn is None:
        yield
        return
    t = Trimmer(fn)

    async def loop():
        while True:
            await asyncio.sleep(every)
            try:
                await asyncio.to_thread(t.tick)
            except Exception as e:  # noqa: BLE001  never take the API down for this
                log.warning("malloc_trim failed: %s", e)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
