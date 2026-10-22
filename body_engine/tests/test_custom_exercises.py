"""Issue #189: each user's own custom exercises (rollout flag muscle_load, training/custom.py, migration 650).
- Added by the AI (add_custom_exercise) or the page; logged sets under that name then match (loads.Index) and the muscle load map
  counts them; nobody else ever sees them (matching, search, detail, endpoints).
- Names the shared catalog already has are refused; muscle keys must be the catalog's; a variant of a banned exercise is banned too.
- delete_custom_exercise asks first (ALWAYS_CONFIRM + the butler's ask list); the admin list shows names and counts, never who.
- Page: 动作库 → 我的动作 (custom-exercises.js formBody, run in node), 「让 Jarvis 补进动作库」 on the muscle map.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.ai import params as ai_params
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.training import catalog, custom, loads, muscle_load as ML, plans

STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
ROOT = Path(__file__).resolve().parents[2]
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CUSTOM_EX", "body_test_custom_exercises")
CSRF = {"X-Body-Web": "1"}
NEW = {"name_zh": "神秘划船", "name_en": "Mystery Row", "aliases": ["mystery row v2"], "primary_muscles": ["lats", "middle back"],
       "secondary_muscles": ["biceps"], "equipment": ["cable"]}


# ---------------------------------------------------------------- pure
def test_slug_round_trip():
    assert custom.slug_of(12) == "my_12" and custom.id_of("my_12") == 12
    assert custom.id_of("lat_pulldown") is None and custom.id_of("my_x") is None and custom.id_of(None) is None


def test_catalog_row_shape():
    row = custom.catalog_row({"id": 3, "name_zh": "弹力带直立划船", "name_en": "Band Upright Row Pulse", "aliases": [],
                              "primary_muscles": ["shoulders"], "secondary_muscles": ["traps"], "equipment": ["bands"]})
    assert row["slug"] == "my_3" and row["source"] == "user_custom" and row["category"] == "strength"
    assert row["family_slug"] == "upright_row" and row["joint_stress"]   # a variant of a restricted exercise carries its tags
    assert row["modality"] == "reps_only" and catalog.norm("弹力带直立划船") in row["_names"]
    view = catalog.format_exercise(row)   # what search / the detail make of it
    assert view["status"] == "caution" and view["primary_muscles"] == ["shoulders"]


def node_json(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = (f"globalThis.I18N = {{ t: (k, p) => k.replace(/\\{{(\\w+)\\}}/g, (_, x) => p[x]), lang: 'zh' }};\n"
             f"const X = require({json.dumps(str(STATIC / 'custom-exercises.js'))});\n"
             f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)


def test_form_body_in_node():
    out = node_json("""[
      X.formBody({ name_zh: " 神秘划船 ", name_en: "", aliases: "a, b，c、 ", primary: ["lats"], secondary: ["lats", "biceps"], equipment: ["cable"] }),
      X.formBody({ name_zh: "", primary: ["lats"] }),
      X.formBody({ name_zh: "x", primary: [] }),
      X.formBody({ name_zh: "x", primary: ["a", "b", "c", "d", "e"] }),
    ]""")
    assert out[0]["body"] == {"name_zh": "神秘划船", "name_en": "", "aliases": ["a", "b", "c"], "primary_muscles": ["lats"],
                              "secondary_muscles": ["biceps"], "equipment": ["cable"]}
    assert out[1]["error"] and out[2]["error"] and out[3]["error"]


def test_page_wiring():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    lib = app[app.index("async function exerciseLibrarySheet"):app.index("function splitPickerSheet")]
    assert 'id="ex-mine"' in lib and "BodyCustomExercises.openList" in lib and 'x.source === "user_custom"' in lib
    ml = (STATIC / "muscle-load.js").read_text(encoding="utf-8")
    assert 'id="mml-fill"' in ml and "C.ask(fillPrompt(S.data.unmatched))" in ml and "add_custom_exercise" in ml
    assert "custom-exercises.js" in (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '<script src="custom-exercises.js" defer></script>' in (STATIC / "index.html").read_text(encoding="utf-8")


def test_delete_asks_first_everywhere():
    assert "delete_custom_exercise" in ai_params.ALWAYS_CONFIRM
    settings = json.loads((ROOT / "assistant/.claude/settings.json").read_text(encoding="utf-8"))
    assert "mcp__body-engine__delete_custom_exercise" in settings["permissions"]["ask"]
    assert "mcp__body-engine__add_custom_exercise" not in settings["permissions"]["ask"]   # adding runs directly


# ---------------------------------------------------------------- database
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
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role) VALUES (%s, 'member') RETURNING id", (n,)).fetchone()["id"]
               for n in "AB"}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training", "exercises"]))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason)
                        VALUES (%s, 'upright_row', '直立划船', 'banned', '肩峰撞击')""", (ids["A"],))
        flags.set_flag(conn, "muscle_load", "all", actor="test")
        today = plans._user_today(conn, ids["A"])
    flags.invalidate()
    return ids, tok, today


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
    return {"Authorization": f"Bearer {token}", **CSRF}


