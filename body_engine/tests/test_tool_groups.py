"""Tool groups and on-demand loading (issue #99, docs/ai-assistant-plan.md §9.17).

Pure part: every tool has a group, the size budgets in ai/params.py (core, each group, meta, MCP tools/list, each page),
a stable tool order (the cached prompt prefix), and the meta tools' guardrails (find_tools / resolve).
Database part: every read tool's summary result on synthetic data stays under its character cap. Uses its own test
database BODY_TEST_DB_NAME (default body_test_tool_groups), dropped and recreated on every run; skipped without Postgres.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.ai import fallback
from body_engine.ai import params as P
from body_engine.assistant import compact, toolsets
from body_engine.assistant.tools import TOOLS, TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_tool_groups")
TZ = ZoneInfo("Australia/Sydney")
CHARS = toolsets.definition_chars


# ---------------------------------------------------------------- groups and budgets
def test_every_tool_has_a_known_group_and_the_tables_are_consistent():
    assert not [t.name for t in TOOLS if t.group not in P.GROUPS]
    assert set(P.CORE_TOOLS) <= set(TOOLS_BY_NAME) and len(P.CORE_TOOLS) == len(set(P.CORE_TOOLS))
    assert P.ALWAYS_CONFIRM <= {t.name for t in TOOLS if t.writes}
    assert all(set(gs) <= set(P.GROUPS) for gs in P.PAGE_GROUPS.values())
    assert set(P.GROUP_BUDGET_CHARS) == set(P.GROUPS)
    assert not set(toolsets.META_BY_NAME) & set(TOOLS_BY_NAME)
    for g in P.GROUPS:   # no empty group: find_tools(group) always returns something
        assert any(t.group == g for t in TOOLS), g


def test_budgets():
    """Before #99 every call carried all 106 tools (58,539 chars). Now: core ≤ 30k, each group under its own cap, the
    MCP tools/list ≤ 35k and every page's in-page set under PAGE_BUDGET_CHARS. A new tool only costs its own group."""
    core = [t for t in TOOLS if t.name in P.CORE_TOOLS]
    assert CHARS(core) <= P.CORE_BUDGET_CHARS, CHARS(core)
    assert CHARS(toolsets.META_TOOLS) <= P.META_BUDGET_CHARS, CHARS(toolsets.META_TOOLS)
    for g, cap in P.GROUP_BUDGET_CHARS.items():
        size = CHARS([t for t in TOOLS if t.group == g])
        assert size <= cap, (g, size)
    assert CHARS(toolsets.for_mcp(True)) <= P.MCP_LIST_BUDGET_CHARS, CHARS(toolsets.for_mcp(True))
    for page in [None, *P.PAGE_GROUPS]:
        size = CHARS(toolsets.for_page(TOOLS, page))
        assert size <= P.PAGE_BUDGET_CHARS, (page, size)
    fb = fallback.tools()   # the fallback AI's whitelist is cut the same way
    assert CHARS(toolsets.for_page(fb, None)) <= CHARS(toolsets.for_page(TOOLS, None))


def test_tool_order_is_stable():
    """Tools come first in the cached prompt prefix: the same page always gives the same list in the same order
    (TOOLS order, meta tools last), and the core is a prefix-stable subset whatever the page."""
    order = {t.name: i for i, t in enumerate(TOOLS)}
    assert TOOLS[0].name == "get_briefing" and not TOOLS[0].writes
    for page in [None, *P.PAGE_GROUPS, "food.unknown", "nope"]:
        names = [t.name for t in toolsets.for_page(TOOLS, page)]
        assert names == [t.name for t in toolsets.for_page(TOOLS, page)]
        real = names[:-len(toolsets.META_TOOLS)]
        assert names[-3:] == [toolsets.FIND, toolsets.RUN_READ, toolsets.RUN_WRITE]
        assert real == sorted(real, key=order.__getitem__) and set(P.CORE_TOOLS) <= set(real)
    mcp = [t.name for t in toolsets.for_mcp(True)]
    assert mcp == [t.name for t in toolsets.for_mcp(True)] and mcp[-3:] == list(toolsets.META_BY_NAME)
    assert P.ALWAYS_CONFIRM <= set(mcp)   # MCP clients approve deletes by tool name
    ro = [t.name for t in toolsets.for_mcp(False)]
    assert not {n for n in ro if n in TOOLS_BY_NAME and TOOLS_BY_NAME[n].writes} and toolsets.RUN_WRITE not in ro


