"""The empty recovery ring says why (web/recovery_empty.py) and offers one next step.

Four accounts of the live survey, built from obviously fictional synthetic data:
  A  only iPhone steps, no watch data         -> no_watch       (sync)
  B  resting heart rate, no sleep, no HRV     -> no_sleep       (sleep_help: how to switch on sleep tracking)
  C  just signed up, nothing received yet     -> awaiting_sync  (sync)
  D  never agreed to the consent form         -> consent        (consent)
plus the smaller cases (sleep not synced yet, no HRV, baseline, still calculating, a day before the data began).

Uses its own test database (body_test_recovery_empty), dropped and recreated on every run; skipped if Postgres is unreachable.
The page side: app.js has a branch for every action the server can return, data-status.js (run in node) gives the sleep
steps.
"""
import json
import re
import shutil
import subprocess
from datetime import datetime, time, timedelta
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
from body_engine.scores import engine as scores_engine
from body_engine.web import recovery_empty

TEST_DB = "body_test_recovery_empty"
TZ = ZoneInfo("Australia/Sydney")
STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"


@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001 - no password file, or the database is not running
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


def _at(d, hh=7):
    return datetime.combine(d, time(hh), TZ)


@pytest.fixture(scope="module")
def world(dsn):
    today = datetime.now(TZ).date()
    ids, tok = {}, {}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name, consented=True):
            uid = conn.execute(
                """INSERT INTO users (display_name, role, consent_at, consent_version, timezone)
                   VALUES (%s, 'member', CASE WHEN %s THEN now() END, CASE WHEN %s THEN 'v' END, 'Australia/Sydney')
                   RETURNING id""", (name, consented, consented)).fetchone()["id"]
            ids[name], tok[name] = uid, auth.create_token(conn, uid, "t", ["read", "write"])
            return uid

        def metric(uid, d, name, value, agg="avg"):
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, agg, source)
                            VALUES (%s, %s, %s, %s, %s, 'ow')""", (uid, d, name, value, agg))

        a, b, c, d_ = add("A"), add("B"), add("C"), add("D", consented=False)
        for k in range(5):   # A: only steps from the iPhone
            metric(a, today - timedelta(days=k), "step_count", 4000, "sum")
        for k in range(1, 6):   # B: a resting heart rate every day, never a sleep or an HRV reading
            metric(b, today - timedelta(days=k), "resting_heart_rate", 58)
        for k in range(1, 6):   # D: full watch data, but no consent: the server does not process it
            metric(d_, today - timedelta(days=k), "resting_heart_rate", 60)
        conn.commit()
        # B's scores come from the real engine, so the reason is the flag the engine really writes
        res = scores_engine.run(conn, None, b, today - timedelta(days=4), today)
        assert res["rows"] and res["with_recovery"] == 0
        conn.commit()
    return ids, tok, today


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def empty_of(client, tok, who, lang="zh", path="/v1/users/me/today"):
    r = client.get(path, headers={"Authorization": f"Bearer {tok[who]}", "X-Body-Lang": lang})
    assert r.status_code == 200, r.text
    sc = r.json()["scores"]
    assert sc["today"] is None or sc["today"].get("recovery") is None
    return sc["recovery_empty"]


def test_a_only_phone_steps(client, world):
    _, tok, _ = world
    e = empty_of(client, tok, "A")
    assert e == {"code": "no_watch", "label": "还没有手表数据", "cta": "手机同步", "action": "sync"}
    assert empty_of(client, tok, "A", "en")["label"] == "No watch data yet"


def test_b_resting_heart_rate_but_no_sleep(client, world):
    _, tok, _ = world
    e = empty_of(client, tok, "B")
    assert e["code"] == "no_sleep" and e["action"] == "sleep_help"
    assert e["label"] == "没有睡眠数据" and e["cta"] == "开启睡眠追踪"
    en = empty_of(client, tok, "B", "en")
    assert en["label"] == "No sleep data" and en["cta"] == "Turn on sleep tracking"


def test_c_just_signed_up(client, world):
    _, tok, _ = world
    e = empty_of(client, tok, "C")
    assert e == {"code": "awaiting_sync", "label": "等待第一次同步", "cta": "手机同步", "action": "sync"}
    assert empty_of(client, tok, "C", "en")["label"] == "Waiting for the first sync"


def test_d_consent_not_given(client, world):
    _, tok, _ = world
    e = empty_of(client, tok, "D")
    assert e == {"code": "consent", "label": "需要先确认同意书", "cta": "去确认", "action": "consent"}
    assert empty_of(client, tok, "D", "en")["label"] == "Review the consent form first"


def test_a_recovery_sheet_still_has_the_old_reasons(client, world):
    """The long per-field reasons stay for the AI next to the new block."""
    _, tok, _ = world
    r = client.get("/v1/users/me/today", headers={"Authorization": f"Bearer {tok['A']}"}).json()["scores"]
    assert r["today_reasons"]["recovery"] and r["recovery_empty"]["code"] == "no_watch"


def test_no_block_when_there_is_a_recovery(dsn, world):
    ids, _, today = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = {"recovery": 70, "flags": {}}
        assert recovery_empty.explain(conn, ids["B"], today, row, today) is None


def test_smaller_cases(dsn, world):
    """Sleep not synced yet, no HRV, baseline, still calculating, a day before the data began, a past night."""
    ids, _, today = world
    b = ids["B"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def ex(day, row):
            return recovery_empty.explain(conn, b, day, row, today)["code"]

        assert ex(today - timedelta(days=30), None) == "no_day"
        # B has slept (watch stages) two days ago: today's missing night is just not synced yet
        w = today - timedelta(days=2)
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min, rem_min, source)
                        VALUES (%s, %s, %s, %s, 420, 250, 70, 100, 'ow')""", (b, w, _at(w, 0), _at(w, 7)))
        assert ex(today, {"recovery": None, "flags": {"no_sleep": "x"}}) == "sleep_pending"
        assert ex(today - timedelta(days=1), {"recovery": None, "flags": {"no_sleep": "x"}}) == "no_sleep_night"
        assert ex(today, None) == "pending"
        assert ex(today, {"recovery": None, "flags": {}}) == "pending"
        assert ex(today, {"recovery": None, "flags": {"hrv_missing": "x"}}) == "no_hrv"
        assert ex(today, {"recovery": None, "flags": {"baseline_insufficient": "x"}}) == "baseline"
        # a sleep without watch stages (iPhone only) does not count as watch sleep
        conn.rollback()
        i = today - timedelta(days=3)
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, source)
                        VALUES (%s, %s, %s, %s, 400, 'ow')""", (b, i, _at(i, 0), _at(i, 7)))
        assert ex(today, {"recovery": None, "flags": {"no_sleep": "x"}}) == "no_sleep"
        conn.rollback()


def test_every_code_has_a_label_and_action_rules():
    assert len(set(recovery_empty.CODES)) == len(recovery_empty.CODES)
    assert set(recovery_empty.ACTIONS) == {"consent", "sync", "sleep_help"}


# ---------------------------------------------------------------- the page
def test_app_js_handles_every_action():
    """app.js has a branch for each action the server can return, and the empty ring shows the label + next step."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    fn = js[js.index("function recoveryNext("):js.index("function scoresHtml(")]
    assert 'action === "consent"' in fn and "BodyProfile" in fn           # consent -> the consent form (profile.js)
    assert 'action === "sleep_help"' in fn and "openDataStatusType" in fn   # sleep_help -> the sleep data status (data-status.js)
    assert "openDataStatus?.()" in fn                                       # sync outside the app -> the data status list
    assert 'empty.action === "sync" && IN_APP' in fn and "watch2whoop://sync" in fn   # sync in the app -> the sync page
    scores = js[js.index("function scoresHtml("):js.index("// ---- Training + nutrition cards")]
    assert "recovery_empty" in scores and "empty.label" in scores and "recoveryNextHtml(empty)" in scores
    assert "window.BodyProfile = " in (STATIC / "profile.js").read_text(encoding="utf-8")
    ds = (STATIC / "data-status.js").read_text(encoding="utf-8")
    assert "openDataStatusType" in ds and "openDataStatus:" in ds


def test_every_page_text_has_english():
    """The new page texts are keys of i18n-en.js (test_i18n.py checks every tr() the same way)."""
    en = (STATIC / "i18n-en.js").read_text(encoding="utf-8")
    ds = (STATIC / "data-status.js").read_text(encoding="utf-8")
    for key in re.findall(r'tr\("([^"]*(?:睡眠追踪|手表上|「手表」|充好电|Watch2Whoop，睡眠)[^"]*)"\)', ds):
        assert f'"{key}":' in en, key


def node_json(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = ("globalThis.window = { I18N: { t: (k, p) => k.replace(/\\{(\\w+)\\}/g, (_, n) => (p || {})[n]) } };\n"
             f"const P = require({json.dumps(str(STATIC / 'data-status.js'))});\n"
             f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)


def test_sleep_steps_and_reasons_in_data_status_js():
    out = node_json("""(() => {
      const t = (key, status) => ({ key, label: key, status, notes: [] });
      return { steps: P.sleepTrackingSteps(), never: P.reasonsFor(t("sleep", "never")), other: P.reasonsFor(t("hrv", "never")) };
    })()""")
    assert len(out["steps"]) == 4 and "睡眠" in out["steps"][0] and "手表" in out["steps"][0]
    assert out["never"][0] == "手表上没有开启睡眠追踪，或者睡觉时没戴手表"     # the watch's own switch is the first reason for sleep
    assert "手表上没有开启睡眠追踪，或者睡觉时没戴手表" not in out["other"]
