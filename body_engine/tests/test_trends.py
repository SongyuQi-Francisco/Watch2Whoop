"""Trends tab: the pure analyses in trends/calc.py, the recovery-index offset in scoring, heart-rate recovery of a workout,
and GET /trends with every series, "vs 上期", recovery colours, the strain target band and the status cards (incl. scope checks).

The DB part uses its own test database BODY_TEST_DB_NAME (default body_test_trends), dropped and recreated each run; skipped without Postgres.
"""
import math
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.scores import recovery as R
from body_engine.training.importer import session_hr_recovery
from body_engine.trends import calc, params as P

TZ = ZoneInfo("Australia/Sydney")
D0 = date(2026, 10, 18)


def series(start: date, values: list) -> dict:
    return {start + timedelta(days=i): v for i, v in enumerate(values) if v is not None}


# ---------------------------------------------------------------- pure analyses
def test_hrv_status_bands():
    base = series(D0 - timedelta(days=66), [50 + (i % 5) - 2 for i in range(60)])   # 48–52 ms, 60 days before the window
    week = series(D0 - timedelta(days=6), [50] * 7)
    assert calc.hrv_status({**base, **week}, D0)["status"] == "balanced"
    low = calc.hrv_status({**base, **series(D0 - timedelta(days=6), [44] * 7)}, D0)
    assert low["status"] == "poor" and low["label"] == "很低" and low["band_ms"][0] < 50 < low["band_ms"][1]
    assert calc.hrv_status({**base, **series(D0 - timedelta(days=6), [60] * 7)}, D0)["status"] == "high"
    assert calc.hrv_status(week, D0) is None                                           # no baseline
    assert calc.hrv_status({**base, **series(D0 - timedelta(days=1), [50, 50])}, D0) is None   # < 3 nights


def test_acwr_and_training_status():
    days = 40
    steady = series(D0 - timedelta(days=days - 1), [50.0] * days)
    ctl = series(D0 - timedelta(days=days - 1), [40.0] * days)
    assert calc.acwr(steady, D0) == 1.0
    assert calc.training_status(steady, ctl, {}, None, D0)["status"] == "maintaining"
    spike = {**steady, **series(D0 - timedelta(days=6), [150.0] * 7)}
    assert calc.training_status(spike, ctl, {}, None, D0)["status"] == "overreaching"
    vo2_up = series(D0 - timedelta(days=50), [40.0, None, None, None, None] * 6 + [42.0] * 21)
    assert calc.training_status(steady, ctl, vo2_up, None, D0)["status"] == "productive"
    assert calc.training_status(steady, ctl, {}, {"status": "low"}, D0)["status"] == "strained"
    rest = {**steady, **series(D0 - timedelta(days=6), [0.0] * 7)}
    ctl_drop = {**ctl, D0 - timedelta(days=28): 60.0, D0: 40.0}
    assert calc.training_status(rest, ctl, {}, None, D0)["status"] == "recovery"
    assert calc.training_status(rest, ctl_drop, {}, None, D0)["status"] == "detraining"
    assert calc.training_status(series(D0 - timedelta(days=9), [50.0] * 10), ctl, {}, None, D0) is None   # < 21 days of TRIMP


def test_load_focus_and_aerobic_minutes():
    z = {D0: [10, 20, 30, 0, 5]}
    f = calc.load_focus(z, D0)
    pct = {g["key"]: g["pct"] for g in f["groups"]}
    assert pct == {"low_aerobic": round(100 * 50 / 165), "high_aerobic": round(100 * 90 / 165), "anaerobic": round(100 * 25 / 165)}
    assert calc.load_focus({}, D0) is None
    assert calc.aerobic_minutes([10, 20, 30, 4, 1]) == 20 + 30 + 2 * 5
    weeks = calc.weekly_sum({date(2026, 10, 12): 30, date(2026, 10, 18): 20, date(2026, 10, 19): 5},
                            [date(2026, 10, 12) + timedelta(days=k) for k in range(8)])
    assert weeks == [{"week": date(2026, 10, 12), "value": 50.0}, {"week": date(2026, 10, 19), "value": 5.0}]


def test_resilience_levels():
    rec = series(D0 - timedelta(days=13), [80] * 14)
    perf = series(D0 - timedelta(days=13), [1.0] * 14)
    calm = calc.resilience(rec, perf, series(D0 - timedelta(days=13), [0.3] * 14), D0)
    tense = calc.resilience(series(D0 - timedelta(days=13), [40] * 14), series(D0 - timedelta(days=13), [0.6] * 14),
                            series(D0 - timedelta(days=13), [2.5] * 14), D0)
    assert calm["level"] in ("strong", "exceptional") and tense["level"] == "limited"
    assert calc.resilience(series(D0 - timedelta(days=4), [80] * 5), perf, {}, D0) is None


