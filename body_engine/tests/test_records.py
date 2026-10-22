"""Health records: API authorization, writes only to oneself, journal analysis, idempotent migration.

Needs Postgres: uses a separate test database BODY_TEST_DB_NAME (default body_test_records), dropped and recreated on every run,
never touching the production DB. Skipped if the database is unreachable.
"""
import os
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import TOOLS, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import analysis, importer, md_seed

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_records")


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
    """Three users: A shares supplements and lab results with B; C shares nothing."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B", "C")}
        tokens = {
            "A": auth.create_token(conn, ids["A"], "test", ["read", "write"]),
            "A_ro": auth.create_token(conn, ids["A"], "test-ro", ["read"]),
            "B": auth.create_token(conn, ids["B"], "test", ["read", "write"]),
            "C": auth.create_token(conn, ids["C"], "test", ["read", "write"]),
        }
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["supplements", "labs"]))
        conn.execute("""INSERT INTO journal_items (user_id, key, label_zh, kind) VALUES
                        (%s, 'alcohol', '喝酒', 'bool'), (%s, 'water_l', '喝水量', 'number')""", (ids["A"], ids["A"]))
    return ids, tokens


@pytest.fixture(scope="module")
def client(dsn):
    # Don't enter the lifespan (it connects to the production DB); swap the connection pool for the test DB directly
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- authorization ----------

def test_requires_token(client, world):
    assert client.get("/v1/users/me/supplements").status_code == 401
    assert client.get("/v1/users/me/supplements", headers=h("be_nope")).status_code == 401


def test_read_needs_share(client, world):
    ids, tok = world
    r = client.post("/v1/users/me/supplements", headers=h(tok["A"]),
                    json={"name": "肌酸", "status": "active", "dose": "5g", "journal_key": "supp_creatine"})
    assert r.status_code == 201, r.text
    # B has the supplements share
    r = client.get(f"/v1/users/{ids['A']}/supplements", headers=h(tok["B"]))
    assert r.status_code == 200 and [s["name"] for s in r.json()] == ["肌酸"]
    # B has no events / journal / exercises share; C has nothing
    for path in ("events", "journal", "journal/items", "exercises"):
        assert client.get(f"/v1/users/{ids['A']}/{path}", headers=h(tok["B"])).status_code == 403, path
    assert client.get(f"/v1/users/{ids['A']}/supplements", headers=h(tok["C"])).status_code == 403
    # adding a supplement also created a journal item
    items = client.get("/v1/users/me/journal/items", headers=h(tok["A"])).json()
    assert "supp_creatine" in [i["key"] for i in items]


def test_write_only_to_self(client, world):
    ids, tok = world
    sid = client.get("/v1/users/me/supplements", headers=h(tok["A"])).json()[0]["id"]
    # B has view access but cannot write to A
    assert client.post(f"/v1/users/{ids['A']}/supplements", headers=h(tok["B"]),
                       json={"name": "x", "status": "active"}).status_code == 403
    assert client.patch(f"/v1/users/{ids['A']}/supplements/{sid}", headers=h(tok["B"]),
                        json={"status": "stopped"}).status_code == 403
    # B modifies A's supplement id via their own path: not found
    assert client.patch(f"/v1/users/me/supplements/{sid}", headers=h(tok["B"]),
                        json={"status": "stopped"}).status_code == 404
    for path, body in (("journal/2026-10-01/alcohol", {"value": 1}), ("events", {"occurred_at": "2026-10-01T10:00",
                                                                                 "title": "x"}),
                       ("labs", {"collected_on": "2026-10-01", "test_name": "x", "value": 1})):
        method = client.put if path.startswith("journal") else client.post
        assert method(f"/v1/users/{ids['A']}/{path}", headers=h(tok["C"]), json=body).status_code == 403, path
    assert client.put(f"/v1/users/{ids['A']}/exercises/dips", headers=h(tok["B"]),
                      json={"status": "banned"}).status_code == 403
    # a read-only token cannot write
    assert client.patch(f"/v1/users/me/supplements/{sid}", headers=h(tok["A_ro"]),
                        json={"status": "stopped"}).status_code == 403
    # one can modify one's own
    r = client.patch(f"/v1/users/me/supplements/{sid}", headers=h(tok["A"]),
                     json={"status": "paused", "stopped_on": "2026-10-16"})
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert client.patch(f"/v1/users/me/supplements/{sid}", headers=h(tok["A"]),
                        json={"status": "bogus"}).status_code == 422


def test_supplement_edit_stop_delete(client, world):
    """「我 → 补剂」: edit name / spec / dose / timing, stop (keeps history), delete a mistaken entry with its own journal item."""
    ids, tok = world
    a = h(tok["A"])
    new = lambda name, key: client.post("/v1/users/me/supplements", headers=a,
                                        json={"name": name, "status": "active", "journal_key": key}).json()
    s = new("维生素 D", "supp_vitd")
    r = client.patch(f"/v1/users/me/supplements/{s['id']}", headers=a,
                     json={"name": "维生素 D3", "ingredients": "1000 IU / 粒", "dose": "2 粒", "timing": "随早餐"})
    assert r.status_code == 200 and (r.json()["name"], r.json()["ingredients"], r.json()["dose"], r.json()["timing"]) == \
        ("维生素 D3", "1000 IU / 粒", "2 粒", "随早餐")
    assert client.patch(f"/v1/users/me/supplements/{s['id']}", headers=a, json={"name": ""}).status_code == 422
    # stop: the row and its journal item stay
    r = client.patch(f"/v1/users/me/supplements/{s['id']}", headers=a, json={"status": "stopped", "stopped_on": "2026-10-20"})
    assert r.json()["status"] == "stopped"
    assert any(i["key"] == "supp_vitd" for i in client.get("/v1/users/me/journal/items", headers=a).json())

    # delete: B (has a view share), C, a read-only token and a wrong id cannot
    dup = new("鱼油 重复", "supp_fish_dup")
    assert client.put("/v1/users/me/journal/2026-10-19/supp_fish_dup", headers=a, json={"value": 1}).status_code == 200
    assert client.delete(f"/v1/users/{ids['A']}/supplements/{dup['id']}", headers=h(tok["B"])).status_code == 403
    assert client.delete(f"/v1/users/me/supplements/{dup['id']}", headers=h(tok["C"])).status_code == 404
    assert client.delete(f"/v1/users/me/supplements/{dup['id']}", headers=h(tok["A_ro"])).status_code == 403
    r = client.delete(f"/v1/users/me/supplements/{dup['id']}", headers=a)
    assert r.status_code == 200
    assert r.json() == {"deleted": {"id": dup["id"], "name": "鱼油 重复"}, "journal_item": "supp_fish_dup", "journal_entries": 1}
    assert all(x["id"] != dup["id"] for x in client.get("/v1/users/me/supplements", headers=a).json())
    assert all(i["key"] != "supp_fish_dup" for i in client.get("/v1/users/me/journal/items", headers=a).json())
    assert client.delete(f"/v1/users/me/supplements/{dup['id']}", headers=a).status_code == 404

    # a journal key shared with another supplement, and a non-supplement journal item, are kept
    one, two = new("锌 A", "supp_zinc_shared"), new("锌 B", "supp_zinc_shared")
    assert client.delete(f"/v1/users/me/supplements/{one['id']}", headers=a).json()["journal_item"] is None
    assert any(i["key"] == "supp_zinc_shared" for i in client.get("/v1/users/me/journal/items", headers=a).json())
    assert client.delete(f"/v1/users/me/supplements/{two['id']}", headers=a).json()["journal_item"] == "supp_zinc_shared"
    odd = client.post("/v1/users/me/supplements", headers=a, json={"name": "酒", "status": "not_needed",
                                                                   "journal_key": "alcohol"}).json()
    assert client.delete(f"/v1/users/me/supplements/{odd['id']}", headers=a).json()["journal_item"] is None
    assert any(i["key"] == "alcohol" for i in client.get("/v1/users/me/journal/items", headers=a).json())


# ---------- lab results ----------

def test_labs_compare_and_trend(client, world):
    ids, tok = world
    for d, v in (("2026-08-30", 535), ("2026-11-20", 460)):
        r = client.post("/v1/users/me/labs", headers=h(tok["A"]), json={
            "collected_on": d, "test_key": "uric_acid", "test_name": "尿酸", "value": v, "unit": "μmol/L",
            "ref_high": 420, "category": "blood"})
        assert r.status_code == 201, r.text
    client.post("/v1/users/me/labs", headers=h(tok["A"]), json={
        "collected_on": "2026-08-30", "test_key": "ecg", "test_name": "心电图", "value_text": "窦性心动过缓",
        "flag": "abnormal"})
    assert client.post("/v1/users/me/labs", headers=h(tok["A"]), json={
        "collected_on": "2026-08-30", "test_name": "空"}).status_code == 422
    summary = client.get(f"/v1/users/{ids['A']}/labs/summary", headers=h(tok["B"])).json()
    ua = next(s for s in summary if s["test_key"] == "uric_acid")
    assert ua["status"] == "high" and ua["latest"]["value"] == 460
    assert [x["value"] for x in ua["history"]] == [535, 460]
    assert ua["change_from_previous"]["delta"] == -75
    assert next(s for s in summary if s["test_key"] == "ecg")["status"] == "abnormal"
    assert len(client.get("/v1/users/me/labs?test=uric_acid&start=2026-11-01", headers=h(tok["A"])).json()) == 1


# ---------- journal ----------

def test_journal_log_and_validation(client, world):
    _, tok = world
    a = h(tok["A"])
    assert client.put("/v1/users/me/journal/2026-10-10/alcohol", headers=a, json={"value": 2}).status_code == 422
    assert client.put("/v1/users/me/journal/2026-10-10/nope", headers=a, json={"value": 1}).status_code == 422
    assert client.put("/v1/users/me/journal/2026-10-10/alcohol", headers=a, json={"value": 1}).status_code == 200
    assert client.put("/v1/users/me/journal/2026-10-10/water_l", headers=a, json={"value": 3.2}).status_code == 200
    r = client.put("/v1/users/me/journal/2026-10-10/alcohol", headers=a, json={"value": 0, "note": "改成没喝"})
    assert r.json()["value"] == 0
    rows = client.get("/v1/users/me/journal?start=2026-10-10&end=2026-10-10", headers=a).json()
    assert {(x["key"], x["value"]) for x in rows} == {("alcohol", 0), ("water_l", 3.2)}
    assert client.delete("/v1/users/me/journal/2026-10-10/water_l", headers=a).status_code == 204
    assert client.delete("/v1/users/me/journal/2026-10-10/water_l", headers=a).status_code == 404
    # New journal item: creating one requires label_zh; key format
    assert client.put("/v1/users/me/journal/items/coffee2", headers=a, json={"kind": "bool"}).status_code == 422
    assert client.put("/v1/users/me/journal/items/Bad-Key", headers=a, json={"label_zh": "x"}).status_code == 422
    r = client.put("/v1/users/me/journal/items/coffee2", headers=a, json={"label_zh": "第二杯咖啡", "kind": "bool"})
    assert r.status_code == 200 and r.json()["label_zh"] == "第二杯咖啡"


def test_journal_analysis(dsn, world):
    ids, _ = world
    uid = ids["A"]
    end = date(2026, 9, 30)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO journal_items (user_id, key, label_zh, kind) VALUES (%s, 'sauna', '桑拿', 'bool')",
                     (uid,))
        for i in range(12):
            d = end - timedelta(days=20 - i)
            did = i % 2 == 0
            conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, 'sauna', %s)",
                         (uid, d, 1 if did else 0))
            # Next day: after a sauna -> resting heart rate 3 lower, HRV higher
            for metric, v in (("resting_heart_rate", 52 if did else 55), ("heart_rate_variability_sdnn", 55 if did else 45)):
                conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, agg, source)
                                VALUES (%s, %s, %s, %s, 'avg', 'test')""", (uid, d + timedelta(days=1), metric, v + i % 3 * 0.5))
        res = analysis.analyze(conn, uid, "sauna", end=end, days=30)
        assert res["days_yes"] == 6 and res["days_no"] == 6
        rhr = res["outcomes"]["next_rhr"]
        assert rhr["status"] == "ok" and rhr["diff_yes_minus_no"] < -2 and rhr["direction"] == "better"
        assert res["outcomes"]["next_ln_hrv"]["direction"] == "better"
        assert rhr["reliable"] is False
        # Window shrunk to < 5 days each: no result
        short = analysis.analyze(conn, uid, "sauna", end=end, days=16)
        assert short["outcomes"]["next_rhr"]["status"] == "insufficient"
        # Days without a record don't count as "no": alcohol has only 1 entry
        assert analysis.analyze(conn, uid, "alcohol", end=date(2026, 10, 31))["days_recorded"] == 1


