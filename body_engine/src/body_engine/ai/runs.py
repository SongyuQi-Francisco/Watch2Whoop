"""In-page chat turns that run to the end without their connection (issue #114).

One turn = the events of one user message or one write confirmation (chat.start_chat / chat.confirm_events). A background
thread iterates the turn's generator to the end and appends every event, numbered from 1, to the turn's buffer; the HTTP
streams only read that buffer. When the page goes to the background and iOS drops the connection, the turn still finishes:
messages, tool calls, usage and pending writes are stored in the database by chat.py as before, and the page picks up the
remaining events with GET /v1/assistant/chat/{conversation_id}/events?after=N.

Assumption: one server process (uvicorn without workers, deploy/). The buffers live in this process's memory, keyed by
conversation; the latest turn of each conversation is kept until KEEP_SECONDS after it ended (run_params.py). After a
restart, or once a buffer expired, the resume stream sends a single `resync` event and the page reloads the conversation
from the database (GET /v1/assistant/conversations/{id}), which holds every completed message and pending write.
"""
import asyncio
import contextvars
import json
import logging
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator

from ..i18n import L
from . import run_params as rp
from .chat import ChatError

log = logging.getLogger(__name__)


class Run:
    """The events of one turn, appended by its worker thread, read by any number of streams."""

    def __init__(self, user_id: int, conv_id: int):
        self.user_id, self.conv_id = user_id, conv_id
        self.events: list[dict] = []          # {"seq": n, "event": ..., "data": ...}; seq = index + 1
        self.done = False
        self.ended_at: float | None = None
        self._lock = threading.Lock()

    def add(self, e: dict) -> None:
        with self._lock:
            self.events.append({"seq": len(self.events) + 1, "event": e["event"], "data": e["data"]})

    def finish(self) -> None:
        with self._lock:
            self.done, self.ended_at = True, time.monotonic()

    def after(self, seq: int) -> tuple[list[dict], bool]:
        """Events numbered above seq, and whether the turn had ended when they were read (no more will come then)."""
        with self._lock:
            return self.events[max(seq, 0):], self.done


_runs: dict[int, Run] = {}                    # conversation id -> its latest turn
_lock = threading.Lock()


def _prune(now: float) -> None:
    ended = sorted((r for r in _runs.values() if r.done), key=lambda r: r.ended_at)
    for i, r in enumerate(ended):
        if now - r.ended_at > rp.KEEP_SECONDS or len(ended) - i > rp.MAX_KEPT_RUNS:
            _runs.pop(r.conv_id, None)


def get(user_id: int, conv_id: int) -> Run | None:
    """This user's latest turn of this conversation while it is kept; None for anyone else's."""
    with _lock:
        _prune(time.monotonic())
        r = _runs.get(conv_id)
    return r if r is not None and r.user_id == user_id else None


def start(user_id: int, conv_id: int, gen: Iterator[dict], cm) -> Run:
    """Run gen to the end in a background thread. cm is the pool connection context gen uses: it is owned by the thread
    from here on (committed and returned when the turn ends). Raises ChatError (and owns nothing) when the conversation
    is still answering; the caller then rolls back and returns cm."""
    run = Run(user_id, conv_id)
    with _lock:
        _prune(time.monotonic())
        cur = _runs.get(conv_id)
        if cur is not None and not cur.done:
            raise ChatError("busy", L("上一条还在回答，等它说完再发", "Still answering the last message; wait for it to finish"))
        _runs[conv_id] = run
    ctx = contextvars.copy_context()      # the request's language (X-Body-Lang) for the texts the turn produces
    threading.Thread(target=ctx.run, args=(_work, run, gen, cm), name=f"chat-run-{conv_id}", daemon=True).start()
    return run


def _work(run: Run, gen: Iterator[dict], cm) -> None:
    deadline = time.monotonic() + rp.MAX_RUN_SECONDS
    try:
        for e in gen:
            run.add(e)
            if time.monotonic() > deadline and e["event"] not in ("done", "confirm"):
                gen.close()
                run.add({"event": "error", "data": {"code": "too_slow", "message": L(
                    f"这一轮超过 {rp.MAX_RUN_SECONDS // 60} 分钟还没说完，先停一下；可以接着问",
                    f"This answer took over {rp.MAX_RUN_SECONDS // 60} minutes, so it stopped here; you can ask it to go on")}})
                break
    except Exception as e:  # Do not send internal details (may contain paths etc.) to the frontend
        log.exception("chat run failed")
        print(f"chat run failed: {type(e).__name__}: {e}", file=sys.stderr)
        run.add({"event": "error", "data": {"code": "internal", "message": L("出错了，稍后再试", "Something went wrong; try again later")}})
    finally:
        try:
            cm.__exit__(None, None, None)      # commits the completed part, returns the connection to the pool
        except Exception:
            log.exception("chat run: returning the connection failed")
        run.finish()


def _frame(e: dict) -> str:
    return f"id: {e['seq']}\nevent: {e['event']}\ndata: {json.dumps(e['data'], ensure_ascii=False, default=str)}\n\n"


async def sse(run: Run, after: int = 0) -> AsyncIterator[str]:
    """The turn's events above `after` as SSE (each with its `id:`), until the turn ended and everything was sent.
    Stopping this (the client went away) does not stop the turn."""
    seq, quiet = after, time.monotonic()
    while True:
        evs, done = run.after(seq)
        for e in evs:
            yield _frame(e)
            seq = e["seq"]
        if done:
            return
        now = time.monotonic()
        if evs:
            quiet = now
        elif now - quiet > rp.HEARTBEAT_SECONDS:
            yield ": keep-alive\n\n"
            quiet = now
        await asyncio.sleep(rp.POLL_SECONDS)


async def resync(conv_id: int) -> AsyncIterator[str]:
    """The turn is no longer in memory (expired, or the server restarted): the page reloads the conversation."""
    yield f"event: resync\ndata: {json.dumps({'conversation_id': conv_id})}\n\n"