def _set_flag(dsn, mode):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        flags.set_flag(c, "muscle_load", mode, actor="test")
    flags.invalidate()


def test_ai_adds_and_the_muscle_map_counts_it(conn, world, monkeypatch):
    ids, _, today = world
    a, b = ids["A"], ids["B"]
    logged = [{"id": "1", "date": today.isoformat(), "name": "背", "exercises": [
        {"exercise_id": None, "name": "神秘划船", "category": "strength", "sets": [{"reps": 10, "weight_kg": 40}, {"reps": 10, "weight_kg": 40}]}]}]
    monkeypatch.setattr(ML, "_sparky", lambda conn, uid, start, end: (logged, "ok"))
    before = ML.compute(conn, a, today)
    assert before["unmatched"] == ["神秘划船"] and {m["key"]: m for m in before["muscles"]}["lats"]["ranges"]["today"]["sets"] == 0
    out = run_tool(conn, a, "test", "add_custom_exercise", dict(NEW))
    assert out["slug"].startswith("my_") and out["source"] == "ai" and out["status"] == "active"
    conn.commit()
    after = ML.compute(conn, a, today)
    m = {x["key"]: x for x in after["muscles"]}
    assert after["unmatched"] == [] and m["lats"]["ranges"]["today"]["sets"] == 2 and m["biceps"]["ranges"]["today"]["sets"] == 1
    assert m["lats"]["ranges"]["today"]["volume_kg"] == 800
    # the alias and the English name match as well, but only for A
    assert loads.Index(conn, a).slug_for(None, "Mystery Row V2") == out["slug"]
    assert loads.Index(conn, b).slug_for(None, "神秘划船") is None
    assert {x["key"]: x for x in ML.compute(conn, b, today)["muscles"]}["lats"]["ranges"]["today"]["sets"] == 0
    # search and detail show it to A; B (even with a share) never sees it
    assert out["slug"] in [x["slug"] for x in catalog.search(conn, a, "神秘")]
    assert out["slug"] not in [x["slug"] for x in catalog.search(conn, b, "神秘")]
    assert catalog.get_exercise(conn, a, out["slug"])["name_zh"] == "神秘划船"
    with pytest.raises(ValueError):
        catalog.get_exercise(conn, b, out["slug"])


def test_names_muscles_and_banned_variants(conn, world):
    ids, _, _ = world
    a, b = ids["A"], ids["B"]
    with pytest.raises(ValueError, match="动作库里已经有"):
        custom.add(conn, a, {**NEW, "name_zh": "高位下拉", "name_en": None, "aliases": []})   # a shared catalog name
    with pytest.raises(ValueError, match="已经有"):
        custom.add(conn, a, {**NEW, "name_en": None, "aliases": []})                         # A's own name again
    with pytest.raises(ValueError, match="肌群"):
        custom.add(conn, a, {**NEW, "name_zh": "怪动作", "name_en": None, "aliases": [], "primary_muscles": ["wings"]})
    with pytest.raises(ValueError):
        custom.add(conn, a, {"name_zh": "无肌群", "primary_muscles": []})
    # the same name for another user is fine (each list is separate)
    other = custom.add(conn, b, {"name_zh": "神秘划船", "primary_muscles": ["背阔肌"]})
    assert other["primary_muscles"] == ["lats"]   # Chinese muscle names are accepted
    # a variant of A's banned upright row is banned for A ("禁用动作照常拦截"), only caution for B (no ban)
    v = custom.add(conn, a, {"name_zh": "弹力带直立划船脉冲", "name_en": "Band Upright Row Pulse", "primary_muscles": ["shoulders"],
                             "equipment": ["弹力带"]})
    assert v["status"] == "banned" and v["restriction_from"] == "upright_row" and v["equipment"] == ["bands"]
    assert v["slug"] not in [x["slug"] for x in catalog.search(conn, a, "直立划船", include_banned=False)]
    conn.commit()