# ---------- events, exercises ----------

def test_events_and_exercises(client, world):
    ids, tok = world
    a = h(tok["A"])
    r = client.post("/v1/users/me/events", headers=a, json={
        "occurred_at": "2026-10-15T12:00", "time_precision": "day", "title": "练腿低血糖", "category": "symptom",
        "severity": 2})
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    assert client.post("/v1/users/me/events", headers=a, json={"occurred_at": "2026-10-15", "title": "x",
                                                               "severity": 9}).status_code == 422
    r = client.patch(f"/v1/users/me/events/{eid}", headers=a, json={"resolved_on": "2026-10-15"})
    assert r.status_code == 200 and r.json()["resolved_on"] == "2026-10-15"
    ev = client.get("/v1/users/me/events?start=2026-10-15&end=2026-10-15", headers=a).json()
    assert ev[0]["local_date"] == "2026-10-15" and ev[0]["time_precision"] == "day"
    assert client.patch(f"/v1/users/me/events/{eid}", headers=h(tok["B"]), json={"title": "x"}).status_code == 404

    r = client.put("/v1/users/me/exercises/dips", headers=a, json={"name_zh": "双杠臂屈伸", "status": "banned"})
    assert r.status_code == 200 and r.json()["status"] == "banned"
    r = client.put("/v1/users/me/exercises/dips", headers=a, json={"app_exercise_id": "sparky-123"})
    assert r.json()["status"] == "banned" and r.json()["app_exercise_id"] == "sparky-123"
    assert [x["exercise_ref"] for x in client.get("/v1/users/me/exercises?status=banned", headers=a).json()] == ["dips"]


