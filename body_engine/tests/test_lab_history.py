"""Lab trend: status cards read the lab table live (any source, home meters included), spark / trend direction, the fasting field,
migration 200 backfill, the history API / get_lab_history tool and their authorization.

Needs Postgres: uses a separate test database BODY_TEST_DB_NAME (default body_test_labhist), dropped and recreated on every run,
never touching the production DB. Skipped if the database is unreachable.
"""
import os
from datetime import date
from importlib import resources

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant import status
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import lab_catalog, lab_history, lab_params

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_labhist")


# ---------------------------------------------------------------- no database needed
def _hist(key, values, sources=None):
    rows = [{"id": i, "collected_on": date.fromordinal(date(2026, 1, 1).toordinal() + 31 * i), "value": v, "value_text": None, "unit": "u", "ref_low": None,
             "ref_high": 420, "flag": None, "source": (sources or ["lab"] * len(values))[i], "fasting": None, "lab": None,
             "note": None, "test_name": key, "category": "blood", "test_key": key} for i, v in enumerate(values)]
    return lab_history._history_of(key, rows)


def test_trend_and_direction():
    assert lab_history.trend(489, 550) == "down" and lab_history.trend(560, 550) == "up"
    assert lab_history.trend(2.24, 2.25) == "flat"            # within 1%: rounding
    assert lab_history.trend(None, 550) is None
    c = lab_history.card(_hist("uric_acid", [550, 470, 489], ["lab", "lab", "home_device"]))
    assert (c["value"], c["trend"], c["good_direction"], c["spark"], c["tone"]) == ("偏高 489", "up", "down", [550, 470, 489], "warn")
    assert "家测" in c["note"] and c["lab_key"] == "uric_acid"
    assert lab_history.card(_hist("uric_acid", [550]))["spark"] is None
    assert lab_history.card(_hist("uric_acid", [560]))["tone"] == "bad"   # > 20% above the upper limit
    assert lab_history.card(_hist("uric_acid", [None])) is None
    for k in ("uric_acid", "alt", "ast", "triglycerides", "ldl", "creatinine"):
        assert lab_params.GOOD_DIRECTION[k] == "down", k
    assert lab_params.GOOD_DIRECTION["hdl"] == "up" and "sodium" not in lab_params.GOOD_DIRECTION
    spark = lab_history.card(_hist("alt", list(range(40, 60))))["spark"]
    assert len(spark) == lab_params.SPARK_POINTS and spark[-1] == 59


def test_history_deltas_and_compact():
    h = _hist("uric_acid", [550, 489], ["lab", "home_device"])
    assert h["rows"][0]["delta"] is None and h["rows"][1]["delta"] == -61 and h["rows"][1]["pct"] == -11.1
    c = lab_history.compact(h)
    assert c["better"] == "down" and c["ref"] == [None, 420]
    assert c["points"][1] == {"date": "2026-02-01", "value": 489, "source": "home_device", "delta": -61, "pct": -11.1,
                              "status": "high"}
    assert len(lab_history.compact(h, 1)["points"]) == 1


def test_tool_schema():
    t = TOOLS_BY_NAME["get_lab_history"]
    assert not t.writes and t.scope == "labs"
    props = TOOLS_BY_NAME["add_lab_results"].input_schema["properties"]
    assert props["source"]["enum"] == ["lab", "home_device"] and "空腹" in props["fasting"]["description"]


# ---------------------------------------------------------------- database
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
    """A shares labs (not memory) with B; C sees nothing. The status card was refreshed just now (no 30-minute refresh due)."""
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B", "C")}
        tokens = {n: auth.create_token(conn, ids[n], "test", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["labs"]))
        conn.execute("""INSERT INTO lab_results (user_id, collected_on, test_key, test_name, category, value, unit, ref_high,
                                                 flag, import_key)
                        VALUES (%s, '2026-08-30', 'uric_acid', '尿酸', 'blood', 550, 'μmol/L', 420, 'H', 'md:uric_acid')""",
                     (ids["A"],))
        status.refresh_auto(conn, ids["A"], date(2026, 10, 18))
    return ids, tokens


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(token):
    return {"Authorization": f"Bearer {token}"}


