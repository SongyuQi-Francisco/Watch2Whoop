"""Token-lean AI tools (docs/ai-assistant-plan.md §9.13): compact results, relaxed MCP schemas, the opening briefing and its cache.

The database part uses a separate test database BODY_TEST_DB_NAME (default body_test_briefing), dropped and recreated on every run,
never the production DB; skipped if Postgres cannot be reached. SparkyFitness is not configured for these users: the training section
falls back to watch workouts only (sparky_error), which is what a user without SparkyFitness sees.
"""
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.ai import params as P
from body_engine.assistant import briefing, compact, toolsets
from body_engine.assistant.tools import TOOLS, TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.toolspec import fill_defaults, relaxed

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_briefing")
TZ = ZoneInfo("Australia/Sydney")


# ---------------------------------------------------------------- no database needed
def test_compact_drops_empties_tables_and_rounds():
    rows = [{"date": date(2026, 10, d), "v": 1.23456, "n": None, "flags": {}} for d in (1, 2, 3)]
    out = compact.compact({"rows": rows, "none": None, "empty": [], "zero": 0, "no": False,
                           "at": datetime(2026, 10, 18, 7, 5, 33, 123456, tzinfo=TZ), "small": 0.123456})
    assert out["rows"] == {"cols": ["date", "v"], "rows": [["2026-10-01", 1.23], ["2026-10-02", 1.23], ["2026-10-03", 1.23]]}
    assert "none" not in out and "empty" not in out and out["zero"] == 0 and out["no"] is False
    assert out["at"] == "2026-10-18T07:05+11:00" and out["small"] == 0.123
    # Two objects stay objects (a table only pays off from 3 rows); compact separators
    assert compact.dumps([{"a": 1}, {"a": 2}]) == '[{"a":1},{"a":2}]'
    assert compact.compact({"x": float("nan")}) == {}


def test_relaxed_schema_and_fill_defaults():
    schema = TOOLS_BY_NAME["log_food"].input_schema
    r = relaxed(schema)
    item = r["properties"]["items"]["items"]
    assert "time" not in item["required"] and "calories" in item["required"] and "additionalProperties" not in r
    assert schema["properties"]["items"]["items"]["required"].count("time") == 1  # the strict original is untouched
    filled = fill_defaults(schema, {"items": [{"date": "2026-10-18", "meal": "lunch", "food": "鸡", "amount": "1",
                                               "calories": 100, "protein_g": 20, "carbs_g": 0, "fat_g": 2}],
                                    "allow_duplicates": False})
    assert filled["items"][0]["time"] is None and filled["items"][0]["notes"] is None
    g = fill_defaults(TOOLS_BY_NAME["get_scores"].input_schema, {"start": "2026-10-01", "end": "2026-10-02"})
    assert g["user"] == "me" and g["details"] is False
    for t in TOOLS:  # every relaxed schema still lists every property, and required ⊆ properties
        rs = relaxed(t.input_schema)
        assert set(rs.get("required", [])) <= set(rs["properties"]) == set(t.input_schema["properties"]), t.name
        assert "user" not in rs.get("required", []), t.name


