"""The native app's read-only view of someone else (apps/ios Features/MemberView): every path it reads, as three other people.

Nobody without a share gets anything; a circle-style share gets exactly its scopes; a family share gets everything but the ECG;
revoking takes effect at once; other people's answers are never cacheable; and a person whose phone computes for itself (2.0.0,
all packages primary) is still fully readable, from the server's copy, without the view changing anything of theirs.
Fictional data only. Separate test database BODY_TEST_DB_NAME_MEMBER_VIEW (default body_test_member_view), recreated each
run; skipped without Postgres.
"""
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.algo import versions
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.nativesync import appscores, derived_kinds, params as P, primary
from body_engine.rollout import flags
from body_engine.scores import params as SP

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_MEMBER_VIEW", "body_test_member_view")
TZ = "Australia/Sydney"
FAMILY = ["scores", "sleep", "training", "nutrition", "metrics", "weight", "journal", "events", "supplements", "exercises", "labs",
          "memory", "reminders"]
CIRCLE = ["scores", "sleep", "training", "nutrition_summary"]
FEATURES = ("sleep_detail", "water", "muscle_load")
# tables a viewer must never change for the person viewed (nativesync primary: what the phone uploaded)
COMPUTED = ("daily_metric_sources", "daily_metrics", "sleep_sessions", "workouts", "workout_hrr", "stress_daily",
            "healthspan_weekly", "reminders", "rule_runs", "daily_scores")


def paths(u, day: date) -> dict[str, list[str]]:
    """What MemberViewStore.readable reads, by the scope the server should ask for (`any` = any share, blocks inside)."""
    d, m = day.isoformat(), day.strftime("%Y-%m")
    p = f"/v1/users/{u}"
    return {
        "any": [f"{p}/today", f"{p}/today?date={d}", f"{p}/trends?days=61", f"{p}/trends?days=7&end={d}", f"{p}/days?month={m}",
                f"{p}/training/week?date={d}", f"{p}/explain/metric/hrv"],
        "scores": [f"{p}/strain-coach?date={d}", f"{p}/vo2max?days=365", f"{p}/reports?period=week"],
        "scores+sleep": [f"{p}/health-monitor?date={d}"],
        "sleep": [f"{p}/sleep/nights?days=14", f"{p}/sleep/night", f"{p}/sleep/details"],
        "training": [f"{p}/training/calendar?month={m}", f"{p}/training/day/{d}"],
        "memory": [f"{p}/memory", f"{p}/explain/status/weight"],
        "nutrition": [f"{p}/water?date={d}", f"{p}/recipes"],
        "journal": [f"{p}/journal/items?active_only=true", f"{p}/journal/frequent", f"{p}/journal?start={d}&end={d}",
                    f"{p}/journal/analysis?days=90"],
        "supplements": [f"{p}/supplements"],
        "labs": [f"{p}/labs/summary", f"{p}/labs/example/history"],
        "events": [f"{p}/events?start={d}&end={d}"],
        "exercises": [f"{p}/exercises"],
    }


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


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


def H(tok):
    return {"Authorization": f"Bearer {tok}", "X-App-Version": "2.0.0"}