def _card(client, tok, key="uric_acid"):
    return next((i for i in client.get("/v1/users/me/memory", headers=h(tok)).json()["status"] if i["key"] == key), None)


UA = dict(test="尿酸", test_key=None, value_text=None, unit="μmol/L", ref_low=None, ref_high=420, flag=None, note=None)


def test_home_reading_updates_card_at_once(client, conn, world):
    ids, tok = world
    before = _card(client, tok["A"])
    assert (before["value"], before["note"], before["lab_key"]) == ("偏高 550", "08-30 · 化验 · 上限 420", "uric_acid")
    res = lab_catalog.add_results(conn, ids["A"], date(2026, 10, 18), "尿酸仪", [dict(UA, value=489)], False,
                                  source="home_device", fasting=False)
    assert res["created"][0]["value"] == 489
    after = _card(client, tok["A"])     # no refresh due (refreshed just now), the card still shows the new reading
    assert (after["value"], after["trend"], after["good_direction"], after["spark"]) == ("偏高 489", "down", "down", [550, 489])
    assert after["note"].startswith("10-18 · 家测") and after["tone"] == "warn"
    row = conn.execute("SELECT value, note, spark FROM assistant_status WHERE user_id = %s AND key = 'uric_acid'",
                       (ids["A"],)).fetchone()
    assert row["value"] == "偏高 489" and row["spark"] == [550, 489]   # sync_labs also rewrote the stored row
    hist = client.get("/v1/users/me/labs/uric_acid/history", headers=h(tok["A"])).json()
    last = hist["rows"][-1]
    assert (last["source"], last["fasting"], last["lab"], last["delta"], last["pct"]) == ("home_device", False, "尿酸仪", -61, -11.1)
    assert hist["ref_high"] == 420 and hist["good_direction"] == "down" and hist["unit"] == "μmol/L"
    # get_lab_results shows the source too
    rep = run_tool(conn, ids["A"], "butler", "get_lab_results", {"user": "me", "tests": ["uric_acid"]})[0]
    assert rep["latest"]["value"] == 489 and rep["latest"]["source"] == "home_device" and rep["latest"]["fasting"] is False


def test_fasting_and_same_day_sources(conn, world):
    ids, _ = world
    a, day = ids["A"], date(2026, 10, 18)
    # Same value again, now saying it was fasting -> fasting is filled in (updated), no duplicate
    again = lab_catalog.add_results(conn, a, day, "尿酸仪", [dict(UA, value=489)], False, source="home_device", fasting=True)
    assert again["updated"][0]["fasting"] is True and not again["created"]
    assert lab_catalog.add_results(conn, a, day, "尿酸仪", [dict(UA, value=489)], False, "home_device", None)["unchanged"]
    # A lab report on the same day is a separate reading (home meter readings are not overwritten by it)
    lab = lab_catalog.add_results(conn, a, day, "Laverty", [dict(UA, value=480)], False, source="lab", fasting=True)
    assert lab["created"]
    rows = lab_history.history(conn, a, "uric_acid")["rows"]
    assert [(r["value"], r["source"], r["fasting"]) for r in rows] == [(550, "lab", None), (489, "home_device", True),
                                                                        (480, "lab", True)]
    with pytest.raises(ValueError, match="source"):
        lab_catalog.add_results(conn, a, day, None, [dict(UA, value=480)], False, source="clinic")
    # Undo both of the butler's readings of that day -> the card falls back to the August lab result
    assert lab_catalog.delete_results(conn, a, day, ["uric_acid"])["deleted"]["test_keys"] == ["uric_acid"]
    card = next(i for i in status.list_status(conn, a) if i["key"] == "uric_acid")
    assert (card["value"], card["trend"], card["spark"]) == ("偏高 550", None, None)
    assert card["note"].startswith("08-30 · 化验")


