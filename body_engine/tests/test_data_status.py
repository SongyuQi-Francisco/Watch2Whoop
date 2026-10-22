"""Data status (issue #163): per health data type what the server received, ok / stale / never, own data only.

Uses a separate test database BODY_TEST_DB_NAME (default body_test_data_status), dropped and recreated on every run;
skipped if it cannot connect.
"""
import json
import os
import shutil
import subprocess
import zlib
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.sync import data_status, params

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_data_status")
TZ = ZoneInfo("Australia/Sydney")


def test_classify():
    today = date(2026, 10, 22)
    assert data_status.classify(None, today, 3) == "never"
    assert data_status.classify(None, today, None) == "never"
    assert data_status.classify(today, today, 2) == "ok"
    assert data_status.classify(today - timedelta(days=2), today, 2) == "ok"      # the boundary still counts
    assert data_status.classify(today - timedelta(days=3), today, 2) == "stale"
    assert data_status.classify(today - timedelta(days=900), today, None) == "ok"   # ECG: never stale
    assert data_status.classify(today + timedelta(days=1), today, 2) == "ok"      # a device clock ahead


def test_every_kind_has_a_threshold():
    assert [k.key for k in data_status.KINDS] == list(params.STALE_AFTER_DAYS)
    assert len(data_status.KINDS) == 15


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
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


def _at(d: date, hh: int = 8) -> datetime:
    return datetime.combine(d, time(hh), TZ)


