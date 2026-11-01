"""FB-5: the training page's plan, the swap_exercise failure, and training day v2 (rollout flag training_day_v2).
- swap_exercise finds a sibling of the planned exercise by movement pattern within the chosen day, and an unknown exercise
  gets an error listing that day's slugs (the AI called cable_face_pull for the plan's band_face_pull_er warm-up).
- get_week_plan suggestions only name exercises of the plan, and not a step the plan already prescribes.
- /training/session: planned exercise cards with a pre-fill target, a draft of sets (each its own reps / weight / done),
  submit to SparkyFitness through log_workout (resubmit replaces the page's workout), and get_training_day's page_draft.
- FB-21: a plan item "A + B" is a superset of A and B on the card; a name the library does not know is still a card
  (flagged, with swap candidates), also for a SparkyFitness weekly plan, where it used to come without a slug and vanish.
"""
import json
import os
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.training import combo, plans, session_log
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_TRAINING_SESSION", "body_test_training_session")
CSRF = {"X-Body-Web": "1"}
FLAG = "training_day_v2"


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


def _day_plan(dow: int, other: int) -> list[dict]:
    ex = lambda slug, sets=3, lo=10, hi=12, w=None: {"exercise": slug, "sets": sets, "rep_low": lo, "rep_high": hi,
                                                      "rest_sec": 90, "duration_sec": None, "weight_kg": w, "note": None}
    return [
        {"day_of_week": dow, "name": "上肢 B（推）", "exercises": [
            ex("band_face_pull_er", 2, 15, 15), ex("machine_chest_press", 4, 8, 12, 25), ex("lat_pulldown", 3, 8, 12, 41.5)]},
        {"day_of_week": other, "name": "上肢 A（拉）", "exercises": [ex("cable_face_pull"), ex("seated_cable_row_close")]},
    ]


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role) VALUES (%s, %s) RETURNING id",
                               (n, "owner" if n == "A" else "member")).fetchone()["id"] for n in ("A", "B")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason)
                        VALUES (%s, 'upright_row', '直立划船', 'banned', '肩峰撞击')""", (ids["A"],))
        today = plans._user_today(conn, ids["A"])
        dow = today.isoweekday()
        plans.save_plan(conn, ids["A"], split_type="custom", days=_day_plan(dow, dow % 7 + 1), name="FB-5 测试",
                        sync_sparky=False)
        for subj, name, nxt in (("a", "高位下拉（正手，略宽于肩）", 41.5), ("b", "保加利亚分腿蹲", 7.5), ("c", "坐姿器械推胸", 27.5)):
            conn.execute(
                """INSERT INTO reminders (user_id, date, rule_key, subject, level, title, message, evidence, rules_version)
                   VALUES (%s, %s, 'progression', %s, 'info', %s, '连续达标', %s::jsonb, 'test')""",
                (ids["A"], today, subj, f"下次加重：{name} → {nxt} kg",
                 f'{{"exercise": "{name}", "next_weight_kg": {nxt}}}'))
    return ids, tok, today


class FakeSparky:
    """The SparkyFitness calls log_workout makes: exercise library, that day's entries, create / delete a workout."""

    def __init__(self):
        self.lib, self.entries, self.deleted = [], [], []

    def get(self, path, **params):
        if path == "/exercises":
            return {"exercises": self.lib, "totalCount": len(self.lib)}
        if path == "/v2/exercise-entries/by-date":
            return [e for e in self.entries if e["entry_date"] == params["selectedDate"]]
        raise AssertionError(f"unexpected GET {path}")

    def request(self, method, path, body=None, params=None, multipart_field=None):
        if (method, path) == ("POST", "/exercises"):
            ex = {"id": f"sp-{len(self.lib) + 1}", **body}
            self.lib.append(ex)
            return ex
        if (method, path) == ("POST", "/exercise-preset-entries"):
            e = {"id": f"entry-{len(self.entries) + len(self.deleted) + 1}", "type": "preset", **body}
            self.entries.append(e)
            return {**e, "exercises": [{"name": x["exercise_id"], "sets": x["sets"]} for x in body["exercises"]]}
        if method == "DELETE" and path.startswith("/exercise-preset-entries/"):
            eid = path.rsplit("/", 1)[1]
            self.deleted.append(eid)
            self.entries = [e for e in self.entries if e["id"] != eid]
            return {}
        raise AssertionError(f"unexpected {method} {path}")


