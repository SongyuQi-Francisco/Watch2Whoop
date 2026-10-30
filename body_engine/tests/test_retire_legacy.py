"""2.0.0 R1 (docs/ios-native-2.0.md §4 R1, rawdata/retire.py): retiring Open Wearables and the iOS Shortcut.
- the cutoff switch BODY_LEGACY_SOURCES_CUTOFF: unset / before = nothing changes; after = the Shortcut receiver
  (health_sync/health_server.py) answers 410 and stores nothing, hourly-batch skips import-ow, the OW poller skips;
- `retire-legacy verify`: per-user ✓ / ⚠ / ✗ from counts and dates only; what lives only in OW is found;
- `--archive`: copies it (nights, workouts, daily values, raw samples, pending Shortcut files), idempotently, nothing
  deleted, then verify is ✓ for a user in the app mode;
- `retire-legacy status`: the switch and who sent legacy data lately (counts only).
Synthetic data of tests/test_apple_sync_derive.py (every number a made-up example value, 示例). Own test database
BODY_TEST_DB_NAME_RETIRE (default body_test_retire_legacy), recreated each run; skipped without Postgres.
"""
import http.client
import importlib.util
import os
import pathlib
import sys
import threading
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

import test_apple_sync_derive as S2
from body_engine import batch
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import ow
from body_engine.rawdata import cutover as CO, engine as EN, retire as RT
from body_engine.rollout import flags
from body_engine.sync import ow_watch

D1, D2 = S2.D1, S2.D2
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_RETIRE", "body_test_retire_legacy")
REPO = pathlib.Path(__file__).resolve().parents[2]
PAST, FUTURE = "2026-01-01", "2999-01-01"


# ---------------------------------------------------------------- the switch (pure)
def test_parse_cutoff():
    assert RT.parse_cutoff("") is None and RT.parse_cutoff(None) is None
    assert RT.parse_cutoff("2026-11-15") == datetime(2026, 11, 15, tzinfo=timezone.utc)
    assert RT.parse_cutoff("2026-11-15T10:00:00+11:00") == datetime(2026, 11, 14, 23, tzinfo=timezone.utc)
    assert RT.parse_cutoff("2026-11-15T10:00:00") == datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        RT.parse_cutoff("soon")


def test_cutoff_state_before_after_invalid():
    now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    assert RT.cutoff_state(now, "") == {"set": False, "cutoff": None, "active": False, "error": None}
    assert RT.cutoff_state(now, "2026-11-16")["active"] is False
    assert RT.cutoff_state(now, "2026-11-15")["active"] is True
    bad = RT.cutoff_state(now, "next week")
    assert bad["active"] is False and bad["error"]


def test_ow_import_stopped_reads_the_environment(monkeypatch):
    monkeypatch.delenv(RT.CUTOFF_ENV, raising=False)
    assert RT.ow_import_stopped() is False
    monkeypatch.setenv(RT.CUTOFF_ENV, FUTURE)
    assert RT.ow_import_stopped() is False
    monkeypatch.setenv(RT.CUTOFF_ENV, PAST)
    assert RT.ow_import_stopped() is True


def test_ow_poller_skips_after_the_cutoff(monkeypatch):
    monkeypatch.setenv(RT.CUTOFF_ENV, PAST)
    assert ow_watch.poll(None, client=object())["skipped"] == "legacy_cutoff"


