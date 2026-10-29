"""2.0.0 package M6 through the database (docs/ios-native-2.0.md section 4): rules, nutrition goals and explain texts.

- rules: the synthetic person of packages/openbodyscore-js/test/cases_rules.py is stored in the tables the rules read
  (exercise_meta, daily_scores, journal_entries, sleep_goals, sleep_sessions, body mass, diet_constraints,
  nutrition_goal_versions, training_day_overrides) with a fake own-store reader for the SparkyFitness library, templates,
  plan, training log, food and weigh-ins. evaluate_ctx(load(...)) (what evaluate-rules stores) must equal
  evaluate_data(assemble(load_raw(...))) (the phone's path: raw sources -> data -> the same rules), and to_data(load(...))
  must evaluate the same;
- the kind `reminders` (nativesync/derived_kinds/reminders.py) through PUT /v1/users/me/derived/app: off by default, the
  spot check passes in shadow, a changed text is caught (names only), primary stores the rows through engine.store (origin
  'app'), a status the user set on the phone moves forward only, another user's rows are untouched; the `records` pull
  stream carries record kind `reminders`; GET /v1/users/me/rules/inputs (flag native_rules, own data only);
- explain: every calc_* on the tables equals metric_out(metric_data(...)) (the phone's twin), on M3's synthetic person
  (cases_series.py), and personal.bedtime_items equals assistant/status.calc_bedtime.
Separate test database BODY_TEST_DB_NAME_NATIVE_RULES (default body_test_native_rules), recreated each run; skipped without
Postgres.
"""
import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.algo import versions
from body_engine.assistant import status as status_mod
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.explain import glossary as GL, personal
from body_engine.nativesync import compute, derived_kinds, params as P
from body_engine.nativesync.derived_kinds import reminders as RK
from body_engine.rollout import flags
from body_engine.rules import context, engine, params as RP

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NATIVE_RULES", "body_test_native_rules")
REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
NEW_APP = {"X-App-Version": "2.0.0"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases_rules_db", JS_TESTS / "cases_rules.py")
CS = _load("openbodyscore_js_cases_series_db", JS_TESTS / "cases_series.py")
TZ = ZoneInfo(C.TZ_NAME)
FRI = date.fromisoformat(C.FRI)
DAYS = (FRI, FRI + timedelta(days=1), FRI + timedelta(days=2), FRI - timedelta(days=3))


class Reader:
    """The own-store reader shape (sparky_client._own): library, templates, plan, training log, weigh-ins, food, goals."""
    own = True

    def __init__(self):
        presets = C.presets(banned=True)
        self.d = {
            "exercises": [{"id": k, **v, "source_id": None} for k, v in C.EX.items()],
            "presets": {p["id"]: p for p in presets}, "plans": [C.plan()],
            "sessions": [C.sess("2026-10-10", ("pull", C.S(4, 12, 39))), C.sess("2026-10-13", ("upright", C.S(3, 10, 20))),
                         C.sess("2026-10-14", ("face", [(15, 18, None), (12, 22.5, None)]), ("hip", C.S(4, 9, 40))),
                         C.sess("2026-10-17", ("ball", [(1, None, None)]), name="篮球")],
            "weights": C.weights("2026-09-26", 23, 91.0, -0.16, 2),
            "food": [dict(f, id=i + 1) for i, f in enumerate(
                [C.food("2026-10-11", "牛肋条", meal="dinner"), C.food("2026-10-13", "牛肉面", meal="lunch"),
                 C.food("2026-10-14", "培根三明治", meal="breakfast"), C.food("2026-10-15", "奶茶", carbs=55.0, sugar_g=45.0),
                 C.food("2026-10-16", "燕麦", carbs=20.0, meal="breakfast"),
                 C.food("2026-10-16", "可乐", carbs=35.0, sugar_g=35.0),
                 C.food("2026-10-14", "橙汁", carbs=26.0, sugar_g=22.0), C.food("2026-10-18", "米饭鸡腿", carbs=90.0)])],
        }
        for f in self.d["food"]:
            for k in ("sugary_drink", "added_sugar_g"):
                f.pop(k, None)

    def exercises(self):
        return self.d["exercises"]

    def workout_presets(self):
        return self.d["presets"]

    def workout_plans(self):
        return self.d["plans"]

    def training_log(self, start, end):
        return [s for s in self.d["sessions"] if start.isoformat() <= s["date"][:10] <= end.isoformat()]

    def weight(self, start, end):
        return [w for w in self.d["weights"] if start.isoformat() <= w["date"] <= end.isoformat()]

    def food_entries(self, start, end):
        return [dict(f) for f in self.d["food"] if start.isoformat() <= f["date"] <= end.isoformat()]

    def goal_for_date(self, day):
        return {"date": day.isoformat(), "name": "默认", "calories": 2200, "protein_g": 140, "carbs_g": 250, "fat_g": 70}


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
        c.commit()
    flags.invalidate()


@pytest.fixture(scope="module")
def users(dsn):
    with conn_of(dsn) as conn:
        def add(name, birth=None, sex=None):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, birth_date, sex, consent_at, consent_version)
                                   VALUES (%s, 'member', %s, %s, %s, now(), '2026-10-22.2') RETURNING id""",
                                (name, C.TZ_NAME, birth, sex)).fetchone()["id"]
        ids = {"a": add("a"), "b": add("b"), "c": add("c", date(1990, 5, 1), "male")}
        flags.set_flag(conn, P.FLAG, "cohort", cohort="native", actor="test")
        for n in ids:
            flags.cohort_add(conn, ids[n], "native", actor="test")
        conn.commit()
        flags.invalidate()
        tok = {n: auth.create_token(conn, i, "app", ["read", "write"]) for n, i in ids.items()}
        conn.commit()
    return ids, tok


@pytest.fixture(scope="module")
def reader():
    r = Reader()
    mp = pytest.MonkeyPatch()
    mp.setattr(sparky_client, "reader_for_user", lambda conn, user_id: r)
    yield r
    mp.undo()


@pytest.fixture(scope="module")
def stored(dsn, users, reader):
    """User a's rule inputs as the server holds them."""
    ids, _ = users
    uid = ids["a"]
    with conn_of(dsn) as c:
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO exercise_meta (user_id, exercise_ref, status, aliases, reason, cues, app_exercise_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            [(uid, m["exercise_ref"], m["status"], m["aliases"], m["reason"], m["cues"], m["app_exercise_id"])
                             for m in C.META])
            for k in range(0, 12):
                d = FRI + timedelta(days=2) - timedelta(days=k)
                flags_ = {"illness_warning": "静息心率比基线高 6、HRV 低于基线 80%"} if k == 2 else {}
                cur.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_debt_min, sleep_actual_min,
                                 sleep_need_min, strain, flags, algo_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'test')""",
                            (uid, d, 22 + 9 * k if k != 5 else None, 70 + k, 60 + 40 * (k % 4), 420 - k, 470, 8.5 + k / 3,
                             Jsonb(flags_)))
            cur.executemany("""INSERT INTO journal_items (user_id, key, label_zh, kind) VALUES (%s, %s, %s, %s)
                               ON CONFLICT DO NOTHING""",
                            [(uid, "low_blood_sugar", "低血糖感", "bool"), (uid, "water_l", "喝水", "number"),
                             (uid, "red_meat_g", "红肉", "number")])
            cur.executemany("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, %s, %s)",
                            [(uid, FRI - timedelta(days=4), "low_blood_sugar", 1), (uid, FRI - timedelta(days=1), "water_l", 2.2),
                             (uid, FRI - timedelta(days=2), "red_meat_g", 90)])
            g = C.GOAL_ROW
            cur.execute("""INSERT INTO sleep_goals (user_id, target_bedtime, target_wake, target_duration_min, pace_min_per_step,
                             step_days, start_date, start_bedtime_off, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (uid, g["target_bedtime"], g["target_wake"], g["target_duration_min"], g["pace_min_per_step"],
                         g["step_days"], g["start_date"], g["start_bedtime_off"], g["status"]))
            cur.executemany("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                               VALUES (%s, %s, %s, %s, %s, %s, 'app')""",
                            [(uid, s["wake_date"], s["start_at"], s["end_at"], s["asleep_min"], s["is_main"])
                             for s in C._sessions("2026-10-19", 30)])
            cur.executemany("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                               VALUES (%s, %s, 'body_mass', %s, 'kg', 'avg', 'app')""",
                            [(uid, FRI - timedelta(days=k), 89.0 + k * 0.07) for k in range(0, 25, 2)])
            cur.executemany("""INSERT INTO diet_constraints (user_id, kind, target, match, value, basis, effective_from, source)
                               VALUES (%s, %s, %s, %s, %s, %s, '2026-09-01', 'user')""",
                            [(uid, r["kind"], r["target"], r["match"] or [], r["value"], r["basis"]) for r in C.DIET])
            for v in reversed(C.VERSIONS):
                cur.execute("""INSERT INTO nutrition_goal_versions (user_id, effective_from, training, rest, training_weekdays, name,
                                 reason, set_by) VALUES (%s, %s, %s, %s, %s, %s, 'test', 'cli')""",
                            (uid, v["effective_from"], Jsonb(v["training"]), Jsonb(v["rest"]), v["training_weekdays"], v["name"]))
            cur.execute("INSERT INTO training_day_overrides (user_id, date, sessions) VALUES (%s, %s, %s)",
                        (uid, FRI + timedelta(days=2), Jsonb([{"dow": 5}, {"label": "游泳"}])))
        c.commit()
    return uid


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def test_load_equals_the_phones_path(dsn, stored, reader):
    """evaluate-rules' results == the phone's raw sources -> assemble -> evaluate_data, on several days."""
    with conn_of(dsn) as c:
        fired = set()
        for day in DAYS:
            ctx = context.load(c, stored, day, reader)
            old = engine.evaluate_ctx(ctx)
            new = engine.evaluate_data(json.loads(json.dumps(context.assemble(context.load_raw(c, stored, day, reader)))))
            assert _same([r.summary() for r in old], new["rules"]), day
            assert _same(engine.reminder_rows(old), new["reminders"]), day
            assert _same(engine.evaluate_data(json.loads(json.dumps(context.to_data(ctx)))), new), day
            fired |= {r["rule_key"] for r in new["reminders"]}
        assert fired >= {"progression", "calorie_adjust", "pre_workout_fuel", "frequency_caps", "sugar_caps",
                         "exercise_restrictions", "readiness", "sleep_goal"}, fired
        raw = context.load_raw(c, stored, FRI, reader)
        assert raw["nutrition_versions"] and raw["sparky_goals"] == {} and raw["overrides"]