def test_pages_accumulate_in_tools_order():
    """A conversation that has been on several pages gets the union of their groups; adding a page only inserts tools."""
    one = [t.name for t in toolsets.for_page(TOOLS, ["today"])]
    two = [t.name for t in toolsets.for_page(TOOLS, ["today", "food.shopping"])]
    assert one == [t.name for t in toolsets.for_page(TOOLS, "today")]
    assert [n for n in two if n in one] == one and set(two) > set(one)
    assert two == [t.name for t in toolsets.for_page(TOOLS, ["food.shopping", "today"])]   # order of visits does not matter
    assert [t.name for t in toolsets.for_page(TOOLS, [])] == [t.name for t in toolsets.for_page(TOOLS, None)]


def _walk(tools, seq, budget):
    pages, sizes = [], []
    for page in seq:
        pages = toolsets.visit_page(tools, pages, page, budget)
        sent = toolsets.for_page(tools, pages)
        names = {t.name for t in sent}
        assert pages[-1] == page and {t.name for t in tools if t.group in toolsets.page_groups(page)} <= names
        assert set(P.CORE_TOOLS) & {t.name for t in tools} <= names   # core and the current page always stay
        sizes.append(CHARS(sent))
    return pages, sizes


def test_page_visits_stay_under_the_cap():
    """Review of #107: groups accumulate across pages but never past the cap (in-page chat PAGE_BUDGET_CHARS, fallback AI
    FALLBACK_PAGE_BUDGET_CHARS): every ordered pair and triple of pages and every page in turn, there and back."""
    import itertools
    ids = list(P.PAGE_GROUPS)
    seqs = [list(s) for n in (2, 3) for s in itertools.permutations(ids, n)] + [ids, ids[::-1], ids + ids[::-1] + ids]
    for tools, budget in ((TOOLS, P.PAGE_BUDGET_CHARS), (fallback.tools(), P.FALLBACK_PAGE_BUDGET_CHARS)):
        for seq in seqs:
            _, sizes = _walk(tools, seq, budget)
            assert max(sizes) <= budget, (seq, sizes)
    # dropping happens: after all pages the oldest ones are gone; revisiting a kept page changes nothing
    pages, _ = _walk(TOOLS, ids, P.PAGE_BUDGET_CHARS)
    assert pages[-1] == ids[-1] and len(pages) < len(ids)
    assert toolsets.visit_page(TOOLS, pages, pages[0], P.PAGE_BUDGET_CHARS)[:-1] == pages[1:]
    assert [t.name for t in toolsets.for_page(TOOLS, toolsets.visit_page(TOOLS, pages, pages[0], P.PAGE_BUDGET_CHARS))] == [
        t.name for t in toolsets.for_page(TOOLS, pages)]


def test_only_known_pages_are_kept():
    assert toolsets.known_page("food.shopping") and toolsets.known_page("trends.sleep") and toolsets.known_page("me")
    assert not any(toolsets.known_page(p) for p in (None, "", "nope", "nope.x", "x" * 40))
    assert toolsets.visit_page(TOOLS, ["today"], "nope", P.PAGE_BUDGET_CHARS) == ["today"]


def test_page_groups():
    assert toolsets.page_groups("food.shopping") == P.PAGE_GROUPS["food.shopping"]
    assert toolsets.page_groups("food.whatever") == P.PAGE_GROUPS["food"]
    assert toolsets.page_groups("nope") == () and toolsets.page_groups(None) == () and toolsets.page_groups("") == ()
    names = {t.name for t in toolsets.for_page(TOOLS, "food.shopping")}
    assert "get_shopping_list" in names and "get_lab_history" not in names
    assert "get_shopping_list" not in {t.name for t in toolsets.for_page(TOOLS, None)}


def test_meta_tool_definitions():
    by = toolsets.META_BY_NAME
    assert not by[toolsets.FIND].writes and not by[toolsets.RUN_READ].writes and by[toolsets.RUN_WRITE].writes
    assert by[toolsets.FIND].strict and not by[toolsets.RUN_READ].strict   # free-form args object: not a strict tool
    for t in toolsets.META_TOOLS:
        assert t.api_definition()["strict"] is t.strict