# ---------------------------------------------------------------- root causes (not behind the flag)
def test_suggestions_only_for_plan_exercises(conn, world):
    ids, _, today = world
    wk = plans.get_week_plan(conn, ids["A"], ids["A"], today)
    sugg = {s["exercise"]: s for s in wk["suggestions"]}
    assert "保加利亚分腿蹲" not in sugg                      # not in this plan
    assert "高位下拉（正手，略宽于肩）" not in sugg            # the plan already prescribes 41.5 kg
    assert sugg["坐姿器械推胸"]["slug"] == "machine_chest_press"
    chest = next(x for p in next(d for d in wk["days"] if d["date"] == today)["planned"] for x in p["exercises"]
                 if x["slug"] == "machine_chest_press")
    assert chest["suggestions"] and chest["suggestions"][0]["next_weight_kg"] == 27.5


def test_swap_unknown_exercise_lists_the_day(conn, world):
    ids, _, today = world
    with pytest.raises(ValueError) as e:
        plans.swap_exercise(conn, ids["A"], from_exercise="dumbbell_lateral_raise", to_exercise="seated_leg_extension",
                            day_of_week=today.isoweekday(), sync_sparky=False)
    msg = str(e.value)
    assert "machine_chest_press" in msg and "band_face_pull_er" in msg and "slug" in msg


def test_swap_sibling_by_movement_pattern(conn, world):
    """The FB-5 call: cable_face_pull on the day whose plan has band_face_pull_er (same movement pattern)."""
    ids, _, today = world
    res = plans.swap_exercise(conn, ids["A"], from_exercise="cable_face_pull", to_exercise="seated_leg_extension",
                              day_of_week=today.isoweekday(), session_name="上肢 B（推）", sync_sparky=False)
    assert res["swapped"] and res["matched_by"] == "movement_pattern"
    assert [(r["from_slug"], r["to_slug"]) for r in res["replaced"]] == [("band_face_pull_er", "seated_leg_extension")]
    # the other day's cable face pull is untouched
    row = plans._active_plan_row(conn, ids["A"])
    other = next(d for d in row["days"] if d["day_of_week"] != today.isoweekday())
    assert other["exercises"][0]["slug"] == "cable_face_pull"


# ---------------------------------------------------------------- training day v2
def test_view_cards_and_targets(conn, world):
    ids, _, today = world
    v = session_log.view(conn, ids["A"], ids["A"], today)
    assert v["is_today"] and v["plan"]["name"] == "FB-5 测试" and v["can_log"]
    cards = {c["slug"]: c for c in v["sessions"][0]["exercises"]}
    chest = cards["machine_chest_press"]
    assert chest["target"]["sets"] == 4 and chest["target"]["weight_kg"] == 25 and chest["target"]["reps"]
    assert chest["name_en"] and chest["status"] == "active"
    # a viewer with the training share can read, not log
    vb = session_log.view(conn, ids["B"], ids["A"], today)
    assert not vb["can_log"] and vb["sessions"]


def test_draft_submit_resubmit_and_ai(conn, world):
    ids, _, today = world
    a = ids["A"]
    with pytest.raises(ValueError):
        session_log.save_draft(conn, a, today + timedelta(days=1), [])
    with pytest.raises(ValueError):
        session_log.save_draft(conn, a, today, [{"slug": "machine_chest_press", "sets": [{"reps": 500}]}])
    draft = [{"slug": "machine_chest_press", "sets": [
        {"reps": 10, "weight_kg": 25, "done": True}, {"reps": 8, "weight_kg": 27.5, "done": True}, {"reps": 8, "weight_kg": 27.5}]},
        {"slug": "lat_pulldown", "sets": [{"reps": 10, "weight_kg": 41.5}]}]
    out = session_log.save_draft(conn, a, today, draft, "膝盖没事")
    assert out["log"]["status"] == "draft" and len(out["log"]["exercises"][0]["sets"]) == 3

    # the AI sees the ticked, unsubmitted sets
    day = run_tool(conn, a, "butler", "get_training_day", {"user": "me", "date": today.isoformat()})
    assert day["page_draft"]["exercises"] == [{"slug": "machine_chest_press", "sets_done": ["25×10", "27.5×8"]}]

    fake = FakeSparky()
    res = session_log.submit(conn, a, today, client=fake)
    assert res["submitted"] and res["exercises"] == 1 and res["sets"] == 2 and not res["replaced"]
    entry = fake.entries[0]
    assert entry["notes"] == "膝盖没事" and [s["weight"] for s in entry["exercises"][0]["sets"]] == [25, 27.5]
    assert [s["reps"] for s in entry["exercises"][0]["sets"]] == [10, 8]
    assert session_log.draft_for_ai(conn, a, today) is None

    # edit after submit: back to draft, resubmit replaces the page's own workout (no duplicate refusal)
    draft[1]["sets"][0]["done"] = True
    assert session_log.save_draft(conn, a, today, draft)["log"]["changed_since_submit"]
    res2 = session_log.submit(conn, a, today, client=fake)
    assert res2["submitted"] and res2["replaced"] and res2["exercises"] == 2
    assert fake.deleted == [entry["id"]] and len(fake.entries) == 1
    assert session_log.view(conn, a, a, today)["log"]["status"] == "submitted"