def test_sleep_perf_whoop_and_combined_strain():
    assert calc.sleep_performance_whoop(1.0, 100, 1.0) == 100
    assert calc.sleep_performance_whoop(0.5, None, None) == 50          # missing parts drop out
    assert calc.sleep_performance_whoop(None, 90, 0.9) is None
    assert calc.sleep_performance_whoop(0.8, 50, 0.9) == round(0.6 * 80 + 0.2 * 50 + 0.2 * 90, 1)
    assert calc.combined_strain(100, None, 3000) == round(calc.strain_from_trimp(100), 1)
    assert calc.combined_strain(100, 3000, 3000) == round(calc.strain_from_trimp(100 + P.MUSCLE_TRIMP_AT_MEDIAN), 1)
    assert calc.combined_strain(None, None, None) is None


def test_illness_risk():
    assert calc.illness_risk({"hrv": 0.4, "rhr": 0.2, "resp_rate": 0.0}, None, None)["level"] == "low"
    sick = calc.illness_risk({"hrv": -2.0, "rhr": -2.0, "resp_rate": -1.5}, 0.8, 92.0)
    assert sick["level"] == "high" and sick["score"] >= 60
    assert calc.illness_risk(None, None, None) is None


def test_chronotype():
    nights = []
    for k in range(14):
        wake_day = D0 - timedelta(days=k)
        free = wake_day.weekday() in (5, 6)
        bed = datetime.combine(wake_day, datetime.min.time(), TZ) + timedelta(hours=3 if free else 2)
        nights.append((bed, bed + timedelta(hours=9 if free else 7)))
    c = calc.chronotype(nights)
    # free days: 03:00–12:00 → mid 07:30, minus half of (9 h − 7 h) = 06:30 → very late
    assert c["mid_sleep"] == "06:30" and c["type"] == "very_late" and c["free_days"]
    assert calc.chronotype(nights[:5]) is None


def test_body_battery_and_recovery_time():
    bb = calc.body_battery(80, None, 9, [{"hour": 14, "score": 2.5, "level": "high"}], 12.0)
    assert bb["wake"] == round(20 + 75 * 0.8) and bb["points"][0]["hour"] == 9 and bb["points"][-1]["hour"] == 23
    assert bb["current"] < bb["wake"] and bb["estimate"]
    assert calc.body_battery(None, None, 9, [], 0) is None
    strain = {D0 - timedelta(days=1): 18.0}
    rt = calc.recovery_time(strain, {}, "yellow", D0)
    assert rt["hours"] == round((18 - 8) * 5 - 13) and rt["from_strain"] == 18.0   # 50 h needed, 13 h passed by 07:00
    assert calc.recovery_time({D0 - timedelta(days=1): 6.0}, {}, None, D0) == {"hours": 0, "from_date": None, "from_strain": None, "estimate": True}
    assert calc.recovery_time({}, {}, None, D0) is None


def test_recovery_index_and_hr_recovery():
    assert calc.recovery_index(120, 480)["level"] == "good"
    assert calc.recovery_index(420, 480)["label"] == "很晚"
    assert calc.recovery_index(None, 480) is None
    assert calc.hr_recovery_level(25) == "good" and calc.hr_recovery_level(12) == "low"
    end = datetime(2026, 10, 17, 18, 0, tzinfo=TZ)
    hr = [(end - timedelta(seconds=30), 150.0), (end + timedelta(seconds=58), 128.0), (end + timedelta(seconds=150), 110.0)]
    assert session_hr_recovery(hr, end) == 22.0
    assert session_hr_recovery(hr[:1], end) is None


def test_lowest_window_reports_when():
    start = datetime(2026, 10, 17, 1, 0, tzinfo=TZ)
    hr = [(start + timedelta(minutes=5 * k), 60.0 - (k == 30) * 12) for k in range(80)]
    v, at = R.lowest_window(hr, start, start + timedelta(hours=7))
    assert at == start + timedelta(minutes=150) and v < 60
    assert R.lowest_window([], start, start) == (None, None)


