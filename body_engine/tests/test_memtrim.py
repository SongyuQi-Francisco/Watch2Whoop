"""The API's periodic malloc_trim (memtrim.py, docs/api-memory.md). No database."""
import asyncio
import platform
import sys

from body_engine import memtrim


def test_trims_only_after_growth():
    calls, sizes = [], iter([300, 200, 210, 230, 190])
    t = memtrim.Trimmer(trim=calls.append, rss=lambda: next(sizes), growth_mb=16)
    assert t.tick() == 100 and calls == [0]          # the first tick always trims: 300 -> 200
    assert t.tick() is None and calls == [0]         # 210: grew less than 16 MB since, nothing to do
    assert t.tick() == 40 and calls == [0, 0]        # 230 -> 190


def test_off_and_unavailable_are_no_ops(monkeypatch):
    async def run():
        async with memtrim.trimmer():
            await asyncio.sleep(0)
    monkeypatch.setenv("BODY_MALLOC_TRIM_S", "0")
    asyncio.run(run())
    monkeypatch.setenv("BODY_MALLOC_TRIM_S", "60")
    monkeypatch.setattr(memtrim, "_libc", lambda: None)
    asyncio.run(run())
    monkeypatch.setenv("BODY_MALLOC_TRIM_S", "junk")
    assert memtrim.interval_s() == 60.0


def test_real_trim_on_linux_glibc():
    fn = memtrim._libc()
    if not (sys.platform.startswith("linux") and platform.libc_ver()[0] == "glibc"):
        assert fn is None or sys.platform.startswith("linux")
        return
    assert fn is not None
    t = memtrim.Trimmer(fn)
    junk = [bytearray(4096) for _ in range(20000)]   # ~80 MB in small blocks, then freed
    del junk
    assert t.tick() is not None and memtrim.rss_mb() > 0
