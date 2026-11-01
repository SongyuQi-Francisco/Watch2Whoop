"""Long-term memory hygiene, the status card (issue #113, last item): get_memory_review also flags the status card items the records
contradict or nobody rewrote for long. Flagged only: nothing is changed, the AI answers with set_status_items.

- no database: the injury / phase verdicts (memory_datacheck.check_status_item) on made-up data;
- database (a separate test database BODY_TEST_DB_NAME, default body_test_status_review, dropped and recreated on every run):
  `status_card` in get_memory_review: injury and phase mismatches, stale items by group, the snooze, auto and lab-linked items left
  out, owner only (never through a memory share), rewriting an item confirms it, the result stays short, the nightly count.
All texts and numbers are made-up examples; no paid API is called.
"""
import json
import os
from datetime import date, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import queries
from body_engine.assistant import briefing, memory_datacheck as dc, memory_params as mp, memory_review, status
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.datalayer import compact
from body_engine.db import migrate
from body_engine.records import lab_catalog
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_status_review")
TODAY = date(2026, 10, 27)


# ---------------------------------------------------------------- no database needed
class Ctx:
    """Stands in for memory_datacheck.Context with fixed data."""
    today = TODAY

    def __init__(self, events=(), change=None):
        self.events, self.weight_change = list(events), change


def _event(title, part, resolved=None):
    return {"day": TODAY - timedelta(days=100), "title": title, "body_part": part, "resolved_on": resolved}


def _item(key, label, value, group="健康"):
    return {"key": key, "label": label, "value": value, "group": group}


def _check(ctx, item):
    return dc.check_status_item(ctx, item)


def test_injury_state_of_a_short_value():
    for value, want in (("受限", "hurt"), ("疼痛", "hurt"), ("还有点疼", "hurt"), ("痊愈", "recovered"), ("无痛", "recovered"),
                        ("基本不痛了", "recovered"), ("healed", "recovered"), ("pain", "hurt")):
        assert dc._injury_state(value) == want, value
    for value in ("明显好转", "疼痛好转", "无痛但受限", "未痊愈", "not recovered", "稳定", "42", ""):   # improving, mixed, negated, no word
        assert dc._injury_state(value) is None, value


def test_injury_item_against_the_body_events():
    open_ev = Ctx([_event("肩关节内旋受限", "右肩")])
    better = Ctx([_event("膝盖扭伤", "左膝", TODAY - timedelta(days=40))])
    hurts = _item("shoulder", "右肩", "受限")
    r = _check(better, _item("knee", "左膝", "疼痛"))
    assert r["kind"] == "injury" and "膝" in r["claim"] and "已标好转" in r["data"]
    assert _check(open_ev, hurts) is None                                           # still open: they agree
    r = _check(open_ev, _item("shoulder", "右肩", "痊愈"))
    assert r["kind"] == "injury" and "还没标好转" in r["data"]
    assert _check(better, _item("knee", "左膝", "痊愈")) is None
    assert _check(Ctx([_event("膝盖扭伤", "左膝", TODAY - timedelta(days=3))]), _item("knee", "左膝", "疼痛")) is None   # better days ago
    assert _check(better, _item("knee", "左膝", "明显好转")) is None                 # "getting better" is not judged
    assert _check(Ctx([_event("腰肌劳损", "腰")]), _item("neck", "颈", "痊愈")) is None   # no event about that part
    assert _check(better, _item("knee_pain", "疼痛", "受限")) is not None            # the key names the part, the label does not
    assert _check(better, _item("recovery_days", "恢复", "5")) is None                # a value without an injury word is not judged
    assert _check(Ctx(), hurts) is None                                              # no events at all