def test_submit_refuses_banned_and_reports_duplicates(conn, world):
    ids, _, today = world
    a, d = ids["A"], today - timedelta(days=1)
    session_log.save_draft(conn, a, d, [{"slug": "upright_row", "sets": [{"reps": 12, "weight_kg": 20, "done": True}]}])
    with pytest.raises(PermissionError):
        session_log.submit(conn, a, d, client=FakeSparky())
    session_log.save_draft(conn, a, d, [{"slug": "machine_chest_press", "sets": [{"reps": 10, "weight_kg": 25}]}])
    with pytest.raises(ValueError):   # nothing ticked
        session_log.submit(conn, a, d, client=FakeSparky())
    fake = FakeSparky()
    fake.lib.append({"id": "sp-chest", "name": "坐姿器械推胸"})
    fake.entries.append({"id": "ai-1", "type": "preset", "entry_date": d.isoformat(),
                         "exercises": [{"exercise_id": "sp-chest"}]})
    session_log.save_draft(conn, a, d, [{"slug": "machine_chest_press", "sets": [{"reps": 10, "weight_kg": 25, "done": True}]}])
    dup = session_log.submit(conn, a, d, client=fake)
    assert dup == {"submitted": False, "duplicate": True, "reason": dup["reason"]}
    assert session_log.submit(conn, a, d, allow_duplicate=True, client=fake)["submitted"]


# ---------------------------------------------------------------- HTTP + flag
@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", **CSRF}


def test_http_behind_flag(client, world, dsn):
    ids, tok, today = world
    url = f"/v1/users/me/training/session"
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        flags.set_flag(c, FLAG, "off", actor="test")
    flags.invalidate()
    assert client.get(url, headers=H(tok["A"])).status_code == 404
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        flags.set_flag(c, FLAG, "all", actor="test")
    flags.invalidate()
    r = client.get(f"{url}?date={today}", headers=H(tok["A"]))
    assert r.status_code == 200 and r.json()["sessions"]
    body = {"exercises": [{"slug": "lat_pulldown", "sets": [{"reps": 10, "weight_kg": 41.5, "done": True}, {}]}]}
    r = client.put(f"{url}/{today}", headers=H(tok["A"]), json=body)
    assert r.status_code == 200 and len(r.json()["log"]["exercises"][0]["sets"]) == 2
    # not linked to a training log: a clear 409, in English when asked
    r = client.post(f"{url}/{today}/submit", headers={**H(tok["A"]), "X-Body-Lang": "en"}, json={})
    assert r.status_code == 409 and "training log" in r.json()["detail"]
    # B reads A's day (training share) but cannot write it
    assert client.get(f"/v1/users/{ids['A']}/training/session?date={today}", headers=H(tok["B"])).status_code == 200
    assert client.put(f"/v1/users/{ids['A']}/training/session/{today}", headers=H(tok["B"]), json=body).status_code == 403
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        flags.set_flag(c, FLAG, "off", actor="test")
    flags.invalidate()


# ---------------------------------------------------------------- FB-21: combination items and names the library does not know
def _user(conn, name: str) -> int:
    return conn.execute("INSERT INTO users (display_name, role) VALUES (%s, 'owner') RETURNING id", (name,)).fetchone()["id"]


def _ex(name: str, sets=3, lo=10, hi=12, **kw) -> dict:
    return {"exercise": name, "sets": sets, "rep_low": lo, "rep_high": hi, "rest_sec": 60, **kw}


def _cards(conn, uid: int, day) -> list[dict]:
    return [c for s in session_log.view(conn, uid, uid, day)["sessions"] for c in s["exercises"]]


