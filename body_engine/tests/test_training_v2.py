"""Training Module V2 tests:
- Curated bilingual exercise catalog (`catalog_data.py` + `catalog.py`) and `free-exercise-db` JSON ingestion
- 3 / 4 / 5-day split templates, weekly muscle-group working set accounting, and readiness autoregulation (`plans.py`)
- Banned-exercise safety gates on `propose_plan`, `save_plan`, and `swap_exercise`
- SparkyFitness best-effort bidirectional sync for exercises, presets, and weekly plans
- MCP tools (`search_exercises`, `get_exercise`, `propose_plan`, `save_plan`, `get_week_plan`, `swap_exercise`) and HTTP endpoints (`/v1/users/{user}/training/...`) with multi-user authorization.
"""
import json
import os
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import md_seed
from body_engine.training import catalog, catalog_data, params as P, plans

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_TRAINING_V2", "body_test_training_v2")
CSRF = {"X-Body-Web": "1"}


# ---------------------------------------------------------------- pure-function tests (no DB required)
def test_catalog_covers_all_md_seed_and_split_template_exercises():
    by_slug = {e["slug"]: e for e in catalog_data.EXERCISES}
    assert len(by_slug) == len(catalog_data.EXERCISES) and len(by_slug) >= 65

    # Every exercise in records/md_seed.py must exist in catalog_data
    for ref, _m in md_seed.EXERCISES:
        assert ref in by_slug, f"missing md_seed exercise: {ref}"

    # Every exercise in 3/4/5-day split templates must exist in catalog_data and not be default-banned
    for split_key, tpl in P.SPLIT_TEMPLATES.items():
        assert tpl["days_per_week"] in (3, 4, 5)
        for dow, sname, ex_rows in tpl["days"]:
            assert 1 <= dow <= 7 and sname and ex_rows
            for slug, sets, lo, hi, rest, dur, _note in ex_rows:
                assert slug in by_slug, f"{split_key} references unknown slug {slug}"
                ex = by_slug[slug]
                assert ex["default_status"] != "banned", f"{split_key} includes default-banned exercise {slug}"
                assert sets >= 1


def test_convert_free_exercise_db_entry():
    raw = {
        "id": "Barbell_Curl",
        "name": "Barbell Curl",
        "force": "pull",
        "level": "beginner",
        "mechanic": "isolation",
        "equipment": "barbell",
        "primaryMuscles": ["biceps"],
        "secondaryMuscles": ["forearms"],
        "instructions": ["Stand up with your torso upright while holding a barbell at a shoulder-width grip."],
        "category": "strength",
    }
    converted = catalog.convert_free_exercise_db_entry(raw)
    assert converted["slug"] == "barbell_curl"
    assert converted["name_en"] == "Barbell Curl"
    assert converted["equipment"] == ["barbell"]
    assert converted["primary_muscles"] == ["biceps"]
    assert converted["secondary_muscles"] == ["forearms"]
    assert converted["movement_pattern"] == "elbow_flexion"
    assert converted["source"] == "free-exercise-db"


def test_splits_and_volume_summary():
    splits = plans.list_splits()
    keys = [s["split_type"] for s in splits]
    assert keys == ["3_day_ppl", "3_day_full", "4_day_upper_lower", "5_day_ul_ppl"]
    by_slug = {e["slug"]: e for e in catalog_data.EXERCISES}
    days = []
    for dow, sname, exs in P.SPLIT_TEMPLATES["4_day_upper_lower"]["days"]:
        days.append({
            "day_of_week": dow,
            "name": sname,
            "exercises": [
                {
                    "slug": slug,
                    "category": by_slug[slug]["category"],
                    "sets": sets,
                    "primary_muscles": by_slug[slug]["primary_muscles"],
                    "secondary_muscles": by_slug[slug]["secondary_muscles"],
                }
                for slug, sets, *_ in exs
            ],
        })
    vol = plans.volume_summary(days)
    assert set(vol) == {"back", "chest", "legs", "shoulders", "arms", "core"}
    assert vol["back"]["direct_sets"] >= 14 and vol["back"]["frequency_days"] >= 2
    assert vol["legs"]["direct_sets"] >= 14 and vol["legs"]["frequency_days"] == 2
    assert vol["chest"]["direct_sets"] >= 10


