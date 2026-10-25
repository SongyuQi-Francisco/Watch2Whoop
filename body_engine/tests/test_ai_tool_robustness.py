"""AI tool robustness (issue found in the production tool-call log): a call that leaves out a required argument gets "缺少参数 x"
instead of an internal KeyError, add_supplement checks its name at the entrance, and log_workout finds the library entry for
everyday Chinese exercise names (aliases) and offers the closest candidates when nothing matches.

Needs Postgres: its own test database BODY_TEST_DB_NAME_AI_TOOL_ROBUSTNESS (default body_test_ai_tool_robustness), dropped and
recreated on every run, never the production DB. SparkyFitness is a small fake; the pure parts run without a database."""
import os

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.assistant import sparky_write
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.toolspec import relaxed
from body_engine.training import catalog as tr_catalog
from body_engine.training import catalog_data as CD
from body_engine.training.gym_aliases import EXTRA_ALIASES

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_AI_TOOL_ROBUSTNESS", "body_test_ai_tool_robustness")


# ---------------------------------------------------------------- no database needed
def test_extra_aliases_point_at_real_exercises_and_stay_unique():
    curated = {x["slug"]: x for x in CD.CURATED_EXERCISES}
    owner: dict[str, str] = {}
    for slug, extra in EXTRA_ALIASES.items():
        assert slug in curated, f"{slug} is not a curated exercise"
        for a in extra:
            k = tr_catalog.norm(a)
            assert owner.setdefault(k, slug) == slug, f"alias {a} is on both {owner[k]} and {slug}"
    # a wording that already is some exercise's own name or alias must not be given to another one
    for slug, x in curated.items():
        for name in [x["name_zh"], x["name_en"], *(x.get("aliases") or [])]:
            k = tr_catalog.norm(name)
            if k in owner:
                assert owner[k] == slug, f"{name} is already an exercise of {slug}, not {owner[k]}"


def test_required_arguments_are_optional_where_the_handler_has_a_default():
    for name, key in (("list_supplements", "statuses"), ("get_reminders", "include_dismissed")):
        assert key not in relaxed(TOOLS_BY_NAME[name].input_schema).get("required", []), (name, key)


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


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def uid(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        return c.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES ('T', '2001-06-15', 180) "
                         "RETURNING id").fetchone()["id"]


def _last_audit(conn):
    return conn.execute("SELECT tool, ok, error FROM assistant_audit ORDER BY id DESC LIMIT 1").fetchone()


# ---------- missing required arguments ----------
@pytest.mark.parametrize("tool,args,param", [
    ("update_profile_card", {"value": "x", "label": None}, "key"),
    ("report_feedback", {"title": "t", "what": "w"}, "kind"),
    ("add_supplement", {"status": "active"}, "name"),
])
def test_missing_required_argument_says_which(conn, uid, tool, args, param):
    with pytest.raises(ValueError, match=f"缺少参数 .*{param}") as e:
        run_tool(conn, uid, "test", tool, args)
    assert not isinstance(e.value, KeyError)
    last = _last_audit(conn)
    assert last["tool"] == tool and not last["ok"] and "缺少参数" in last["error"]   # the audit keeps the readable reason


def test_several_missing_arguments_are_named_together(conn, uid):
    with pytest.raises(ValueError, match="缺少参数 kind") as e:
        run_tool(conn, uid, "test", "report_feedback", {})
    assert "title" in str(e.value) and "what" in str(e.value)


def test_a_tool_that_copes_with_a_missing_argument_keeps_working(conn, uid):
    # log_weight reads `overwrite` with .get: leaving it out was never an error, and still is not one
    assert "overwrite" not in str(_missing(conn, uid))


def _missing(conn, uid):
    try:
        run_tool(conn, uid, "test", "log_weight", {"date": "2000-01-01", "weight_kg": 80})
    except Exception as e:  # noqa: BLE001  SparkyFitness is not linked here: any error but the missing-argument one is fine
        return e
    return ""


def test_tools_that_used_to_raise_keyerror_now_run_without_the_optional_arguments(conn, uid):
    run_tool(conn, uid, "test", "list_supplements", {})                        # no KeyError: statuses left out = all
    res = run_tool(conn, uid, "test", "get_reminders", {})
    assert "reminders" in res                                                  # include_dismissed left out = false


# ---------- add_supplement ----------
@pytest.mark.parametrize("name", ["", "   ", None])
def test_add_supplement_without_a_name_is_refused_at_the_entrance(conn, uid, name):
    args = {"status": "active", "dose": None, "timing": None, "ingredients": None, "started_on": None,
            "rationale": None, "advice": None, "journal_key": None, "name": name}
    with pytest.raises(ValueError, match="缺少参数 name"):
        run_tool(conn, uid, "test", "add_supplement", args)
    assert conn.execute("SELECT count(*) AS n FROM supplements WHERE user_id = %s", (uid,)).fetchone()["n"] == 0


