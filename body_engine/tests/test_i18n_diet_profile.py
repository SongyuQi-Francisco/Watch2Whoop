"""「我的饮食档案」 in English (issue #42): with `X-Body-Lang: en` the server-built labels of GET /diet-profile (goal set
names, the no-goals error, error details) carry no Chinese; without the header and in AI tool runs they stay Chinese.
Separate test DB BODY_TEST_DB_NAME (default body_test_i18n_diet_profile); skipped without Postgres."""
import os
import re

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.goals import nutrition
from body_engine.i18n import use_lang

CJK = re.compile(r"[　-〿一-鿿＀-￯]")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_i18n_diet_profile")
EN = {"X-Body-Lang": "en"}


@pytest.fixture(scope="module")
def world():
    if TEST_DB in ("body", "body_pr1", "body_accept", "body_test_accept", ""):
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
        migrate(conn)
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '1990-01-01', 180) "
                               "RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B")}
        tokens = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        nutrition.set_goals(conn, ids["A"], reason="体重两周没动", set_by="butler", actor_user_id=ids["A"],
                            training={"calories": 2500, "protein_g": 180, "carbs_g": 270, "fat_g": 70},
                            rest={"calories": 2100, "protein_g": 180, "carbs_g": 170, "fat_g": 70},
                            training_weekdays=[1, 2, 3, 4, 5])
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield dsn, ids, tokens, TestClient(api.app)
    api.pool.close()


def h(token, en=False):
    return {"Authorization": f"Bearer {token}", **(EN if en else {})}


def test_goal_set_names_follow_the_language(world):
    dsn, ids, tokens, client = world
    zh = client.get("/v1/users/me/diet-profile", headers=h(tokens["A"])).json()
    en = client.get("/v1/users/me/diet-profile", headers=h(tokens["A"], en=True)).json()
    assert [s["name"] for s in zh["goals"]["sets"]] == ["训练日", "休息日"]
    assert [s["name"] for s in en["goals"]["sets"]] == ["Training day", "Rest day"]
    assert en["goals"]["reason"] == "体重两周没动"   # stored data stays as written
    # AI tools always run in zh, also when the request is English
    with psycopg.connect(dsn, row_factory=dict_row) as conn, use_lang("en"):
        v = run_tool(conn, ids["A"], "test", "get_diet_profile", {})
    assert [s["name"] for s in v["goals"]["sets"]] == ["训练日", "休息日"]


def test_no_goals_and_errors_in_english(world):
    _, ids, tokens, client = world
    b = client.get("/v1/users/me/diet-profile", headers=h(tokens["B"], en=True)).json()
    assert b["goals"]["sets"] == [] and b["goals"]["error"] and not CJK.search(b["goals"]["error"])
    assert CJK.search(client.get("/v1/users/me/diet-profile", headers=h(tokens["B"])).json()["goals"]["error"])
    r = client.get(f"/v1/users/{ids['A']}/diet-profile?brief=true", headers=h(tokens["B"], en=True))
    assert r.status_code == 403 and not CJK.search(r.json()["detail"])
