"""Data for the Today L1 sheets (2.0.1): the strain sheet's day curve and forecast (GET /strain-coach `day_curve`,
`forecast`), the sleep sheet's nightly deep-sleep / regularity series (GET /sleep/night `series`).

Pure parts need no database. The rest uses its own test database BODY_TEST_DB_NAME_TODAY_SHEETS (default
body_test_today_sheets), recreated each run; skipped without Postgres. Synthetic data only.
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

from body_engine import api, auth, queries
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.scores import day_curve, engine, params as SP, strain as L
from body_engine.sleep_night import view as night_view
from body_engine.training import forecast, params as TP

from test_scores import make_inputs

TZ = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_TODAY_SHEETS", "body_test_today_sheets")


# ---------------------------------------------------------------- pure: the day curve
def _day_samples(d: date, bpm_by_hour: dict[int, float], every_s: int = 60):
    t0 = datetime.combine(d, datetime.min.time(), TZ)
    return [(t0 + timedelta(hours=h, seconds=k * every_s), bpm) for h, bpm in sorted(bpm_by_hour.items())
            for k in range(3600 // every_s)]


def test_cumulative_trimp_ends_at_the_days_trimp():
    d = date(2026, 9, 10)
    hr = _day_samples(d, {7: 110.0, 12: 120.0, 18: 165.0, 19: 140.0})
    minutes = L.sample_minutes([t for t, _ in hr])
    cum = day_curve.cumulative_trimp(hr, minutes, 190.0, TZ)
    trimp, _ = L.edwards_trimp(hr, minutes, 190.0)
    assert len(cum) == 24 and cum[-1] == trimp                    # same sum, same order: bit for bit
    assert cum[6] == 0 and cum[7] > 0 and cum == sorted(cum)      # nothing before 07:00, never decreasing
    assert cum[17] == cum[12] and cum[18] > cum[17]               # flat while nothing happens


def test_hourly_strain_ends_at_the_days_strain():
    d = date(2026, 9, 10)
    hr = _day_samples(d, {9: 150.0, 18: 170.0})
    minutes = L.sample_minutes([t for t, _ in hr])
    cum = day_curve.cumulative_trimp(hr, minutes, 190.0, TZ)
    s = round(L.strain(cum[-1]), 2)
    curve = day_curve.hourly_strain(cum, s)
    assert len(curve) == 24 and curve[-1] == s and curve == sorted(curve)
    assert curve[8] == 0 and 0 < curve[9] < s
    assert curve[9] == pytest.approx(L.strain(cum[9]), abs=0.011)   # without rounding drift the mapping is the identity
    assert day_curve.inverse_strain(s) == pytest.approx(cum[-1], rel=1e-3)
    # a day in progress (up to 10:59) and the edge cases
    assert day_curve.hourly_strain(cum, 7.5, hours=11)[-1] == 7.5 and len(day_curve.hourly_strain(cum, 7.5, hours=11)) == 11
    assert day_curve.hourly_strain([], 5.0) == [] and day_curve.hourly_strain(cum, 5.0, hours=0) == []
    assert day_curve.hourly_strain([0.0] * 24, 0.0) == [0.0] * 24
    assert day_curve.inverse_strain(SP.STRAIN_MAX) > 0 and math.isfinite(day_curve.inverse_strain(SP.STRAIN_MAX))


def test_attach_matches_the_scored_strain_of_every_day():
    inp = make_inputs(20)
    first, last = date(2026, 9, 1), date(2026, 9, 20)
    rows = engine.score_days(inp, first, last, today=date(2026, 10, 16))
    before = [dict(r) for r in rows]
    day_curve.attach(rows, inp.hr, inp.tz, datetime(2026, 10, 16, 9, 30, tzinfo=TZ))
    with_strain = [r for r in rows if r["strain"] is not None]
    assert with_strain
    for r in with_strain:
        curve = r["details"]["load"]["hourly_strain"]
        assert len(curve) == 24 and curve[-1] == r["strain"] and curve == sorted(curve)
        assert curve[17] < curve[18]   # the 18:00 training is the steep hour
    for r, b in zip(rows, before):     # nothing else of the row changes
        assert {k: v for k, v in r.items() if k != "details"} == {k: v for k, v in b.items() if k != "details"}


def test_attach_today_ends_now_and_skips_days_without_strain():
    d = date(2026, 9, 10)
    hr = _day_samples(d, {7: 90.0, 9: 160.0})
    rows = [{"date": d, "strain": 4.2, "hr_max": 190.0, "details": {"load": {"hr_samples": 1}}},
            {"date": d - timedelta(days=1), "strain": None, "hr_max": None, "details": {}},
            {"date": d + timedelta(days=1), "strain": 3.0, "hr_max": 190.0, "details": {}}]   # no samples that day
    day_curve.attach(rows, hr, TZ, datetime.combine(d, datetime.min.time(), TZ).replace(hour=10, minute=5))
    curve = rows[0]["details"]["load"]["hourly_strain"]
    assert len(curve) == 11 and curve[-1] == 4.2 and rows[0]["details"]["load"]["hr_samples"] == 1
    assert "load" not in rows[1]["details"] and "load" not in rows[2]["details"]
    day_curve.attach([], hr, TZ, datetime.now(TZ))   # nothing to do
    day_curve.attach(rows, [], TZ, datetime.now(TZ))


def test_cumulative_trimp_across_a_dst_change():
    d = date(2026, 4, 5)   # Sydney: 03:00 -> 02:00, a 25-hour day
    t0 = datetime.combine(d, datetime.min.time(), TZ)
    hr = [(t0 + timedelta(minutes=m), 150.0) for m in range(0, 25 * 60, 2)]
    minutes = L.sample_minutes([t for t, _ in hr])
    cum = day_curve.cumulative_trimp(hr, minutes, 190.0, TZ)
    assert len(cum) == 24 and cum[-1] == L.edwards_trimp(hr, minutes, 190.0)[0] and cum == sorted(cum)


# ---------------------------------------------------------------- pure: the forecast
def test_forecast_compute():
    d = date(2026, 11, 14)
    days = {d - timedelta(days=k): {"下肢b"} for k in (2, 9, 16, 23, 30)}
    days[d - timedelta(days=4)] = {"上肢a"}
    days[d] = {"下肢b"}   # today itself never counts
    strain = {x: 10.0 + k for k, x in enumerate(sorted(days, reverse=True))}
    strain[d - timedelta(days=16)] = None   # a day without strain is skipped, the next one counts
    out = forecast.compute(["下肢 B"], days, strain, d)
    assert out["n"] == TP.FORECAST_SESSIONS == out["of"] == 4
    assert out["dates"] == [d - timedelta(days=k) for k in (2, 9, 23, 30)] and out["sessions"] == ["下肢 B"]
    assert out["strain"] == round(sum(strain[x] for x in out["dates"]) / 4, 1)
    few = forecast.compute(["上肢 A"], days, strain, d)
    assert few["n"] == 1 and few["strain"] == strain[d - timedelta(days=4)]
    assert forecast.compute(["篮球"], days, strain, d) == {"sessions": ["篮球"], "strain": None, "n": 0, "of": 4, "dates": []}
    assert forecast.compute([], days, strain, d) is None
    assert forecast.norm(" 下肢 B ") == forecast.norm("下肢b")


# ---------------------------------------------------------------- pure: the sleep series
def _session(wake: date, bed_h: int, hours: float, deep, core, rem, watch=True):
    start = datetime.combine(wake - timedelta(days=1), datetime.min.time(), TZ) + timedelta(hours=bed_h)
    asleep = (deep or 0) + (core or 0) + (rem or 0) or round(hours * 60)
    return {"wake_date": wake, "start_at": start, "end_at": start + timedelta(hours=hours), "asleep_min": asleep,
            "deep_min": deep, "core_min": core, "rem_min": rem, "awake_min": 0, "unspecified_min": None if deep else asleep,
            "is_main": True, "source": "ow", "device": "Apple Watch" if watch else "iPhone"}


def test_series_compute_values_and_prev_mean():
    day = date(2026, 11, 14)
    rows = [_session(day - timedelta(days=k), 23, 8, 60 if k < 7 else 90, 300, 100) for k in range(0, 21) if k != 3]
    out = night_view.series_compute(rows, TZ, day, 7)
    assert out["nights"] == 7 and out["dates"] == [day - timedelta(days=k) for k in range(6, -1, -1)]
    deep = out["deep_min"]
    assert deep["values"] == [60, 60, 60, None, 60, 60, 60] and deep["n"] == 6 and deep["mean"] == 60
    assert deep["prev_mean"] == 90 and deep["prev_n"] == 7
    reg = out["regularity_pct_7d"]
    assert reg["values"][3] is None and reg["values"][-1] is not None and 0 <= reg["values"][-1] <= 100
    assert reg["prev_n"] == 7
    # the last value is the night page's own 7-night regularity
    from body_engine.insights import healthspan_store as HSS, sleep_detail
    watch = sleep_detail.watch_sessions(HSS.utc_sessions(rows))
    assert reg["values"][-1] == sleep_detail.consistency_pct(sleep_detail.sri_from(*watch, TZ, day - timedelta(days=6), day)["sri"])


def test_series_compute_empty_and_unstaged():
    day = date(2026, 11, 14)
    empty = night_view.series_compute([], TZ, day, 5)
    assert empty["deep_min"] == {"values": [None] * 5, "n": 0, "mean": None, "prev_mean": None, "prev_n": 0}
    assert empty["regularity_pct_7d"]["values"] == [None] * 5
    phone = [_session(day, 23, 7, None, None, None, watch=False)]   # a night without stages from a phone
    out = night_view.series_compute(phone, TZ, day, 1)
    assert out["deep_min"]["values"] == [None] and out["regularity_pct_7d"]["values"] == [None]


# ---------------------------------------------------------------- database: the endpoints
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


@pytest.fixture(scope="module")
def world(dsn):
    """A: scores for 40 days (strain; the last 3 days with a stored curve), a plan with 下肢 B on today's weekday, past
    logged sessions and a watch basketball workout, 40 nights of sleep. S: shares scores + sleep only (no training);
    T: shares scores + training; N: a new user with nothing."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, timezone) VALUES (%s, 'Australia/Sydney') RETURNING id",
                               (n,)).fetchone()["id"] for n in "ASTN"}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        a = ids["A"]
        for viewer, scopes in (("S", ["scores", "sleep"]), ("T", ["scores", "training"])):
            conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (a, ids[viewer], scopes))
        today = queries.local_today(conn, a)
        for k in range(1, 41):
            d = today - timedelta(days=k)
            details = {"load": {"hourly_strain": [0.0] * 18 + [6.0, 9.0, 9.5, 9.6, 9.7, 9.8]}} if k <= 3 else {}
            conn.execute("""INSERT INTO daily_scores (user_id, date, strain, details, flags, algo_version)
                            VALUES (%s, %s, %s, %s, '{}', 'test')""", (a, d, 9.8 if k <= 3 else 8.0 + k % 5, Jsonb(details)))
        conn.execute("""INSERT INTO daily_scores (user_id, date, strain, details, flags, algo_version)
                        VALUES (%s, %s, NULL, '{}', '{"no_raw_hr": "x"}', 'test')""", (a, today))
        dow = today.isoweekday()
        days = [{"day_of_week": dow, "name": "下肢 B", "exercises": []},
                {"day_of_week": dow % 7 + 1, "name": "上肢 A", "exercises": []}]
        conn.execute("""INSERT INTO training_plans (user_id, name, split_type, days_per_week, start_date, days)
                        VALUES (%s, '测试计划', 'custom', 2, %s, %s)""", (a, today - timedelta(days=60), Jsonb(days)))
        for k in (7, 14, 21, 28, 35):   # 下肢 B every week on this weekday; 21 days ago also via a joined name
            name = "下肢 B · 加练" if k == 21 else "下肢 B"
            conn.execute("INSERT INTO logged_workouts (user_id, date, name) VALUES (%s, %s, %s)",
                         (a, today - timedelta(days=k), name))
        conn.execute("INSERT INTO logged_workouts (user_id, date, name) VALUES (%s, %s, '上肢 A')", (a, today - timedelta(days=6)))
        start = datetime.combine(today - timedelta(days=5), datetime.min.time(), TZ).replace(hour=18)
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, start_at, end_at, local_date,
                                              duration_min)
                        VALUES (%s, 'ow', 'w1', 'basketball', '篮球', %s, %s, %s, 60)""",
                     (a, start, start + timedelta(hours=1), today - timedelta(days=5)))
        for k in range(0, 40):
            wake = today - timedelta(days=k)
            st = datetime.combine(wake - timedelta(days=1), datetime.min.time(), TZ) + timedelta(hours=23)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, deep_min, core_min,
                              rem_min, awake_min, is_main, source, device)
                            VALUES (%s, %s, %s, %s, 460, %s, 270, 100, 20, true, 'ow', 'Apple Watch')""",
                         (a, wake, st, st + timedelta(hours=8), 90 if k < 30 else 60))
        flags.set_flag(conn, "sleep_detail", "all", actor="test")
    flags.invalidate()
    return ids, tok, today


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def get(client, tok, path):
    return client.get(path, headers={"Authorization": f"Bearer {tok}"})