def test_add_supplement_with_a_name_still_works(conn, uid):
    res = run_tool(conn, uid, "test", "add_supplement", {"name": " 镁 ", "status": "active"})
    assert res["name"] == "镁"


# ---------- log_workout: everyday Chinese names ----------
class FakeSparky:
    """A SparkyFitness whose exercise library is named the way people name their own exercises."""

    def __init__(self, names):
        self.library = [{"id": f"ex-{i}", "name": n, "category": "strength", "description": None} for i, n in enumerate(names)]
        self.writes = []

    def get(self, path, **params):
        if path == "/exercises":
            return {"exercises": self.library, "totalCount": len(self.library)}
        if path == "/v2/exercise-entries/by-date":
            return []
        raise AssertionError(path)

    def request(self, method, path, body=None, params=None, multipart_field=None):
        self.writes.append((method, path, body))
        if path == "/exercise-preset-entries":
            return {"id": "ws-1", "name": body["name"], "exercises": [
                {"exercise_snapshot": {"name": e["exercise_id"]}, "sets": e["sets"]} for e in body["exercises"]]}
        return None


LIBRARY = ["杠铃深蹲", "器械腿屈伸", "硬拉", "高位下拉", "器械腿弯举", "坐姿划船", "哑铃卧推"]


def _resolve(conn, uid, name, library=LIBRARY):
    idx = sparky_write.exercise_index(conn, uid, FakeSparky(library))
    hit, cands = sparky_write.resolve_exercise(idx, name, None)
    return (hit or {}).get("name"), cands


@pytest.mark.parametrize("asked,expected", [
    ("杠铃背深蹲", "杠铃深蹲"),        # the catalog's own standard name; the library calls it 杠铃深蹲
    ("坐姿腿屈伸", "器械腿屈伸"),      # standard name -> alias the library uses
    ("传统杠铃硬拉", "硬拉"),
    ("杠铃后蹲", "杠铃深蹲"),          # extra aliases (training/gym_aliases.py)
    ("背蹲", "杠铃深蹲"),
    ("腿伸展", "器械腿屈伸"),
    ("常规硬拉", "硬拉"),
    ("宽握下拉", "高位下拉"),
    ("坐姿腿屈曲", "器械腿弯举"),
])
def test_common_chinese_names_reach_the_library_entry(conn, uid, asked, expected):
    hit, cands = _resolve(conn, uid, asked)
    assert hit == expected and cands == []


def test_no_match_still_offers_the_closest_names(conn, uid):
    hit, cands = _resolve(conn, uid, "史密斯机深蹲")   # nothing with that name anywhere
    assert hit is None
    assert 1 <= len(cands) <= 5 and cands[0] == "杠铃深蹲"
    hit, cands = _resolve(conn, uid, "完全不相干的动作名")
    assert hit is None and len(cands) == 5            # never an empty list while the library has entries
    assert _resolve(conn, uid, "杠铃背深蹲", library=[]) == (None, [])   # an empty library has nothing to offer


def test_substring_candidates_still_come_first(conn, uid):
    hit, cands = _resolve(conn, uid, "卧推", ["上斜哑铃卧推（30°）", "高位下拉"])
    assert hit is None and cands[0] == "上斜哑铃卧推（30°）"


def test_log_workout_with_a_catalog_name_writes_the_library_exercise(conn, uid):
    d = sparky_write.user_today(conn, uid).isoformat()
    fake = FakeSparky(LIBRARY)
    sets = [{"reps": 8, "weight_kg": 80, "rir": 2, "duration_s": None, "set_type": None, "notes": None}] * 3
    w = {"date": d, "name": "腿日", "notes": None, "allow_duplicate": False, "create_missing_exercises": False,
         "exercises": [{"exercise": n, "exercise_id": None, "notes": None, "sets": sets} for n in ("杠铃背深蹲", "坐姿腿屈伸")]}
    sparky_write.log_workout(conn, uid, w, client=fake)
    (_, path, body), = fake.writes
    assert path == "/exercise-preset-entries"
    assert [e["exercise_id"] for e in body["exercises"]] == ["ex-0", "ex-1"]


def test_log_workout_unknown_name_error_lists_the_closest(conn, uid):
    d = sparky_write.user_today(conn, uid).isoformat()
    sets = [{"reps": 8, "weight_kg": 20, "rir": 2, "duration_s": None, "set_type": None, "notes": None}]
    w = {"date": d, "name": "x", "notes": None, "allow_duplicate": False, "create_missing_exercises": False,
         "exercises": [{"exercise": "史密斯机深蹲", "exercise_id": None, "notes": None, "sets": sets}]}
    with pytest.raises(ValueError, match="对不上") as e:
        sparky_write.log_workout(conn, uid, w, client=FakeSparky(LIBRARY))
    assert '"candidates": []' not in str(e.value) and "杠铃深蹲" in str(e.value)
