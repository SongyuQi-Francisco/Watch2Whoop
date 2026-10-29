"""2.0.0 package M4 (docs/ios-native-2.0.md section 4): the endpoints' answers are their pure parts on what they read, and
the phone's body-age loader gives the server's rows.

The made-up person of packages/openbodyscore-js/test/cases_coach.py (synthetic values) is stored for a user: score rows
with details, daily metrics, sleep sessions (naps, two mains, a phone-only night), watch workouts, the journal's strength
days, an active training plan, and — monkeypatched — the SparkyFitness strength log / weigh-ins, the imported (Hevy) minutes
and the goal weight of the case's `full` body-age parts. Then:
- insights/healthspan_store.compute (the server's loader + weekly) == weekly_local(the case's parts), the reference the JS
  bundle's healthspanLocal is golden-tested against, and == weekly_local(load_parts(…)) (the split);
- the strain coach, sleep coach, health monitor and the bedtime goal of the endpoints == their *_compute on the same rows;
- sleep_night.view.build: the pure main-sleep pick, the naps and the 7 / 30-night regularity equal the SQL they replaced;
- web/views.sleep_today: the pure night pick equals the old `ORDER BY wake_date DESC … LIMIT 1`;
- kind `healthspan` (nativesync/derived_kinds/healthspan.py) through PUT /v1/users/me/derived/app: off by default, the
  spot check passes in shadow, a changed body age is caught (names only), primary stores the weeks (origin 'app'),
  another user's rows are untouched; the `records` pull stream carries record kind `healthspan`.
Separate test database BODY_TEST_DB_NAME_NATIVE_COACH (default body_test_native_coach), recreated each run; skipped
without Postgres.
"""
import importlib.util
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth, queries, sparky_client
from body_engine.algo import versions
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.goals import store as goal_store
from body_engine.insights import coach, healthspan_store as HSS, sleep_detail
from body_engine.nativesync import compute, derived_kinds, params as P, primary
from body_engine.records import defaults
from body_engine.rollout import flags
from body_engine.sleep_night import view as night_view
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NATIVE_COACH", "body_test_native_coach")
REPO = Path(__file__).resolve().parents[2]
NEW_APP = {"X-App-Version": "2.0.0"}