def test_phase_against_the_weight_trend():
    rising, falling, flat = Ctx(change=(72.0, 73.4)), Ctx(change=(75.0, 73.6)), Ctx(change=(72.0, 72.6))
    phase = lambda v, **kw: _item(kw.get("key", "phase"), kw.get("label", "当前阶段"), v, "训练")
    r = _check(rising, phase("减脂期"))
    assert r["kind"] == "phase" and "72.0→73.4" in r["data"] and "减脂" in r["claim"]
    assert _check(falling, phase("增肌"))["kind"] == "phase"
    assert _check(flat, phase("减脂")) is None and _check(flat, phase("增肌")) is None      # inside the wobble
    assert _check(falling, phase("减脂")) is None and _check(rising, phase("增肌")) is None   # moving the way it says
    assert _check(rising, phase("减脂增肌")) is None and _check(falling, phase("增肌减脂")) is None   # recomposition: both ways
    assert _check(rising, phase("维持")) is None and _check(rising, phase("准备减脂")) is None
    assert _check(rising, phase("不减脂")) is None                                    # negated
    assert _check(Ctx(change=None), phase("减脂")) is None                            # too few weigh-ins
    assert _check(rising, _item("phase", "x", "cutting", "训练"))["kind"] == "phase"   # found by key; English words
    assert _check(rising, _item("stage", "当前阶段", "减脂", "训练"))["kind"] == "phase"   # found by label
    assert _check(rising, _item("goal_text", "目标", "减脂", "目标")) is None           # not the phase item


def test_every_group_has_a_stale_limit():
    assert set(mp.STATUS_STALE_DAYS) == set(status.GROUPS)       # a new group must be given a limit on purpose
    assert mp.STATUS_SNOOZE_DAYS == mp.DATA_SNOOZE_DAYS and mp.STATUS_MAX <= 8


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


@pytest.fixture()
def users(conn):
    """A owns the card and the records; B has a memory share from A (and only that); C has nothing. The flag is on for everyone."""
    ids = {n: conn.execute("INSERT INTO users (display_name, timezone) VALUES (%s, 'Australia/Sydney') RETURNING id",
                           (n,)).fetchone()["id"] for n in "ABC"}
    conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["memory"]))
    flags.set_flag(conn, "memory_hygiene", "all", actor="test")
    conn.commit()
    flags.invalidate()
    briefing.clear()
    return ids


def _put(conn, uid, key, value, label="示例", group="健康", age=0, source="assistant", unit=None):
    """A status card row written `age` days ago."""
    conn.execute(
        """INSERT INTO assistant_status (user_id, key, label, value, unit, tone, "group", source, updated_at)
           VALUES (%s, %s, %s, %s, %s, 'neutral', %s, %s, now() - make_interval(days => %s))
           ON CONFLICT (user_id, key) DO UPDATE SET label = EXCLUDED.label, value = EXCLUDED.value, "group" = EXCLUDED."group",
             source = EXCLUDED.source, updated_at = EXCLUDED.updated_at""", (uid, key, label, value, unit, group, source, age))


def _review(conn, uid, **kw):
    return run_tool(conn, uid, "butler", "get_memory_review", kw)


def _cards(conn, uid):
    return {c["key"]: c for c in _review(conn, uid)["status_card"]}


def _rows(conn, uid):
    return conn.execute("SELECT key, value, tone, source, updated_at FROM assistant_status WHERE user_id = %s ORDER BY key", (uid,)).fetchall()


def _weigh(conn, uid, first, last):
    """Weigh-ins on 4 days of the first week and 4 days of the last week of the 4-week window."""
    t = queries.local_today(conn, uid)
    for i in (27, 26, 25, 24):
        conn.execute("INSERT INTO body_measurements (user_id, date, weight_kg) VALUES (%s, %s, %s)", (uid, t - timedelta(days=i), first))
    for i in (6, 5, 4, 3):
        conn.execute("INSERT INTO body_measurements (user_id, date, weight_kg) VALUES (%s, %s, %s)", (uid, t - timedelta(days=i), last))


def _injury(conn, uid, part, title, resolved_days_ago=None):
    t = queries.local_today(conn, uid)
    conn.execute("INSERT INTO events (user_id, occurred_at, category, body_part, title, resolved_on) "
                 "VALUES (%s, now() - interval '90 days', 'injury', %s, %s, %s)",
                 (uid, part, title, None if resolved_days_ago is None else t - timedelta(days=resolved_days_ago)))