def test_strain_coach_day_curve(client, world):
    ids, tok, today = world
    d = today - timedelta(days=1)
    r = get(client, tok["A"], f"/v1/users/me/strain-coach?date={d}").json()
    c = r["day_curve"]
    assert c["step_min"] == 60 and len(c["values"]) == 24 and c["reason"] is None and c["computed_at"]
    assert c["values"][-1] == r["current_strain"] == 9.8
    assert {"target_low", "target_high", "progress", "status"} <= set(r)   # the old fields stay
    old = get(client, tok["A"], f"/v1/users/me/strain-coach?date={today - timedelta(days=10)}").json()["day_curve"]
    assert old == {"step_min": 60, "values": [], "reason": "not_computed", "computed_at": None}
    now = get(client, tok["A"], f"/v1/users/me/strain-coach?date={today}").json()["day_curve"]
    assert now["reason"] == "no_raw_hr" and now["values"] == []
    none = get(client, tok["N"], "/v1/users/me/strain-coach").json()
    assert none["day_curve"]["reason"] == "no_scores" and "forecast" not in none


def test_strain_coach_forecast(client, world, dsn):
    ids, tok, today = world
    r = get(client, tok["A"], f"/v1/users/me/strain-coach?date={today}").json()
    fc = r["forecast"]
    assert fc["sessions"] == ["下肢 B"] and fc["n"] == 4 and fc["of"] == TP.FORECAST_SESSIONS
    assert fc["dates"] == [(today - timedelta(days=k)).isoformat() for k in (7, 14, 21, 28)]
    assert fc["strain"] == round(sum(8.0 + k % 5 for k in (7, 14, 21, 28)) / 4, 1) and "hidden" not in r
    # a day change to a free activity: the watch basketball counts; rest: no forecast
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO training_day_overrides (user_id, date, sessions) VALUES (%s, %s, %s)",
                     (ids["A"], today, Jsonb([{"label": "篮球"}])))
        conn.execute("INSERT INTO training_day_overrides (user_id, date, sessions) VALUES (%s, %s, '[]')",
                     (ids["A"], today + timedelta(days=1)))
    try:
        fc = get(client, tok["A"], f"/v1/users/me/strain-coach?date={today}").json()["forecast"]
        assert fc["sessions"] == ["篮球"] and fc["n"] == 1 and fc["strain"] == 8.0 + 5 % 5
        assert "forecast" not in get(client, tok["A"], f"/v1/users/me/strain-coach?date={today + timedelta(days=1)}").json()
    finally:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("DELETE FROM training_day_overrides WHERE user_id = %s", (ids["A"],))
    # before the plan starts: nothing planned
    assert "forecast" not in get(client, tok["A"], f"/v1/users/me/strain-coach?date={today - timedelta(days=61)}").json()