def _cases():
    path = REPO / "packages" / "openbodyscore-js" / "test" / "cases_coach.py"
    spec = importlib.util.spec_from_file_location("openbodyscore_js_cases_coach_db", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


CC = _cases()
PERSON = CC.person()
PARTS = CC.healthspan_parts(PERSON, "full")
SESSIONS = CC.sessions(PERSON)
TZ = ZoneInfo(CC.TZ_NAME)
END = CC.END


def _ln(d: date, recovery) -> float | None:
    return None if recovery is None else round(math.log(40 + (d.toordinal() * 7) % 30), 4)


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


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def set_flag(dsn, key, state, cohort=None):
    with conn_of(dsn) as c:
        flags.set_flag(c, key, state, cohort=cohort, actor="test")
        if key == P.COMPUTE_PRIMARY_FLAG:   # 2.0.0 S9: primary also needs a grant per package (nativesync/primary.py)
            c.execute("DELETE FROM app_compute_primary")
            for pkg in P.COMPUTE_PACKAGES if state != "off" else ():
                primary.set_primary(c, pkg, cohort=cohort if state == "cohort" else None, everyone=state != "cohort",
                                    force=True, actor="test")
        c.commit()
    flags.invalidate()


@pytest.fixture(scope="module")
def users(dsn):
    with conn_of(dsn) as conn:
        def add(name, birth, sex, height):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, birth_date, sex, height_cm, consent_at,
                                                      consent_version)
                                   VALUES (%s, 'member', %s, %s, %s, %s, now(), '2026-10-22.2') RETURNING id""",
                                (name, CC.TZ_NAME, birth, sex, height)).fetchone()["id"]
        ids = {"a": add("a", PARTS["birth"], PARTS["sex"], PARTS["height"]), "b": add("b", PARTS["birth"], "female", 170)}
        flags.set_flag(conn, P.FLAG, "cohort", cohort="native", actor="test")
        for n in ids:
            flags.cohort_add(conn, ids[n], "native", actor="test")
        conn.commit()
        flags.invalidate()
        tok = {n: auth.create_token(conn, i, "app", ["read", "write"]) for n, i in ids.items()}
        conn.commit()
    return ids, tok


@pytest.fixture(scope="module")
def stored(dsn, users):
    """User a's history as the server holds it; SparkyFitness, the imported minutes and the goal weight patched in."""
    ids, _ = users
    uid = ids["a"]
    mp = pytest.MonkeyPatch()
    mp.setattr(sparky_client, "reader_for_user", lambda conn, user_id: object())
    mp.setattr(sparky_client, "training_log", lambda client, start, end: [
        s for s in PARTS["sparky_sessions"] if start <= date.fromisoformat(s["date"][:10]) <= end])
    mp.setattr(sparky_client, "weight", lambda client, start, end: [
        w for w in PARTS["sparky_weights"] if start <= date.fromisoformat(w["date"]) <= end])
    mp.setattr(HSS, "imported_strength_days", lambda conn, user_id, start, end: {
        d: m for d, m in PARTS["imported"].items() if start <= d <= end} if user_id == uid else {})
    from body_engine.profile import store as profile
    real_get = profile.get
    mp.setattr(profile, "get", lambda conn, user_id: {**real_get(conn, user_id), "goal_weight_kg": PARTS["goal_kg"]}
               if user_id == uid else real_get(conn, user_id))
    with conn_of(dsn) as c:
        with c.cursor() as cur:
            rows = []
            for r in PERSON["scores"]:
                d = r["date"]
                rows.append((uid, d, r["recovery"], r["sleep_score"], r["sleep_performance"],
                             None if r["sleep_actual_min"] is None else round(r["sleep_actual_min"]),
                             round(r["sleep_need_min"]), r["sleep_debt_min"], _ln(d, r["recovery"]),
                             50.0 + (d.toordinal() * 3) % 9, r["resp_rate_night"], r["strain"], r["trimp"],
                             r["muscle_load"], r["ctl"], r["atl"], r["tsb"], Jsonb(r["flags"]), Jsonb(r["details"])))
            cur.executemany("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_performance,
                                 sleep_actual_min, sleep_need_min, sleep_debt_min, hrv_night_ln, rhr_night, resp_rate_night,
                                 strain, trimp, muscle_load, ctl, atl, tsb, flags, details, algo_version)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'test')""", rows)
            cur.executemany("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                               VALUES (%s, %s, %s, %s, '', 'avg', 1, 'app')""",
                            [(uid, d, m, v) for m, xs in PARTS["metrics"].items() for d, v in xs.items()])
            cur.executemany("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, deep_min, core_min,
                                 rem_min, awake_min, unspecified_min, is_main, source, device)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            [(uid, s["wake_date"], s["start_at"], s["end_at"], s["asleep_min"], s["deep_min"], s["core_min"],
                              s["rem_min"], s["awake_min"], s["unspecified_min"], s["is_main"], s["source"], s["device"])
                             for s in SESSIONS])
            cur.executemany("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                                 duration_min, hr_zone_min) VALUES (%s, 'app', %s, %s, '示例', %s, %s, %s, %s, %s)""",
                            [(uid, f"w{k}", w["type"], w["local_date"], CC.at(w["local_date"], 18.0),
                              CC.at(w["local_date"], 18.0) + timedelta(minutes=w["duration_min"]), w["duration_min"],
                              w["hr_zone_min"]) for k, w in enumerate(PARTS["workouts"])])
        defaults.init_journal_items(c, uid)
        for d in PARTS["journal"]:
            c.execute("INSERT INTO journal_entries (user_id, date, key, value, source) VALUES (%s, %s, 'training_legs', 1, 'ai')",
                      (uid, d))
        plan = PARTS["plan"]
        c.execute("""INSERT INTO training_plans (user_id, name, split_type, days_per_week, start_date, days)
                     VALUES (%s, '示例计划', '4_day_upper_lower', %s, %s, %s)""",
                  (uid, plan["days_per_week"], plan["start_date"], Jsonb([])))
        c.commit()
    yield uid
    mp.undo()


# ---------------------------------------------------------------- body age: the server's loader == the phone's
def test_server_body_age_equals_the_phones(dsn, stored):
    want = HSS.weekly_local(PARTS, END)
    with conn_of(dsn) as c:
        got = HSS.compute(c, stored, today=END)
        parts = HSS.load_parts(c, stored, PARTS["origin"] - timedelta(days=1), END)
    assert parts["origin"] == PARTS["origin"]
    assert HSS.weekly_local(parts, END) == got
    assert [r["week_start"] for r in got] == [r["week_start"] for r in want]
    assert got == want
    assert got[-1]["body_age"] is not None and any(i["key"] == "activity" for i in got[-1]["contributors"])


