"""Explanation sheets: static entries, rule-based advice from the owner's own numbers, status items (conditions, labs, exercise limits),
and per-user isolation (a family member sees only shared blocks; nobody gets another user's numbers through their own account).

Uses a separate test database BODY_TEST_DB_NAME_EXPLAIN (default body_test_explain), dropped and recreated on every run; skipped if Postgres is unreachable.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.explain import glossary as G

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_EXPLAIN", "body_test_explain")
TZ = ZoneInfo("Australia/Sydney")
TODAY = datetime.now(TZ).date()


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # no password file, or the database is not running
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


def _status(conn, uid, key, label, value, group, tone="warn", unit=None, note=None, source="assistant"):
    conn.execute("""INSERT INTO assistant_status (user_id, key, label, value, unit, tone, "group", note, sort, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s)""", (uid, key, label, value, unit, tone, group, note, source))


@pytest.fixture(scope="module")
def world(dsn):
    """A: a bad week (HRV down, resting HR up, short late sleep, red recovery), a shoulder limit, a sleep problem, high uric acid.
    B: shared A's scores + sleep only. C: no shares. B has a little data of their own."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, sex, birth_date) VALUES (%s, 'male', '2001-06-15') RETURNING id",
                               (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["scores", "sleep"]))
        a, b = ids["A"], ids["B"]
        for k in range(80):  # baseline HRV ~50 ± 3, resting HR ~50 ± 1; the last 7 days HRV ~35, resting HR ~57
            d = TODAY - timedelta(days=k)
            recent = k < 7
            hrv = 35 + (k % 2) if recent else 50 + (k % 3 - 1) * 3
            rhr = 57 if recent else 50 + (k % 2)
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source) VALUES
                            (%s, %s, 'heart_rate_variability_sdnn', %s, 'ms', 'avg', 1, 'ow'),
                            (%s, %s, 'resting_heart_rate', %s, 'count/min', 'avg', 1, 'ow')""", (a, d, hrv, a, d, rhr))
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source) VALUES
                        (%s, %s, 'vo2_max', 38, 'mL/min·kg', 'avg', 1, 'ow'),
                        (%s, %s, 'heart_rate_variability_sdnn', 80, 'ms', 'avg', 1, 'ow')""",
                     (a, TODAY - timedelta(days=3), b, TODAY))
        for k in range(7):
            d = TODAY - timedelta(days=k)
            details = {"sleep": {"duration": 40, "stages": 90, "consistency": 30, "interruptions": 95},
                       "recovery": {"z": {"hrv": -1.6, "rhr": -1.2, "sleep_performance": -0.4, "resp_rate": 0.1}}}
            conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_actual_min, sleep_need_min,
                              sleep_debt_min, strain, ctl, atl, tsb, flags, details, algo_version)
                            VALUES (%s, %s, 25, 55, 330, 520, 150, 15.5, 40, 52, -12, '{}', %s, 'test')""", (a, d, Jsonb(details)))
            start = datetime.combine(d, datetime.min.time(), TZ) + timedelta(hours=2, minutes=30)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                            VALUES (%s, %s, %s, %s, 330, true, 'ow')""", (a, d, start, start + timedelta(hours=6, minutes=30)))
        _status(conn, a, "right_shoulder", "肩", "内旋受限", "健康", note="无痛弹响 · 避开禁用动作")
        _status(conn, a, "sleep_issue", "作息", "睡眠障碍", "作息", tone="bad")
        _status(conn, a, "uric_acid", "尿酸", "偏高 550", "健康", tone="bad", unit="μmol/L", source="auto")
        _status(conn, a, "sleep_hours", "睡眠时长", "5.5", "作息", tone="bad", unit="h", source="auto")
        _status(conn, a, "mystery", "其他", "待观察", "其他", tone="neutral", note="管家写的")
        conn.execute("""INSERT INTO lab_results (user_id, collected_on, category, test_key, test_name, value, unit, ref_low, ref_high)
                        VALUES (%s, %s, 'chem', 'uric_acid', '尿酸', 550, 'μmol/L', 200, 420)""",
                     (a, TODAY - timedelta(days=120)))
        conn.execute("""INSERT INTO assistant_profile (user_id, key, label, value, sort, source) VALUES
                        (%s, 'goals', '目标和进度', '增肌 + 减脂；肩膀不紧张、改善圆肩（1）。', 20, 'seed'),
                        (%s, 'injuries', '伤病和禁用动作', '肩内旋受限（后关节囊偏紧），内旋时无痛弹响；推类动作手肘约 45°（2.3、4.3）。', 40, 'seed'),
                        (%s, 'schedule', '作息规律', '作息不规律，常凌晨 1 点左右入睡。', 50, 'seed')""", (a, a, a))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason, cues) VALUES
                        (%s, 'upright_row', '直立划船', 'banned', '肩内旋位上举', NULL),
                        (%s, 'db_press', '哑铃推举', 'caution', NULL, '手肘 45°')""", (a, a))
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token):
    return {"Authorization": f"Bearer {token}"}


def get(client, tok, path):
    return client.get(path, headers=H(tok))


# ---------- static entries ----------

def test_glossary_entries_are_complete():
    for e in [*G.METRICS.values(), *G.LABS.values(), *G.STATUS.values(), *G.CONDITIONS]:
        assert e["title"] and e["what"]
        assert all(t in ("good", "mid", "bad", "plain") for _, _, t in e.get("read", []))
    for c in G.CONDITIONS:
        from body_engine.explain.personal import CALC
        assert all(m in CALC for m in c.get("metrics", []))
    assert G.condition_for("肩 内旋受限")["title"] == "肩关节内旋受限"
    assert G.condition_for("作息 睡眠障碍")["title"] == "睡眠障碍"
    assert G.condition_for("体重 89") is None


def test_unknown_metric_404(client, world):
    _, tok = world
    assert get(client, tok["A"], "/v1/users/me/explain/metric/nope").status_code == 404
    assert get(client, tok["A"], "/v1/users/me/explain/status/nope").status_code == 404


# ---------- metrics: advice from the owner's numbers ----------

def test_hrv_below_baseline(client, world):
    _, tok = world
    r = get(client, tok["A"], "/v1/users/me/explain/metric/hrv").json()
    assert r["title"].startswith("HRV") and "SDNN" in r["what"] and r["recovery"]
    assert r["current"]["tone"] == "bad" and r["current"]["unit"] == "ms"
    assert any(f["label"] == "你的基线" for f in r["facts"])
    assert "低于基线" in r["advice"][0]["text"]
    assert "HRV" in r["ai_prompt"] and str(r["current"]["value"]) in r["ai_prompt"]


def test_resting_hr_above_baseline(client, world):
    _, tok = world
    r = get(client, tok["A"], "/v1/users/me/explain/metric/resting_hr").json()
    assert r["current"]["tone"] == "bad" and "比基线高" in r["advice"][0]["text"]


def test_sleep_and_recovery(client, world):
    _, tok = world
    s = get(client, tok["A"], "/v1/users/me/explain/metric/sleep_hours").json()
    assert s["current"]["value"] == 5.5 and s["current"]["tone"] == "bad"
    assert "明显不够" in s["advice"][0]["text"] and any("今晚" in a["text"] for a in s["advice"])
    sc = get(client, tok["A"], "/v1/users/me/explain/metric/sleep_score").json()
    assert "规律性" in sc["advice"][0]["text"]        # the weakest part (consistency 30)
    assert {"label": "时长", "value": "40"} in sc["facts"]
    rec = get(client, tok["A"], "/v1/users/me/explain/metric/recovery").json()
    assert rec["current"]["tone"] == "bad" and "恢复为主" in rec["advice"][0]["text"]
    assert any("HRV" in a["text"] for a in rec["advice"])
    bt = get(client, tok["A"], "/v1/users/me/explain/metric/bedtime").json()
    assert bt["current"]["value"] == "凌晨 2:30" and bt["current"]["tone"] == "bad"
    eff = get(client, tok["A"], "/v1/users/me/explain/metric/sleep_efficiency").json()
    assert eff["current"]["value"] == 85 and eff["current"]["tone"] == "good"
    st = get(client, tok["A"], "/v1/users/me/explain/metric/strain").json()
    assert st["current"]["value"] == 15.5 and any("10 以下" in a["text"] for a in st["advice"])


def test_vo2max_level(client, world):
    _, tok = world
    r = get(client, tok["A"], "/v1/users/me/explain/metric/vo2max").json()
    assert r["current"]["value"] == 38 and any(f["label"] == "同龄同性别" for f in r["facts"])


def test_no_data_gives_reason_not_numbers(client, world):
    _, tok = world
    r = get(client, tok["C"], "/v1/users/me/explain/metric/hrv").json()
    assert r["current"] is None and r["missing"] and r["what"]


# ---------- status items ----------

def test_shoulder_status(client, world):
    _, tok = world
    r = get(client, tok["A"], "/v1/users/me/explain/status/right_shoulder").json()
    assert r["title"] == "肩关节内旋受限" and "内旋" in r["what"]
    assert r["avoid"] and any("45°" in t for t in r["tips"])
    assert [x["name"] for x in r["exercises"]["banned"]] == ["直立划船"]
    assert r["exercises"]["caution"][0]["cues"] == "手肘 45°"
    assert r["profile"][0].startswith("肩内旋受限") and "2.3" not in r["profile"][0]
    assert r["item"]["value"] == "内旋受限" and r["current"]["value"] == "内旋受限"
    assert "肩：内旋受限" in r["ai_prompt"]


def test_sleep_problem_status_attaches_own_sleep(client, world):
    _, tok = world
    r = get(client, tok["A"], "/v1/users/me/explain/status/sleep_issue").json()
    assert r["title"] == "睡眠障碍" and "失眠" in r["what"]
    labels = [f["label"] for f in r["facts"]]
    assert "入睡时间" in labels and "睡眠时长" in labels
    assert r["current"]["value"] == "睡眠障碍"
    assert any("凌晨" in p for p in r["profile"])


def test_lab_status(client, world):
    _, tok = world
    r = get(client, tok["A"], "/v1/users/me/explain/status/uric_acid").json()
    assert r["title"] == "尿酸" and r["current"]["value"] == 550 and r["current"]["tone"] == "bad"
    assert "高于上限 31%" in r["advice"][0]["text"] and any("复查" in a["text"] for a in r["advice"][1:])


def test_status_metric_and_unknown(client, world):
    _, tok = world
    r = get(client, tok["A"], "/v1/users/me/explain/status/sleep_hours").json()
    assert r["kind"] == "status" and r["title"] == "睡眠时长" and r["current"]["value"] == 5.5
    m = get(client, tok["A"], "/v1/users/me/explain/status/mystery").json()
    assert m["title"] == "其他" and m["what"] == "管家写的" and m["current"]["value"] == "待观察"


# ---------- multi-user ----------

def test_family_sees_only_shared_blocks(client, world):
    ids, tok = world
    a = ids["A"]
    hrv = get(client, tok["B"], f"/v1/users/{a}/explain/metric/hrv").json()      # metrics not shared
    assert hrv["current"] is None and hrv["facts"] == [] and "metrics" in hrv["hidden"] and hrv["what"]
    sleep = get(client, tok["B"], f"/v1/users/{a}/explain/metric/sleep_hours").json()   # scores shared
    assert sleep["current"]["value"] == 5.5
    assert get(client, tok["B"], f"/v1/users/{a}/explain/status/right_shoulder").status_code == 403   # memory not shared
    assert get(client, tok["C"], f"/v1/users/{a}/explain/metric/sleep_hours").status_code == 403
    assert get(client, tok["A"], f"/v1/users/{ids['B']}/explain/metric/hrv").status_code == 403


def test_own_account_gets_own_numbers(client, world):
    _, tok = world
    r = get(client, tok["B"], "/v1/users/me/explain/metric/hrv").json()
    assert r["current"]["value"] == 80 and r["current"]["tone"] == "plain"   # B's single reading, no baseline yet
    assert get(client, tok["B"], "/v1/users/me/explain/status/right_shoulder").status_code == 404


def test_page_uses_explain_endpoints():
    from pathlib import Path
    js = (Path(api.__file__).parent / "web" / "static" / "app.js").read_text()
    assert "/explain/${kind}/" in js and 'explainInto(root, "status", it.key' in js
    assert "openChat(prompt)" in js   # "Ask AI" goes to the viewer's own AI chat