def add_user(c, name):
    return c.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                        VALUES (%s, 'member', %s, now(), '2026-10-22.2') RETURNING id""", (name, TZ)).fetchone()["id"]


@pytest.fixture(scope="module")
def world(dsn):
    today = datetime.now(ZoneInfo(TZ)).date()
    with conn_of(dsn) as c:
        for key in FEATURES:   # on for everyone at 2.0.0; 404 before any share check while off
            flags.set_flag(c, key, "all", actor="test")
        ids = {n: add_user(c, n) for n in ("owner", "family", "circle", "stranger")}
        tok = {n: auth.create_token(c, i, "app", ["read", "write"]) for n, i in ids.items()}
        c.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s), (%s, %s, %s)",
                  (ids["owner"], ids["family"], FAMILY, ids["owner"], ids["circle"], CIRCLE))
        c.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, strain, flags, algo_version)
                     VALUES (%s, %s, 71, 82, 9.5, '{}', 'test')""", (ids["owner"], today))
        c.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                     VALUES (%s, %s, 'step_count', 7654, 'count', 'sum', 'ow')""", (ids["owner"], today))
        c.execute("""INSERT INTO reminders (user_id, date, rule_key, subject, level, title, message, status, rules_version)
                     VALUES (%s, %s, 'example', 's', 'info', '示例提醒', 'm', 'new', 'test')""", (ids["owner"], today))
        c.commit()
    flags.invalidate()
    return {"ids": ids, "tok": tok, "today": today}


def status(client, tok, path):
    return client.get(path, headers=H(tok)).status_code


def test_no_share_no_data(client, world):
    u, today = world["ids"]["owner"], world["today"]
    for scope, group in paths(u, today).items():
        for p in group:
            assert status(client, world["tok"]["stranger"], p) == 403, p


def test_circle_share_gets_only_its_scopes(client, world):
    u, today, tok = world["ids"]["owner"], world["today"], world["tok"]["circle"]
    by_scope = paths(u, today)
    for scope, group in by_scope.items():
        for p in group:
            code = status(client, tok, p)
            if scope in ("any", "scores", "scores+sleep", "sleep", "training"):
                assert code != 403, p
            else:
                assert code == 403, p
    doc = client.get(f"/v1/users/{u}/today", headers=H(tok)).json()
    assert doc["scores"].get("code") != "forbidden" and doc["sleep"].get("code") != "forbidden"
    # the blocks of what is not shared: a refusal and nothing else
    for block in ("nutrition", "reminders"):
        assert doc[block]["code"] == "forbidden" and set(doc[block]) == {"error", "code"}, block
    assert "示例提醒" not in json.dumps(doc, ensure_ascii=False)
    # Trends: no daily metrics (steps) or weight in a share without them
    t = client.get(f"/v1/users/{u}/trends?days=7", headers=H(tok)).json()
    assert "7654" not in json.dumps(t["series"].get("steps", {})) and not any(
        v is not None for v in t["series"].get("weight", {}).get("values", []))
    assert t["series"]["recovery"]["values"][-1] == 71


def test_family_share_reads_everything_but_ecg_and_ai(client, world):
    u, today, tok = world["ids"]["owner"], world["today"], world["tok"]["family"]
    for scope, group in paths(u, today).items():
        for p in group:
            r = client.get(p, headers=H(tok))
            # 404: a lab test or night without data, an empty answer of the shared block; never a refusal
            assert r.status_code in (200, 404), (p, r.status_code, r.text[:120])
    doc = client.get(f"/v1/users/{u}/today", headers=H(tok)).json()
    assert doc["self"] is False and "示例提醒" in json.dumps(doc["reminders"], ensure_ascii=False)
    assert client.get(f"/v1/users/{u}/trends?days=7", headers=H(tok)).json()["series"]["steps"]["values"][-1] == 7654
    # never part of an ordinary share
    for p in (f"/v1/users/{u}/ecg", f"/v1/users/{u}/ai-providers", f"/v1/users/{u}/ai-tokens",
              f"/v1/users/{u}/ai-choice", f"/v1/users/{u}/sync/overview", f"/v1/users/{u}/weight/recent", f"/v1/users/{u}/ai-fallback"):
        assert status(client, tok, p) == 403, p


def test_a_viewer_writes_nothing(client, world):
    u, today, tok = world["ids"]["owner"], world["today"], world["tok"]["family"]
    d = today.isoformat()
    for method, path, body in (
            ("post", f"/v1/users/{u}/water", {"ml": 250}),
            ("put", f"/v1/users/{u}/journal/{d}/example", {"value": 1}),
            ("post", f"/v1/users/{u}/weight", {"weight_kg": 70}),
            ("post", f"/v1/users/{u}/events", {"title": "x"}),
            ("patch", f"/v1/users/{u}/reminders/1", {"status": "seen"})):
        code = client.request(method, path, headers=H(tok), json=body).status_code
        assert code in (403, 404, 422), (path, code)
        assert code != 200


def test_answers_about_someone_else_are_not_cacheable(client, world):
    u, today, tok = world["ids"]["owner"], world["today"], world["tok"]["family"]
    for group in paths(u, today).values():
        for p in group:
            cc = client.get(p, headers=H(tok)).headers.get("cache-control", "")
            assert "no-store" in cc, (p, cc)


def test_viewable_lists_who_shares_and_stops_at_once(client, dsn, world):
    ids, tok = world["ids"], world["tok"]
    today = world["today"]
    me = client.get("/v1/web/me", headers=H(tok["family"])).json()["viewable"]
    assert {(v["id"], v["self"]) for v in me} == {(ids["family"], True), (ids["owner"], False)}
    assert [v["id"] for v in client.get("/v1/web/me", headers=H(tok["stranger"])).json()["viewable"]] == [ids["stranger"]]
    # the owner takes one scope away, then the whole share: the view's next request says so
    assert status(client, tok["family"], f"/v1/users/{ids['owner']}/supplements") == 200
    with conn_of(dsn) as c:
        c.execute("UPDATE shares SET scopes = %s WHERE owner_id = %s AND viewer_id = %s",
                  ([s for s in FAMILY if s != "supplements"], ids["owner"], ids["family"]))
        c.commit()
    assert status(client, tok["family"], f"/v1/users/{ids['owner']}/supplements") == 403
    assert status(client, tok["family"], f"/v1/users/{ids['owner']}/today") == 200
    with conn_of(dsn) as c:
        c.execute("DELETE FROM shares WHERE owner_id = %s AND viewer_id = %s", (ids["owner"], ids["family"]))
        c.commit()
    for group in paths(ids["owner"], today).values():
        for p in group:
            assert status(client, tok["family"], p) == 403, p
    assert [v["id"] for v in client.get("/v1/web/me", headers=H(tok["family"])).json()["viewable"]] == [ids["family"]]


# ---------------------------------------------------------------- someone whose phone computes for itself (2.0.0)
def snapshot(c, uid: int) -> dict:
    return {t: c.execute(f"""SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) AS h
                             FROM {t} x WHERE user_id = %s""", (uid,)).fetchone() for t in COMPUTED}


def test_app_mode_owner_is_read_from_the_server_copy(client, dsn, world, monkeypatch):
    for mod in derived_kinds.kinds().values():
        monkeypatch.setattr(mod, "recompute", lambda conn, uid, day, uploaded: uploaded[day])
    monkeypatch.setattr(appscores, "server_rows", lambda conn, uid, days, end=None: {})
    from test_native_s9 import daily_rows, healthspan_rows, reminder_rows, sleep_rows, stress_rows, workout_row

    today = datetime.now(ZoneInfo(TZ)).date()
    days = [today - timedelta(days=i) for i in (3, 2, 1)]
    monday = today - timedelta(days=today.weekday())
    with conn_of(dsn) as c:
        uid, viewer = add_user(c, "phone"), add_user(c, "viewer")
        for key in [P.FLAG, P.COMPUTE_PRIMARY_FLAG, *(s["flag"] for s in P.COMPUTE_PACKAGES.values())]:
            flags.set_flag(c, key, "cohort", cohort="member_view_test", actor="test")
        flags.cohort_add(c, uid, "member_view_test", actor="test")
        tok, tok_v = auth.create_token(c, uid, "app", ["read", "write"]), auth.create_token(c, viewer, "app", ["read", "write"])
        c.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (uid, viewer, FAMILY))
        for i, d in enumerate(days):
            w = workout_row(d, f"w-{i}", 0)
            c.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                                               duration_min, active_kcal, total_kcal)
                         VALUES (%s, 'app', %s, 'running', '跑步', %s, to_timestamp(%s), to_timestamp(%s), 40, 300, 360)""",
                      (uid, w["external_id"], d, w["start"], w["end"]))
        c.commit()
    flags.invalidate()

    def upload(kind, items, bundle):
        mod = derived_kinds.get(kind)
        r = client.put("/v1/users/me/derived/app", headers=H(tok),
                       json={"kind": kind, "algo_version": versions.current(mod.ALGO_KEY), "bundle_version": bundle,
                             "days": [{"date": d.isoformat(), "rows": rows} for d, rows in items]})
        assert r.status_code == 200, (kind, r.text)
        return r.json()

    phone = {
        "daily": [(d, daily_rows(12000 + i)) for i, d in enumerate(days)],
        "sleep": [(d, sleep_rows(d, 431 + i)) for i, d in enumerate(days)],
        "workouts": [(d, [workout_row(d, f"w-{i}", 42.5 + i)]) for i, d in enumerate(days)],
        "stress": [(d, stress_rows(1.11 + i / 100)) for i, d in enumerate(days)],
        "healthspan": [(monday, healthspan_rows(monday, today, 23.1))],
        "reminders": [(d, reminder_rows(d, f"手机算的提醒 {d.isoformat()}")) for d in [*days[1:], today]],
    }
    for bundle in ("9.0.0", "9.0.1", "9.0.2"):
        for kind, items in phone.items():
            upload(kind, items, bundle)
    with conn_of(dsn) as c:
        for package in P.COMPUTE_PACKAGES:
            primary.set_primary(c, package, user_id=uid, force=True, actor="test")
        c.commit()
    for kind, items in phone.items():
        assert upload(kind, items, "9.0.2")["state"] == "primary", kind
    r = client.put("/v1/users/me/scores/app", headers=H(tok), json={
        "algo_version": SP.ALGO_VERSION, "bundle_version": "9.0.2",
        "days": [{"date": d.isoformat(), "row": {"recovery": 61 + i, "sleep_score": 80, "strain": 10.5}} for i, d in enumerate(days)]})
    assert r.status_code == 200 and r.json()["app_scoring"], r.text
    with conn_of(dsn) as c:
        assert primary.primary_packages(c, uid) == set(P.COMPUTE_PACKAGES)
        before = snapshot(c, uid)

    # every path of the view answers, with the values the phone uploaded
    def get(path, **params):
        r = client.get(f"/v1/users/{uid}{path}", headers=H(tok_v), params=params)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        return r.json()

    last = days[-1]
    assert get("/today", date=last.isoformat())["scores"]["today"]["recovery"] == 63
    assert "手机算的提醒" in json.dumps(get("/today"), ensure_ascii=False)
    assert get("/today", date=last.isoformat())["sleep"]["date"] == last.isoformat()
    t = get("/trends", days=7)
    assert t["series"]["recovery"]["values"][-2] == 63 and t["series"]["steps"]["values"][-2] == 12002
    assert t["series"]["stress"]["values"][-2] == pytest.approx(1.13)
    assert "433" in json.dumps(get("/sleep/nights", end=last.isoformat(), days=3))
    assert get("/sleep/night", date=last.isoformat())["date"] == last.isoformat()
    assert "42.5" in json.dumps(get(f"/training/day/{days[0].isoformat()}"))
    assert get("/training/calendar", month=last.strftime("%Y-%m"))["days"]
    assert any(d.get("scores") for d in get("/days", month=last.strftime("%Y-%m"))["days"])
    for path, params in (("/strain-coach", {"date": last.isoformat()}), ("/health-monitor", {"date": last.isoformat()}),
                         ("/sleep/details", {}), ("/vo2max", {"days": 365}), ("/reports", {"period": "week"}), ("/memory", {}),
                         ("/explain/metric/recovery", {}), ("/explain/metric/hrv", {})):
        assert get(path, **params) is not None, path

    # looking changes nothing of theirs, and the view never needs their phone's documents
    with conn_of(dsn) as c:
        assert snapshot(c, uid) == before, "a viewer's request wrote computed rows of the person viewed"
    # the circle-style reader of the same person: its scopes only, also in app mode
    with conn_of(dsn) as c:
        c.execute("UPDATE shares SET scopes = %s WHERE owner_id = %s AND viewer_id = %s", (CIRCLE, uid, viewer))
        c.commit()
    doc = get("/today")
    assert doc["reminders"]["code"] == "forbidden" and "手机算的提醒" not in json.dumps(doc, ensure_ascii=False)
    assert client.get(f"/v1/users/{uid}/memory", headers=H(tok_v)).status_code == 403