# ---------------------------------------------------------------- the coaches and the goal: endpoint == compute(rows)
def test_coach_endpoints_are_their_pure_parts(dsn, stored):
    rows = {r["date"]: r for r in PERSON["scores"]}
    with conn_of(dsn) as c:
        for k in range(0, 80, 9):
            d = END - timedelta(days=k)
            pick = lambda r: None if r is None else {x: r[x] for x in ("recovery", "strain", "tsb", "flags")}  # noqa: E731
            assert coach.strain_coach(c, stored, d) == coach.strain_coach_compute(
                pick(rows.get(d)), pick(rows.get(d - timedelta(days=1))), d)
            want = CC._coach_rows(PERSON, d)
            for r in want:
                if r["sleep_actual_min"] is not None:
                    r["sleep_actual_min"] = round(r["sleep_actual_min"])
            assert coach.sleep_coach(c, stored, d, TZ, goal=None) == coach.sleep_coach_compute(want, d, TZ, None)
            mon = c.execute("""SELECT s.date, s.rhr_night, s.hrv_night_ln, s.resp_rate_night, s.details->'recovery' AS rec,
                                      s.flags ? 'rhr_from_daily' AS rhr_daily, m.value AS apple_rhr
                               FROM daily_scores s LEFT JOIN daily_metrics m ON m.user_id = s.user_id AND m.date = s.date
                                    AND m.metric = 'resting_heart_rate'
                               WHERE s.user_id = %s AND s.date BETWEEN %s AND %s ORDER BY s.date""",
                            (stored, d - timedelta(days=coach.MONITOR_DAYS), d)).fetchall()
            got = coach.health_monitor(c, stored, d)
            assert got == coach.health_monitor_compute(mon, d)
            assert any(m["low"] is not None for m in got["metrics"])


def test_sleep_goal_view_is_its_pure_part(dsn, stored):
    goal = {"target_bedtime": "22:30", "target_wake": None, "target_duration_min": None, "pace_min_per_step": 20,
            "step_days": 3, "start_date": END - timedelta(days=10), "start_bedtime_off": 30, "status": "active"}
    with conn_of(dsn) as c:
        c.execute("""INSERT INTO sleep_goals (user_id, target_bedtime, pace_min_per_step, step_days, start_date,
                                              start_bedtime_off, status) VALUES (%s, '22:30', 20, 3, %s, 30, 'active')""",
                  (stored, goal["start_date"]))
        for night in (END, END - timedelta(days=16), CC.DST_DAY - timedelta(days=1)):
            got = goal_store.view(c, stored, night)
            ss = [s for s in SESSIONS if night - timedelta(days=21) <= s["wake_date"] <= night + timedelta(days=1)]
            want = goal_store.view_compute(goal_store.with_plan(goal), ss, TZ, night, goal_store.latest_need(c, stored, night))
            assert got == want, night
            assert got["progress"]["nights_7d"] and (got["tonight_target_bedtime"] or night < goal["start_date"])
        c.rollback()


