"""Scoring engine end to end: create tables -> synthesize a person's data -> compute -> idempotent DB write -> API / assistant queries and authorization.
Uses a separate test database body_test_scores (configurable via BODY_TEST_DB_NAME), reset on every run; skipped if it cannot connect."""
import dataclasses
import os
from datetime import date, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import auth
from body_engine.config import settings
from body_engine.db import migrate
from body_engine.scores import engine, store
from test_scores import make_inputs

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_scores")
TEST_SETTINGS = dataclasses.replace(settings, db_name=TEST_DB)


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(TEST_SETTINGS.dsn, row_factory=dict_row, autocommit=True)
    except (psycopg.OperationalError, RuntimeError) as e:
        pytest.skip(f"test DB {TEST_DB} unreachable: {e}")
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    migrate(c)
    yield c
    c.close()


class FakeOW:
    """Implements only the two endpoints the scoring engine uses: /timeseries (raw samples) and /events/sleep (stages)."""

    def __init__(self, inp):
        self.raw = []
        for key, code in (("hr", "heart_rate"), ("hrv", "heart_rate_variability_sdnn"), ("resp", "respiratory_rate")):
            for ts, v in getattr(inp, key):
                self.raw.append({"timestamp": ts.isoformat(), "type": code, "value": v, "unit": "bpm",
                                 "source": {"source": "healthkit", "device_type": "watch", "device": "Watch7,9"}})
        self.sleep = [{"source": {"device_name": "Watch"}, "sleep_stage_intervals": [
            {"stage": {"core": "light"}.get(st, st), "start_time": a.isoformat(), "end_time": b.isoformat()}
            for a, b, st in inp.segments]}]

    def paginate(self, path, **params):
        if path.endswith("/timeseries"):
            lo, hi = datetime.fromisoformat(params["start_time"]), datetime.fromisoformat(params["end_time"])
            yield from (r for r in self.raw if r["type"] in params["types"]
                        and lo <= datetime.fromisoformat(r["timestamp"]) < hi)
        elif path.endswith("/events/sleep"):
            yield from self.sleep


def seed(conn, inp) -> int:
    uid = conn.execute("""INSERT INTO users (display_name, role, birth_date, timezone, ow_user_id)
                          VALUES ('测试', 'owner', '2001-06-15', 'Australia/Sydney', 'ow-1') RETURNING id""").fetchone()["id"]
    for n in inp.nights:
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min,
                          rem_min, awake_min, is_main, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,'ow')""",
                     (uid, n.wake_date, n.start, n.end, n.asleep_min, n.asleep_min - n.deep_min - n.rem_min,
                      n.deep_min, n.rem_min, n.awake_min))
    for d, v in inp.daily_rhr.items():
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                        VALUES (%s, %s, 'resting_heart_rate', %s, 'count/min', 'avg', 'ow')""", (uid, d, v))
    return uid


def test_compute_write_idempotent_and_query(conn):
    inp = make_inputs(20)
    uid = seed(conn, inp)
    client = FakeOW(inp)
    out = engine.run(conn, client, uid, end=date(2026, 9, 21))
    assert out["origin"] == date(2026, 9, 1) and out["rows"] == 21
    assert out["with_recovery"] == 6 and out["with_strain"] == 20      # full 14 nights from 9-15; no data on 9-21
    before = conn.execute("SELECT date, recovery, strain, ctl FROM daily_scores WHERE user_id = %s ORDER BY date",
                          (uid,)).fetchall()
    engine.run(conn, client, uid, end=date(2026, 9, 21))                # running again gives exactly the same result
    assert conn.execute("SELECT date, recovery, strain, ctl FROM daily_scores WHERE user_id = %s ORDER BY date",
                        (uid,)).fetchall() == before
    # --start / --end only rewrite those days, but CTL etc. are still computed over the whole range from the start, matching a full run
    conn.execute("UPDATE daily_scores SET strain = -1 WHERE user_id = %s", (uid,))
    assert engine.run(conn, client, uid, date(2026, 9, 10), date(2026, 9, 12))["rows"] == 3
    rows = store.scores(conn, uid, date(2026, 9, 1), date(2026, 9, 30))
    assert len(rows) == 21
    assert [r["strain"] for r in rows if r["strain"] == -1] == [-1] * 18
    fixed = {r["date"]: r for r in rows}
    old = {r["date"]: r for r in before}
    assert all(fixed[d]["ctl"] == old[d]["ctl"] and fixed[d]["strain"] == old[d]["strain"]
               for d in (date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)))
    r = fixed[date(2026, 9, 15)]
    assert r["algo_version"] and r["hrv_kind"] == "sdnn" and r["hrv_night_ms"] == pytest.approx(55, abs=0.1)
    assert "details" not in r and "details" in store.scores(conn, uid, date(2026, 9, 15), date(2026, 9, 15), True)[0]
    # Has stage details (FakeOW's /events/sleep), no missing-data reasons, only the CTL warm-up hint remains
    assert r["flags"] == {"ctl_warmup": "评分起点后的前 42 天，CTL 还没稳定"}