@pytest.fixture(scope="module")
def users(dsn):
    today = datetime.now(TZ).date()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version, timezone)
                                   VALUES (%s, 'member', now(), 'v', 'Australia/Sydney') RETURNING id""", (name,)).fetchone()["id"]
        a, b = add("a"), add("b")
        # a: heart rate today and yesterday (ok, 2 days), weight 40 days ago (stale), steps from two sources
        for d, n in ((today, 300), (today - timedelta(days=1), 280)):
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                            VALUES (%s, %s, 'heart_rate', 62, 'count/min', 'avg', %s, 'ow')""", (a, d, n))
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                        VALUES (%s, %s, 'body_mass', 80, 'kg', 'avg', 1, 'ow')""", (a, today - timedelta(days=40)))
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                        VALUES (%s, %s, 'step_count', 9000, 'count', 'sum', NULL, 'ow')""", (a, today))
        for src in ("ow", "shortcut"):
            conn.execute("""INSERT INTO daily_metric_sources (user_id, date, metric, source, value, unit, agg)
                            VALUES (%s, %s, 'step_count', %s, 9000, 'count', 'sum')""", (a, today, src))
        # sleep 5 days ago (stale after 3), one workout 3 days ago (ok within 14) and a duplicate of it (not counted)
        w = today - timedelta(days=5)
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, source)
                        VALUES (%s, %s, %s, %s, 420, 'ow')""", (a, w, _at(w, 0), _at(w, 7)))
        d3 = today - timedelta(days=3)
        wid = conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
                              VALUES (%s, 'ow', 'w1', 'walking', '步行', %s, %s, %s, 30) RETURNING id""",
                           (a, d3, _at(d3, 7), _at(d3, 8))).fetchone()["id"]
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min, dup_of)
                        VALUES (%s, 'shortcut', 'w1s', 'walking', '步行', %s, %s, %s, 30, %s)""",
                     (a, d3, _at(d3, 7), _at(d3, 8), wid))
        # an ECG from long ago (never stale) and two drinks from Apple Health today (a button drink does not count)
        old = today - timedelta(days=200)
        conn.execute("""INSERT INTO ecg_recordings (user_id, start_at, origin, sample_hz, n_samples, voltage, classification)
                        VALUES (%s, %s, 'app', 512, 1, %s, 'sinusRhythm')""", (a, _at(old), zlib.compress(b"\x00\x00")))
        for i, src in enumerate(("apple_health", "apple_health", "button")):
            conn.execute("""INSERT INTO water_intake (user_id, at, local_date, ml, source) VALUES (%s, %s, %s, 250, %s)""",
                         (a, _at(today, 7 + i), today, src))
        tok = {"a": auth.create_token(conn, a, "t", ["read", "write"]), "b": auth.create_token(conn, b, "t", ["read", "write"]),
               "a_ro": auth.create_token(conn, a, "ro", ["read"]),
               "a_ingest": auth.create_token(conn, a, "app:ingest:x", [auth.INGEST_SCOPE])}
        conn.commit()
    return {"a": a, "b": b}, tok, today


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(t, **extra):
    return {"Authorization": f"Bearer {t}", **extra}


def by_key(res):
    return {t["key"]: t for t in res["types"]}


def test_three_statuses(dsn, users):
    ids, _, today = users
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        res = data_status.collect(conn, ids["a"], today)
    t = by_key(res)
    assert list(t) == list(params.STALE_AFTER_DAYS)
    assert t["heart_rate"]["status"] == "ok" and t["heart_rate"]["days"] == 2 and t["heart_rate"]["count"] == 580
    assert t["heart_rate"]["last_date"] == today.isoformat() and t["heart_rate"]["days_since"] == 0
    assert t["weight"]["status"] == "stale" and t["weight"]["days"] == 0 and t["weight"]["days_since"] == 40
    assert t["steps"]["status"] == "ok" and t["steps"]["count"] == 1 and t["steps"]["sources"] == ["ow", "shortcut"]
    assert t["sleep"]["status"] == "stale" and t["sleep"]["last_at"].startswith((today - timedelta(days=5)).isoformat())
    assert t["workouts"]["status"] == "ok" and t["workouts"]["count"] == 1 and t["workouts"]["sources"] == ["ow"]
    assert t["ecg"]["status"] == "ok" and t["ecg"]["days_since"] == 200 and t["ecg"]["stale_after_days"] is None
    assert t["water"]["status"] == "ok" and t["water"]["count"] == 2 and t["water"]["sources"] == ["apple_health"]
    for k in ("hrv", "resting_heart_rate", "active_energy", "blood_oxygen", "respiratory_rate", "wrist_temperature", "vo2max", "heartbeats"):
        assert t[k]["status"] == "never" and t[k]["last_date"] is None and t[k]["count"] == 0, k
    assert "flag_off" in t["water"]["notes"]   # the water flag is off on this server (default)
    assert "sdk_unsupported" in t["wrist_temperature"]["notes"] and t["heartbeats"]["channel"] == "app"
    assert res["summary"] == {"ok": 5, "stale": 2, "never": 8}
    # a week later the heart rate is stale too (the ECG still is not)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        later = by_key(data_status.collect(conn, ids["a"], today + timedelta(days=7)))
    assert later["heart_rate"]["status"] == "stale" and later["heart_rate"]["days"] == 0
    assert later["ecg"]["status"] == "ok"


def test_own_data_only(client, users):
    ids, tok, _ = users
    assert client.get("/v1/users/me/data-status").status_code == 401
    assert client.get(f"/v1/users/{ids['a']}/data-status", headers=H(tok["b"])).status_code == 403
    mine = client.get("/v1/users/me/data-status", headers=H(tok["b"])).json()
    assert mine["summary"] == {"ok": 0, "stale": 0, "never": 15}
    a = client.get(f"/v1/users/{ids['a']}/data-status", headers=H(tok["a_ro"])).json()   # read scope is enough
    assert by_key(a)["heart_rate"]["status"] == "ok"
    # even with a share, someone else's data status stays private
    with psycopg.connect(api.pool.conninfo, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["a"], ids["b"], ["metrics", "sleep", "scores", "training"]))
        conn.commit()
    assert client.get(f"/v1/users/{ids['a']}/data-status", headers=H(tok["b"])).status_code == 403


def test_ingest_path_for_the_app(client, users):
    ids, tok, _ = users
    assert client.get("/v1/ingest/data-status").status_code == 401
    # the upload-only token works here (and only for its own user), not on /v1/users/...
    r = client.get("/v1/ingest/data-status", headers=H(tok["a_ingest"]))
    assert r.status_code == 200 and by_key(r.json())["heart_rate"]["status"] == "ok"
    assert client.get("/v1/users/me/data-status", headers=H(tok["a_ingest"])).status_code == 401
    assert client.get("/v1/ingest/data-status", headers=H(tok["b"])).json()["summary"]["never"] == 15


def test_labels_follow_the_language(client, users):
    _, tok, _ = users
    zh = by_key(client.get("/v1/users/me/data-status", headers=H(tok["a"])).json())
    en = by_key(client.get("/v1/users/me/data-status", headers=H(tok["a"], **{"X-Body-Lang": "en"})).json())
    assert zh["heart_rate"]["label"] == "心率" and en["heart_rate"]["label"] == "Heart rate"
    assert en["workouts"]["label"] == "Workouts"


def test_flag_declared():
    from body_engine.rollout.registry import FLAGS
    assert "data_status" in FLAGS


# ---------------------------------------------------------------- page script (data-status.js, run in node)
STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"


def node_json(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = ("globalThis.window = { I18N: { t: (k, p) => k.replace(/\\{(\\w+)\\}/g, (_, n) => (p || {})[n]) } };\n"
             f"const P = require({json.dumps(str(STATIC / 'data-status.js'))});\n"
             f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)


def test_page_reasons_and_steps():
    out = node_json("""(() => {
      const t = (key, status, notes = []) => ({ key, label: key, status, notes });
      return {
        ok: [P.reasonsFor(t("heart_rate", "ok")), P.stepsFor(t("heart_rate", "ok"))],
        never: [P.reasonsFor(t("hrv", "never")), P.stepsFor(t("hrv", "never"))],
        stale: P.reasonsFor(t("sleep", "stale")),
        ecg: P.reasonsFor(t("ecg", "never", ["app_direct", "on_demand", "consent"])),
        temp: [P.reasonsFor(t("wrist_temperature", "never", ["sdk_unsupported"])), P.stepsFor(t("wrist_temperature", "never", ["sdk_unsupported"]))],
        water: P.stepsFor(t("water", "never", ["app_direct", "flag_off"])),
        names: Object.keys(P.IOS_NAME),
      };
    })()""")
    assert out["ok"] == [[], []]                                   # nothing to explain when the data arrives
    reasons, steps = out["never"]
    assert "健康权限里没有打开这一项" in reasons and len(steps) == 5
    assert steps[3] == "打开「心率变异性」，或者全部打开"            # the switch's name as iOS shows it
    assert "健康权限被关掉了" in out["stale"]
    assert "只有在手表上测过心电图才会有" in out["ecg"] and "要先在今天页确认新版同意书" in out["ecg"]
    assert out["temp"][0] == ["app 的同步还读不了这一项，要用快捷指令或导入健康历史"] and out["temp"][1] == []   # a permission would not help
    assert out["water"] == []
    assert out["names"] == list(params.STALE_AFTER_DAYS)


def test_page_strength_records():
    """The strength-training records row (#210): days and sessions per source, SparkyFitness' state when not counted."""
    out = node_json("""[P.strengthText({ weeks: 4, days: 3, sessions: { watch: 1, sparky: null, hevy: 2 }, sparky: "not_linked" }),
      P.strengthText({ weeks: 4, days: 2, sessions: { watch: 0, sparky: 4, hevy: 0 }, sparky: "connected" }), P.strengthText(null)]""")
    assert out[0] == {"days": "3 天有记录", "sources": "手表 1 · SparkyFitness 未连接 · Hevy 2"}
    assert out[1]["sources"] == "手表 0 · SparkyFitness 4 · Hevy 0" and out[2] is None


def test_page_wiring():
    js = (STATIC / "data-status.js").read_text(encoding="utf-8")
    assert '"/v1/users/me/data-status"' in js and 'BodyFlags?.on("data_status")' in js
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="data-status.js"') < html.index('src="app.js"')
    assert '"data-status.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