# ---------------------------------------------------------------- the sleep detail and Today's sleep: the pure picks == SQL
def test_sleep_night_pure_parts_equal_the_sql_they_replaced(dsn, stored):
    with conn_of(dsn) as c:
        for day in (END, END - timedelta(days=1), CC.DST_DAY, END - timedelta(days=95)):
            data = night_view.load(c, stored, stored, day)
            lo = day - timedelta(days=30)
            mains = views.main_rows(data["sessions"], lo, day)
            old = night_view._mains(c, stored, lo, day)
            assert {d: (r["start_at"], r["asleep_min"]) for d, r in mains.items()} == \
                   {d: (r["start_at"], r["asleep_min"]) for d, r in old.items()}
            out = night_view.compute(data)
            assert out == night_view.build(c, stored, stored, day)
            naps = c.execute("""SELECT start_at, end_at, asleep_min FROM sleep_sessions WHERE user_id = %s AND wake_date = %s
                                AND NOT is_main ORDER BY start_at""", (stored, day)).fetchall()
            assert [n["asleep_min"] for n in out["naps"]] == [n["asleep_min"] for n in naps]
            # sri_for on the same rows, in UTC instants (sleep_detail converts every interval itself now: the rows come back
            # in the user's zone, and wall-time arithmetic was an hour off in a window holding the DST night)
            rows = HSS.utc_sessions(c.execute(
                """SELECT wake_date, start_at, end_at, asleep_min, awake_min, deep_min, rem_min, core_min, is_main, device
                   FROM sleep_sessions WHERE user_id = %s AND wake_date BETWEEN %s AND %s ORDER BY start_at""",
                (stored, lo, day)).fetchall())
            for n, k in ((7, "regularity_pct_7d"), (30, "regularity_pct_30d")):
                sri = sleep_detail.sri_from(*sleep_detail.watch_sessions(rows), TZ, day - timedelta(days=n - 1), day)["sri"]
                assert out["baseline"][k] == sleep_detail.consistency_pct(sri), (day, k)
                old_sri = sleep_detail.sri_for(c, stored, TZ, day - timedelta(days=n - 1), day)["sri"]   # also across the DST night
                assert out["baseline"][k] == sleep_detail.consistency_pct(old_sri), (day, k)
        for day in (END, CC.DST_DAY, END - timedelta(days=40)):
            old = c.execute("""SELECT wake_date, start_at FROM sleep_sessions WHERE user_id = %s AND is_main
                               AND wake_date BETWEEN %s AND %s ORDER BY wake_date DESC, asleep_min DESC NULLS LAST LIMIT 1""",
                            (stored, day - timedelta(days=views.LATEST_LOOKBACK_DAYS), day)).fetchone()
            got = views.sleep_today(c, stored, day)
            assert got["date"] == old["wake_date"] and got["main"]["start_at"] == old["start_at"].astimezone(TZ).isoformat()


# ---------------------------------------------------------------- kind `healthspan`
def test_kind_registered_and_package_implemented():
    mod = derived_kinds.get("healthspan")
    assert mod.ALGO_KEY == "coach" and mod.PACKAGE == "coach" and mod.RECORD_KIND == "healthspan"
    assert versions.current("coach") == coach.COACH_VERSION
    assert compute.package_implemented("coach")


def _phone_rows() -> dict:
    """What the phone uploads: healthspanLocal's rows by week (dates as strings, stamped like the bundle)."""
    out = {}
    for r in HSS.weekly_local(PARTS, END):
        row = json.loads(json.dumps({k: v for k, v in r.items() if k not in ("week_start", "notes")}, default=str,
                                    ensure_ascii=False))
        out[r["week_start"].isoformat()] = [{**row, "algo_version": coach.COACH_VERSION, "bundle_version": "0.3.0-test"}]
    return out


def _upload(rows_by_day: dict, version: str = "0.3.0-test") -> dict:
    return {"kind": "healthspan", "algo_version": coach.COACH_VERSION, "bundle_version": version,
            "days": [{"date": d, "rows": rows} for d, rows in sorted(rows_by_day.items(), reverse=True)]}


