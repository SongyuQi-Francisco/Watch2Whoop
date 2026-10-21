"""English labels the Today page shows verbatim from the server (issue #42): /today, /trends, training calendar / day,
healthspan, health monitor, grocery and recipes. With `X-Body-Lang: en` the label fields carry no Chinese; without the
header they are unchanged. Plus a static check that every Chinese label table has an English twin.

The DB part uses its own test database BODY_TEST_DB_NAME (default body_test_i18n_labels), dropped and recreated each run;
skipped without Postgres. SparkyFitness is faked with monkeypatch.
"""
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.assistant import recipes
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.goals import nutrition as goals_nutrition
from body_engine.grocery import params as GP
from body_engine.i18n import use_lang
from body_engine.insights import coach, fitness, healthspan_store
from body_engine.rawdata import ecg
from body_engine.training import history, params as TP
from body_engine.trends import calc, params as P, series
from body_engine.web import views

CJK = re.compile(r"[　-〿一-鿿＀-￯]")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_i18n_labels")
TZ = ZoneInfo("Australia/Sydney")
TODAY = datetime.now(TZ).date()
YDAY = TODAY - timedelta(days=1)
EN = {"X-Body-Lang": "en"}


def no_cjk(*texts):
    bad = [t for t in texts if t and CJK.search(str(t))]
    assert not bad, bad


# ---------------------------------------------------------------- static: every Chinese table has an English twin
def _zh_labels():
    out = [lbl for _, lbl, _, _, _ in series.SERIES] + [u for _, _, u, _, _ in series.SERIES if CJK.search(u)]
    out += [lbl for _, lbl, _ in P.LOAD_FOCUS] + [lbl for _, _, lbl in P.RESILIENCE_LEVELS]
    out += [lbl for _, _, lbl in P.ILLNESS_LEVELS] + [lbl for _, _, lbl in P.CHRONO_BINS]
    out += [*calc.TRAINING_STATUS.values(), *calc.HRV_STATUS.values(), *calc.RI_LEVELS.values()]
    return [x for x in out if CJK.search(x)]   # "VO₂max" needs no twin


def test_trend_labels_have_english():
    missing = [z for z in _zh_labels() if z not in P.EN]
    assert not missing, missing
    no_cjk(*P.EN.values())


@pytest.mark.parametrize("zh,en", [
    (views.RECOVERY_WORD, views.RECOVERY_WORD_EN), (views.LOAD_STATUS, views.LOAD_STATUS_EN),
    (TP.TYPE_NAMES, TP.TYPE_NAMES_EN), (TP.INTENSITY_LABELS, TP.INTENSITY_LABELS_EN),
    (history.STATUS_ZH, history.STATUS_EN), (history.BASIS_ZH, history.BASIS_EN),
    (healthspan_store.LABELS, healthspan_store.LABELS_EN), (coach.MONITOR, coach.MONITOR_EN),
    ({k: zh for _, k, zh in fitness.LEVELS}, fitness.LEVELS_EN),
    (goals_nutrition.DAY_TYPE_ZH, goals_nutrition.DAY_TYPE_EN),
    (GP.CATEGORIES, GP.CATEGORIES_EN), (ecg.CLASS_ZH, ecg.CLASS_EN),
])
def test_key_tables_have_english_twins(zh, en):
    assert set(zh) == set(en)
    no_cjk(*(v if isinstance(v, str) else " ".join(v) for v in en.values()))


def test_value_tables_cover_every_label():
    assert set(GP.FLAG_LABELS.values()) | {"高嘌呤", "红肉", "牛肋条"} <= set(GP.FLAGS_EN)
    assert set(history.SOURCE_EN) == {"快捷指令", "Apple 健康"}
    for table in (GP.FLAGS_EN, history.SOURCE_EN, recipes.AUTO_TAGS_EN):
        no_cjk(*table.values())
    no_cjk(coach.RHR_APPLE_LABEL[1])


# ---------------------------------------------------------------- pure: status labels at read time
def _s(start, values):
    return {start + timedelta(days=i): v for i, v in enumerate(values) if v is not None}