def test_api_and_assistant_respect_shares(conn, monkeypatch):
    from fastapi.testclient import TestClient

    from body_engine import api
    from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool

    inp = make_inputs(16)
    uid = seed(conn, inp)
    engine.run(conn, FakeOW(inp), uid, end=date(2026, 9, 16))
    other = conn.execute("INSERT INTO users (display_name) VALUES ('家人') RETURNING id").fetchone()["id"]
    t_owner = auth.create_token(conn, uid, "test", ["read"])
    t_other = auth.create_token(conn, other, "test", ["read"])

    monkeypatch.setattr(api, "settings", TEST_SETTINGS)
    with TestClient(api.app) as http:
        q = "?start=2026-09-01&end=2026-09-16"
        res = http.get(f"/v1/users/me/scores{q}", headers={"Authorization": f"Bearer {t_owner}"})
        assert res.status_code == 200 and len(res.json()) == 16
        assert res.json()[-1]["recovery"] is not None
        assert http.get(f"/v1/users/{uid}/scores{q}", headers={"Authorization": f"Bearer {t_other}"}).status_code == 403
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, '{sleep}')", (uid, other))
        assert http.get(f"/v1/users/{uid}/scores{q}", headers={"Authorization": f"Bearer {t_other}"}).status_code == 403
        conn.execute("UPDATE shares SET scopes = '{sleep,scores}' WHERE owner_id = %s", (uid,))
        res = http.get(f"/v1/users/{uid}/scores{q}&details=true", headers={"Authorization": f"Bearer {t_other}"})
        assert res.status_code == 200 and "sleep" in res.json()[-1]["details"]

    assert TOOLS_BY_NAME["get_scores"].scope == "scores" and not TOOLS_BY_NAME["get_scores"].writes
    args = {"user": str(uid), "start": "2026-09-15", "end": "2026-09-16", "details": False}
    rows = run_tool(conn, other, "mcp", "get_scores", args)
    assert [r["date"] for r in rows] == [date(2026, 9, 15), date(2026, 9, 16)]
    conn.execute("DELETE FROM shares")
    with pytest.raises(PermissionError):
        run_tool(conn, other, "mcp", "get_scores", args)
    audit = conn.execute("SELECT tool, ok FROM assistant_audit ORDER BY id").fetchall()
    assert audit == [{"tool": "get_scores", "ok": True}, {"tool": "get_scores", "ok": False}]


def test_user_without_watch_data(conn):
    uid = conn.execute("INSERT INTO users (display_name) VALUES ('没戴表') RETURNING id").fetchone()["id"]
    assert engine.run(conn, None, uid)["rows"] == 0


def test_shortcut_archive_fills_hrv_and_sleep_stages(conn, tmp_path):
    """For days without OW data, read HRV, respiratory rate and sleep stages from the iOS Shortcut archive; the Shortcut's heart rate cannot be used."""
    inp = make_inputs(16)
    uid = seed(conn, inp)
    night = inp.nights[-1]
    lines = [f"Heart Rate Variability|{(night.start + timedelta(hours=2)).isoformat()}|{(night.start + timedelta(hours=2)).isoformat()}|80|ms|表",
             f"Heart Rate|{night.start.isoformat()}|{night.end.isoformat()}|9999|count/min|表",
             f"Sleep|{night.start.isoformat()}|{(night.start + timedelta(hours=4)).isoformat()}|Core||",
             f"Sleep|{(night.start + timedelta(hours=4)).isoformat()}|{(night.start + timedelta(hours=4, minutes=30)).isoformat()}|Awake||",
             f"Sleep|{(night.start + timedelta(hours=4, minutes=30)).isoformat()}|{night.end.isoformat()}|Deep||"]
    (tmp_path / "health_2026-09-16_120000.txt").write_text("\n".join(lines))
    client = FakeOW(inp)
    client.raw = [r for r in client.raw if datetime.fromisoformat(r["timestamp"]) < night.start - timedelta(hours=1)]
    client.sleep = []
    engine.run(conn, client, uid, end=date(2026, 9, 16), archive_dir=tmp_path)
    r = store.scores(conn, uid, date(2026, 9, 16), date(2026, 9, 16), True)[0]
    assert r["hrv_night_ms"] == pytest.approx(80, abs=0.1)
    assert r["details"]["sleep"]["waso_min"] == 30 and r["details"]["sleep"]["awakenings"] == 1
    assert "rhr_from_daily" in r["flags"] and r["rhr_night"] == 55
    assert r["strain"] is None and "no_raw_hr" in r["flags"]