def test_upload_shadow_check_then_primary(client, users, dsn, stored, monkeypatch):
    ids, tok = users
    uid = stored
    today = datetime.now(TZ).date()
    monkeypatch.setattr(P, "CHECK_WITHIN_DAYS", (today - END).days + 9)
    rows = _phone_rows()
    put = lambda body, t="a": client.put("/v1/users/me/derived/app", json=body, headers={  # noqa: E731
        "Authorization": f"Bearer {tok[t]}", **NEW_APP})
    assert put(_upload(rows)).status_code == 409, "native_coach is off by default"
    set_flag(dsn, "native_coach", "cohort", cohort="native")
    try:
        r = put(_upload(rows)).json()
        assert r["state"] == "shadow" and r["stored"] == 0
        assert r["check"]["compared"] and r["check"]["ok"], r["check"]
        last = max(rows)
        assert r["check"]["date"] == last
        # a body age 0.3 years off is caught (names only); a contributor's own numbers are not compared
        bad = {**rows, last: [dict(rows[last][0], body_age=rows[last][0]["body_age"] + 0.3)]}
        r = put(_upload(bad, "0.3.1-test")).json()
        assert r["status"] == "mismatch" and r["check"]["fields_over"] == ["body_age"], r["check"]
        near = {**rows, last: [dict(rows[last][0], contributors=[dict(c, value=0) for c in rows[last][0]["contributors"]])]}
        assert put(_upload(near, "0.3.2-test")).json()["check"]["ok"]
        gone = {**rows, last: [dict(rows[last][0], contributors=rows[last][0]["contributors"][1:])]}
        assert put(_upload(gone, "0.3.3-test")).json()["check"]["fields_over"] == ["contributors"]
        # malformed rows are refused (not a Monday, as_of outside the week, an unknown key), another algorithm is stale
        mon = date.fromisoformat(last)
        assert put(_upload({(mon + timedelta(days=1)).isoformat(): rows[last]})).status_code == 422
        assert put(_upload({last: [dict(rows[last][0], as_of=(mon - timedelta(days=1)).isoformat())]})).status_code == 422
        assert put(_upload({last: [dict(rows[last][0], insufficient=[{"key": "x"}])]})).status_code == 422
        assert put({**_upload(rows), "algo_version": "coach-0"}).json()["status"] == "stale_algo"
        # primary: the weeks are the user's (origin app), b's untouched
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "cohort", cohort="native")
        body = _upload(rows, "0.3.4-test")
        assert put(body).json()["state_after"] == "primary"
        got = put(body).json()
        assert got["state"] == "primary" and got["stored"] == len(rows), got
        with conn_of(dsn) as c:
            n = c.execute("""SELECT count(*) AS n, bool_and(bundle_version = '0.3.4-test') AS v FROM healthspan_weekly
                             WHERE user_id = %s AND origin = 'app'""", (uid,)).fetchone()
            assert n["n"] == len(rows) and n["v"]
            one = c.execute("SELECT * FROM healthspan_weekly WHERE user_id = %s AND week_start = %s", (uid, mon)).fetchone()
            assert one["body_age"] == rows[last][0]["body_age"] and one["algo_version"] == HSS.CURRENT.ALGO_VERSION
            assert c.execute("SELECT count(*) AS n FROM healthspan_weekly WHERE user_id = %s", (ids["b"],)).fetchone()["n"] == 0
            assert HSS.read(c, uid)["body_age"] == rows[last][0]["body_age"]
    finally:
        set_flag(dsn, "native_coach", "off")
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "off")


def test_server_write_keeps_unchanged_weeks_out_of_the_change_log(dsn, stored):
    with conn_of(dsn) as c:
        rows = HSS.compute(c, stored, today=END)
        HSS.write(c, stored, rows)
        c.commit()
        n0 = c.execute("SELECT count(*) AS n FROM sync_changes WHERE user_id = %s AND kind = 'healthspan'", (stored,)).fetchone()["n"]
        HSS.write(c, stored, rows)
        c.commit()
        n1 = c.execute("SELECT count(*) AS n FROM sync_changes WHERE user_id = %s AND kind = 'healthspan'", (stored,)).fetchone()["n"]
        assert n1 == n0, "an unchanged week is no change"
        assert c.execute("SELECT count(*) AS n FROM healthspan_weekly WHERE user_id = %s", (stored,)).fetchone()["n"] == len(rows)


def test_pull_records_carry_healthspan(client, users, dsn, stored):
    ids, tok = users
    with conn_of(dsn) as c:
        HSS.write(c, stored, HSS.compute(c, stored, today=END))
        c.commit()
    r = client.post("/v1/sync/pull", json={"cursors": {"records": ""}, "limit": 2000},
                    headers={"Authorization": f"Bearer {tok['a']}", **NEW_APP})
    assert r.status_code == 200, r.text
    got = {x["date"]: x["body"] for x in r.json()["changes"] if x["stream"] == "records" and x["kind"] == "healthspan" and x["body"]}
    week = (END - timedelta(days=END.weekday())).isoformat()
    assert week in got, sorted(got)[-3:]
    item = got[week]["items"][0]
    assert {"body_age", "pace_of_aging", "contributors", "insufficient"} <= set(item) and "user_id" not in item
    rb = client.post("/v1/sync/pull", json={"cursors": {"records": ""}},
                     headers={"Authorization": f"Bearer {tok['b']}", **NEW_APP}).json()
    assert not [x for x in rb["changes"] if x["kind"] == "healthspan" and x["body"]]


def test_today_and_endpoints_still_answer(client, users, stored):
    ids, tok = users
    h = {"Authorization": f"Bearer {tok['a']}"}
    for path, params in (("/strain-coach", {"date": END.isoformat()}), ("/sleep-coach", {"date": END.isoformat()}),
                         ("/health-monitor", {"date": END.isoformat()})):
        r = client.get(f"/v1/users/{ids['a']}{path}", params=params, headers=h)
        assert r.status_code == 200, (path, r.text)
    assert client.get(f"/v1/users/{ids['a']}/strain-coach", headers={"Authorization": f"Bearer {tok['b']}"}).status_code in (403, 404)


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None