def test_tool_definitions_budget():
    """tools/list is sent with every model call. ~47.6k chars before the 2026-10-18 slimming; keep it from growing back.
    36k -> 41k on 2026-10-20 for the 9 grocery tools (docs/grocery-plan.md).
    41k -> 43.5k on 2026-10-21 for 6 edit / delete / audit tools (#30: update_food_entry, update_training_set, update_workout,
    delete_workout, get_write_log, rollback_write; descriptions kept to one short line).
    43.5k -> 46.5k on 2026-10-21 for the nutrition goal tools and the sugar / fiber / sodium fields on log_food, meal
    templates and recipe ingredients (#31).
    46.5k -> 47k on 2026-10-21 for lookup_barcode and save_product's product_url / nutrition_source (#29, grocery-plan §7).
    47k -> 49k on 2026-10-21 for the 2 diet profile tools (#33).
    49k -> 52.5k for the Training V2 tools (#1: get_exercise, propose_plan, save_plan, get_week_plan, swap_exercise, list_splits).
    52.5k -> 53.5k for the day-change tools (#17: reschedule_day, swap_days, replan_week, undo_day_changes).
    53.5k -> 54.5k for plan start / training_days / addons and the ask-first wording on propose_plan / save_plan (#65, #66); set_stores (#44) fits in.
    54.5k -> 55k for get_weekly_specials (grocery-sync, #48).
    55k -> 55.5k for report_feedback / list_feedback (root rules, docs/ai-feedback-plan.md).
        55.5k -> 57.5k for get_shopping_context / record_store_prices / get_price_history (prices from the user's AI, grocery-plan §9).
    57.5k -> 59k for the per-user diet constraint tools (#76: get_diet_constraints, set_diet_constraint, end_diet_constraint).
        59k -> 59.5k for combine_food_entries (issue #93).
    From #99 on not every tool is sent: only the resident core (+ the page's groups in the in-page chat) and three meta tools
    (find_tools / run_read_tool / run_write_tool). The budget is now per set: core ≤ 30k, a cap per group, meta, MCP tools/list
    ≤ 35k, each page (ai/params.py; all checked in tests/test_tool_groups.py::test_budgets)."""
    core = [t for t in TOOLS if t.name in P.CORE_TOOLS]
    assert toolsets.definition_chars(core) <= P.CORE_BUDGET_CHARS
    assert toolsets.definition_chars(toolsets.for_mcp(True)) <= P.MCP_LIST_BUDGET_CHARS
    assert TOOLS[0].name == "get_briefing" and not TOOLS[0].writes


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


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


def _today() -> date:
    return datetime.now(TZ).date()


@pytest.fixture(scope="module")
def world(dsn):
    """A: scores, sleep, a workout, a reminder, memory, a banned exercise. B: own data; A shares profile + scores with B
    (not memory). C: no shares."""
    today = _today()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, birth_date, sex, height_cm, timezone)
                                  VALUES (%s, '1990-01-01', 'male', 180, 'Australia/Sydney') RETURNING id""",
                               (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["profile", "scores"]))
        for uid, base in ((ids["A"], 60), (ids["B"], 20)):
            for k in range(30):
                d = today - timedelta(days=k)
                conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_actual_min, sleep_debt_min,
                                    hrv_night_ln, rhr_night, strain, algo_version)
                                VALUES (%s, %s, %s, 420, 30, 3.8, 52, 12.5, 'test')""", (uid, d, base + k % 5))
            start = datetime.combine(today, datetime.min.time(), TZ) - timedelta(hours=1)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                            VALUES (%s, %s, %s, %s, 420, true, 'shortcut')""", (uid, today, start, start + timedelta(hours=7)))
        conn.execute("""INSERT INTO reminders (user_id, date, rule_key, level, title, message, evidence, rules_version)
                        VALUES (%s, %s, 'frequency_caps', 'warning', 'A 的红肉超了', '换鸡鱼蛋奶', '{"big": [1,2,3]}', 't')""",
                     (ids["A"], today))
        conn.execute("""INSERT INTO assistant_observations (user_id, text, source) VALUES
                        (%s, 'A 喜欢早上练', 'user'), (%s, 'B 乳糖不耐', 'user')""", (ids["A"], ids["B"]))
        conn.execute("""INSERT INTO assistant_profile (user_id, key, label, value, source) VALUES
                        (%s, 'health_constraints', '健康约束', '尿酸高：中低嘌呤', 'seed')""", (ids["A"],))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason)
                        VALUES (%s, 'upright_row', '直立划船', 'banned', '练上斜方')""", (ids["A"],))
    briefing.clear()
    return ids


def test_briefing_own_data_and_size(conn, world):
    a = world["A"]
    b = run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    assert b["me"]["display_name"] == "A" and b["now"].startswith(f"{_today():%Y-%m-%d}")
    assert b["scores"]["latest"]["date"] == _today() and len(b["scores"]["days"]) == 7
    assert b["scores"]["avg_7d_vs_60d"]["recovery"][0] is not None
    assert any("A 的红肉超了" in r for r in b["reminders"])
    assert b["exercises"]["banned"] == ["直立划船"]
    assert "尿酸高" in b["memory"]["profile_card"]["health_constraints"]
    text = compact.dumps(b)
    assert "A 喜欢早上练" in text and "乳糖" not in text            # nobody else's memory
    assert "big" not in text                                           # no reminder evidence
    rules = len(compact.dumps({"system_rules": b["system_rules"]}))   # root rules: fixed text, on every briefing
    assert len(text) - rules < 4500, len(text)                          # ~1.5k tokens + the root rules


