"""Per-user diet constraints (issue #76, nutrition/constraints.py, migration 450):
- the vocabulary, validation, versioned set / end and history, what is in effect on a day
- carrying a user's own profile card text over once (db.DATA_STEPS), generic patterns, values from the text
- a user with constraints vs a user without: rules context, briefing, diet profile, food logging warnings
- AI tools (write only for oneself, read with the nutrition share) and GET /diet-constraints
Test limits on purpose differ from any real person's.
"""
import os
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, db
from body_engine.assistant import briefing, diet_profile, sparky_write
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.nutrition import constraint_params as CP, constraints as C

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_DIET_CONSTRAINTS", "body_test_diet_constraints")
TEXT = "痛风 → 低嘌呤、多喝水；红肉 ≤ 300g/周，肥牛 ≤ 2 次/周，红肉 ≤ 3 顿/周；高血脂 → 低饱和脂肪、不喝酒。"


# ---------------------------------------------------------------- no database
def test_parse_profile_text_generic_patterns():
    got = C.parse_profile_text(TEXT)
    assert {"kind": "red_meat_g_per_week", "value": 300.0} in got
    assert {"kind": "red_meat_meals_per_week", "value": 3.0} in got
    assert {"kind": "food_times_per_week", "target": "肥牛", "value": 2.0} in got
    assert {"kind": "low_purine"} in got and {"kind": "low_saturated_fat"} in got and {"kind": "avoid_alcohol"} in got
    assert len(got) == 6
    assert C.parse_profile_text("每天走 8000 步；早睡") == [] and C.parse_profile_text(None) == []


def test_kinds_have_both_languages_and_a_reference():
    for k, v in CP.KINDS.items():
        assert v["label"][0] and v["label"][1].isascii() and v["reference"], k
        assert (v["unit"] is None) == (v["range"] is None), k


def test_sat_fat_warning_only_with_the_constraint():
    item = {"food": "芝士汉堡", "calories": 700, "protein_g": 35, "carbs_g": 50, "fat_g": 40, "saturated_fat_g": 15,
            "alcohol_g": None}
    assert sparky_write._check_food(item) == []
    w = sparky_write._check_food(item, low_sat_fat=True)
    assert len(w) == 1 and "饱和脂肪" in w[0] and "脂肪肝" not in w[0]


