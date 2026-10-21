"""Several logged food entries -> a quick-meal template, or merged into one entry (issue #93): nutrition sums (unknown sugar /
fiber / sodium stay unknown), red-meat label carried over, split / rollback_write restores the originals, the template shows up
in the quick-meal list, and nobody can read, merge or split another user's entries. SparkyFitness is faked per user (each user's
own key only sees their own entries). Needs Postgres (BODY_TEST_DB_NAME_COMBINE, default body_test_food_combine)."""
import os
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.ai.chat import ALWAYS_CONFIRM
from body_engine.assistant import food_combine as FC, meals, writelog
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.nutrition import daily
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_COMBINE", "body_test_food_combine")
DAY = date(2026, 10, 21)
FAKES: dict = {}


class FakeSparky:
    """One user's SparkyFitness: food entries by date, custom foods, meal types."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.foods: list[dict] = []
        self.n = 0

    def add(self, name, meal, cal, p, c, f, *, unit="1 份", qty=1, serving=1, sat=None, sugar=None, fiber=None, sodium=None,
            time=None, day=DAY):
        self.n += 1
        rid = f"e{self.n}"
        self.rows[rid] = {"id": rid, "entry_date": day.isoformat(), "entry_time": time, "meal_type": meal, "food_name": name,
                          "quantity": qty, "serving_size": serving, "unit": unit, "calories": cal, "protein": p, "carbs": c,
                          "fat": f, "saturated_fat": sat, "sugars": sugar, "dietary_fiber": fiber, "sodium": sodium,
                          "food_id": f"food-{rid}", "variant_id": f"var-{rid}", "notes": None, "source": "app",
                          "source_id": f"src-{rid}"}
        return rid

    def get(self, path, **params):
        if path.startswith("/food-entries/by-date/"):
            d = path.rsplit("/", 1)[1]
            return [r for r in self.rows.values() if r["entry_date"][:10] == d]
        if path.startswith("/food-entries/range/"):
            s, e = path.split("/")[-2:]
            return [r for r in self.rows.values() if s <= r["entry_date"][:10] <= e]
        if path == "/meal-types":
            return [{"id": f"mt-{m}", "name": m} for m in ("breakfast", "lunch", "dinner", "snacks")]
        if path == "/foods/foods-paginated":
            return {"foods": self.foods}
        raise AssertionError(path)

    def request(self, method, path, body=None, params=None, multipart_field=None):
        if method == "POST" and path == "/foods":
            food = {"id": f"food-{len(self.foods)}", "default_variant": {"id": f"var-{len(self.foods)}"}, **body}
            self.foods.append(food)
            return food
        if method == "POST" and path == "/food-entries":
            self.n += 1
            rid = f"e{self.n}"
            food = next((x for x in self.foods if x["id"] == body.get("food_id")), None)
            src = food or {}
            self.rows[rid] = {"id": rid, "entry_date": body["entry_date"], "entry_time": body.get("entry_time"),
                              "meal_type": body.get("meal_type"), "food_name": src.get("name", body.get("_name", "restored")),
                              "quantity": body.get("quantity", 1), "serving_size": 1, "unit": body.get("unit"),
                              "calories": src.get("calories"), "protein": src.get("protein"), "carbs": src.get("carbs"),
                              "fat": src.get("fat"), "saturated_fat": src.get("saturated_fat"), "sugars": src.get("sugars"),
                              "dietary_fiber": src.get("dietary_fiber"), "sodium": src.get("sodium"),
                              "food_id": body.get("food_id"), "variant_id": body.get("variant_id"), "notes": body.get("notes"),
                              "source": body.get("source"), "source_id": body.get("source_id")}
            return self.rows[rid]
        if method == "DELETE" and path.startswith("/food-entries/"):
            self.rows.pop(path.rsplit("/", 1)[1])
            return None
        raise AssertionError((method, path))


class Restoring(FakeSparky):
    """Recreating a deleted entry (rollback / split) brings back its own name and nutrition (real SparkyFitness keeps the food)."""

    def __init__(self):
        super().__init__()
        self.gone: dict[str, dict] = {}

    def request(self, method, path, body=None, params=None, multipart_field=None):
        if method == "DELETE" and path.startswith("/food-entries/"):
            rid = path.rsplit("/", 1)[1]
            self.gone[self.rows[rid]["food_id"]] = self.rows[rid]
        row = super().request(method, path, body, params, multipart_field)
        if method == "POST" and path == "/food-entries" and body.get("food_id") in self.gone:
            old = self.gone[body["food_id"]]
            row.update({k: old[k] for k in ("food_name", "calories", "protein", "carbs", "fat", "saturated_fat", "sugars",
                                             "dietary_fiber", "sodium", "serving_size", "quantity", "unit")})
        return row


# ---------------------------------------------------------------- no database
def test_helpers_and_tool():
    t = TOOLS_BY_NAME["combine_food_entries"]
    assert t.writes and t.scope == "nutrition" and "combine_food_entries" not in ALWAYS_CONFIRM   # reversible: no confirm
    assert "rollback_write" in ALWAYS_CONFIRM                                                     # splitting back asks first
    assert FC.grams_of({"unit": "150 g", "quantity": 1}) == 150 and FC.grams_of({"unit": "g", "quantity": 80}) == 80
    assert FC.grams_of({"unit": "1 份", "quantity": 1, "food": "鸡胸肉（150g，红肉 0g）"}) == 150
    assert FC.grams_of({"unit": "1 碗", "quantity": 1, "food": "面条"}) is None
    assert FC.clean_name("鸡胸肉（150g，红肉 0g）") == "鸡胸肉" and FC.clean_name("牛奶 (250ml)") == "牛奶"
    assert FC.clean_name("咖啡（无糖）") == "咖啡（无糖）"


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_test_accept", ""):
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
    return dsn


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm, timezone) VALUES (%s, '1990-01-01', 175, "
                               "'Australia/Sydney') RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tok["A_ro"] = auth.create_token(conn, ids["A"], "ro", ["read"])
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["nutrition"]))
        # rollout flag meal_merge (docs/rollout.md): on for the beta cohort (A, B); C is not in it
        flags.set_flag(conn, "meal_merge", "cohort", actor="test")
        for n in ("A", "B"):
            flags.cohort_add(conn, ids[n], actor="test")
        flags.invalidate()
    return ids, tok


@pytest.fixture()
def sparky(world, monkeypatch):
    """Each user gets their own fake SparkyFitness (their own key)."""
    ids, _ = world
    fakes = FAKES.setdefault("users", {uid: Restoring() for uid in ids.values()})   # one SparkyFitness per user for the module
    monkeypatch.setattr(sparky_client, "write_client_for_user", lambda conn, uid: fakes[uid])
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: fakes[uid])
    return fakes


def _lunch(fake):
    return [fake.add("鸡胸肉（150g）", "lunch", 165, 31, 0, 3.6, unit="150 g", sat=1.0, sodium=100, time="12:30"),
            fake.add("米饭", "lunch", 260, 5, 57, 0.5, unit="200 g", sugar=0.2, fiber=0.8, time="12:31"),
            fake.add("牛肉片（80g，红肉 80g）", "lunch", 200, 20, 0, 13, sat=5.5, time="12:32")]


def test_merge_sums_and_split(conn, world, sparky):
    ids, _ = world
    a = ids["A"]
    fake = sparky[a]
    e = _lunch(fake)
    out = FC.combine(conn, a, DAY, e, "merge", "午饭")
    m = out["merge"]
    assert m["replaced"] == 3 and set(fake.rows) == {m["entry_id"]}                 # the three are replaced by one
    new = fake.rows[m["entry_id"]]
    assert new["food_name"] == "午饭（红肉 80g）" and new["meal_type"] == "lunch" and new["entry_time"] == "12:30"
    t = out["totals"]
    assert t["calories"] == 625 and t["protein_g"] == 56 and t["carbs_g"] == 57 and t["fat_g"] == 17.1
    assert t["saturated_fat_g"] == 6.5 and t["red_meat_g"] == 80
    assert t["sodium_mg"] == 100 and t["sugar_g"] == 0.2 and t["fiber_g"] == 0.8                   # only the entries that know them
    assert new["calories"] == 625 and new["sodium"] == 100 and new["unit"] == "430 g"   # one portion = the whole meal (all portions known in grams)
    # the Today page sees which entry is a merge
    rows = daily.entries(conn, a, DAY, DAY, client=fake)
    assert rows[0]["merge_id"] == m["id"] and rows[0]["calories"] == 625
    # split: the three come back with their own nutrition, the merged one is gone
    s = FC.split(conn, a, m["id"])
    assert s["restored"] == 3 and m["entry_id"] not in fake.rows
    back = sorted((r["food_name"], r["calories"]) for r in fake.rows.values())
    assert back == [("牛肉片（80g，红肉 80g）", 200), ("米饭", 260), ("鸡胸肉（150g）", 165)]
    with pytest.raises(LookupError):
        FC.split(conn, a, m["id"])                                                   # once
    with pytest.raises(ValueError):
        FC.combine(conn, a, DAY, [next(iter(fake.rows))], "merge")                   # merging needs two
    conn.commit()


def test_template_and_quick_list(conn, world, sparky):
    ids, _ = world
    a = ids["A"]
    fake = sparky[a]
    e = list(fake.rows)
    out = FC.combine(conn, a, DAY, e, "template", "常吃午饭")
    tpl = out["template"]
    assert tpl["items"] == 3 and len(fake.rows) == 3                                  # entries stay
    t = next(x for x in meals.list_templates(conn, a) if x["id"] == tpl["id"])        # shows up in the quick-meal list
    by = {i["food"]: i for i in t["items"]}
    assert by["鸡胸肉"]["grams"] == 150 and by["米饭"]["grams"] == 200 and by["牛肉片"]["grams"] == 80
    assert by["牛肉片"]["red_meat_g"] == 80 and t["totals"]["kcal"] == 625 and t["meal"] == "lunch"
    assert not t["needs_confirmation"]
    # a portion not in grams: kept at its nutrition, grams to confirm
    odd = fake.add("面条", "dinner", 300, 10, 60, 2, unit="1 碗")
    t2 = FC.combine(conn, a, DAY, [odd], "template", "面")["template"]
    assert t2["needs_confirmation"]
    with pytest.raises(ValueError):                                                   # same name twice
        FC.combine(conn, a, DAY, [odd], "template", "面")
    conn.commit()


def test_both_via_tool_and_rollback(conn, world, sparky):
    ids, _ = world
    a = ids["A"]
    fake = sparky[a]
    fake.rows.clear()
    e = _lunch(fake)
    out = run_tool(conn, a, "butler", "combine_food_entries", {"date": DAY.isoformat(), "entry_ids": e, "action": "both",
                                                                 "name": "健身午饭", "meal": None})
    assert out["template"]["name"] == "健身午饭" and out["merge"]["replaced"] == 3
    log = {(r["kind"], r["action"]): r for r in writelog.list_log(conn, a)}
    assert log[("merge", "merge")]["rollback"] == "ok" and log[("template", "create")]["rollback"] == "ok"
    res = writelog.rollback(conn, a, log[("merge", "merge")]["id"], client=fake)        # rollback_write splits it back
    assert res["result"]["restored"] == 3 and len(fake.rows) == 3
    res = writelog.rollback(conn, a, log[("template", "create")]["id"])
    assert res["result"]["template_removed"] == out["template"]["id"]
    assert all(t["id"] != out["template"]["id"] for t in meals.list_templates(conn, a))
    with pytest.raises(ValueError):
        writelog.rollback(conn, a, log[("merge", "merge")]["id"], client=fake)        # once
    conn.commit()


def test_cross_user(conn, world, sparky, dsn):
    ids, tok = world
    a, b = ids["A"], ids["B"]
    fa, fb = sparky[a], sparky[b]
    fa.rows.clear()
    e = _lunch(fa)
    m = FC.combine(conn, a, DAY, e[:2], "merge")["merge"]
    conn.commit()
    # B's own key does not see A's entries: merging / templating A's ids is "not found", even with a nutrition share
    with pytest.raises(LookupError):
        FC.combine(conn, b, DAY, [m["entry_id"], e[2]], "merge")
    with pytest.raises(LookupError):
        run_tool(conn, b, "butler", "combine_food_entries", {"date": DAY.isoformat(), "entry_ids": [e[2]], "action": "template",
                                                              "name": None, "meal": None})
    with pytest.raises(LookupError):
        FC.split(conn, b, m["id"])                                                     # A's merge
    with pytest.raises(ValueError):
        writelog.rollback(conn, b, writelog.list_log(conn, a)[0]["id"])               # A's log row
    conn.rollback()
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=2, open=True)
    try:
        c = TestClient(api.app)
        hb, ha = {"Authorization": f"Bearer {tok['B']}"}, {"Authorization": f"Bearer {tok['A']}"}
        body = {"date": DAY.isoformat(), "entry_ids": [e[2]], "action": "template"}
        assert c.post(f"/v1/users/{a}/nutrition/combine", json=body, headers=hb).status_code == 403     # writes only for oneself
        assert c.post("/v1/users/me/nutrition/combine", json=body, headers={"Authorization": f"Bearer {tok['A_ro']}"}).status_code == 403
        assert c.post("/v1/users/me/nutrition/combine", json=body, headers=hb).status_code == 404       # not B's entry
        assert c.delete(f"/v1/users/me/nutrition/merges/{m['id']}", headers=hb).status_code == 404
        assert c.delete(f"/v1/users/{a}/nutrition/merges/{m['id']}", headers=hb).status_code == 403
        assert c.post("/v1/users/me/nutrition/combine", json={**body, "action": "explode"}, headers=ha).status_code == 422
        r = c.delete(f"/v1/users/me/nutrition/merges/{m['id']}", headers=ha)
        assert r.status_code == 200 and r.json()["restored"] == 2
    finally:
        api.pool.close()
    assert fb.rows == {}                                                                  # B's data untouched throughout


def test_flag_off_hides_everything(conn, world, sparky, dsn):
    """meal_merge off for C (not in beta): the tool says not available, the endpoints 404, /v1/web/me has no meal_merge."""
    ids, tok = world
    c = ids["C"]
    fc = sparky[c]
    e = _lunch(fc)
    with pytest.raises(flags.FlagOff):
        FC.combine(conn, c, DAY, e, "merge")
    with pytest.raises(LookupError, match="还没有对你开放"):
        run_tool(conn, c, "butler", "combine_food_entries", {"date": DAY.isoformat(), "entry_ids": e, "action": "template",
                                                              "name": None, "meal": None})
    conn.rollback()
    assert len(fc.rows) == 3                                                           # nothing changed
    tc = auth.create_token(conn, c, "t", ["read", "write"])
    conn.commit()
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=2, open=True)
    try:
        cl = TestClient(api.app)
        h = {"Authorization": f"Bearer {tc}"}
        assert cl.post("/v1/users/me/nutrition/combine", json={"date": DAY.isoformat(), "entry_ids": e, "action": "merge"},
                       headers=h).status_code == 404
        assert cl.delete("/v1/users/me/nutrition/merges/1", headers=h).status_code == 404
        assert "meal_merge" not in cl.get("/v1/users/me/flags", headers=h).json().get("flags", [])
        ha = {"Authorization": f"Bearer {tok['A']}"}
        assert "meal_merge" in cl.get("/v1/users/me/flags", headers=ha).json().get("flags", [])
    finally:
        api.pool.close()