# ---------------------------------------------------------------- find_tools / resolve guardrails
ALL = toolsets.Access(TOOLS_BY_NAME, True)


def test_find_tools_index_group_and_query():
    idx = toolsets.find(ALL)
    assert [g["group"] for g in idx["groups"]] == list(P.GROUPS)
    assert sum(len(g["tools"]) for g in idx["groups"]) == len(TOOLS)
    rec = toolsets.find(ALL, group="records")["tools"]
    by = {e["name"]: e for e in rec}
    assert by["get_lab_history"]["run"] == toolsets.RUN_READ and by["add_lab_results"]["run"] == toolsets.RUN_WRITE
    assert "required" not in by["get_lab_history"]["input_schema"] or "user" not in by["get_lab_history"]["input_schema"]["required"]
    q = toolsets.find(ALL, query="化验 趋势")["tools"]
    assert q[0]["name"] == "get_lab_history" and len(q) <= P.FIND_TOOLS_MAX
    assert toolsets.find(ALL, query="zzz不存在")["tools"] == []
    with pytest.raises(ValueError, match="没有这个组"):
        toolsets.find(ALL, group="nope")
    # a whole-group answer stays well under the in-page tool result cut (ai/chat.MAX_TOOL_RESULT_CHARS)
    for g in P.GROUPS:
        assert len(compact.dumps(toolsets.find(ALL, group=g))) < 30000, g


def test_find_tools_respects_read_only_and_the_fallback_whitelist():
    ro = toolsets.Access(TOOLS_BY_NAME, False)
    assert all(not TOOLS_BY_NAME[e["name"]].writes for e in toolsets.find(ro, group="records")["tools"])
    fb = toolsets.Access({t.name: t for t in fallback.tools()}, True)
    names = {n for g in toolsets.find(fb)["groups"] for n in g["tools"]}
    assert names == set(fallback.TOOL_NAMES) and "get_ecg_list" not in names
    assert not [e for e in toolsets.find(fb, query="心电图")["tools"] if e["name"] == "get_ecg_list"]
    gb = {e["name"]: e for e in toolsets.find(fb, group="body")["tools"]}
    assert "user" not in gb["get_scores"]["input_schema"]["properties"]   # the fallback's own schemas (no family data)
    mcp = toolsets.Access(TOOLS_BY_NAME, True, frozenset(P.ALWAYS_CONFIRM))
    assert {e["name"]: e["run"] for e in toolsets.find(mcp, group="records")["tools"]}["delete_lab_results"] == "直接调用"


def test_resolve_guardrails():
    tool, args = toolsets.resolve(ALL, toolsets.RUN_READ, {"name": "get_lab_history", "args": {"test": "尿酸"}})
    assert tool.name == "get_lab_history" and args == {"test": "尿酸"}
    with pytest.raises(ValueError, match="缺少参数：test"):       # checked against the real schema before anything runs
        toolsets.resolve(ALL, toolsets.RUN_READ, {"name": "get_lab_history", "args": {"name": "尿酸"}})
    with pytest.raises(ValueError, match="observation_id"):      # ... and before a confirm card would pop up
        toolsets.resolve(ALL, toolsets.RUN_WRITE, {"name": "delete_observation", "args": {"id": 1}})
    assert toolsets.resolve(ALL, toolsets.RUN_READ, {"name": "get_profile", "args": None})[1] == {}
    assert toolsets.resolve(ALL, toolsets.RUN_READ, {"name": "get_profile", "args": '{"user": "me"}'})[1] == {"user": "me"}
    with pytest.raises(PermissionError, match="run_write_tool"):   # read / write never mix
        toolsets.resolve(ALL, toolsets.RUN_READ, {"name": "add_event", "args": {}})
    with pytest.raises(PermissionError, match="run_read_tool"):
        toolsets.resolve(ALL, toolsets.RUN_WRITE, {"name": "get_profile", "args": {}})
    with pytest.raises(PermissionError, match="只读"):            # read-only token / login
        toolsets.resolve(toolsets.Access(TOOLS_BY_NAME, False), toolsets.RUN_WRITE, {"name": "add_event", "args": {}})
    mcp = toolsets.Access(TOOLS_BY_NAME, True, frozenset(P.ALWAYS_CONFIRM))
    with pytest.raises(PermissionError, match="直接调用"):          # MCP: deletes keep the client's approval by name
        toolsets.resolve(mcp, toolsets.RUN_WRITE, {"name": "delete_observation", "args": {"id": 1}})
    fb = toolsets.Access({t.name: t for t in fallback.tools()}, True)
    with pytest.raises(ValueError, match="get_ecg_list"):         # the fallback stays inside its whitelist
        toolsets.resolve(fb, toolsets.RUN_READ, {"name": "get_ecg_list", "args": {}})
    for bad in ({"name": toolsets.RUN_READ, "args": {}}, {"name": None}, {"name": "nope"},
                {"name": "get_profile", "args": [1]}, {"name": "get_profile", "args": "{oops"}):
        with pytest.raises(ValueError):
            toolsets.resolve(ALL, toolsets.RUN_READ, bad)