def test_trend_status_labels_follow_language():
    d0 = date(2026, 10, 18)
    hrv = {**_s(d0 - timedelta(days=66), [50 + (i % 5) - 2 for i in range(60)]), **_s(d0 - timedelta(days=6), [44] * 7)}
    trimp = _s(d0 - timedelta(days=39), [50.0] * 40)
    ctl = _s(d0 - timedelta(days=39), [40.0] * 40)
    rec, perf = _s(d0 - timedelta(days=13), [80] * 14), _s(d0 - timedelta(days=13), [1.0] * 14)
    nights = [(datetime(2026, 9, 1, 23, 0, tzinfo=TZ) + timedelta(days=k), datetime(2026, 9, 2, 7, 0, tzinfo=TZ) + timedelta(days=k))
              for k in range(14)]

    def labels():
        return [calc.hrv_status(hrv, d0)["label"], calc.training_status(trimp, ctl, {}, None, d0)["label"],
                *(g["label"] for g in calc.load_focus({d0: [10, 20, 30, 0, 5]}, d0)["groups"]),
                calc.resilience(rec, perf, {}, d0)["label"], calc.illness_risk({"rhr": -3, "hrv": -3}, 0.8, 92)["label"],
                calc.chronotype(nights)["label"], calc.recovery_index(400, 480)["label"]]

    zh = labels()
    assert zh[0] == "很低" and zh[1] == "保持" and all(CJK.search(x) for x in zh)
    with use_lang("en"):
        en = labels()
    assert en[0] == "Very low" and en[1] == "Maintaining" and en[-1] == "Very late"
    no_cjk(*en)
    assert labels() == zh   # back to zh outside the context


def test_training_display_name():
    assert history.display_name("力量训练 · 腿") == "力量训练 · 腿"
    with use_lang("en"):
        assert history.display_name("力量训练 · 腿") == "Strength training · 腿"   # the session name is the user's own
        assert history.display_name("步行 / 爬坡") == "Walking / incline"
        assert history.display_name("爬坡（跑步机）") == "爬坡（跑步机）"          # a SparkyFitness name: user data
        assert history.display_name(None) is None


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_test_accept", ""):
        pytest.exit("the test DB must not be a real DB")
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


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        a = conn.execute("""INSERT INTO users (display_name, birth_date, sex, timezone) VALUES ('A', '1990-01-01', 'm',
                            'Australia/Sydney') RETURNING id""").fetchone()["id"]
        tok = auth.create_token(conn, a, "web", ["read", "write"])
        for d, rec, tsb in ((YDAY, 75, -20), (TODAY, 80, -5)):
            conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_actual_min, strain, ctl, atl,
                              tsb, flags, algo_version) VALUES (%s, %s, %s, 80, 450, 12.0, 50, 70, %s, '{}', 'test')""",
                         (a, d, rec, tsb))
        start = datetime.combine(YDAY, datetime.min.time(), TZ) + timedelta(hours=19)
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                          duration_min, avg_hr, max_hr) VALUES (%s, 'shortcut', 'w1', 'strength_training', '力量训练', %s, %s, %s,
                          60, 110, 150), (%s, 'ow', 'w2', 'basketball', '篮球', %s, %s, %s, 50, 140, 175)""",
                     (a, YDAY, start, start + timedelta(minutes=60),
                      a, YDAY, start - timedelta(hours=3), start - timedelta(hours=2, minutes=10)))
        conn.execute("""INSERT INTO healthspan_weekly (user_id, week_start, as_of, chrono_age, body_age, delta_years,
                          contributors, insufficient, algo_version) VALUES (%s, %s, %s, 25.2, 24.0, -1.2, %s, %s, 'test')""",
                     (a, TODAY - timedelta(days=TODAY.weekday()), TODAY,
                      Jsonb([{"key": "steps", "label": "日均步数", "category": "activity", "value": 9000, "unit": "步",
                              "years": -0.8}, {"key": "rhr", "label": "静息心率", "category": "fitness", "value": 55,
                                               "unit": "bpm", "years": -0.4}]),
                      Jsonb([{"key": "vo2max", "label": "VO₂max", "category": "fitness", "reason": "没填性别"},
                             {"key": "mvpa", "label": "每周中高强度运动", "category": "activity", "reason": "x"}])))
    return a, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def fake_sparky(monkeypatch):
    dow = TODAY.isoweekday() % 7
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: object())
    monkeypatch.setattr(sparky_client, "exercises", lambda c: [
        {"id": "ex1", "name": "卧推", "category": "strength", "description": None},
        {"id": "ex2", "name": "平板支撑", "category": "strength", "description": None}])
    monkeypatch.setattr(sparky_client, "workout_presets", lambda c: {7: {"id": 7, "name": "上肢 A", "exercises": [
        {"exercise_id": "ex1", "name": "卧推", "rep_goal": None, "sets": [{"set": 1, "type": "Working Set"}] * 3},
        {"exercise_id": "ex2", "name": "平板支撑", "rep_goal": None,
         "sets": [{"set": i, "type": "Working Set", "duration_s": 60} for i in (1, 2)]}]}})
    monkeypatch.setattr(sparky_client, "workout_plans", lambda c: [{
        "id": 1, "name": "V3", "start_date": None, "end_date": None, "is_active": True, "schedule_type": "weekly",
        "assignments": [{"day_of_week": dow, "sort_order": 0, "workout_preset_id": 7, "workout_preset_name": "上肢 A"}]}])
    monkeypatch.setattr(sparky_client, "training_log", lambda c, s, e: [
        {"id": "s1", "date": d.isoformat(), "name": "胸", "notes": None, "total_duration_minutes": 0, "exercises": [
            {"exercise_id": "ex1", "name": "卧推", "category": "strength", "duration_minutes": 0,
             "sets": [{"set": 1, "type": "Working Set", "reps": 8, "weight_kg": None}]}]}
        for d in (s + timedelta(days=k) for k in range((e - s).days + 1)) if d in (TODAY, YDAY)])
    monkeypatch.setattr(sparky_client, "food_entries", lambda c, s, e: [])
    monkeypatch.setattr(sparky_client, "goal_for_date", lambda c, d: {"name": "默认", "calories": 2400, "protein_g": 160,
                                                                      "carbs_g": 255, "fat_g": 62})
    monkeypatch.setattr(sparky_client, "weight", lambda c, s, e: [])