def test_delete_returns_to_previous(client, conn, world):
    ids, tok = world
    lab_catalog.add_results(conn, ids["A"], date(2026, 10, 20), None, [dict(UA, value=470)], False, "home_device", True)
    assert _card(client, tok["A"])["value"] == "偏高 470"
    lab_catalog.delete_results(conn, ids["A"], date(2026, 10, 20), ["uric_acid"])
    assert _card(client, tok["A"])["value"] == "偏高 550"


def test_new_auto_card_and_removal(conn, world):
    ids, _ = world
    a = ids["A"]
    lab_catalog.add_results(conn, a, date(2026, 10, 19), "Laverty", [
        dict(test="ALT", test_key=None, value=52, value_text=None, unit="U/L", ref_low=None, ref_high=40, flag=None, note=None)],
        False)
    alt = next(i for i in status.list_status(conn, a) if i["key"] == "alt")   # appears straight away
    assert alt["value"] == "偏高 52" and alt["source"] == "auto" and alt["lab_key"] == "alt"
    lab_catalog.delete_results(conn, a, date(2026, 10, 19), ["alt"])
    assert "alt" not in {i["key"] for i in status.list_status(conn, a)}
    assert conn.execute("SELECT 1 FROM assistant_status WHERE user_id = %s AND key = 'alt'", (a,)).fetchone() is None


def test_butler_written_lab_card_uses_lab_table(conn, world):
    ids, _ = world
    a = ids["A"]
    status.set_items(conn, a, [{"key": "hdl", "label": "HDL", "value": "偏低", "unit": None, "tone": "warn", "trend": None,
                                "good_direction": None, "group": "健康", "spark": None, "note": "多做有氧"}], [])
    for d, v in (("2026-08-30", 0.9), ("2026-10-19", 1.1)):
        lab_catalog.add_results(conn, a, date.fromisoformat(d), "Laverty", [
            dict(test="HDL", test_key=None, value=v, value_text=None, unit="mmol/L", ref_low=1.0, ref_high=None, flag=None,
                 note=None)], False)
    hdl = next(i for i in status.list_status(conn, a) if i["key"] == "hdl")
    assert (hdl["value"], hdl["trend"], hdl["good_direction"], hdl["tone"], hdl["label"]) == ("正常 1.1", "up", "up", "good", "HDL")
    assert hdl["note"].endswith("· 多做有氧") and hdl["source"] == "assistant" and hdl["lab_key"] == "hdl"
    with pytest.raises(ValueError, match="自动算"):
        status.set_items(conn, a, [{"key": "uric_acid", "label": "尿酸", "value": "550", "tone": "bad", "group": "健康"}], [])


def test_history_api_auth(client, world):
    ids, tok = world
    a = ids["A"]
    assert client.get(f"/v1/users/{a}/labs/uric_acid/history", headers=h(tok["B"])).status_code == 200
    assert client.get(f"/v1/users/{a}/labs/uric_acid/history", headers=h(tok["C"])).status_code == 403
    assert client.get("/v1/users/me/labs/uric_acid/history", headers=h(tok["C"])).status_code == 404
    assert client.get("/v1/users/me/labs/nothing/history", headers=h(tok["A"])).status_code == 404
    # B may see the labs but not A's memory: the status cards stay hidden, the lab explanation has no profile lines
    assert client.get(f"/v1/users/{a}/memory", headers=h(tok["B"])).status_code == 403
    x = client.get(f"/v1/users/{a}/explain/lab/uric_acid", headers=h(tok["B"]))
    assert x.status_code == 200 and x.json()["current"]["value"] == 550 and x.json()["profile"] == []
    assert client.get(f"/v1/users/{a}/explain/lab/uric_acid", headers=h(tok["C"])).status_code == 403
    gen = client.get("/v1/users/me/explain/lab/hdl", headers=h(tok["A"])).json()
    assert gen["title"] == "高密度脂蛋白胆固醇" and gen["kind"] == "lab"