def test_briefing_family_view_respects_shares(conn, world):
    a, b, c = world["A"], world["B"], world["C"]
    v = run_tool(conn, b, "mcp", "get_briefing", {"user": str(a)})
    assert v["me"]["display_name"] == "A" and "scores" in v
    assert {"memory", "reminders", "training", "exercises", "bedtimes"} <= set(v["hidden"])
    assert "memory" not in v and "A 喜欢早上练" not in compact.dumps(v) and "cached" not in v
    with pytest.raises(PermissionError):
        run_tool(conn, c, "mcp", "get_briefing", {"user": str(a)})
    with pytest.raises(PermissionError):  # B shares nothing with A
        run_tool(conn, a, "mcp", "get_briefing", {"user": str(b)})


def test_briefing_cache_and_invalidation(conn, world, monkeypatch):
    a = world["A"]
    briefing.clear()
    first = run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    assert "cached" not in first
    second = run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    assert second["cached"] is True and second["scores"] == first["scores"]
    # 1. a write tool of the same user drops the cache at once
    run_tool(conn, a, "mcp", "add_observation", {"text": "A 周三打篮球", "evidence": None})
    third = run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    assert "cached" not in third and any("周三打篮球" in o for o in third["memory"]["observations"]["recent"])
    # 2. data changed outside the tools (importer / Today page): the fingerprint changes
    assert run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})["cached"] is True
    conn.execute("UPDATE daily_scores SET recovery = 99, computed_at = now() WHERE user_id = %s AND date = %s",
                 (a, _today()))
    fourth = run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    assert "cached" not in fourth and fourth["scores"]["latest"]["recovery"] == 99
    conn.execute("UPDATE reminders SET status = 'dismissed', status_changed_at = now() WHERE user_id = %s", (a,))
    assert run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})["reminders"] == []
    # 3. TTL (covers SparkyFitness, which is outside the fingerprint)
    assert run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})["cached"] is True
    monkeypatch.setattr(briefing, "CACHE_TTL_S", 0)
    assert "cached" not in run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    # 4. another user's write does not drop A's cache, and A's cache never answers B
    monkeypatch.setattr(briefing, "CACHE_TTL_S", 600)
    run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    run_tool(conn, world["B"], "mcp", "add_observation", {"text": "B 晚上练", "evidence": None})
    assert run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})["cached"] is True
    own_b = run_tool(conn, world["B"], "mcp", "get_briefing", {"user": "me"})
    assert own_b["me"]["display_name"] == "B" and "A 喜欢早上练" not in compact.dumps(own_b)


def test_slim_defaults_and_detail(conn, world):
    a = world["A"]
    rem = run_tool(conn, a, "mcp", "get_reminders", {"user": "me", "start": None, "end": None, "include_dismissed": True})
    assert rem["reminders"] and "evidence" not in rem["reminders"][0]
    full = run_tool(conn, a, "mcp", "get_reminders", {"user": "me", "start": None, "end": None, "include_dismissed": True,
                                                      "detail": True})
    assert full["reminders"][0]["evidence"] == {"big": [1, 2, 3]}
    mem = run_tool(conn, a, "mcp", "get_memory", {"user": "me"})
    assert "evidence" not in mem["observations"][0] and "spark" not in json.dumps(mem, default=str)
    assert "created_at" in run_tool(conn, a, "mcp", "get_memory", {"user": "me", "detail": True})["observations"][0]
    cal = run_tool(conn, a, "mcp", "get_training_calendar", {"user": "me", "month": None, "start": None, "end": None})
    assert len(cal["days"]) == 28 and cal["end"] == _today()        # no arguments = the last 28 days (not the month)
    sc = run_tool(conn, a, "mcp", "get_scores", {"user": "me", "start": str(_today()), "end": str(_today()),
                                                 "details": False})
    assert "algo_version" not in sc[0] and "hrv_night_ms" in sc[0]