def test_kind_registered_and_package_implemented():
    assert derived_kinds.get("reminders") is RK and RK.ALGO_KEY == "rules" and RK.PACKAGE == "rules"
    assert versions.current("rules") == RP.RULES_VERSION and versions.current("explain") == personal.EXPLAIN_VERSION
    assert compute.package_implemented("rules")


def _phone_rows(conn, uid: int, day: date, now: datetime) -> list[dict]:
    result = engine.evaluate_data(json.loads(json.dumps(context.assemble(context.load_raw(conn, uid, day, None, now)))))
    return [dict(r, status=None) if r["role"] == "reminder" else r for r in RK.rows_of(result, now.isoformat())]


def _upload(rows_by_day: dict, version: str = "0.3.0-test") -> dict:
    return {"kind": "reminders", "algo_version": RP.RULES_VERSION, "bundle_version": version,
            "days": [{"date": d, "rows": rows} for d, rows in sorted(rows_by_day.items(), reverse=True)]}


def test_upload_shadow_check_then_primary(client, users, dsn, stored, monkeypatch):
    ids, tok = users
    uid = stored
    monkeypatch.setattr(P, "CHECK_WITHIN_DAYS", (datetime.now(TZ).date() - FRI).days + 2)
    now = datetime(2026, 10, 16, 23, 59, tzinfo=TZ)
    with conn_of(dsn) as c:
        rows = {FRI.isoformat(): _phone_rows(c, uid, FRI, now)}
    day = FRI.isoformat()
    assert any(r["role"] == "reminder" for r in rows[day])
    put = lambda body, t="a": client.put("/v1/users/me/derived/app", json=body, headers={  # noqa: E731
        "Authorization": f"Bearer {tok[t]}", **NEW_APP})
    assert put(_upload(rows)).status_code == 409, "native_rules is off by default"
    set_flag(dsn, "native_rules", "cohort", cohort="native")
    try:
        r = put(_upload(rows)).json()
        assert r["state"] == "shadow" and r["stored"] == 0
        assert r["check"]["compared"] and r["check"]["ok"], r["check"]
        # a changed text and a changed level are caught by name; the phone's own status is not compared
        rem = next(i for i, x in enumerate(rows[day]) if x["role"] == "reminder")
        bad = [dict(x, title_en=x["title_en"] + "!", level="alert") if i == rem else x for i, x in enumerate(rows[day])]
        r = put(_upload({day: bad}, "0.3.1-test")).json()
        assert r["status"] == "mismatch" and r["check"]["fields_over"] == ["level", "title_en"], r["check"]
        seen = [dict(x, status="seen") if x["role"] == "reminder" else x for x in rows[day]]
        assert put(_upload({day: seen}, "0.3.2-test")).json()["check"]["ok"]
        missing = [x for i, x in enumerate(rows[day]) if i != rem]
        assert put(_upload({day: missing}, "0.3.3-test")).json()["check"]["fields_over"] == ["rows"]
        # malformed rows are refused, another algorithm is stale
        assert put(_upload({day: [x for x in rows[day] if x["role"] == "reminder"]})).status_code == 422
        assert put(_upload({day: rows[day] + [rows[day][rem]]})).status_code == 422
        assert put(_upload({day: [dict(x, level="urgent") if i == rem else x for i, x in enumerate(rows[day])]})).status_code == 422
        assert put({**_upload(rows), "algo_version": "v0"}).json()["status"] == "stale_algo"
        # primary: engine.store with the phone's rows (origin app); the phone's dismissal moves forward only
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "cohort", cohort="native")
        assert put(_upload(rows, "0.3.4-test")).json()["state_after"] == "primary"
        dismissed = [dict(x, status="dismissed") if i == rem else x for i, x in enumerate(rows[day])]
        got = put(_upload({day: dismissed}, "0.3.4-test")).json()
        assert got["state"] == "primary" and got["stored"] == sum(x["role"] == "reminder" for x in rows[day]), got
        target = rows[day][rem]
        with conn_of(dsn) as c:
            all_ = c.execute("SELECT * FROM reminders WHERE user_id = %s AND date = %s ORDER BY rule_key, subject",
                             (uid, FRI)).fetchall()
            assert all_ and all(x["origin"] == "app" and x["bundle_version"] == "0.3.4-test" for x in all_)
            one = next(x for x in all_ if (x["rule_key"], x["subject"]) == (target["rule_key"], target["subject"]))
            assert one["status"] == "dismissed" and one["title"] == target["title"]
            assert c.execute("SELECT count(*) AS n FROM rule_runs WHERE user_id = %s AND date = %s", (uid, FRI)).fetchone()["n"] == 1
        # the next upload without a status does not bring it back
        put(_upload(rows, "0.3.4-test"))
        with conn_of(dsn) as c:
            st = c.execute("SELECT status FROM reminders WHERE user_id = %s AND date = %s AND rule_key = %s AND subject = %s",
                           (uid, FRI, target["rule_key"], target["subject"])).fetchone()["status"]
            assert st == "dismissed"
            assert c.execute("SELECT count(*) AS n FROM reminders WHERE user_id = %s", (ids["b"],)).fetchone()["n"] == 0
    finally:
        set_flag(dsn, "native_rules", "off")
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "off")