def test_injury_and_phase_mismatch_are_flagged_and_nothing_changes(conn, users):
    a = users["A"]
    _injury(conn, a, "右肩", "肩关节内旋受限", resolved_days_ago=30)          # marked better a month ago
    _injury(conn, a, "左膝", "膝盖扭伤")                                       # still open
    _weigh(conn, a, 70.0, 71.6)                                               # up 1.6 kg
    _put(conn, a, "shoulder", "受限", "右肩", age=45)                         # says it hurts, the event says better
    _put(conn, a, "knee", "受限", "左膝", age=45)                             # says it hurts, the event is open: agree
    _put(conn, a, "phase", "减脂", "当前阶段", "训练", age=45)                  # cutting while the weight went up
    before = _rows(conn, a)
    r = _review(conn, a)
    cards = {c["key"]: c for c in r["status_card"]}
    assert set(cards) == {"shoulder", "phase"}
    s, p = cards["shoulder"], cards["phase"]
    assert (s["why"], s["check"], s["label"], s["value"]) == ("mismatch", "injury", "右肩", "受限") and "已标好转" in s["data"]
    assert (p["why"], p["check"]) == ("mismatch", "phase") and "70.0→71.6" in p["data"] and "减脂" in p["claim"]
    assert r["count"] >= 2
    assert _rows(conn, a) == before                                            # reading changes nothing
    memory_review.run_all(conn)
    assert _rows(conn, a) == before                                            # nor does the nightly run


def test_a_recently_written_item_is_not_judged_against_the_records(conn, users):
    a = users["A"]
    _injury(conn, a, "右肩", "肩关节内旋受限", resolved_days_ago=30)
    _put(conn, a, "shoulder", "受限", "右肩", age=mp.STATUS_SNOOZE_DAYS - 5)    # someone looked at it lately
    assert _cards(conn, a) == {}
    conn.execute("UPDATE assistant_status SET updated_at = now() - make_interval(days => %s) WHERE user_id = %s",
                 (mp.STATUS_SNOOZE_DAYS + 1, a))
    assert set(_cards(conn, a)) == {"shoulder"}                                # asked again after the snooze


def test_stale_by_group_and_listed_once(conn, users):
    a = users["A"]
    lim = mp.STATUS_STALE_DAYS
    _put(conn, a, "goal_text", "示例目标", group="目标", age=lim["目标"] - 5)       # not yet
    _put(conn, a, "habit", "示例习惯", group="作息", age=lim["作息"] - 5)           # not yet (a longer limit)
    _put(conn, a, "plan", "示例计划", group="训练", age=lim["训练"] + 30)           # 1.5 times the limit
    _put(conn, a, "diet", "示例饮食", group="饮食", age=lim["饮食"] + 10)
    _put(conn, a, "injury", "示例伤病", group="健康", age=lim["健康"] + 90)         # 2.5 times the limit: first
    cards = _review(conn, a)["status_card"]
    assert [c["key"] for c in cards] == ["injury", "plan", "diet"]               # the stalest (against its own limit) first
    first = cards[0]
    assert abs(first.pop("days") - (lim["健康"] + 90)) <= 1                          # (a daylight saving change can shift it by an hour)
    assert first == {"key": "injury", "label": "示例", "value": "示例伤病", "why": "stale", "limit": lim["健康"]}
    _injury(conn, a, "右肩", "肩关节内旋受限", resolved_days_ago=30)
    _put(conn, a, "shoulder", "受限", "右肩", age=300)                           # stale AND contradicted: one entry, as a mismatch
    got = [c for c in _review(conn, a)["status_card"] if c["key"] == "shoulder"]
    assert len(got) == 1 and got[0]["why"] == "mismatch"


def test_auto_and_lab_linked_items_are_left_out(conn, users):
    a = users["A"]
    _put(conn, a, "recovery", "60", "恢复", group="身体", age=400, source="auto")      # recomputed by the backend
    _put(conn, a, "uric_acid", "偏高 480", "尿酸", age=400, source="auto")             # read from the lab table
    _put(conn, a, "weight", "72", "体重", group="身体", age=400)                        # an auto key is the backend's, whatever the row says
    status.set_items(conn, a, [{"key": "hdl", "label": "HDL", "value": "偏低", "unit": None, "tone": "warn", "trend": None,
                                "good_direction": None, "group": "健康", "spark": None, "note": "多做有氧"}], [])
    lab_catalog.add_results(conn, a, date(2026, 10, 1), "Lab", [
        dict(test="HDL", test_key=None, value=0.9, value_text=None, unit="mmol/L", ref_low=1.0, ref_high=None, flag=None, note=None)], False)
    _put(conn, a, "hdl", "偏低", "HDL", age=400)                                      # the butler's old row: the card shows the lab value
    assert next(c for c in status.list_status(conn, a) if c["key"] == "hdl")["lab_key"] == "hdl"
    _put(conn, a, "hba1c", "示例", "糖化", age=400)                                    # a lab-like key with no reading: an ordinary item
    assert set(_cards(conn, a)) == {"hba1c"}