def get(client, tok, path, lang=None):
    r = client.get(path, headers={"Authorization": f"Bearer {tok}", **(lang or {})})
    assert r.status_code == 200, r.text
    return r.json()


def test_today_labels(client, world, monkeypatch):
    _, tok = world
    fake_sparky(monkeypatch)
    zh, en = get(client, tok, "/v1/users/me/today"), get(client, tok, "/v1/users/me/today", EN)
    assert zh["scores"]["recovery_word"] == "适合高强度" and zh["scores"]["load_status"]["label"] == "积累疲劳"
    assert en["scores"]["recovery_word"] == "Ready to push" and en["scores"]["load_status"]["label"] == "Building fatigue"
    sets = lambda d: [x["sets_text"] for p in d["training"]["planned"] for x in p["exercises"]] + \
        [x["sets_text"] for s in d["training"]["logged"] for x in s["exercises"]]
    assert sets(zh) == ["3 组", "2 组 × 60 秒", "8 次"]
    assert sets(en) == ["3 sets", "2 sets × 60 s", "8 reps"]
    assert en["scores"]["today_reasons"] == zh["scores"]["today_reasons"]   # reasons stay Chinese (pattern-matched)


def test_trends_labels(client, world, monkeypatch):
    _, tok = world
    fake_sparky(monkeypatch)
    zh, en = get(client, tok, "/v1/users/me/trends?days=7"), get(client, tok, "/v1/users/me/trends?days=7", EN)
    assert zh["series"]["recovery"]["label"] == "恢复分" and zh["series"]["steps"]["unit"] == "步"
    assert en["series"]["recovery"]["label"] == "Recovery" and en["series"]["steps"]["unit"] == "steps"
    assert set(en["series"]) == set(zh["series"]) and len(en["series"]) > 20
    no_cjk(*(s["label"] for s in en["series"].values()), *(s["unit"] for s in en["series"].values()))
    no_cjk(*(st["label"] for st in en["status"].values() if isinstance(st, dict) and "label" in st))
    assert en["unavailable"] == zh["unavailable"]   # the page looks for 授权 in these


