"""Observability of the API process (obs.py): X-Body-User on responses, the daily file log, and the import cost of the API
(pypinyin is loaded only when voice input is used). No database needed."""
import logging
import os
import subprocess
import sys
import time

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from body_engine import obs


def _app():
    app = FastAPI()
    app.add_middleware(obs.UserHeaderMiddleware)

    def who(n: int = 0):   # a sync dependency: FastAPI runs it in a worker thread, like the real auth
        obs.note_user(n or None)
        return n

    @app.get("/x")
    def x(n=Depends(who)):
        return {"n": n}

    @app.get("/boom")
    def boom(n=Depends(who)):
        raise RuntimeError("boom")
    return app


def test_user_header_from_a_worker_thread():
    c = TestClient(_app())
    assert c.get("/x?n=7").headers["x-body-user"] == "7"
    assert "x-body-user" not in c.get("/x").headers   # not signed in: no header
    assert c.get("/x?n=8").headers["x-body-user"] == "8"   # nothing leaks from one request to the next


def test_user_header_on_unhandled_errors():
    c = TestClient(_app(), raise_server_exceptions=False)
    r = c.get("/boom?n=9")
    assert r.status_code == 500 and r.headers["x-body-user"] == "9"


def test_note_user_outside_a_request_is_a_no_op():
    obs.note_user(5)


def test_daily_file_log_writes_and_prunes(tmp_path):
    old = tmp_path / "engine-2020-01-01.log"
    old.write_text("x\n")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    other = tmp_path / "hourly-2020-01-01.log"
    other.write_text("y\n")
    os.utime(other, (time.time() - 40 * 86400,) * 2)
    h = obs.DailyFileHandler(tmp_path, keep_days=14)
    h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    lg = logging.getLogger("body_engine.test_obs")
    lg.addHandler(h)
    try:
        lg.warning("something failed")
    finally:
        lg.removeHandler(h)
        h.close()
    today = tmp_path / f"engine-{time.strftime('%Y-%m-%d')}.log"
    assert "WARNING something failed" in today.read_text()
    assert not old.exists() and other.exists()   # only its own files are pruned


def test_api_import_does_not_load_pypinyin():
    """pypinyin's dictionaries are ~50 MB; the API process loads them only when voice input is used (ai/voice_fix.py)."""
    code = "import sys, body_engine.api; print('pypinyin' in sys.modules, 'jellyfish' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ}, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "False False"