# ---------------------------------------------------------------- read results on synthetic data
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


METRICS = {"step_count": ("count", "sum", 9000), "active_energy_burned": ("kcal", "sum", 600),
           "resting_heart_rate": ("count/min", "avg", 52), "heart_rate_variability_sdnn": ("ms", "avg", 48),
           "respiratory_rate": ("count/min", "avg", 14.5), "oxygen_saturation": ("%", "avg", 97),
           "body_mass": ("kg", "avg", 78.2), "vo2_max": ("mL/kg·min", "avg", 47)}


@pytest.fixture(scope="module")
def heavy(dsn):
    """One user with a long, dense history: 400 days of scores, 120 nights, 120 days of 8 metrics, 160 workouts,
    40 events, 60 observations, a year of journal entries, labs and supplements."""
    today = datetime.now(TZ).date()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("""INSERT INTO users (display_name, birth_date, sex, height_cm, timezone)
                              VALUES ('H', '1990-01-01', 'male', 180, 'Australia/Sydney') RETURNING id""").fetchone()["id"]
        for k in range(400):
            d = today - timedelta(days=k)
            conn.execute("""INSERT INTO daily_scores (user_id, date, sleep_score, sleep_need_min, sleep_debt_min,
                                sleep_performance, hrv_kind, hrv_night_ln, rhr_night, recovery, trimp, strain, ctl, atl, tsb,
                                algo_version)
                            VALUES (%s,%s,%s,480,%s,0.9,'sdnn',3.85,52,%s,%s,%s,40,45,-5,'test')""",
                         (uid, d, 70 + k % 20, 20 + k % 40, 40 + k % 50, 50 + k % 60, 8 + k % 10))
        for k in range(120):
            d = today - timedelta(days=k)
            start = datetime.combine(d, datetime.min.time(), TZ) - timedelta(hours=1, minutes=k % 50)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min,
                                rem_min, awake_min, is_main, source)
                            VALUES (%s,%s,%s,%s,430,250,80,100,30,true,'shortcut')""",
                         (uid, d, start, start + timedelta(hours=7, minutes=40)))
            for m, (unit, agg, v) in METRICS.items():
                conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, min_value, max_value, source)
                                VALUES (%s,%s,%s,%s,%s,%s,10,%s,%s,'shortcut')""",
                             (uid, d, m, v * (1 + (k % 7) / 50), unit, agg, v * 0.9, v * 1.1))
        for k in range(160):
            d = today - timedelta(days=k * 2 // 3)
            start = datetime.combine(d, datetime.min.time(), TZ) + timedelta(hours=7 + k % 10)
            conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                                duration_min, active_kcal, total_kcal, avg_hr, max_hr, trimp)
                            VALUES (%s,'shortcut',%s,%s,%s,%s,%s,%s,55,420,500,128,171,85)""",
                         (uid, f"w{k}", ("strength_training", "basketball", "walking")[k % 3], ("力量", "篮球", "步行")[k % 3],
                          d, start, start + timedelta(minutes=55)))
        for k in range(40):
            conn.execute("INSERT INTO events (user_id, occurred_at, category, body_part, severity, title, note) "
                         "VALUES (%s, %s, 'pain', '膝', 2, %s, '打完球有点疼，第二天好了')",
                         (uid, datetime.combine(today - timedelta(days=k * 9), datetime.min.time(), TZ), f"膝盖疼 {k}"))
        for k in range(60):
            conn.execute("INSERT INTO assistant_observations (user_id, text, source) VALUES (%s, %s, 'user')",
                         (uid, f"观察 {k}：晚饭太晚第二天恢复分低一些，喝酒后 HRV 掉 10% 左右"))
        conn.execute("INSERT INTO journal_items (user_id, key, label_zh, kind) VALUES (%s,'alcohol','喝酒','bool'),"
                     " (%s,'late_meal','晚饭太晚','bool')", (uid, uid))
        conn.commit()
        for k in range(0, 365, 2):
            for key in ("alcohol", "late_meal"):
                run_tool(conn, uid, "test", "log_journal", {"date": (today - timedelta(days=k)).isoformat(), "key": key,
                                                            "value": int(k % 3 == 0), "note": None})
        for n in ("维生素 D", "鱼油", "镁", "肌酸", "锌"):
            conn.execute("INSERT INTO supplements (user_id, name, dose, status) VALUES (%s, %s, '1 粒', 'active')", (uid, n))
        for k in range(12):
            for key, item, v in (("uric_acid", "尿酸", 420 - k * 5), ("total_cholesterol", "总胆固醇", 5.1),
                                 ("triglycerides", "甘油三酯", 1.4), ("glucose_fasting", "空腹血糖", 5.0), ("alt", "ALT", 30)):
                conn.execute("""INSERT INTO lab_results (user_id, collected_on, test_key, test_name, value, unit, lab)
                                VALUES (%s, %s, %s, %s, %s, 'mmol/L', 'lab')""",
                             (uid, today - timedelta(days=30 * k), key, item, v))
        conn.commit()
    return uid


# Arguments for read tools whose required fields cannot be guessed from the schema (ids, names, codes).
EXPLICIT_ARGS = {
    "get_lab_history": {"test": "uric_acid"},
    "get_exercise": {"exercise": "bench_press"},
    "get_recipe": {"recipe": "1"},
    "lookup_barcode": {"barcode": "9300633000000"},
    "get_report": {"period": "month"},
    "search_exercises": {"q": "卧推"},
}
# Read tools that need what the test database does not have (SparkyFitness, a saved recipe): they may fail here with
# their own error; everything else must run.
MAY_FAIL = {"get_training_log", "get_nutrition", "get_weight", "suggest_meals", "get_recipe"}


def _args_for(tool, today: date) -> dict:
    out = dict(EXPLICIT_ARGS.get(tool.name, {}))
    for k, prop in tool.input_schema["properties"].items():
        if k in out:
            continue
        t = prop.get("type")
        nullable = isinstance(t, list) and "null" in t
        base = [x for x in t if x != "null"][0] if isinstance(t, list) else t
        if k == "user":
            out[k] = "me"
        elif k in ("start",):
            out[k] = (today - timedelta(days=29)).isoformat()
        elif k in ("end", "date", "day", "week"):
            out[k] = today.isoformat()
        elif k == "month":
            out[k] = f"{today:%Y-%m}"
        elif nullable:
            out[k] = None
        elif base == "boolean":
            out[k] = False
        elif base == "integer":
            out[k] = {"weeks": 26, "days": 365, "limit": 50}.get(k, 30)
        elif "enum" in prop:
            out[k] = prop["enum"][0]
        elif base == "array":
            out[k] = []
        else:
            raise AssertionError(f"{tool.name}.{k}: add it to EXPLICIT_ARGS")
    return out


def test_every_read_tool_summary_fits_its_cap(dsn, heavy):
    """Each read tool's default (summary) answer on a dense year of synthetic data, as the model reads it (compact.dumps),
    stays under READ_RESULT_MAX_CHARS (or its own entry in READ_RESULT_MAX_CHARS_BY_TOOL)."""
    today = datetime.now(TZ).date()
    sizes, failed = {}, {}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for t in TOOLS:
            if t.writes:
                continue
            args = _args_for(t, today)
            try:
                res = run_tool(conn, heavy, "test", t.name, args)
            except Exception as e:
                conn.rollback()
                failed[t.name] = f"{type(e).__name__}: {e}"
                continue
            sizes[t.name] = len(compact.dumps(res))
    assert set(failed) <= MAY_FAIL, {k: v for k, v in failed.items() if k not in MAY_FAIL}
    over = {n: s for n, s in sizes.items() if s > P.READ_RESULT_MAX_CHARS_BY_TOOL.get(n, P.READ_RESULT_MAX_CHARS)}
    assert not over, over
    assert len(sizes) >= 30, sorted(sizes)   # the bulk of the read tools really ran