def test_v2_tools_registered_and_read_write_flags():
    for r_tool in ("search_exercises", "get_exercise", "propose_plan", "get_week_plan"):
        assert r_tool in TOOLS_BY_NAME and not TOOLS_BY_NAME[r_tool].writes
    for w_tool in ("save_plan", "swap_exercise"):
        assert w_tool in TOOLS_BY_NAME and TOOLS_BY_NAME[w_tool].writes


# ---------------------------------------------------------------- database fixtures
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
    """Users:
    - A: owner with shoulder impingement restrictions (upright_row, dips, barbell_bench_press banned; goblet_squat caution)
         plus recovery score and progression reminder.
    - B: family member granted `training` and `scores` (but NOT `exercises`) by A.
    - C: unrelated user with no shares.
    """
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {
            n: conn.execute(
                "INSERT INTO users (display_name, role) VALUES (%s, %s) RETURNING id",
                (n, "owner" if n == "A" else "member"),
            ).fetchone()["id"]
            for n in ("A", "B", "C")
        }
        tok = {
            "A": auth.create_token(conn, ids["A"], "web", ["read", "write"]),
            "A_ro": auth.create_token(conn, ids["A"], "web-ro", ["read"]),
            "B": auth.create_token(conn, ids["B"], "web", ["read", "write"]),
            "C": auth.create_token(conn, ids["C"], "web", ["read", "write"]),
        }
        conn.execute(
            "INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
            (ids["A"], ids["B"], ["training", "scores"]),
        )
        # Seed user A's personal exercise restrictions from md_seed
        for ref, ex in md_seed.EXERCISES:
            conn.execute(
                """INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, name_en, status, aliases, cues, reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (ids["A"], ref, ex["name_zh"], ex.get("name_en"), ex.get("status", "active"),
                 ex.get("aliases") or [], ex.get("cues"), ex.get("reason")),
            )
        today = date.today()
        conn.execute(
            """INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_debt_min, strain, algo_version)
               VALUES (%s, %s, 74.0, 82.0, 30.0, 10.5, 'test')""",
            (ids["A"], today),
        )
        conn.execute(
            """INSERT INTO reminders (user_id, date, rule_key, subject, level, title, message, evidence, rules_version)
               VALUES (%s, %s, 'progression', 'lat_pulldown:39:up', 'info',
                       '下次加重：高位下拉 39 kg → 41.5 kg', '连续达标',
                       '{"exercise": "高位下拉（正手，略宽于肩）", "next_weight_kg": 41.5}'::jsonb, 'test')""",
            (ids["A"], today),
        )
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


class FakeSparkyPlans:
    """Fake SparkyFitness client supporting /exercises, /workout-presets, and /workout-plan-templates."""

    def __init__(self):
        self.exercises = []
        self.presets = []
        self.plans = []
        self.writes = []

    def get(self, path, **params):
        if path == "/exercises":
            return {"exercises": self.exercises, "totalCount": len(self.exercises)}
        if path == "/workout-presets":
            return {"presets": self.presets, "total": len(self.presets)}
        if path == "/workout-plan-templates":
            return self.plans
        raise AssertionError(f"unexpected GET {path}")

    def request(self, method, path, body=None, params=None, multipart_field=None):
        self.writes.append((method, path, body))
        if path == "/exercises" and method == "POST":
            ex = {"id": f"sp-ex-{len(self.exercises) + 1}", **body}
            self.exercises.append(ex)
            return ex
        if path == "/workout-presets" and method == "POST":
            p = {"id": len(self.presets) + 1, **body}
            self.presets.append(p)
            return p
        if path.startswith("/workout-presets/") and method == "PUT":
            pid = int(path.rsplit("/", 1)[1])
            for i, p in enumerate(self.presets):
                if p["id"] == pid:
                    self.presets[i] = {"id": pid, **body}
            return {"id": pid, **body}
        if path == "/workout-plan-templates" and method == "POST":
            pl = {"id": len(self.plans) + 100, **body}
            self.plans.append(pl)
            return pl
        if path.startswith("/workout-plan-templates/") and method == "PUT":
            pl_id = int(path.rsplit("/", 1)[1])
            return {"id": pl_id, **body}
        raise AssertionError(f"unexpected {method} {path}")


# ---------------------------------------------------------------- catalog + free-exercise-db ingestion
def test_seed_catalog_and_free_exercise_db_import(conn, tmp_path):
    res1 = catalog.seed_catalog(conn)
    assert res1["curated"] == len(catalog_data.EXERCISES)
    # Import a free-exercise-db JSON file containing 1 overlapping exercise and 1 brand-new exercise
    sample_json = tmp_path / "exercises.json"
    sample_json.write_text(json.dumps([
        {
            "id": "Goblet_Squat",
            "name": "Goblet Squat",
            "equipment": "kettlebell",
            "primaryMuscles": ["quadriceps"],
            "secondaryMuscles": [],
            "instructions": ["English instruction that should not overwrite curated Chinese overlay."],
            "category": "strength",
        },
        {
            "id": "Spider_Curl",
            "name": "Spider Curl",
            "mechanic": "isolation",
            "force": "pull",
            "equipment": "dumbbell",
            "primaryMuscles": ["biceps"],
            "secondaryMuscles": ["forearms"],
            "instructions": ["Lie prone on an incline bench and curl the dumbbells."],
            "category": "strength",
        },
    ]), encoding="utf-8")
    res2 = catalog.seed_catalog(conn, sample_json)
    assert res2["imported_free_db"] == 1 and res2["merged_free_db"] == 1

    # Curated entry keeps its Chinese cues and instructions
    goblet = catalog.resolve(conn, "goblet_squat")
    assert goblet["source"] == "curated" and "高脚杯深蹲" in goblet["name_zh"]
    # Imported free-exercise-db entry is searchable
    spider = catalog.resolve(conn, "Spider Curl")
    assert spider is not None and spider["slug"] == "spider_curl" and spider["source"] == "free-exercise-db"


def test_search_and_get_exercise_with_restrictions_and_alternatives(conn, world):
    ids, _ = world
    a, b = ids["A"], ids["B"]

    # Search by muscle + equipment
    chest_db = catalog.search(conn, user_id=a, muscle="chest", equipment="dumbbell")
    slugs = [x["slug"] for x in chest_db]
    assert "dumbbell_bench_press" in slugs and "incline_dumbbell_press" in slugs

    # Search by Chinese query via MCP run_tool
    hits = run_tool(conn, a, "butler", "search_exercises", {"user": "me", "query": "高位下拉"})
    assert any(h["slug"] == "lat_pulldown" and h["status"] == "active" for h in hits)

    # Get a banned exercise for User A -> status is 'banned', reason is populated, alternatives are all safe (not banned)
    upright = run_tool(conn, a, "butler", "get_exercise", {"user": "me", "exercise": "直立划船"})
    assert upright["slug"] == "upright_row"
    assert upright["status"] == "banned" and "上斜方" in upright["reason"]
    assert upright["joint_flags"].get("shoulder_internal_rotation") == "high"
    assert len(upright["alternatives"]) >= 2
    assert all(alt["status"] != "banned" for alt in upright["alternatives"])
    assert any(alt["slug"] == "dumbbell_lateral_raise" for alt in upright["alternatives"])

    # User B has no personal ban on upright_row in exercise_meta -> status is caution (from default_status), not banned
    upright_b = catalog.get_exercise(conn, b, "upright_row")
    assert upright_b["status"] == "caution"


# ---------------------------------------------------------------- weekly plans, autoregulation, and exercise swap
def test_propose_plan_detects_and_auto_swaps_banned_exercises(conn, world):
    ids, _ = world
    a = ids["A"]

    # Built-in 4-day upper/lower template has no banned exercises and valid volume
    prop4 = run_tool(conn, a, "butler", "propose_plan", {
        "user": "me", "split_type": "4_day_upper_lower", "name": None, "notes": None,
        "auto_swap_banned": False, "custom_days": None,
    })
    assert prop4["can_save"] is True and prop4["banned_conflicts"] == []
    assert prop4["volume_by_group"]["back"]["status"] == "optimal"
    assert prop4["readiness"]["tier"] == "green"

    # Custom plan containing a banned exercise ('upright_row') -> flagged when auto_swap_banned=False
    custom_days = [{
        "day_of_week": 1,
        "name": "测试推日",
        "exercises": [
            {"exercise": "哑铃卧推", "sets": 4, "rep_low": 8, "rep_high": 12, "rest_sec": 120,
             "duration_sec": None, "weight_kg": 20, "note": None},
            {"exercise": "直立划船", "sets": 3, "rep_low": 12, "rep_high": 15, "rest_sec": 60,
             "duration_sec": None, "weight_kg": 15, "note": None},
        ],
    }]
    bad_prop = plans.propose_plan(conn, a, split_type="custom", custom_days=custom_days, auto_swap_banned=False)
    assert bad_prop["can_save"] is False
    assert len(bad_prop["banned_conflicts"]) == 1
    assert bad_prop["banned_conflicts"][0]["slug"] == "upright_row"
    assert bad_prop["banned_conflicts"][0]["alternatives"]

    # Saving without auto_swap_banned raises PermissionError
    with pytest.raises(PermissionError, match="禁用动作"):
        plans.save_plan(conn, a, split_type="custom", days=custom_days, auto_swap_banned=False, sync_sparky=False)

    # With auto_swap_banned=True, upright_row is automatically swapped to a safe shoulder isolation exercise
    swapped_prop = plans.propose_plan(conn, a, split_type="custom", custom_days=custom_days, auto_swap_banned=True)
    assert swapped_prop["can_save"] is True and len(swapped_prop["auto_swapped"]) == 1
    assert swapped_prop["days"][0]["exercises"][1]["slug"] != "upright_row"
    assert swapped_prop["days"][0]["exercises"][1]["status"] == "active"


def test_save_plan_sparky_sync_and_swap_exercise(conn, world):
    ids, _ = world
    a = ids["A"]
    fake_sp = FakeSparkyPlans()

    saved = plans.save_plan(conn, a, split_type="3_day_ppl", name="3练推拉腿", sync_sparky=True, client=fake_sp)
    assert saved["saved"]["split_type"] == "3_day_ppl"
    assert saved["sparky_sync"]["synced"] is True
    assert saved["sparky_sync"]["presets_synced"] == 3
    assert len(fake_sp.presets) == 3 and len(fake_sp.plans) == 1

    # Read week plan via MCP tool
    wk = run_tool(conn, a, "butler", "get_week_plan", {"user": "me", "date": None, "detail": True})
    assert wk["plan"]["name"] == "3练推拉腿"
    assert len(wk["days"]) == 7
    assert any(s["next_weight_kg"] == 41.5 for s in wk["suggestions"])

    # Preview alternatives for goblet_squat via swap_exercise with to_exercise=None
    preview = plans.swap_exercise(conn, a, from_exercise="高脚杯深蹲", to_exercise=None, sync_sparky=False)
    assert preview["swapped"] is False and len(preview["recommended_alternatives"]) >= 1

    # Attempt to swap to a banned exercise -> rejected with PermissionError
    with pytest.raises(PermissionError, match="禁用动作"):
        plans.swap_exercise(conn, a, from_exercise="哑铃卧推", to_exercise="双杠臂屈伸", sync_sparky=False)

    # Swap dumbbell_bench_press -> machine_chest_press
    swapped = plans.swap_exercise(conn, a, from_exercise="哑铃卧推", to_exercise="器械坐姿推胸",
                                  sync_sparky=True, client=fake_sp)
    assert swapped["swapped"] is True
    assert swapped["to_exercise"]["slug"] == "machine_chest_press"
    today = plans._user_today(conn, a)
    wk_after = plans.get_week_plan(conn, a, a, today + timedelta(days=7 - today.weekday()))   # next week: the plan started today (#65)
    mon_exs = [e["slug"] for e in wk_after["days"][0]["planned"][0]["exercises"]]
    assert "machine_chest_press" in mon_exs and "dumbbell_bench_press" not in mon_exs


def test_readiness_autoregulation_tiers(conn, world):
    ids, _ = world
    c = ids["C"]
    today = date.today()
    # Unknown when no scores exist
    assert plans.readiness_advice(conn, c, today)["tier"] == "unknown"

    # Red tier (< 34)
    conn.execute(
        """INSERT INTO daily_scores (user_id, date, recovery, sleep_debt_min, algo_version)
           VALUES (%s, %s, 25.0, 40.0, 'test')""",
        (c, today),
    )
    red = plans.readiness_advice(conn, c, today)
    assert red["tier"] == "red" and red["set_factor"] < 0.7 and red["rir_delta"] == 2

    # Yellow tier due to high sleep debt even with recovery 70
    conn.execute("UPDATE daily_scores SET recovery = 70.0, sleep_debt_min = 150.0 WHERE user_id = %s AND date = %s",
                 (c, today))
    yellow = plans.readiness_advice(conn, c, today)
    assert yellow["tier"] == "yellow" and yellow["rir_delta"] == 1


# ---------------------------------------------------------------- HTTP API + multi-user auth
def test_training_v2_http_endpoints_and_auth(client, world):
    ids, tok = world
    a, b = ids["A"], ids["B"]

    # 1. Browse exercise catalog
    r = client.get("/v1/users/me/training/exercises?muscle=back&equipment=cable", headers=H(tok["A"]))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 3 and any(x["slug"] == "lat_pulldown" for x in body["exercises"])

    # 2. Exercise detail: User A sees personal 'banned' status on upright_row; User B (shared 'training' but NOT 'exercises') sees default status
    ra = client.get(f"/v1/users/{a}/training/exercises/upright_row", headers=H(tok["A"]))
    assert ra.status_code == 200 and ra.json()["status"] == "banned" and ra.json()["reason"]
    rb = client.get(f"/v1/users/{a}/training/exercises/upright_row", headers=H(tok["B"]))
    assert rb.status_code == 200 and rb.json()["status"] == "caution" and rb.json()["reason"] is None

    # User C has no share on A -> 403
    assert client.get(f"/v1/users/{a}/training/exercises", headers=H(tok["C"])).status_code == 403
    assert client.get(f"/v1/users/{a}/training/plan", headers=H(tok["C"])).status_code == 403

    # 3. List splits & propose plan
    sp = client.get("/v1/users/me/training/splits", headers=H(tok["A"]))
    assert sp.status_code == 200 and len(sp.json()["splits"]) == 4
    prop = client.post("/v1/users/me/training/plan/propose", headers=H(tok["A"]),
                       json={"split_type": "5_day_ul_ppl"})
    assert prop.status_code == 200 and prop.json()["days_per_week"] == 5

    # 4. Write authorization: read-only token cannot save or swap; B cannot write to A
    assert client.put("/v1/users/me/training/plan", headers=H(tok["A_ro"]),
                      json={"split_type": "4_day_upper_lower"}).status_code == 403
    assert client.put(f"/v1/users/{a}/training/plan", headers=H(tok["B"]),
                      json={"split_type": "4_day_upper_lower"}).status_code == 403

    # 5. Save plan + swap exercise via HTTP
    saved = client.put("/v1/users/me/training/plan", headers=H(tok["A"]),
                       json={"split_type": "4_day_upper_lower", "sync_sparky": False})
    assert saved.status_code == 200 and saved.json()["saved"]["split_type"] == "4_day_upper_lower"

    # Blocking banned exercise swap via HTTP -> 409 Conflict
    bad_swap = client.post("/v1/users/me/training/plan/swap", headers=H(tok["A"]),
                           json={"from_exercise": "lat_pulldown", "to_exercise": "upright_row", "sync_sparky": False})
    assert bad_swap.status_code == 409

    ok_swap = client.post("/v1/users/me/training/plan/swap", headers=H(tok["A"]),
                          json={"from_exercise": "lat_pulldown", "to_exercise": "neutral_grip_pulldown",
                                "day_of_week": 1, "sync_sparky": False})
    assert ok_swap.status_code == 200 and ok_swap.json()["swapped"] is True

    # Today endpoint and /training/week now serve the active Body Engine plan even without SparkyFitness connected
    td = client.get("/v1/users/me/today", headers=H(tok["A"])).json()
    assert td["training"]["plan"]["split_type"] == "4_day_upper_lower"
    tw = client.get("/v1/users/me/training/week", headers=H(tok["A"])).json()
    assert tw["plan"]["split_type"] == "4_day_upper_lower" and "volume_by_group" in tw


def test_body_engine_plan_shown_when_sparkyfitness_unreachable(client, world, monkeypatch):
    """SparkyFitness connected but timing out (a read timeout is an OSError, not a URLError): the Today page and the week
    strip still show the active Body Engine plan; only the logged part says why it is missing."""
    from body_engine import sparky_client

    def timeout(*_a, **_k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: sparky_client.SparkyClient("test-key"))
    monkeypatch.setattr(sparky_client.urllib.request, "urlopen", timeout)
    _ids, tok = world
    assert client.put("/v1/users/me/training/plan", headers=H(tok["A"]),
                      json={"split_type": "4_day_upper_lower", "sync_sparky": False}).status_code == 200
    td = client.get("/v1/users/me/today", headers=H(tok["A"])).json()["training"]
    assert td["plan"]["split_type"] == "4_day_upper_lower" and "error" not in td
    assert "连不上 SparkyFitness" in td["sparky_error"] and td["logged"] == []
    tw = client.get("/v1/users/me/training/week", headers=H(tok["A"])).json()
    assert tw["plan"]["split_type"] == "4_day_upper_lower" and len(tw["days"]) == 7