def test_api_writes_source_and_fasting(client, world):
    ids, tok = world
    r = client.post("/v1/users/me/labs", headers=h(tok["A"]), json={
        "collected_on": "2026-10-21", "test_name": "尿酸", "test_key": "uric_acid", "value": 455, "unit": "μmol/L",
        "ref_high": 420, "source": "home_device", "fasting": True})
    assert r.status_code == 201 and r.json()["source"] == "home_device" and r.json()["fasting"] is True
    assert _card(client, tok["A"])["value"] == "偏高 455"
    assert client.post("/v1/users/me/labs", headers=h(tok["A"]), json={
        "collected_on": "2026-10-21", "test_name": "x", "value": 1, "source": "clinic"}).status_code == 422
    plain = client.post("/v1/users/me/labs", headers=h(tok["A"]), json={"collected_on": "2026-10-22", "test_name": "钠",
                                                                         "test_key": "sodium", "value": 140})
    assert plain.json()["source"] == "lab" and plain.json()["fasting"] is None
    lid = r.json()["id"]
    assert client.patch(f"/v1/users/me/labs/{lid}", headers=h(tok["A"]), json={"fasting": False}).json()["fasting"] is False
    assert client.patch(f"/v1/users/me/labs/{lid}", headers=h(tok["A"]), json={"source": None}).status_code == 422
    assert client.patch(f"/v1/users/{ids['A']}/labs/{lid}", headers=h(tok["B"]), json={"fasting": True}).status_code == 403


def test_get_lab_history_tool(conn, world):
    ids, _ = world
    out = run_tool(conn, ids["B"], "mcp", "get_lab_history", {"user": str(ids["A"]), "test": "尿酸", "limit": None})
    assert out["test_key"] == "uric_acid" and out["better"] == "down" and out["points"][0]["value"] == 550
    assert out["points"][-1]["source"] == "home_device" and out["points"][-1]["fasting"] is False   # patched above
    assert "note" not in out["points"][0]                                   # compact: notes stay in get_lab_results
    assert len(run_tool(conn, ids["A"], "mcp", "get_lab_history", {"user": "me", "test": "uric_acid", "limit": 1})["points"]) == 1
    with pytest.raises(PermissionError):
        run_tool(conn, ids["C"], "mcp", "get_lab_history", {"user": str(ids["A"]), "test": "uric_acid", "limit": None})
    with pytest.raises(ValueError, match="没有"):
        run_tool(conn, ids["A"], "mcp", "get_lab_history", {"user": "me", "test": "ggt", "limit": None})


def test_migration_backfill(conn, world):
    ids, _ = world
    c = ids["C"]
    rows = [("尿酸仪", "早上空腹测", "uric_acid"), (None, "家测，饭后 2 小时", "uric_acid"), ("Laverty", "非空腹采血", "alt"),
            ("Laverty", "化验单原名：空腹葡萄糖", "glucose_fasting"), ("Laverty", "参考上限是文件里写的", "ast")]
    for i, (lab, note, key) in enumerate(rows):
        conn.execute("""INSERT INTO lab_results (user_id, collected_on, test_key, test_name, value, lab, note)
                        VALUES (%s, %s, %s, %s, 1, %s, %s)""", (c, date(2026, 9, 1 + i), key, key, lab, note))
    sql = (resources.files("body_engine.migrations") / "200_lab_source_fasting.sql").read_text()
    conn.execute(sql[sql.index("UPDATE lab_results SET source"):])     # the backfill part, re-run on these rows
    got = [(r["source"], r["fasting"]) for r in conn.execute(
        "SELECT source, fasting FROM lab_results WHERE user_id = %s ORDER BY collected_on", (c,))]
    assert got == [("home_device", True), ("home_device", False), ("lab", False), ("lab", None), ("lab", None)]