def test_training_calendar_and_day_labels(client, world, monkeypatch):
    _, tok = world
    fake_sparky(monkeypatch)
    path = f"/v1/users/me/training/day/{YDAY}"
    zh, en = get(client, tok, path), get(client, tok, path, EN)
    assert [s["name"] for s in zh["sessions"]] == ["篮球", "力量训练 · 胸"]
    assert [s["name"] for s in en["sessions"]] == ["Basketball", "Strength training · 胸"]
    for s in en["sessions"]:
        no_cjk(s["type_name"], s["intensity_label"], s["intensity_basis_label"], s["source_label"],
               *(e["status"] for e in s["exercises"]))
    assert {s["source_label"] for s in en["sessions"]} == {"Apple Health", "Shortcut + SparkyFitness"}
    no_cjk(en["daily"]["intensity_label"])
    month = YDAY.strftime("%Y-%m")
    zc, ec = (get(client, tok, f"/v1/users/me/training/calendar?month={month}", lang) for lang in (None, EN))
    assert zc["intensity_levels"][0]["label"] == "休息" and ec["intensity_levels"][0]["label"] == "Rest"
    day = next(d for d in ec["days"] if d["date"] == YDAY.isoformat())
    no_cjk(day["intensity_label"], *(lbl.split(" · ")[0] for lbl in day["labels"]))
    no_cjk(*(lv["label"] for lv in ec["intensity_levels"]))


def test_insight_labels(client, world):
    _, tok = world
    zh, en = get(client, tok, "/v1/users/me/healthspan"), get(client, tok, "/v1/users/me/healthspan", EN)
    assert [c["label"] for c in zh["contributors"]] == ["日均步数", "静息心率"]
    assert [(c["label"], c["unit"]) for c in en["contributors"]] == [("Daily steps", "steps"), ("Resting HR", "bpm")]
    no_cjk(*(x["label"] for x in en["insufficient"]))
    zm, em = get(client, tok, "/v1/users/me/health-monitor"), get(client, tok, "/v1/users/me/health-monitor", EN)
    assert zm["metrics"][2]["unit"] == "次/分" and em["metrics"][2]["unit"] == "/min"
    no_cjk(*(m["label"] for m in em["metrics"]), *(m["unit"] for m in em["metrics"]))
    assert get(client, tok, "/v1/users/me/vo2max", EN)["level_label"] is None   # no readings: no level


def test_grocery_labels(client, world):
    _, tok = world
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/v1/users/me/grocery/products", headers=h, json={
        "name": "Bacon", "category": "meat", "package_size": 500, "package_unit": "g", "price": 8, "currency": "AUD",
        "red_meat": True, "nutrition": {"kcal": 400, "protein_g": 20, "carbs_g": 0, "fat_g": 35, "sat_fat_g": 12}})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post("/v1/users/me/grocery/list/items", headers=h, json={"items": [
        {"name": "Bacon", "product_id": pid}, {"name": "Eggs", "qty": 12, "unit": "pcs"}]})
    assert r.status_code == 201, r.text
    zl, el = get(client, tok, "/v1/users/me/grocery/list"), get(client, tok, "/v1/users/me/grocery/list", EN)
    assert {i["category_label"] for i in zl["items"]} >= {"肉类"} and "红肉 500g" in [w["label"] for w in zl["warnings"]]
    assert {i["qty_label"] for i in el["items"]} == {"1 pk · 500 g", "12 pcs"}
    no_cjk(*(i["category_label"] for i in el["items"]), *(i["qty_label"] for i in el["items"]),
           *(w["label"] for w in el["warnings"]))
    assert {w["label"] for w in el["warnings"]} == {"Red meat 500g", "Bacon High sat fat"}
    zp, ep = get(client, tok, "/v1/users/me/grocery/products"), get(client, tok, "/v1/users/me/grocery/products", EN)
    assert zp[0]["flags"] == ["饱和脂肪高", "红肉"] and ep[0]["flags"] == ["High sat fat", "Red meat"]
    no_cjk(ep[0]["category_label"], ep[0]["size_label"])