def test_combo_and_unknown_items_are_all_on_the_card(conn):
    """The reproduction: 4 plan items ("A + B", a name nothing matches, a face pull variant, a plain one) -> 5 cards, none lost."""
    uid = _user(conn, "combo-a")
    today = plans._user_today(conn, uid)
    plans.save_plan(conn, uid, split_type="custom", sync_sparky=False, name="FB-21", days=[
        {"day_of_week": today.isoweekday(), "name": "肩 + 手臂", "exercises": [
            _ex("哑铃侧平举 + 哑铃弯举", 3, 10, 12, weight_kg=12.5), _ex("完全不存在的动作xyz", 2, 8, 8),
            _ex("面拉 + 外旋", 2, 15, 15), _ex("杠铃平板卧推", 4, 6, 8)]}])
    cards = _cards(conn, uid, today)
    assert [c["slug"] for c in cards] == ["dumbbell_lateral_raise", "dumbbell_bicep_curl", "完全不存在的动作xyz", "面拉 + 外旋", "barbell_bench_press"]
    lat, curl, nope, face, bench = cards
    # the pair: one superset group, each exercise with its own sets and weight; the pair's weight is not given to both
    assert lat["group"] == curl["group"] and lat["group"]
    assert lat["target"]["sets"] == curl["target"]["sets"] == 3 and lat["target"]["weight_kg"] != 12.5
    assert not lat.get("unresolved") and bench["group"] is None
    # names the library does not know: the card is there, flagged, with exercises to swap to
    assert nope["unresolved"] and nope["candidates"] == [] and nope["target"]["sets"] == 2
    assert face["unresolved"] and {c["slug"] for c in face["candidates"]} >= {"cable_face_pull", "band_face_pull_er"}
    assert all(set(c) == {"slug", "name_zh", "name_en"} for c in face["candidates"])


def test_unknown_item_swaps_for_a_candidate(conn):
    uid = _user(conn, "combo-b")
    today = plans._user_today(conn, uid)
    plans.save_plan(conn, uid, split_type="custom", sync_sparky=False, name="FB-21 swap", days=[
        {"day_of_week": today.isoweekday(), "name": "拉", "exercises": [_ex("面拉 + 外旋"), _ex("高位下拉")]}])
    face = _cards(conn, uid, today)[0]
    assert face["unresolved"]
    res = plans.swap_exercise(conn, uid, from_exercise=face["slug"], to_exercise=face["candidates"][0]["slug"], sync_sparky=False)
    assert res["swapped"] and res["replaced"][0]["to_slug"] == face["candidates"][0]["slug"]
    after = _cards(conn, uid, today)
    assert not after[0].get("unresolved") and after[0]["slug"] == face["candidates"][0]["slug"] and len(after) == 2


def test_each_part_of_a_combo_keeps_its_own_restriction(conn):
    """A part the user marked careful / forbidden is flagged on the card like a single exercise would be."""
    uid = _user(conn, "combo-c")
    today = plans._user_today(conn, uid)
    plans.save_plan(conn, uid, split_type="custom", sync_sparky=False, name="FB-21 rules", days=[
        {"day_of_week": today.isoweekday(), "name": "肩", "exercises": [_ex("哑铃前平举 + 哑铃侧平举"), _ex("哑铃弯举 + 锤式弯举")]}])
    for ref, status, why in (("dumbbell_front_raise", "banned", "肩峰撞击（示例）"), ("dumbbell_lateral_raise", "caution", "控制幅度（示例）")):
        conn.execute("INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason) VALUES (%s, %s, %s, %s, %s)",
                     (uid, ref, ref, status, why))
    front, lat, curl, hammer = _cards(conn, uid, today)
    assert front["group"] == lat["group"] != curl["group"] == hammer["group"]
    assert front["status"] == "banned" and front["reason"] == "肩峰撞击（示例）"
    assert lat["status"] == "caution" and curl["status"] == hammer["status"] == "active"