def test_status_card_is_for_the_owner_only(conn, users):
    a, b, c = users["A"], users["B"], users["C"]
    _injury(conn, a, "右肩", "肩关节内旋受限", resolved_days_ago=30)
    _weigh(conn, a, 70.0, 71.6)
    _put(conn, a, "shoulder", "受限", "右肩", age=45)
    _put(conn, a, "phase", "减脂", "当前阶段", "训练", age=45)
    _put(conn, a, "plan", "示例计划", group="训练", age=200)
    assert len(_review(conn, a)["status_card"]) == 3
    seen = _review(conn, b, user=str(a))                                          # B reads A's review through the memory share
    assert "status_card" not in seen
    text = json.dumps(seen, ensure_ascii=False)                                   # nothing of the records or the cards leaks
    for leak in ("70.0", "71.6", "已标好转", "示例计划", "shoulder", "体重周均"):
        assert leak not in text, leak
    assert _review(conn, b)["status_card"] == []                                  # B's own review has no card items
    with pytest.raises(PermissionError):
        _review(conn, c, user=str(a))
    assert _review(conn, c)["status_card"] == []                                  # C sees nothing of A's


def test_writing_the_item_again_confirms_it(conn, users):
    a = users["A"]
    _put(conn, a, "plan", "示例计划", group="训练", age=200)
    _put(conn, a, "old", "示例旧项", group="其他", age=300)
    assert set(_cards(conn, a)) == {"plan", "old"}
    item = {"key": "plan", "label": "示例", "value": "示例计划", "unit": None, "tone": "neutral", "trend": None,
            "good_direction": None, "group": "训练", "spark": None, "note": None}
    res = run_tool(conn, a, "butler", "set_status_items", {"items": [item], "remove_keys": ["old"]})
    assert res["written"] == ["plan"] and res["removed"] == ["old"]
    assert _review(conn, a)["status_card"] == []                                  # rewritten unchanged = confirmed; the other removed
    row = next(r for r in _rows(conn, a) if r["key"] == "plan")
    assert row["value"] == "示例计划"


def test_review_stays_short_and_counts_every_item(conn, users):
    a = users["A"]
    for i in range(10):
        _put(conn, a, f"item_{i}", f"值{i}", f"示例{i}", group="其他", age=200 + i)
    r = _review(conn, a)
    assert len(r["status_card"]) == mp.STATUS_MAX and r["count"] >= 10           # the group is capped, the count is not
    assert len(json.dumps(r["status_card"], ensure_ascii=False)) < 1500
    packed = compact.compact(r["status_card"])
    assert isinstance(packed, dict) and set(packed) == {"cols", "rows"}          # a table: each key written once
    assert len(compact.dumps(r["status_card"])) < len(json.dumps(r["status_card"], ensure_ascii=False))
    memory_review.run_all(conn)
    assert memory_review.get_state(conn, a)["candidates"] == r["count"]           # the number the briefing reminder reads


def test_two_users_do_not_mix(conn, users):
    a, c = users["A"], users["C"]
    _weigh(conn, a, 70.0, 71.6)
    _put(conn, a, "phase", "减脂", "当前阶段", "训练", age=45)
    _put(conn, c, "phase", "减脂", "当前阶段", "训练", age=45)                    # C has the same item but no weigh-ins
    assert set(_cards(conn, a)) == {"phase"} and _cards(conn, c) == {}
    res = {r["user_id"]: r for r in memory_review.run_all(conn)}
    assert res[a]["candidates"] >= 1 and res[c]["candidates"] == 0