def test_strain_coach_shares(client, world):
    ids, tok, today = world
    s = get(client, tok["S"], f"/v1/users/{ids['A']}/strain-coach?date={today}").json()
    assert "forecast" not in s and s["hidden"] == ["training"] and "day_curve" in s
    t = get(client, tok["T"], f"/v1/users/{ids['A']}/strain-coach?date={today}").json()
    assert t["forecast"]["n"] == 4 and "hidden" not in t
    assert get(client, tok["N"], f"/v1/users/{ids['A']}/strain-coach").status_code == 403


def test_strain_coach_ai_tool_unchanged(dsn, world):
    from body_engine.insights import views
    ids, _, today = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        out = views.strain_coach(conn, ids["A"], today)
    assert "day_curve" not in out and "forecast" not in out   # the page-only blocks never reach the model


def test_sleep_night_series(client, world):
    ids, tok, today = world
    r = get(client, tok["A"], f"/v1/users/me/sleep/night?date={today}").json()
    s = r["series"]
    assert s["nights"] == 30 and len(s["dates"]) == 30 and s["dates"][-1] == today.isoformat()
    assert s["deep_min"]["values"] == [90] * 30 and s["deep_min"]["mean"] == 90
    assert s["deep_min"]["prev_mean"] == 60 and s["deep_min"]["prev_n"] == 10   # only 10 nights before the 30
    assert s["regularity_pct_7d"]["values"][-1] == r["baseline"]["regularity_pct_7d"]
    short = get(client, tok["A"], f"/v1/users/me/sleep/night?date={today}&series_nights=7").json()["series"]
    assert short["nights"] == 7 and short["deep_min"]["prev_mean"] == 90
    assert "series" not in get(client, tok["A"], f"/v1/users/me/sleep/night?date={today}&series_nights=0").json()
    assert get(client, tok["A"], f"/v1/users/me/sleep/night?series_nights=91").status_code == 422
    shared = get(client, tok["S"], f"/v1/users/{ids['A']}/sleep/night?date={today}").json()
    assert shared["series"]["deep_min"]["values"] == s["deep_min"]["values"]
    assert get(client, tok["T"], f"/v1/users/{ids['A']}/sleep/night").status_code == 403   # no sleep share
    empty = get(client, tok["N"], "/v1/users/me/sleep/night").json()["series"]
    assert empty["deep_min"]["n"] == 0 and empty["regularity_pct_7d"]["prev_mean"] is None