def test_old_plan_with_a_combo_item_is_fixed_when_read_and_when_written(conn):
    """A plan saved before combos existed holds "A + B" as one unknown exercise: read as a superset, stored split by the next write."""
    uid = _user(conn, "combo-d")
    today = plans._user_today(conn, uid)
    old = [{"day_of_week": today.isoweekday(), "name": "旧计划", "exercises": [
        {"slug": "哑铃侧平举 + 哑铃弯举", "name_zh": "哑铃侧平举 + 哑铃弯举", "in_catalog": False, "sets": 3, "rep_low": 10, "rep_high": 12,
         "rest_sec": 60, "weight_kg": None, "modality": "weight_reps", "category": "strength"}]}]
    conn.execute("INSERT INTO training_plans (user_id, name, split_type, days_per_week, start_date, active, days) "
                 "VALUES (%s, 'old', 'custom', 1, %s, TRUE, %s::jsonb)", (uid, today - timedelta(days=30), json.dumps(old, ensure_ascii=False)))
    wk = plans.get_week_plan(conn, uid, uid, today, detail=False)
    exs = next(d for d in wk["days"] if d["date"] == today)["planned"][0]["exercises"]
    assert [e["slug"] for e in exs] == ["dumbbell_lateral_raise", "dumbbell_bicep_curl"] and exs[0]["group"] == exs[1]["group"]
    plans.swap_exercise(conn, uid, from_exercise="dumbbell_bicep_curl", to_exercise="dumbbell_hammer_curl", sync_sparky=False)
    stored = conn.execute("SELECT days FROM training_plans WHERE user_id = %s", (uid,)).fetchone()["days"][0]["exercises"]
    assert [e["slug"] for e in stored] == ["dumbbell_lateral_raise", "dumbbell_hammer_curl"] and stored[0]["group"] == stored[1]["group"]


def test_a_superset_rests_once_per_round(conn):
    uid = _user(conn, "combo-e")
    today = plans._user_today(conn, uid)
    plans.save_plan(conn, uid, split_type="custom", sync_sparky=False, name="FB-21 time", days=[
        {"day_of_week": today.isoweekday(), "name": "A", "exercises": [_ex("哑铃侧平举 + 哑铃弯举", 4, 10, 10, rest_sec=120)]}])
    cards = session_log.view(conn, uid, uid, today)["sessions"][0]["exercises"]
    paired = session_log._minutes(cards)
    alone = session_log._minutes([{**c, "group": None} for c in cards])
    assert paired < alone   # two exercises, one rest per round


def test_sparky_weekly_plan_unknown_names_are_not_dropped(conn, monkeypatch):
    """The root cause of FB-21: in a SparkyFitness weekly plan a name the library does not know had slug None, which the
    app's card parser dropped. Now it is a flagged card (slug = its name) and "A + B" a superset."""
    uid = _user(conn, "combo-f")
    today = plans._user_today(conn, uid)
    x = lambda name, **kw: {"exercise_id": None, "name": name, "sets_text": "3 × 10-12", "rep_low": 10, "rep_high": 12, "status": "active", **kw}
    planned = [{"name": "Sparky 计划", "exercises": [x("哑铃侧平举 + 哑铃弯举"), x("完全不存在的动作xyz"), x("杠铃平板卧推")]}]
    monkeypatch.setattr(views, "training_today", lambda *a, **k: {"planned": planned})
    cards = _cards(conn, uid, today)
    assert [c["slug"] for c in cards] == ["dumbbell_lateral_raise", "dumbbell_bicep_curl", "完全不存在的动作xyz", "barbell_bench_press"]
    assert all(c["slug"] for c in cards)
    assert cards[0]["group"] == cards[1]["group"] and cards[0]["target"]["sets"] == 3
    assert cards[2]["unresolved"] and not cards[3].get("unresolved")
    assert not session_log.view(conn, uid, uid, today)["can_edit_plan"]   # no Body Engine plan: nothing to swap in


def test_combo_name_rules(conn):
    uid = _user(conn, "combo-g")
    parts = lambda q: combo.parts_of(conn, uid, q)
    assert parts("哑铃侧平举 + 哑铃弯举") == ["哑铃侧平举", "哑铃弯举"]
    assert parts("哑铃侧平举＋哑铃弯举") == ["哑铃侧平举", "哑铃弯举"] and parts("侧平举+哑铃弯举+面拉") == ["侧平举", "哑铃弯举", "面拉"]
    assert parts("侧平举 & 面拉") == ["侧平举", "面拉"]
    assert parts("绳索面拉 + 外旋") is None             # a name the library has as a whole
    assert parts("面拉 + 外旋") is None                 # one part is not an exercise: stays one item, flagged, with candidates
    assert parts("硬拉 / 罗马尼亚硬拉") is None          # "/" is part of real names
    assert parts("5+3") is None and parts("引体向上 8+") is None and parts(None) is None
    assert parts("侧平举 + 哑铃弯举 + 面拉 + 前平举 + 锤式弯举") is None   # more than COMBO_MAX_PARTS