def test_update_delete_and_privacy(client, conn, world, dsn):
    ids, tok, _ = world
    _set_flag(dsn, "all")
    r = client.get("/v1/users/me/training/custom-exercises", headers=H(tok["A"]))
    assert r.status_code == 200
    body = r.json()
    mine = next(x for x in body["exercises"] if x["name_zh"] == "神秘划船")
    assert {m["key"] for m in body["muscles"]} == set(custom.CD.MUSCLE_ZH) and body["equipment"]
    # B: not A's list, not A's ids
    assert client.get(f"/v1/users/{ids['A']}/training/custom-exercises", headers=H(tok["B"])).status_code == 403
    assert client.patch(f"/v1/users/me/training/custom-exercises/{mine['id']}", json={"name_en": "x"}, headers=H(tok["B"])).status_code == 404
    assert client.delete(f"/v1/users/me/training/custom-exercises/{mine['id']}", headers=H(tok["B"])).status_code == 404
    assert client.post(f"/v1/users/{ids['A']}/training/custom-exercises", json=NEW, headers=H(tok["B"])).status_code == 403
    # A edits: unspecified fields stay, an empty English name clears it
    r = client.patch(f"/v1/users/me/training/custom-exercises/{mine['id']}", json={"name_en": "", "secondary_muscles": ["biceps", "forearms"]},
                     headers=H(tok["A"]))
    assert r.status_code == 200 and r.json()["name_en"] is None and r.json()["secondary_muscles"] == ["biceps", "forearms"]
    assert r.json()["primary_muscles"] == ["lats", "middle back"]
    assert client.patch(f"/v1/users/me/training/custom-exercises/{mine['id']}", json={"primary_muscles": ["nope"]},
                        headers=H(tok["A"])).status_code == 422
    r = client.post("/v1/users/me/training/custom-exercises", json={"name_zh": "手动加的", "primary_muscles": ["calves"]}, headers=H(tok["A"]))
    assert r.status_code == 200 and r.json()["source"] == "manual"
    assert client.delete(f"/v1/users/me/training/custom-exercises/{r.json()['id']}", headers=H(tok["A"])).status_code == 200
    # the AI deletes by name, only among the user's own
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        with pytest.raises(ValueError):
            run_tool(c, ids["B"], "test", "delete_custom_exercise", {"exercise": "弹力带直立划船脉冲"})
        out = run_tool(c, ids["A"], "test", "delete_custom_exercise", {"exercise": "弹力带直立划船脉冲"})
        assert out["deleted"]["name_zh"] == "弹力带直立划船脉冲"
        assert c.execute("SELECT count(*) AS n FROM custom_exercises WHERE name_zh = '弹力带直立划船脉冲'").fetchone()["n"] == 0


def test_admin_list_shows_names_and_counts_not_users(conn, world, dsn, capsys, monkeypatch):
    rows = custom.admin_names(conn)
    hit = next(r for r in rows if r["name_zh"] == "神秘划船")
    assert hit["users"] == 2 and set(hit) == {"name_zh", "name_en", "users", "last_added"}
    from body_engine import cli
    monkeypatch.setattr(cli, "connect", lambda: psycopg.connect(dsn, row_factory=dict_row))
    monkeypatch.setattr("sys.argv", ["body-engine", "exercises", "custom-list"])
    cli.main()
    printed = capsys.readouterr().out
    assert "神秘划船" in printed and "   2  " in printed
    assert "A" not in printed.split() and "B" not in printed.split()


def test_flag_off_changes_nothing(client, conn, world, dsn):
    ids, tok, _ = world
    _set_flag(dsn, "off")
    try:
        assert client.get("/v1/users/me/training/custom-exercises", headers=H(tok["A"])).status_code == 404
        assert client.post("/v1/users/me/training/custom-exercises", json=NEW, headers=H(tok["A"])).status_code == 404
        with pytest.raises(flags.FlagOff):
            run_tool(conn, ids["A"], "test", "add_custom_exercise", {**NEW, "name_zh": "关着"})
        with pytest.raises(flags.FlagOff):
            run_tool(conn, ids["A"], "test", "delete_custom_exercise", {"exercise": "神秘划船"})
    finally:
        _set_flag(dsn, "all")