def test_tools_registered():
    assert not TOOLS_BY_NAME["get_diet_constraints"].writes
    for name in ("set_diet_constraint", "end_diet_constraint"):
        t = TOOLS_BY_NAME[name]
        assert t.writes and "user" not in t.input_schema["properties"] and len(t.description) < 60
    from body_engine.ai.chat import ALWAYS_CONFIRM
    assert not {"set_diet_constraint", "end_diet_constraint"} & ALWAYS_CONFIRM   # history is kept: not a delete


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", ""):
        pytest.exit("the test DB must not be a real DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        db.migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def world(dsn):
    """A: will get constraints; B: A shares nutrition with B; C: no share, no constraints."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"] for n in "ABC"}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in "ABC"}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["nutrition"]))
    return ids, tok


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_set_change_end_and_history(conn, world):
    ids, _ = world
    a = ids["A"]
    d0 = date(2026, 9, 1)
    res = C.set_constraint(conn, a, "red_meat_g_per_week", 350, basis="医生建议", effective_from=d0)
    assert res["changed"] and res["before"] is None and res["constraint"]["text"] == "红肉每周 ≤ 350 g"
    assert C.set_constraint(conn, a, "red_meat_g_per_week", 350, effective_from=d0)["changed"] is False   # same value: no new version
    res = C.set_constraint(conn, a, "red_meat_g_per_week", 300, effective_from=d0 + timedelta(days=30))
    assert res["before"]["value"] == 350 and res["constraint"]["basis"] == "医生建议"   # basis carried over when not given
    assert C.for_user(conn, a, d0 + timedelta(days=10)).red_meat_g == 350                # the old version still answers for its days
    assert C.for_user(conn, a, d0 + timedelta(days=30)).red_meat_g == 300
    assert C.for_user(conn, a, d0 - timedelta(days=1)).empty                              # before anything was set
    C.set_constraint(conn, a, "food_times_per_week", 2, target="肥牛", match=["fatty beef"], effective_from=d0)
    C.set_constraint(conn, a, "low_purine", effective_from=d0)
    lst = C.listing(conn, a, history=True)
    assert {c["kind"] for c in lst["constraints"]} == {"red_meat_g_per_week", "food_times_per_week", "low_purine"}
    assert len(lst["history"]) == 4 and {k["kind"] for k in lst["kinds"]} == set(CP.KINDS)
    end = C.end_constraint(conn, a, "low_purine", effective_from=d0 + timedelta(days=40))
    assert end["changed"] and not C.for_user(conn, a, d0 + timedelta(days=40)).low_purine
    assert C.for_user(conn, a, d0 + timedelta(days=39)).low_purine
    assert C.end_constraint(conn, a, "avoid_alcohol")["changed"] is False
    with pytest.raises(ValueError, match="没有这种约束"):
        C.set_constraint(conn, a, "no_such_kind", 1)
    with pytest.raises(ValueError, match="哪种食物"):
        C.set_constraint(conn, a, "food_times_per_week", 1)
    with pytest.raises(ValueError, match="超出范围"):
        C.set_constraint(conn, a, "red_meat_g_per_week", 99999)
    with pytest.raises(ValueError, match="数值"):
        C.set_constraint(conn, a, "red_meat_meals_per_week", None)
    with pytest.raises(ValueError, match="生效日期"):
        C.set_constraint(conn, a, "red_meat_g_per_week", 280, effective_from=d0)
    conn.commit()


def test_carry_over_from_the_users_own_profile_card(conn, world):
    """The data step reads each user's own profile card text: A's card has constraints, B's has none, C has no card."""
    ids, _ = world
    b = ids["B"]
    conn.execute("""INSERT INTO assistant_profile (user_id, key, label, value, sort, source)
                    VALUES (%s, 'health_constraints', '健康约束', %s, 10, 'assistant')""", (b, TEXT))
    conn.execute("DELETE FROM schema_migrations WHERE name = '450_diet_constraints_data'")
    applied = db.migrate(conn)
    assert applied == ["450_diet_constraints_data"] and db.migrate(conn) == []   # once
    got = C.for_user(conn, b)
    assert got.red_meat_g == 300 and got.red_meat_meals == 3 and got.low_purine and got.low_sat_fat and got.avoid_alcohol
    assert got.food_limits[0]["target"] == "肥牛" and got.food_limits[0]["max"] == 2
    assert {r["source"] for r in got.rows} == {"migration"} and all(r["basis"] == CP.CARRY_OVER_BASIS[0] for r in got.rows)
    assert C.for_user(conn, ids["C"]).empty
    conn.execute("DELETE FROM diet_constraints WHERE user_id = %s", (b,))
    conn.execute("DELETE FROM assistant_profile WHERE user_id = %s", (b,))
    conn.commit()


def test_with_and_without_constraints_everywhere(conn, world):
    from body_engine.rules import context as rules_context
    ids, _ = world
    a, c = ids["A"], ids["C"]
    assert rules_context.load(conn, a).diet.red_meat_g == 300
    assert rules_context.load(conn, c).diet.empty
    assert "红肉每周 ≤ 300 g" in briefing.build(conn, a, a)["diet_constraints"]
    assert briefing.build(conn, c, c)["diet_constraints"] == []
    view = diet_profile.view(conn, a, a)
    assert {x["kind"] for x in view["constraint_rules"]["current"]} == {"red_meat_g_per_week", "food_times_per_week"}
    assert any(h["kind"] == "low_purine" for h in view["constraint_rules"]["history"])   # ended one shows in the log
    assert diet_profile.view(conn, c, c)["constraint_rules"] == {"current": [], "history": []}


def test_tools_and_api_auth(conn, world, client):
    ids, tok = world
    a, b, c = ids["A"], ids["B"], ids["C"]
    res = run_tool(conn, c, "butler", "set_diet_constraint",
                   {"kind": "avoid_alcohol", "value": None, "target": None, "match": None, "basis": "自己想戒", "effective_from": None})
    assert res["changed"] and C.for_user(conn, c).avoid_alcohol
    with pytest.raises(PermissionError):   # writes only for oneself
        run_tool(conn, b, "butler", "set_diet_constraint",
                 {"user": str(a), "kind": "low_purine", "value": None, "target": None, "match": None, "basis": None,
                  "effective_from": None})
    seen = run_tool(conn, b, "butler", "get_diet_constraints", {"user": str(a), "history": False})
    assert any(x["kind"] == "red_meat_g_per_week" for x in seen["constraints"])
    with pytest.raises(PermissionError):
        run_tool(conn, c, "butler", "get_diet_constraints", {"user": str(a), "history": False})
    run_tool(conn, c, "butler", "end_diet_constraint", {"kind": "avoid_alcohol", "target": None, "effective_from": None})
    assert C.for_user(conn, c, date.today() + timedelta(days=1)).empty
    conn.commit()
    r = client.get("/v1/users/me/diet-constraints?history=true", headers=H(tok["A"]))
    assert r.status_code == 200 and r.json()["history"] and r.json()["constraints"]
    assert client.get(f"/v1/users/{a}/diet-constraints", headers=H(tok["B"])).status_code == 200
    assert client.get(f"/v1/users/{a}/diet-constraints", headers=H(tok["C"])).status_code == 403
    en = client.get("/v1/users/me/diet-constraints", headers={**H(tok["A"]), "X-Body-Lang": "en"}).json()
    assert any(x["label"].startswith("Red meat") for x in en["constraints"])


def test_cli_diet_constraint_set_is_idempotent_and_lists(dsn, world, monkeypatch, capsys):
    """Deploy step for carried-over data the profile card does not hold (issue #76): run twice, the second run changes nothing."""
    import json
    import sys
    from body_engine import cli
    ids, _ = world
    c = ids["C"]
    monkeypatch.setattr(cli, "connect", lambda: psycopg.connect(dsn, row_factory=dict_row))

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["body-engine", "diet-constraint-set", "--user-id", str(c), *argv])
        cli.main()
        return json.loads(capsys.readouterr().out)

    first = run("--kind", "red_meat_meals_per_week", "--value", "4", "--basis", "测试")
    assert first["changed"] and first["constraint"]["source"] == "user"
    assert run("--kind", "red_meat_meals_per_week", "--value", "4", "--basis", "测试")["changed"] is False
    lst = run("--list")
    assert [x["kind"] for x in lst["constraints"]] == ["red_meat_meals_per_week"] and "kinds" not in lst
    assert len(run("--list", "--history")["history"]) >= 1
    assert run("--kind", "red_meat_meals_per_week", "--end")["changed"]
    with pytest.raises(SystemExit):
        run("--kind", "red_meat_meals_per_week")            # a value is needed
    with pytest.raises(SystemExit):
        run("--value", "3")                                  # --kind or --list
    capsys.readouterr()