# ---------- assistant tools ----------

def test_assistant_tools(dsn, world):
    ids, _ = world
    names = {t.name for t in TOOLS}
    assert {"list_supplements", "get_lab_results", "log_journal", "get_journal", "analyze_journal", "list_events",
            "add_event", "update_event", "get_exercise_rules"} <= names
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # B can see A's lab results but not A's journal
        assert run_tool(conn, ids["B"], "mcp", "get_lab_results", {"user": str(ids["A"]), "tests": ["uric_acid"]})
        with pytest.raises(PermissionError):
            run_tool(conn, ids["B"], "mcp", "get_journal", {"user": str(ids["A"]), "start": None, "end": None, "keys": []})
        row = run_tool(conn, ids["A"], "mcp", "log_journal", {"date": "2026-10-11", "key": "alcohol", "value": 1,
                                                             "note": None})
        assert row["value"] == 1 and row["source"] == "assistant"
        # Write tools can only write to oneself even when given a user parameter
        with pytest.raises(PermissionError):
            run_tool(conn, ids["B"], "mcp", "log_journal", {"user": str(ids["A"]), "date": "2026-10-11",
                                                            "key": "alcohol", "value": 0, "note": None})
        with pytest.raises(ValueError):
            run_tool(conn, ids["B"], "mcp", "update_supplement", {"supplement_id": 1, "status": "stopped", "dose": None,
                                                                  "timing": None, "started_on": None,
                                                                  "stopped_on": None, "advice": None})
        # supplements: update name / spec, then delete a mistaken entry with its own journal item
        s = run_tool(conn, ids["A"], "mcp", "add_supplement", {"name": "镁", "status": "active", "journal_key": "supp_mg_tool"})
        row = run_tool(conn, ids["A"], "mcp", "update_supplement", {"supplement_id": s["id"], "name": "甘氨酸镁",
                                                                    "ingredients": "200mg / 粒"})
        assert row["name"] == "甘氨酸镁" and row["ingredients"] == "200mg / 粒" and row["status"] == "active"
        with pytest.raises(PermissionError):   # B can view A's supplements but cannot delete them
            run_tool(conn, ids["B"], "mcp", "delete_supplement", {"user": str(ids["A"]), "supplement_id": s["id"]})
        with pytest.raises(ValueError):        # A's id through B's own data: not found
            run_tool(conn, ids["B"], "mcp", "delete_supplement", {"supplement_id": s["id"]})
        out = run_tool(conn, ids["A"], "mcp", "delete_supplement", {"supplement_id": s["id"]})
        assert out["deleted"]["name"] == "甘氨酸镁" and out["journal_item"] == "supp_mg_tool"
        assert not conn.execute("SELECT 1 FROM journal_items WHERE user_id = %s AND key = 'supp_mg_tool'", (ids["A"],)).fetchone()
        rules = run_tool(conn, ids["A"], "mcp", "get_exercise_rules", {"user": "me"})   # default: the slim summary
        assert [x["ref"] for x in rules["banned"]] == ["dips"] and "aliases" not in rules["banned"][0]
        full = run_tool(conn, ids["A"], "mcp", "get_exercise_rules", {"user": "me", "detail": True})
        assert [x["exercise_ref"] for x in full["banned"]] == ["dips"] and "aliases" in full["banned"][0]
        audit = conn.execute("SELECT tool, ok FROM assistant_audit ORDER BY id").fetchall()
        assert ("log_journal", True) in {(a["tool"], a["ok"]) for a in audit}