def test_pull_records_carry_reminders(client, users, dsn, stored, reader):
    ids, tok = users
    with conn_of(dsn) as c:
        engine.run(c, stored, FRI - timedelta(days=3), reader)
        c.commit()
    r = client.post("/v1/sync/pull", json={"cursors": {"records": ""}, "limit": 2000, "kinds": {"records": ["reminders"]}},
                    headers={"Authorization": f"Bearer {tok['a']}", **NEW_APP})
    assert r.status_code == 200, r.text
    got = {x["date"]: x["body"] for x in r.json()["changes"] if x["kind"] == "reminders" and x["body"]}
    day = (FRI - timedelta(days=3)).isoformat()
    assert day in got
    item = got[day]["items"][0]
    assert {"id", "rule_key", "subject", "level", "title", "message", "status", "evidence"} <= set(item) and "user_id" not in item
    rb = client.post("/v1/sync/pull", json={"cursors": {"records": ""}, "kinds": {"records": ["reminders"]}},
                     headers={"Authorization": f"Bearer {tok['b']}", **NEW_APP}).json()
    assert not [x for x in rb["changes"] if x["kind"] == "reminders" and x["body"]]


def test_inputs_endpoint(client, users, dsn, stored):
    ids, tok = users
    h = {"Authorization": f"Bearer {tok['a']}"}
    assert client.get("/v1/users/me/rules/inputs", headers=h).status_code == 404, "flag native_rules off"
    set_flag(dsn, "native_rules", "cohort", cohort="native")
    try:
        r = client.get("/v1/users/me/rules/inputs", params={"date": FRI.isoformat()}, headers=h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert set(context.INPUT_KEYS) <= set(j) and j["date"] == FRI.isoformat() and j["rules_version"] == RP.RULES_VERSION
        assert j["meta"] and j["nutrition_versions"] and j["sessions"] and j["diet"]
        assert not set(context.LOCAL_KEYS) & set(j), "the phone's own tables are not sent"
        assert client.get(f"/v1/users/{ids['b']}/rules/inputs", headers=h).status_code == 403
        assert client.get(f"/v1/users/{ids['a']}/rules/inputs", headers=h).status_code == 200
    finally:
        set_flag(dsn, "native_rules", "off")


# ---------------------------------------------------------------- explain
@pytest.fixture(scope="module")
def person(dsn, users):
    """User c = M3's synthetic person (cases_series.person): scores with details, daily metrics, sleep sessions."""
    ids, _ = users
    uid = ids["c"]
    p = CS.person()
    with conn_of(dsn) as c:
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_actual_min, sleep_need_min,
                                 sleep_debt_min, strain, ctl, atl, tsb, flags, details, algo_version)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'test')""",
                            [(uid, s["date"], s["recovery"], s["sleep_score"],
                              None if s["sleep_actual_min"] is None else round(s["sleep_actual_min"]), round(s["sleep_need_min"]),
                              s["sleep_debt_min"], s["strain"], s["ctl"], s["atl"], s["tsb"], Jsonb(s["flags"]),
                              Jsonb(json.loads(json.dumps(s["details"], default=str)))) for s in p["scores"]])
            cur.executemany("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                               VALUES (%s, %s, %s, %s, %s, 'avg', 'app')""",
                            [(uid, m["date"], m["metric"], m["value"], "kg" if m["metric"] == "body_mass" else "")
                             for m in p["metrics"]] + [(uid, d, "body_mass", w, "kg") for d, w in p["weights"].items()])
            cur.executemany("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                               VALUES (%s, %s, %s, %s, %s, true, 'app')""",
                            [(uid, s["wake_date"], s["start_at"], s["end_at"], s["asleep_min"]) for s in p["sleep"]])
        c.commit()
    return uid


def _out(o) -> dict:
    return {"current": o.current, "facts": o.facts, "advice": o.advice, "missing": o.missing}


def test_explain_calculators_equal_the_phones(dsn, person):
    keys = list(personal.CALC)
    assert set(GL.TREND_METRICS) <= set(keys)
    with conn_of(dsn) as c:
        for day in (CS.END, CS.PAST, CS.START + timedelta(days=10)):
            for key in keys:
                server = personal.CALC[key](c, person, day)
                data = json.loads(json.dumps(personal.metric_data(c, person, key, day), default=str))
                assert _same(_out(server), _out(personal.metric_out(key, data, day))), (key, day)
            mains = personal.metric_data(c, person, "bedtime", day)["mains"]
            got = personal.bedtime_items([{"wake_date": date.fromisoformat(m["wake_date"]),
                                           "start_at": datetime.fromisoformat(m["start_at"])} for m in mains], TZ, day)
            assert got == status_mod.calc_bedtime(c, person, day), day


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None