# ---------------------------------------------------------------- GET /trends
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_trends")


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
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
    """A: 120 days of scores, metrics, stress, sleep and one workout; B sees A's sleep only; C sees nothing."""
    today = datetime.now(TZ).date()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, timezone) VALUES (%s, 'Australia/Sydney') RETURNING id",
                               (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["sleep"]))
        a = ids["A"]
        for k in range(120):
            d = today - timedelta(days=k)
            rec = 30 + (k * 7) % 60
            details = {"sleep": {"deep_min": 60, "rem_min": 90, "asleep_min": 420, "awakenings": k % 3, "consistency": 70,
                                 "efficiency": 0.95, "wake_time": f"{d.isoformat()}T09:00+11:00"},
                       "recovery": {"z": {"hrv": 0.3, "rhr": -0.2, "resp_rate": 0.1}, "spo2_night": 96.0,
                                    "wrist_temp": 35.5 + (0.6 if k == 0 else 0), "rhr_low_offset_min": 150},
                       "load": {"zone_minutes": [20, 15, 10, 2, 0]}}
            conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_actual_min, sleep_need_min,
                              sleep_debt_min, sleep_performance, resp_rate_night, trimp, strain, muscle_load, ctl, atl, tsb,
                              flags, details, algo_version)
                            VALUES (%s,%s,%s,75,420,480,60,0.875,16.5,%s,%s,%s,40,45,-5,'{}',%s,'test')""",
                         (a, d, rec, 60 + k % 10, 10 + k % 6, 3000 if k % 7 == 0 else None, Jsonb(details)))
            for metric, value, unit in (("heart_rate_variability_sdnn", 45 + k % 5, "ms"), ("resting_heart_rate", 55, "count/min"),
                                        ("step_count", 8000 + k, "count"), ("active_energy_burned", 500, "kcal"),
                                        ("basal_energy_burned", 1900, "kcal"), ("time_in_daylight", 40, "min")):
                conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                                VALUES (%s,%s,%s,%s,%s,'avg',1,'ow')""", (a, d, metric, value, unit))
            conn.execute("""INSERT INTO stress_daily (user_id, date, score, level, hours, algo_version)
                            VALUES (%s,%s,1.2,'medium',%s,'test')""", (a, d, Jsonb([{"hour": 15, "score": 2.4, "level": "high"}])))
            bed = datetime.combine(d, datetime.min.time(), TZ) + timedelta(hours=2)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                            VALUES (%s,%s,%s,%s,420,true,'ow')""", (a, d, bed, bed + timedelta(hours=7, minutes=20)))
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                        VALUES (%s,%s,'vo2_max',44,'mL/kg·min','avg',1,'ow')""", (a, today - timedelta(days=3)))
        conn.execute("""INSERT INTO healthspan_weekly (user_id, week_start, as_of, chrono_age, body_age, delta_years, algo_version)
                        VALUES (%s,%s,%s,25.2,23.9,-1.3,'test')""", (a, today - timedelta(days=13), today - timedelta(days=7)))
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                          duration_min, flags) VALUES (%s,'ow','w1','running','跑步',%s,%s,%s,30,%s)""",
                     (a, today - timedelta(days=2), datetime.combine(today - timedelta(days=2), datetime.min.time(), TZ),
                      datetime.combine(today - timedelta(days=2), datetime.min.time(), TZ) + timedelta(minutes=30),
                      Jsonb({"hr_recovery_bpm": 24.0})))
    return ids, tok, today


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(autouse=True)
def no_sparky(monkeypatch):
    def fail(*a, **k):
        raise sparky_client.SparkyError("no SparkyFitness in tests")
    monkeypatch.setattr(sparky_client, "client_for_user", fail)


def test_trends_all_series_and_previous_period(client, world):
    ids, tok, today = world
    r = client.get("/v1/users/me/trends?days=30", headers=bearer(tok["A"]))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["start"] == str(today - timedelta(days=29)) and d["prev_start"] == str(today - timedelta(days=59))
    s = d["series"]
    for k in ("recovery", "hrv", "resting_hr", "resp_rate", "spo2", "wrist_temp", "illness", "recovery_index", "sleep_hours",
              "sleep_need_hours", "sleep_debt_hours", "sleep_performance", "sleep_perf_whoop", "restorative_hours", "restorative_pct",
              "sleep_efficiency", "awakenings", "daylight", "strain", "strain_combined", "ctl", "atl", "tsb", "acwr", "aerobic_min",
              "steps", "active_kcal", "total_kcal", "hr_recovery", "stress", "resilience", "vo2max", "body_age"):
        assert k in s, k
        assert len(s[k]["values"]) == 30 and "prev_mean" in s[k] and s[k]["group"]
    assert s["restorative_hours"]["mean"] == 2.5 and s["restorative_pct"]["mean"] == round(100 * 150 / 420, 1)
    assert s["total_kcal"]["mean"] == 2400 and s["sleep_performance"]["mean"] == 87.5 and s["sleep_need_hours"]["mean"] == 8
    assert s["steps"]["prev_mean"] == pytest.approx(8000 + sum(range(30, 60)) / 30)
    assert s["aerobic_min"]["mean"] == 15 + 10 + 2 * 2 and s["recovery_index"]["mean"] == round(100 * 150 / 420)
    assert s["wrist_temp"]["values"][-1] == 0.6 and s["hr_recovery"]["values"][-3] == 24.0
    assert s["weight"] if "weight" in s else d["unavailable"]["weight"]
    z = s["recovery"]["zones"]
    assert sum(z.values()) == 30 and len(s["recovery"]["contributors"]) == 30
    t = s["strain"]["target"]
    assert len(t["bands"]) == 30 and t["days_with_strain"] == 30 and 0 <= t["in_range_days"] <= 30
    assert d["aerobic_weeks"] and d["aerobic_weeks"][0]["target"] == 150 and d["hr_zones"]["total_min"][0] == 600
    st = d["status"]
    assert st["hrv_status"]["status"] in ("balanced", "high", "low", "poor")
    assert st["training_status"]["status"] == "maintaining" and st["load_focus"]["groups"][0]["key"] == "low_aerobic"
    assert st["resilience"]["level"] and st["chronotype"]["mid_sleep"] == "05:40"
    assert st["body_battery"]["estimate"] and st["recovery_time"] is not None
    assert st["hr_recovery"] == {"bpm": 24.0, "date": str(today - timedelta(days=2)), "level": "good"}
    assert st["illness"]["level"] in ("low", "watch", "high")


def test_trends_end_date_and_ranges(client, world):
    ids, tok, today = world
    end = today - timedelta(days=40)
    d = client.get(f"/v1/users/me/trends?days=7&end={end}", headers=bearer(tok["A"])).json()
    assert d["end"] == str(end) and len(d["dates"]) == 7
    assert client.get("/v1/users/me/trends?days=365", headers=bearer(tok["A"])).status_code == 200
    assert client.get("/v1/users/me/trends?days=400", headers=bearer(tok["A"])).status_code == 422


def test_trends_scopes(client, world):
    ids, tok, today = world
    d = client.get(f"/v1/users/{ids['A']}/trends?days=30", headers=bearer(tok["B"])).json()
    assert "sleep_efficiency" in d["series"] and "recovery" not in d["series"] and "hrv" not in d["series"]
    assert "chronotype" in d["status"] and "training_status" not in d["status"] and "body_battery" not in d["status"]
    assert "授权" in d["unavailable"]["recovery"]
    assert client.get(f"/v1/users/{ids['A']}/trends", headers=bearer(tok["C"])).status_code in (403, 404)


def test_trend_explanations(client, world):
    ids, tok, today = world
    for key in ("hrv_status", "training_status", "resilience", "illness", "body_battery", "recovery_time", "chronotype",
                "resp_rate", "restorative", "aerobic_min", "behavior"):
        r = client.get(f"/v1/users/me/explain/metric/{key}", headers=bearer(tok["A"]))
        assert r.status_code == 200, (key, r.text)
        assert r.json()["what"]
    x = client.get("/v1/users/me/explain/metric/training_status", headers=bearer(tok["A"])).json()
    assert x["current"]["value"] == "保持"
    hidden = client.get(f"/v1/users/{ids['A']}/explain/metric/hrv_status", headers=bearer(tok["B"])).json()
    assert hidden["current"] is None and "metrics" in hidden["hidden"]


def test_baseline_band_window_slice_matches_the_full_scan():
    """series.baseline_band slices the 60-day window out of date-ordered rows (the trends tab's hottest loop); the answer
    is the old scan's, value for value (same values, same summing order), also for unordered input."""
    import random
    from body_engine.trends import series

    def scan(by_date, dates):
        mean, lo, hi = [], [], []
        for d in dates:
            xs = [v for k, v in by_date.items() if d - timedelta(days=series.BASELINE_DAYS) <= k < d and v is not None]
            if len(xs) < series.BASELINE_MIN_N:
                mean.append(None); lo.append(None); hi.append(None)
                continue
            m = sum(xs) / len(xs)
            sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
            mean.append(round(m, 2)); lo.append(round(m - sd, 2)); hi.append(round(m + sd, 2))
        return {"mean": mean, "lo": lo, "hi": hi}

    rnd = random.Random(3)
    base = date(2026, 1, 1)
    for trial in range(60):
        keys = [base + timedelta(days=i) for i in range(400) if rnd.random() < 0.8]
        if trial % 3 == 0:
            rnd.shuffle(keys)
        by_date = {k: None if rnd.random() < 0.05 else rnd.uniform(20, 90) for k in keys}
        dates = [base + timedelta(days=200 + i) for i in range(90)]
        assert series.baseline_band(by_date, dates) == scan(by_date, dates)