# ---------------------------------------------------------------- the Shortcut receiver
@pytest.fixture
def receiver(tmp_path, monkeypatch):
    base = tmp_path / "health_data"
    monkeypatch.setenv("HEALTH_SYNC_BASE", str(base))
    monkeypatch.setenv("HEALTH_SYNC_SECRETS", str(tmp_path / "secrets"))
    monkeypatch.setenv("HEALTH_SYNC_IMPORT", "0")
    monkeypatch.delenv("HEALTH_SYNC_REQUIRE_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location("health_server_r1", REPO / "health_sync" / "health_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.makedirs(mod.ARCHIVE, exist_ok=True)
    srv = mod.ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(method, path, body=b""):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request(method, path, body=body, headers={"Content-Length": str(len(body))})
        r = c.getresponse()
        out = (r.status, r.read().decode())
        c.close()
        return out
    yield req, base
    srv.shutdown()
    sys.modules.pop("health_server_r1", None)


def _files(base):
    return sorted(p.name for p in (base / "archive").glob("health_*.txt"))


def test_shortcut_upload_accepted_before_the_cutoff(receiver, monkeypatch):
    req, base = receiver
    monkeypatch.delenv(RT.CUTOFF_ENV, raising=False)
    assert req("POST", "/upload?name=health", b"x")[0] == 200
    monkeypatch.setenv(RT.CUTOFF_ENV, FUTURE)
    assert req("POST", "/upload?name=health", b"y")[0] == 200
    monkeypatch.setenv(RT.CUTOFF_ENV, "not a date")     # invalid: keep accepting, nothing is lost
    assert req("POST", "/upload?name=health", b"z")[0] == 200
    assert len(_files(base)) == 3


def test_shortcut_upload_gone_after_the_cutoff(receiver, monkeypatch):
    req, base = receiver
    monkeypatch.setenv(RT.CUTOFF_ENV, PAST)
    code, text = req("POST", "/upload?name=health", b"x")
    assert code == 410 and text.startswith("快捷指令同步已停用") and "Watch2Whoop" in text
    assert _files(base) == [] and not (base / "health_latest.txt").exists()
    code, text = req("GET", "/legacy-status")
    assert code == 200 and '"active": true' in text
    assert req("GET", "/ping") == (200, "health-sync ok\n")


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if not TEST_DB.startswith("body_test_"):
        pytest.exit("the test DB must be a body_test_* database")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def new_user(conn, name, ow_user=None) -> int:
    return conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version, timezone, birth_date, sex,
                                              height_cm, ow_user_id)
                           VALUES (%s, 'member', '2026-10-01', '2026-10-22.1', 'Australia/Sydney', '1990-01-01', 'male',
                                   180, %s) RETURNING id""", (name, ow_user)).fetchone()["id"]


class CountingOW(S2.FakeOW):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def paginate(self, path, **params):
        self.calls += 1
        return super().paginate(path, **params)

    def get(self, path, **params):
        self.calls += 1
        return super().get(path, **params)


def snapshot(conn, uid):
    return {t: conn.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id = %s", (uid,)).fetchone()["n"]
            for t in ("daily_metric_sources", "sleep_sessions", "workouts", "ow_sample_cache", "ow_archive_segments",
                      "shortcut_samples", "shortcut_uploads")}


def test_verify_archive_and_verify_again(dsn, monkeypatch):
    monkeypatch.delenv(RT.CUTOFF_ENV, raising=False)
    fake = CountingOW()
    with conn_of(dsn) as conn:
        uid = new_user(conn, "示例甲", "ow-a")
        ow.run(conn, fake, uid, D1, D2)
        # simulate what never reached Body Engine: one night, one workout and one day of daily values only in OW
        conn.execute("DELETE FROM sleep_sessions WHERE user_id = %s AND wake_date = %s", (uid, D2))
        gone = conn.execute("""DELETE FROM workouts WHERE id = (SELECT min(id) FROM workouts WHERE user_id = %s
                               AND source = 'ow') RETURNING external_id""", (uid,)).fetchone()["external_id"]
        conn.execute("DELETE FROM daily_metric_sources WHERE user_id = %s AND date = %s AND source = 'ow'", (uid, D1))

        offline = RT.verify(conn, uid, None)
        assert offline["status"] == "✗" and "ow_not_archived" in offline["fails"] and "ow_not_checked" in offline["warns"]
        before = RT.verify(conn, uid, fake)
        assert {"nights_only_in_ow", "workouts_only_in_ow"} <= set(before["fails"])
        assert before["only_in_ow"]["nights"]["n"] >= 1 and before["only_in_ow"]["workouts"]["n"] == 1
        assert before["only_in_ow"]["nights"]["first"] == D2

        res = RT.archive(conn, uid, fake)
        assert res["nights_added"] >= 1 and res["workouts_added"] == 1 and res["daily_rows_added"] > 0
        assert conn.execute("SELECT 1 FROM workouts WHERE user_id = %s AND source = 'ow' AND external_id = %s",
                            (uid, gone)).fetchone()
        assert conn.execute("SELECT 1 FROM sleep_sessions WHERE user_id = %s AND wake_date = %s AND source = 'ow'",
                            (uid, D2)).fetchone()
        once = snapshot(conn, uid)
        again = RT.archive(conn, uid, fake)                   # idempotent, nothing deleted
        assert again["nights_added"] == again["workouts_added"] == again["daily_rows_added"] == 0
        assert snapshot(conn, uid) == once

        after = RT.verify(conn, uid, fake)
        assert after["fails"] == [] and after["status"] == "⚠" and "not_app_mode" in after["warns"]
        assert after["ow_days_raw_only"]["n"] == 0 and after["ow_archive"]["complete"] is True

        # in the app mode (the only mode after 2.0.0) the user is green
        flags.set_flag(conn, EN.FLAG, "cohort", cohort="r1", actor="test")
        flags.cohort_add(conn, uid, "r1", actor="test")
        flags.set_flag(conn, EN.PRIMARY_FLAG, "cohort", cohort=CO.PRIMARY_COHORT, actor="test")
        flags.cohort_add(conn, uid, CO.PRIMARY_COHORT, actor="test")
        CO._set_state(conn, uid, mode="app", pin=None)
        green = RT.verify(conn, uid, fake)
        assert green["status"] == "✓", green

        # OW received samples after the archive: stale until archived again
        conn.execute("UPDATE sync_state SET cursor = %s WHERE user_id = %s AND source = 'ow'",
                     ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), uid))
        assert "ow_archive_stale" in RT.verify(conn, uid, fake)["fails"]


def test_archive_without_resting_heart_rate(dsn, monkeypatch):
    """OW data but no resting heart rate (no scoring start): the archive starts at the first OW day instead of skipping."""
    monkeypatch.delenv(RT.CUTOFF_ENV, raising=False)
    fake = CountingOW()
    with conn_of(dsn) as conn:
        uid = new_user(conn, "示例丁", "ow-d")
        ow.run(conn, fake, uid, D1, D2)
        conn.execute("DELETE FROM daily_metrics WHERE user_id = %s AND metric = 'resting_heart_rate'", (uid,))
        assert "ow_not_archived" in RT.verify(conn, uid, fake)["fails"]
        res = RT.archive(conn, uid, fake)
        assert "skipped" not in res["ow_archive"] and res["ow_archive"]["windows"] >= 1
        after = RT.verify(conn, uid, fake)
        assert after["fails"] == [] and after["ow_archive"]["complete"] is True


def test_shortcut_files_and_users_without_legacy_data(dsn, tmp_path, monkeypatch):
    with conn_of(dsn) as conn:
        sc_user, empty = new_user(conn, "示例乙"), new_user(conn, "示例丙")
        t = lambda hh: datetime(2026, 10, 16, hh, tzinfo=S2.TZ).isoformat()   # noqa: E731
        (tmp_path / "health_2026-10-16_100000.txt").write_text("\n".join(
            [f"Heart Rate|{t(9)}|{t(9)}|61|count/min|Example Watch", f"Steps|{t(9)}|{t(10)}|500|count|Example Watch"]))
        v = RT.verify(conn, sc_user, None, archive_dir=tmp_path)
        assert v["status"] == "⚠" and v["warns"] == ["shortcut_files_pending"] and v["shortcut_pending_files"] == 1
        assert RT.archive(conn, sc_user, None, archive_dir=tmp_path)["shortcut_files"] == 1
        v = RT.verify(conn, sc_user, None, archive_dir=tmp_path)
        assert v["status"] == "✓" and v["sources"]["shortcut"]["raw"]["n"] == 2
        assert RT.archive(conn, sc_user, None, archive_dir=tmp_path) == {"user_id": sc_user}   # nothing left to do

        e = RT.verify(conn, empty, None, archive_dir=tmp_path / "none")
        assert e["status"] == "—"
        assert sc_user in RT.users(conn) and empty not in RT.users(conn)

        text = RT.markdown([v, e])
        assert "示例" not in text and "Example" not in text           # ids, counts and dates only
        assert f"| {sc_user} | ✓ |" in text and "合计 2 人" in text

        # status: counts only; the Shortcut user imported just now
        monkeypatch.setenv(RT.CUTOFF_ENV, PAST)
        st = RT.status(conn)
        assert st["cutoff"]["active"] is True and st["shortcut_users_recent"] >= 1
        assert set(st) == {"cutoff", "recent_days", "ow_users_recent", "shortcut_users_recent", "ow_linked"}


def test_hourly_import_ow_before_and_after_the_cutoff(dsn, monkeypatch):
    with conn_of(dsn) as conn:
        uid = new_user(conn, "示例丁", "ow-d")
        u = batch.list_users(conn, [uid])[0]
    fake = CountingOW()
    monkeypatch.delenv(RT.CUTOFF_ENV, raising=False)
    res = batch.process_user(u, ("import-ow",), False, False, 6.0, dsn=dsn, client=fake)
    assert any(line.startswith(f"✓ import-ow user {uid}") for line in res["lines"]) and fake.calls > 0
    with conn_of(dsn) as conn:
        kept = snapshot(conn, uid)
    assert kept["daily_metric_sources"] > 0

    fake = CountingOW()
    monkeypatch.setenv(RT.CUTOFF_ENV, PAST)
    res = batch.process_user(u, ("import-ow",), False, False, 6.0, dsn=dsn, client=fake)
    assert res["errors"] == [] and fake.calls == 0
    assert any("跳过 import-ow" in line for line in res["lines"])
    with conn_of(dsn) as conn:
        assert snapshot(conn, uid) == kept                     # imported data untouched


# ---------------------------------------------------------------- doctor (deploy/ops/ops.py)
def test_doctor_row():
    spec = importlib.util.spec_from_file_location("ops_r1", REPO / "deploy" / "ops" / "ops.py")
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    base = {"recent_days": 7, "ow_users_recent": 2, "shortcut_users_recent": 1}
    off = {"set": False, "cutoff": None, "active": False, "error": None}
    st, detail = ops.legacy_sources_row({**base, "cutoff": off})
    assert st == "✓" and "未设停用时间" in detail and "OW 数据 2 人" in detail and "快捷指令上传 1 人" in detail
    soon = {"set": True, "cutoff": "2999-01-01T00:00:00+00:00", "active": False, "error": None}
    assert ops.legacy_sources_row({**base, "cutoff": soon})[0] == "✓"
    on = {**soon, "active": True}
    assert ops.legacy_sources_row({**base, "cutoff": on})[0] == "⚠"
    assert ops.legacy_sources_row({**base, "ow_users_recent": 0, "shortcut_users_recent": 0, "cutoff": on})[0] == "✓"
    assert ops.legacy_sources_row({**base, "cutoff": {**off, "set": True, "error": "x"}})[0] == "⚠"
