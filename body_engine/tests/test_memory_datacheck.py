"""Long-term memory hygiene, phase 3 (issue #113): memory against the user's own data, and promotion to the profile card.

- no database: the claim extractors and verdicts (memory_datacheck) on made-up data;
- database (a separate test database BODY_TEST_DB_NAME, default body_test_memory_datacheck, dropped and recreated on every run):
  data_mismatch / promote in get_memory_review, owner only (never through a memory share), snoozing a mismatch, the nightly
  confirmation of what the data backs (idempotent, logged, rollback_write), the confirmation counter, user isolation.
All texts and numbers are made-up examples; no paid API is called.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.assistant import briefing, memory, memory_datacheck as dc, memory_params as mp, memory_review, writelog
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_memory_datacheck")
TODAY = date(2026, 10, 27)   # a Tuesday


# ---------------------------------------------------------------- no database needed
class Ctx:
    """Stands in for memory_datacheck.Context with fixed data."""
    today = TODAY

    def __init__(self, training=None, weight=None, goal=None, supplements=(), events=()):
        self.training = training or {}
        self.window = [TODAY - timedelta(days=i) for i in range(mp.TRAIN_WINDOW_DAYS)]
        self.enough_training = len(self.training) >= mp.TRAIN_MIN_DAYS_EVER
        self.weight_now, self.goal_kg = weight, goal
        self.supplements, self.events = list(supplements), list(events)


def _days(weekdays=None, every=1, types=("strength_training",), n=56):
    """Training days in the window: every `every`-th day, or only on those weekdays."""
    out = {}
    for i in range(n):
        d = TODAY - timedelta(days=i)
        if (weekdays is None and i % every == 0) or (weekdays is not None and d.weekday() in weekdays):
            out[d] = set(types)
    return out


def _v(ctx, text, topic=None):
    return dc.check_text(ctx, text, topic)


def test_training_frequency():
    five = Ctx(_days(every=2))                                   # ~3.5 days a week over 8 weeks
    r = _v(five, "每周练 5 天")
    assert r["verdict"] == "mismatch" and r["kind"] == "training_frequency" and "3.5" in r["data"] and "5" in r["claim"]
    assert _v(five, "每周练 3-4 次")["verdict"] == "match"
    assert _v(five, "一周训练四天")["verdict"] == "match"        # within the tolerance of one day
    assert _v(Ctx(_days(every=1)), "每周练 3 天")["verdict"] == "mismatch"   # trains more than said
    assert _v(five, "5 days a week at the gym")["verdict"] == "mismatch"
    r = _v(five, "每周三打篮球")                                    # 三 is a weekday here, not a count: no basketball on Wednesdays
    assert r["kind"] == "training_day" and r["verdict"] == "mismatch"


def test_training_claims_that_are_no_claims_or_cannot_be_judged():
    five = Ctx(_days(every=2))
    for text in ("想每周练 5 天", "打算每周练5天", "以前每周练 5 天", "建议每周练 5 天", "每周吃 3 次鱼", "不要每周练 5 天"):
        assert _v(five, text) is None, text
    assert _v(Ctx(_days(n=2)), "每周练 5 天") is None            # fewer than TRAIN_MIN_DAYS_EVER training days: no data to judge with
    assert _v(Ctx(), "每周练 5 天") is None


def test_sport_frequency_and_weekday_habits():
    wed = Ctx({**_days(weekdays=(2,), types=("basketball",)), **_days(weekdays=(0, 4), types=("strength_training",))})
    assert _v(wed, "每周三晚上打篮球")["verdict"] == "match" and "8 次" in _v(wed, "每周三晚上打篮球")["data"]
    r = _v(wed, "每周六打篮球")                                   # no basketball on Saturdays in 8 weeks
    assert r["verdict"] == "mismatch" and r["kind"] == "training_day" and "0 次" in r["data"]
    assert _v(wed, "周六打篮球") is None                          # no "every": maybe one match, not a habit
    assert _v(wed, "每周游泳 2 次")["verdict"] == "mismatch"      # a sport-specific frequency: no swimming at all
    assert _v(wed, "每周篮球 1 次")["verdict"] == "match"
    assert _v(wed, "每周练 5 天，周末篮球")["kind"] == "training_frequency"   # the count is general, the sport is a weekend extra
    assert _v(wed, "每周六不打篮球") is None                      # negated
    assert _v(wed, "每周三、周六打篮球和游泳") is None            # two sports, two days: cannot pair them up


def test_weight_and_goal():
    c = Ctx(weight=72.4, goal=65.0)
    assert _v(c, "最近体重 72kg")["verdict"] == "match"
    r = _v(c, "最近体重 77kg")
    assert r["verdict"] == "mismatch" and r["kind"] == "weight" and "72.4" in r["data"]
    assert _v(c, "体重 145 斤")["verdict"] == "match"             # 斤 = half a kilo
    assert _v(c, "2026-09-07 体重 77kg") is None and _v(c, "10 月中体重 72kg") is None   # a past moment
    assert _v(c, "卧推 60kg") is None                              # not body weight
    assert _v(c, "目标体重 65kg")["verdict"] == "match"
    assert _v(c, "目标：3 个月内先到 70kg，最终 60–65kg")["verdict"] == "match"
    r = _v(c, "目标 60kg")
    assert r["verdict"] == "mismatch" and r["kind"] == "goal_weight" and "65" in r["data"]
    assert _v(Ctx(weight=None, goal=None), "最近体重 72kg") is None and _v(Ctx(weight=72, goal=None), "目标 65kg") is None
    assert _v(c, "体重从 77kg 降到 72kg")["verdict"] == "match"   # either number may be the current one


def _supp(name, status="active", stopped=None, ingredients=None):
    return {"name": name, "ingredients": ingredients, "status": status, "stopped_on": stopped}


def test_supplements():
    c = Ctx(supplements=[_supp("Creatine Monohydrate"), _supp("Fish Oil", "paused", TODAY - timedelta(days=30)), _supp("Zinc Plus")])
    assert _v(c, "每天吃肌酸 5g")["verdict"] == "match"
    r = _v(c, "每天吃鱼油")
    assert r["verdict"] == "mismatch" and "paused" in r["data"]
    r = _v(c, "每天随餐吃褪黑素")
    assert r["verdict"] == "mismatch" and "没有" in r["data"]
    assert _v(c, "补剂：肌酸 5g", "supplements")["verdict"] == "match"
    assert _v(c, "补剂：肌酸、鱼油", "supplements")["verdict"] == "mismatch"
    for text in ("不吃鱼油", "鱼油停了", "想吃褪黑素", "建议每天吃褪黑素", "肌酸会让血肌酐偏高"):    # negated / wish / just a fact about it
        assert _v(c, text) is None, text
    assert _v(Ctx(supplements=[]), "每天吃肌酸") is None          # the supplements table is not used: nothing to compare with


def _event(title, part, resolved=None):
    return {"day": TODAY - timedelta(days=100), "title": title, "body_part": part, "resolved_on": resolved}


def test_injuries():
    ongoing = Ctx(events=[_event("左踝反复扭伤", "左踝")])
    assert _v(ongoing, "左踝疼，反复扭伤")["verdict"] == "match"
    r = _v(ongoing, "脚踝已经好了，不痛了")
    assert r["verdict"] == "mismatch" and "还没标好转" in r["data"]
    better = Ctx(events=[_event("膝盖扭伤", "左膝", TODAY - timedelta(days=40))])
    r = _v(better, "左膝疼，不能深蹲")
    assert r["verdict"] == "mismatch" and "已标好转" in r["data"]
    assert _v(better, "膝盖好了")["verdict"] == "match"
    assert _v(Ctx(events=[_event("膝盖扭伤", "左膝", TODAY - timedelta(days=3))]), "左膝疼") is None   # better only a few days ago
    assert _v(ongoing, "禁用动作：深蹲跳（踝痛）") is None    # an exercise rule, not a status
    assert _v(ongoing, "腰疼") is None                             # no event about the lower back: nothing to compare with


def test_entry_level_rules():
    c = Ctx(_days(every=2), weight=72.0, supplements=[_supp("Creatine")])
    r = _v(c, "每周练 3 天。最近体重 85kg。每天吃肌酸")
    assert r["verdict"] == "mismatch" and r["kind"] == "weight"   # one wrong claim makes the entry a mismatch
    r = _v(c, "每周练 3 天。每天吃肌酸")
    assert r["verdict"] == "match"
    assert len(r["claim"]) <= mp.DATA_CLAIM_CHARS and len(r["data"]) <= mp.DATA_CLAIM_CHARS
    assert _v(c, "喜欢吃苹果") is None and _v(c, "") is None


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
        c.rollback()


@pytest.fixture(autouse=True)
def distinct_notes(monkeypatch):
    """The made-up notes share most of their words; only (nearly) identical ones count as duplicates here."""
    monkeypatch.setattr(mp, "SIMILAR_MIN_RATIO", 0.99)


@pytest.fixture()
def users(conn):
    """A owns the memory and the data; B has a memory share from A (and only that); C has nothing. The flag is on for everyone."""
    ids = {n: conn.execute("INSERT INTO users (display_name, timezone) VALUES (%s, 'Australia/Sydney') RETURNING id",
                           (n,)).fetchone()["id"] for n in "ABC"}
    conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["memory"]))
    flags.set_flag(conn, "memory_hygiene", "all", actor="test")
    conn.commit()
    flags.invalidate()
    briefing.clear()
    return ids


def _today(conn, uid):
    from body_engine import queries
    return queries.local_today(conn, uid)


def _workout(conn, uid, day, typ, minutes=60):
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=9)
    conn.execute(
        """INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
           VALUES (%s, 'ow', %s, %s, %s, %s, %s, %s, %s)""",
        (uid, f"{typ}-{day}", typ, typ, day, start, start + timedelta(minutes=minutes), minutes))


def _train(conn, uid, every=2, typ="strength_training", weekdays=None):
    """Workouts over the last 8 weeks: every `every`-th day, or on those weekdays only."""
    t = _today(conn, uid)
    for i in range(56):
        if (weekdays is None and i % every == 0) or (weekdays is not None and (t - timedelta(days=i)).weekday() in weekdays):
            _workout(conn, uid, t - timedelta(days=i), typ)


def _note(conn, uid, text, days=0, created_days=None, origin=None, topic=None, confirms=0):
    n = memory.add_observation(conn, uid, text, origin=origin, topic=topic)
    conn.execute("UPDATE assistant_observations SET confirmed_at = now() - make_interval(days => %s), "
                 "created_at = now() - make_interval(days => %s), confirm_count = %s WHERE id = %s",
                 (days, days if created_days is None else created_days, confirms, n["id"]))
    return n["id"]


def _review(conn, uid, **kw):
    return run_tool(conn, uid, "butler", "get_memory_review", kw)


def _apply(conn, uid, *items):
    return run_tool(conn, uid, "butler", "apply_memory_changes", {"items": [
        {"into": None, "text": None, "user_ok": False, **it} for it in items]})


def _row(conn, nid):
    return conn.execute("SELECT status, confirm_count, confirmed_at FROM assistant_observations WHERE id = %s", (nid,)).fetchone()


def test_mismatch_is_offered_and_snoozed_once_the_user_has_looked(conn, users):
    a = users["A"]
    _train(conn, a, every=2)                                         # about 3.5 days a week
    bad = _note(conn, a, "每周练 5 天", days=3, created_days=3)       # the claim does not fit
    ok = _note(conn, a, "每周练 3 天", days=3, created_days=3)
    r = _review(conn, a)
    mism = {m["ref"]: m for m in r["data_mismatch"]}
    assert set(mism) == {f"n{bad}"} and mism[f"n{bad}"]["check"] == "training_frequency"
    assert "5" in mism[f"n{bad}"]["claim"] and "3.5" in mism[f"n{bad}"]["data"] and r["count"] >= 1
    assert f"n{ok}" not in mism
    # the AI asks the user, who says it is a goal: confirm. The mismatch is not offered again for DATA_SNOOZE_DAYS
    res = _apply(conn, a, {"action": "confirm", "ref": f"n{bad}", "reason": "是目标，不是现状"})
    assert res["results"][0]["status"] == "ok"
    assert _review(conn, a)["data_mismatch"] == []
    conn.execute("UPDATE assistant_observations SET confirmed_at = now() - make_interval(days => %s) WHERE id = %s",
                 (mp.DATA_SNOOZE_DAYS + 1, bad))
    assert [m["ref"] for m in _review(conn, a)["data_mismatch"]] == [f"n{bad}"]    # asked again after the snooze


def test_mismatch_is_not_listed_twice_and_nothing_changes_by_itself(conn, users):
    a = users["A"]
    _train(conn, a, every=2)
    stale = _note(conn, a, "每周练 5 天", days=300, topic="training_preferences")   # stale AND contradicted by the data
    before = _row(conn, stale)
    r = _review(conn, a)
    assert [m["ref"] for m in r["data_mismatch"]] == [f"n{stale}"] and r["stale"] == []   # one place, not two
    assert _row(conn, stale) == before                                # reading changes nothing
    memory_review.run_all(conn)
    assert _row(conn, stale)["status"] == "active" and _row(conn, stale)["confirmed_at"] == before["confirmed_at"]


def test_weight_goal_supplement_injury_end_to_end(conn, users):
    a = users["A"]
    t = _today(conn, a)
    for i in range(7):
        conn.execute("INSERT INTO body_measurements (user_id, date, weight_kg) VALUES (%s, %s, %s)", (a, t - timedelta(days=i), 71.6))
    conn.execute("INSERT INTO assistant_status (user_id, key, label, value, tone, \"group\", source) "
                 "VALUES (%s, 'goal_weight', '目标体重', '65', 'neutral', '目标', 'assistant')", (a,))
    conn.execute("INSERT INTO supplements (user_id, name, status) VALUES (%s, 'Creatine', 'paused')", (a,))
    conn.execute("INSERT INTO events (user_id, occurred_at, category, body_part, title, resolved_on) "
                 "VALUES (%s, now() - interval '90 days', 'injury', '左膝', '膝盖扭伤', %s)", (a, t - timedelta(days=30)))
    ids = {k: _note(conn, a, txt, days=5, created_days=5) for k, txt in (
        ("weight", "最近体重 77kg"), ("goal", "目标体重 60kg"), ("supp", "每天吃肌酸 5g"), ("injury", "左膝疼，不能深蹲"),
        ("fine", "最近体重 72kg"))}
    got = {m["check"]: m["ref"] for m in _review(conn, a)["data_mismatch"]}
    assert got == {"weight": f"n{ids['weight']}", "goal_weight": f"n{ids['goal']}", "supplement": f"n{ids['supp']}",
                   "injury": f"n{ids['injury']}"}


def test_the_data_comparison_is_for_the_owner_only(conn, users):
    a, b, c = users["A"], users["B"], users["C"]
    _train(conn, a, every=2)
    _note(conn, a, "每周练 5 天", days=3, created_days=3)
    _note(conn, a, "最近体重 70kg", days=100, created_days=100, topic="goals", confirms=3)
    mine = _review(conn, a)
    assert mine["data_mismatch"] and "promote" in mine
    seen = _review(conn, b, user=str(a))                              # B reads A's review through the memory share
    assert "data_mismatch" not in seen and "promote" not in seen
    text = json.dumps(seen, ensure_ascii=False)                       # the memory itself is shared, what the records say is not
    assert "近 8 周" not in text and "近期体重" not in text and '"claim"' not in text
    with pytest.raises(PermissionError):
        _review(conn, c, user=str(a))
    other = _review(conn, c)                                          # C's own review is empty and sees nothing of A's data
    assert other["data_mismatch"] == [] and other["promote"] == [] and "5 天" not in json.dumps(other, ensure_ascii=False)


def test_nightly_confirmation_is_monthly_logged_idempotent_and_reversible(conn, users):
    a, b = users["A"], users["B"]
    _train(conn, a, weekdays=(0, 1, 2, 3, 4))                                 # five days a week
    due = _note(conn, a, "每周练 5 天", days=mp.DATA_CONFIRM_GAP_DAYS + 5, created_days=100, topic="training_preferences", confirms=1)
    fresh = _note(conn, a, "每周练 6 天", days=3, created_days=3)
    wrong = _note(conn, a, "每周练 2 天", days=mp.DATA_CONFIRM_GAP_DAYS + 5, created_days=100)
    other = _note(conn, b, "每周练 5 天", days=60, created_days=60)           # B has no workouts: no data, nothing confirmed
    res = {r["user_id"]: r for r in memory_review.run_all(conn)}
    assert res[a]["confirmed_by_data"] == 1 and res[b]["confirmed_by_data"] == 0
    r = _row(conn, due)
    assert r["confirm_count"] == 2 and datetime.now(timezone.utc) - r["confirmed_at"] < timedelta(minutes=5)
    assert _row(conn, fresh)["confirm_count"] == 0 and _row(conn, wrong)["confirm_count"] == 0 and _row(conn, other)["confirm_count"] == 0
    assert memory_review.run_all(conn, a)[0]["confirmed_by_data"] == 0        # the second run changes nothing
    assert _row(conn, due)["confirm_count"] == 2
    log = conn.execute("SELECT id, tool, channel, kind, target FROM ai_write_log WHERE user_id = %s AND kind = 'memory_state'", (a,)).fetchall()
    assert [(x["tool"], x["channel"], x["target"]) for x in log] == [("memory-review-run", "system", f"n{due}")]
    out = writelog.LOG.rollback(conn, a, log[0]["id"])                       # one call puts the old confirmation back
    assert out["kind"] == "memory_state" and _row(conn, due)["confirm_count"] == 1


def test_confirmation_counter(conn, users):
    a = users["A"]
    nid = _note(conn, a, "喜欢周日长跑", days=10, created_days=10)
    assert _row(conn, nid)["confirm_count"] == 0
    memory.confirm_observation(conn, a, nid)                                  # 「还准确」
    assert _row(conn, nid)["confirm_count"] == 1
    memory.confirm_observation(conn, a, nid)                                  # again the same day: still one
    memory.add_observation(conn, a, "喜欢周日长跑")                            # stated again the same day: still one
    assert _row(conn, nid)["confirm_count"] == 1
    conn.execute("UPDATE assistant_observations SET confirmed_at = now() - interval '3 days' WHERE id = %s", (nid,))
    assert memory.add_observation(conn, a, "喜欢周日长跑")["duplicate"] is True      # stated again on another day
    assert _row(conn, nid)["confirm_count"] == 2
    conn.execute("UPDATE assistant_observations SET confirmed_at = now() - interval '3 days' WHERE id = %s", (nid,))
    _apply(conn, a, {"action": "confirm", "ref": f"n{nid}", "reason": "还对"})        # confirmed in a review
    assert _row(conn, nid)["confirm_count"] == 3
    _apply(conn, a, {"action": "confirm", "ref": f"n{nid}", "reason": "还对"})        # the same batch again: no more
    assert _row(conn, nid)["confirm_count"] == 3
    memory.update_profile(conn, a, "goals", "示例：先减脂")
    conn.execute("UPDATE assistant_profile SET confirmed_at = now() - interval '2 days' WHERE user_id = %s", (a,))
    memory.confirm_profile(conn, a, "goals")
    assert conn.execute("SELECT confirm_count FROM assistant_profile WHERE user_id = %s AND key = 'goals'", (a,)).fetchone()["confirm_count"] == 1


def test_promotion_candidates_and_apply(conn, users):
    a, b = users["A"], users["B"]
    memory.update_profile(conn, a, "training_preferences", "示例：喜欢哑铃，不喜欢跑步机")
    twice = _note(conn, a, "喜欢在周日长跑", days=5, created_days=20, topic="training_preferences", confirms=2)
    old = _note(conn, a, "常在健身房晨练", days=5, created_days=mp.PROMOTE_MIN_AGE_DAYS + 5, topic="training_preferences")
    young = _note(conn, a, "喜欢游泳", days=5, created_days=10, topic="training_preferences", confirms=1)
    stale = _note(conn, a, "器械只用哑铃", days=400, created_days=500, topic="training_preferences", confirms=5)
    already = _note(conn, a, "喜欢哑铃", days=5, created_days=90, topic="training_preferences")     # the card item says it already
    no_topic = _note(conn, a, "示例：zzz", days=5, created_days=90, confirms=3)
    _note(conn, b, "喜欢瑜伽", days=5, created_days=90, topic="training_preferences", confirms=3)
    promote = {p["ref"]: p for p in _review(conn, a)["promote"]}
    assert set(promote) == {f"n{twice}", f"n{old}"}                           # not young, not stale, not said already, has a topic
    p = promote[f"n{twice}"]
    assert p["into"] == "k:training_preferences" and p["confirms"] == 2 and "哑铃" in p["card"]
    assert list(promote)[0] == f"n{twice}"                                    # most confirmed first
    # the user agrees: merge the note into its card item
    res = _apply(conn, a, {"action": "promote", "ref": f"n{twice}", "into": "k:training_preferences",
                           "text": "示例：喜欢哑铃，不喜欢跑步机；周日长跑", "reason": "确认过两次的习惯", "user_ok": True})
    assert res["results"][0]["status"] == "ok"
    assert _row(conn, twice)["status"] == "archived"
    assert f"n{twice}" not in {x["ref"] for x in _review(conn, a)["promote"]}
    assert all(f"n{x}" not in {y["ref"] for y in _review(conn, a)["promote"]} for x in (young, stale, already, no_topic))


def test_review_result_stays_short_and_reminder_counts_the_new_candidates(conn, users):
    a = users["A"]
    _train(conn, a, every=2)
    for i in range(9):
        _note(conn, a, f"示例 {i}：每周练 {5 + i % 2} 天，最近体重 {70 + i}kg", days=3, created_days=3, topic="goals")
    r = _review(conn, a)
    assert len(r["data_mismatch"]) == mp.DATA_MAX and len(r["promote"]) <= mp.PROMOTE_MAX
    assert r["count"] >= 9
    assert len(json.dumps(r, ensure_ascii=False)) < 7000
    memory_review.run_all(conn)
    st = memory_review.get_state(conn, a)
    assert st["candidates"] == r["count"]                                     # the stored number is what the briefing reminder reads


def test_two_users_do_not_mix(conn, users):
    a, c = users["A"], users["C"]
    _train(conn, a, every=2)
    _note(conn, a, "每周练 5 天", days=3, created_days=3)
    ra = {r["user_id"]: r for r in memory_review.run_all(conn)}
    assert ra[a]["candidates"] >= 1 and ra[c]["candidates"] == 0
    res = _apply(conn, c, {"action": "confirm", "ref": "n1", "reason": "x"})
    assert res["results"][0]["status"] == "error"