# ---------- migration ----------

def test_import_records_idempotent(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("INSERT INTO users (display_name) VALUES ('import') RETURNING id").fetchone()["id"]
        first = importer.run(conn, uid)
        assert all(r["现有"] == r["应有"] for r in first), importer.format_report(first)
        assert sum(r["本次写入"] or 0 for r in first) > 0

        # The user later changed it in the database: 护肝片 stopped
        conn.execute("""UPDATE supplements SET status = 'stopped' WHERE user_id = %s
                        AND import_key = 'md:supplement:liver_detox'""", (uid,))
        counts = {t: conn.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id = %s", (uid,)).fetchone()["n"]
                  for t in ("supplements", "lab_results", "events", "exercise_meta", "journal_items", "journal_entries")}
        second = importer.run(conn, uid)
        assert all((r["本次写入"] or 0) == 0 for r in second), importer.format_report(second)
        assert all(r["现有"] == r["应有"] for r in second)
        assert counts == {t: conn.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id = %s", (uid,)).fetchone()["n"]
                          for t in counts}
        assert conn.execute("""SELECT status FROM supplements WHERE user_id = %s
                               AND import_key = 'md:supplement:liver_detox'""", (uid,)).fetchone()["status"] == "stopped"

        assert counts["supplements"] == len(md_seed.SUPPLEMENTS)
        assert counts["lab_results"] == len(md_seed.LABS)
        assert counts["events"] == len(md_seed.EVENTS)
        assert counts["journal_entries"] == len(md_seed.JOURNAL_BACKFILL)
        banned = {r["exercise_ref"] for r in conn.execute(
            "SELECT exercise_ref FROM exercise_meta WHERE user_id = %s AND status = 'banned'", (uid,))}
        assert banned == {"upright_row", "behind_neck_press", "behind_neck_pulldown", "dips"}
        # The hypoglycemia example event lands on 10-08 in the user's time zone
        ev = conn.execute("""SELECT (occurred_at AT TIME ZONE 'Australia/Sydney')::date AS d FROM events
                             WHERE user_id = %s AND import_key = 'md:event:hypoglycemia_example'""", (uid,)).fetchone()
        assert ev["d"] == date(2026, 10, 8)
