"""Issue #172: remove planned exercises, the day card shows only what was trained, the plan sits in a collapsed card.
- Removing for one date changes that date only (the same weekday next week, other days and the template are untouched),
  the day lists it under `removed` (the AI's get_week_plan / scenes see it), and undo brings it back.
- Removing from the template needs the confirmation, changes every week and never empties a session.
- Training page script (training-day.js, run in node with a stub DOM): the plan card is collapsed by default, opens on a tap
  and then lists every exercise with a remove button; the remove sheet asks twice for the template and offers undo for a day.
- app.js: the day card (daySheet) has no plan in it.
- HTTP: no flag, self only, a viewer with the training share cannot remove.
"""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.training import plan_add, plan_remove, plans, session_log

STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_PLAN_REMOVE", "body_test_plan_remove")
CSRF = {"X-Body-Web": "1"}


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


def _plan_days(dow: int, other: int) -> list[dict]:
    ex = lambda slug, sets=3, lo=8, hi=12: {"exercise": slug, "sets": sets, "rep_low": lo, "rep_high": hi, "rest_sec": 90,
                                            "duration_sec": None, "weight_kg": None, "note": None}
    return [{"day_of_week": dow, "name": "上肢 B（推）", "exercises": [ex("machine_chest_press", 4), ex("lat_pulldown"), ex("seated_cable_row_close")]},
            {"day_of_week": other, "name": "下肢 A", "exercises": [ex("seated_leg_extension")]}]


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role) VALUES (%s, %s) RETURNING id",
                               (n, "owner" if n == "A" else "member")).fetchone()["id"] for n in ("A", "B")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
        today = plans._user_today(conn, ids["A"])
        dow = today.isoweekday()
        plans.save_plan(conn, ids["A"], split_type="custom", days=_plan_days(dow, dow % 7 + 1), name="移除测试", sync_sparky=False)
    return ids, tok, today


def _slugs(conn, uid, day):
    wk = plans.get_week_plan(conn, uid, uid, day)
    entry = next(d for d in wk["days"] if d["date"] == day)
    return [e["slug"] for p in entry["planned"] for e in p["exercises"]], entry


def _template(conn, uid, name="上肢 B（推）"):
    return [e["slug"] for d in plans._active_plan_row(conn, uid)["days"] if d["name"] == name for e in d["exercises"]]


# ---------------------------------------------------------------- one day only, undo
def test_remove_for_one_day_only_and_undo(conn, world):
    ids, _, today = world
    a = ids["A"]
    full = ["machine_chest_press", "lat_pulldown", "seated_cable_row_close"]
    assert _slugs(conn, a, today)[0] == full
    res = plan_remove.remove(conn, a, "day", "machine_chest_press", day=today)
    assert res["removed"] and res["exercise"]["slug"] == "machine_chest_press"
    slugs, entry = _slugs(conn, a, today)
    assert slugs == ["lat_pulldown", "seated_cable_row_close"]
    assert [r["slug"] for r in entry["removed"]] == ["machine_chest_press"]            # the AI sees that the user took it off
    # only that date: the same weekday next week, the other session and the template keep it
    nxt, nxt_entry = _slugs(conn, a, today + timedelta(days=7))
    assert nxt == full and "removed" not in nxt_entry
    assert _template(conn, a) == full
    assert _slugs(conn, a, today + timedelta(days=1))[0] == ["seated_leg_extension"]   # the other session (tomorrow; on a Sunday that is next week)
    # training day v2 reads the same day, and the volume follows
    v = session_log.view(conn, a, a, today)
    assert [x["slug"] for s in v["sessions"] for x in s["exercises"]] == ["lat_pulldown", "seated_cable_row_close"]
    assert [r["slug"] for r in v["removed"]] == ["machine_chest_press"] and v["can_edit_plan"] and v["minutes"]
    assert v["sessions"][0]["day_of_week"] == today.isoweekday() and v["sessions"][0]["minutes"] == v["minutes"]
    # the AI's plan scene
    from body_engine.assistant import scenes
    sec = scenes._plan_section(conn, a, a, today)
    assert any(d.get("removed_by_user") for d in sec["days"])
    # a second remove is harmless; undo puts it back where the plan has it
    plan_remove.remove(conn, a, "day", "machine_chest_press", day=today)
    assert plan_remove.restore_for_day(conn, a, "machine_chest_press", today)["restored"]
    assert _slugs(conn, a, today)[0] == full
    assert not plan_remove.restore_for_day(conn, a, "machine_chest_press", today)["restored"]


def test_remove_rules(conn, world):
    ids, _, today = world
    a = ids["A"]
    with pytest.raises(ValueError):                                    # past days
        plan_remove.remove(conn, a, "day", "lat_pulldown", day=today - timedelta(days=1))
    with pytest.raises(ValueError):                                    # beyond the window of day changes
        plan_remove.remove(conn, a, "day", "lat_pulldown", day=today + timedelta(days=400))
    with pytest.raises(ValueError):                                    # not planned that day
        plan_remove.remove(conn, a, "day", "plank", day=today)
    with pytest.raises(ValueError):
        plan_remove.remove(conn, a, "day", "lat_pulldown")             # needs a date
    assert not conn.execute("SELECT 1 FROM training_day_removals WHERE user_id = %s", (a,)).fetchall()
    # an exercise added for one day can be removed again the same way
    day = today + timedelta(days=2)
    plan_add.add(conn, a, "day", "dumbbell_lateral_raise", day=day)
    assert "dumbbell_lateral_raise" in _slugs(conn, a, day)[0]
    plan_remove.remove(conn, a, "day", "dumbbell_lateral_raise", day=day)
    assert "dumbbell_lateral_raise" not in _slugs(conn, a, day)[0]
    plan_remove.restore_for_day(conn, a, "dumbbell_lateral_raise", day)


def test_removing_every_exercise_of_a_day_keeps_the_session_and_the_way_back(conn, world):
    ids, _, today = world
    a = ids["A"]
    day = today + timedelta(days=1)                                    # the 下肢 A day has one exercise
    plan_remove.remove(conn, a, "day", "seated_leg_extension", day=day)
    slugs, entry = _slugs(conn, a, day)
    assert slugs == [] and entry["planned"] and not entry["rest_day"] and entry["removed"]
    plan_remove.restore_for_day(conn, a, "seated_leg_extension", day)
    assert _slugs(conn, a, day)[0] == ["seated_leg_extension"]


# ---------------------------------------------------------------- the template ("never again")
def test_remove_from_template_needs_confirmation_and_changes_every_week(conn, world):
    ids, _, today = world
    a = ids["A"]
    dow = today.isoweekday()
    with pytest.raises(ValueError, match="确认"):
        plan_remove.remove(conn, a, "template", "lat_pulldown", day_of_week=dow, session_name="上肢 B（推）", sync_sparky=False)
    assert "lat_pulldown" in _template(conn, a)
    res = plan_remove.remove(conn, a, "template", "lat_pulldown", day_of_week=dow, session_name="上肢 B（推）", confirm=True, sync_sparky=False)
    assert res["removed"] and res["scope"] == "template"
    assert _template(conn, a) == ["machine_chest_press", "seated_cable_row_close"]
    assert "lat_pulldown" not in _slugs(conn, a, today)[0] and "lat_pulldown" not in _slugs(conn, a, today + timedelta(days=14))[0]
    assert [e["slug"] for d in plans._active_plan_row(conn, a)["days"] if d["name"] == "下肢 A" for e in d["exercises"]] == ["seated_leg_extension"]
    with pytest.raises(ValueError):                                    # not in the session any more
        plan_remove.remove(conn, a, "template", "lat_pulldown", day_of_week=dow, session_name="上肢 B（推）", confirm=True, sync_sparky=False)
    plan_remove.remove(conn, a, "template", "seated_cable_row_close", day_of_week=dow, session_name="上肢 B（推）", confirm=True, sync_sparky=False)
    with pytest.raises(ValueError, match="至少"):                      # never empties a session
        plan_remove.remove(conn, a, "template", "machine_chest_press", day_of_week=dow, session_name="上肢 B（推）", confirm=True, sync_sparky=False)
    assert _template(conn, a) == ["machine_chest_press"]


# ---------------------------------------------------------------- HTTP
@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str, lang: str = "zh") -> dict:
    return {"Authorization": f"Bearer {token}", "X-Body-Lang": lang, **CSRF}


def test_http_remove_restore_self_only(client, world):
    ids, tok, today = world
    base = "/v1/users/me/training/plan"
    day = (today + timedelta(days=1)).isoformat()                     # the 下肢 A day
    body = {"exercise": "seated_leg_extension", "scope": "day", "day": day}
    r = client.post(f"{base}/remove-exercise", headers=H(tok["A"], "en"), json={**body, "exercise": "plank"})
    assert r.status_code == 422 and "not in the plan" in r.json()["detail"]
    r = client.post(f"{base}/remove-exercise", headers=H(tok["A"]), json=body)
    assert r.status_code == 200 and r.json()["removed"], r.text
    r = client.post(f"{base}/restore-exercise", headers=H(tok["A"]), json={"exercise": body["exercise"], "day": day})
    assert r.status_code == 200 and r.json()["restored"]
    # template without the confirmation: 422; bad scope: 422
    r = client.post(f"{base}/remove-exercise", headers=H(tok["A"]), json={"exercise": "machine_chest_press", "scope": "template",
                    "day_of_week": today.isoweekday()})
    assert r.status_code == 422
    assert client.post(f"{base}/remove-exercise", headers=H(tok["A"]), json={**body, "scope": "week"}).status_code == 422
    # a viewer with the training share cannot write another user's plan
    assert client.post(f"/v1/users/{ids['A']}/training/plan/remove-exercise", headers=H(tok["B"]), json=body).status_code == 403
    assert client.post(f"/v1/users/{ids['A']}/training/plan/restore-exercise", headers=H(tok["B"]),
                       json={"exercise": body["exercise"], "day": day}).status_code == 403
    # the viewer sees the plan without the removed exercise
    client.post(f"{base}/remove-exercise", headers=H(tok["A"]), json=body)
    r = client.get(f"/v1/users/{ids['A']}/training/plan?date={day}", headers=H(tok["B"]))
    entry = next(d for d in r.json()["days"] if d["date"] == day)
    assert [x["slug"] for x in entry["removed"]] == ["seated_leg_extension"]
    client.post(f"{base}/restore-exercise", headers=H(tok["A"]), json={"exercise": body["exercise"], "day": day})


# ---------------------------------------------------------------- page scripts (node, stub DOM)
def run_node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


HARNESS = r"""
globalThis.window = globalThis;
const fill = (k, p) => String(k).replace(/\{(\w+)\}/g, (m, n) => (p && n in p ? p[n] : m));
globalThis.I18N = { t: fill, num: (v) => String(v), lang: "zh" };
globalThis.document = { querySelector: () => null, head: { appendChild() {} }, createElement: () => ({}) };
require(%(path)s);
const card = (slug, name) => ({ slug, name_zh: name, status: "active", modality: "weight_reps", target: { sets: 3, reps: 10, rest_sec: 90 },
  load: null, last: [], primary_muscles: [], secondary_muscles: [] });
const V = { date: "2026-10-22", is_today: true, can_edit_plan: true, can_log: true, training_log: "connected", minutes: 60, removed: [],
  sessions: [{ name: "上肢 B", day_of_week: 4, minutes: 60, exercises: [card("a", "卧推"), card("b", "划船")] }], log: null };
const calls = [];
const toasts = [];
const ctx = { esc: (v) => String(v), owner: "me", self: true, date: V.date,
  api: async (url, o) => { calls.push({ url, method: o?.method || "GET", body: o?.body }); return JSON.parse(JSON.stringify(V)); },
  toast: (...a) => toasts.push(a), openSheet() {}, closeSheet() {}, openDetail() {}, onPlanChanged() {}, onSubmitted() {} };
const el = { innerHTML: "", querySelector: () => null };
const click = (attr) => el.onclick({ target: { closest: (sel) => (sel === `[${attr}]` ? { dataset: {} } : null) } });
"""


def harness(body: str):
    return run_node(HARNESS % {"path": json.dumps(str(STATIC / "training-day.js"))} + body)


def test_plan_card_is_collapsed_by_default_and_opens():
    out = harness("""(async () => {
      await window.BodyTrainingDay.mount(el, ctx);
      const collapsed = el.innerHTML;
      click("data-plan-toggle");
      const opened = el.innerHTML;
      click("data-plan-toggle");
      console.log(JSON.stringify({ collapsed, opened, again: el.innerHTML }));
    })()""")
    c, o = out["collapsed"], out["opened"]
    assert "今日训练计划" in c and "上肢 B · 2 个动作 · 约 60 分钟" in c and 'aria-expanded="false"' in c
    assert "data-slug" not in c and "卧推" not in c and "data-rm" not in c                     # only the title line
    assert 'aria-expanded="true"' in o and o.count("data-slug=") == 2 and o.count("data-rm") == 2   # every exercise, each removable
    assert "卧推" in o and "划船" in o
    assert out["again"] == c                                                                    # and it closes again


def test_plan_card_hides_remove_when_the_plan_is_not_editable():
    out = harness("""(async () => {
      V.can_edit_plan = false;
      await window.BodyTrainingDay.mount(el, ctx);
      click("data-plan-toggle");
      console.log(JSON.stringify({ html: el.innerHTML }));
    })()""")
    assert "data-slug=" in out["html"] and "data-rm" not in out["html"]


def test_removed_exercises_can_be_restored():
    out = harness("""(async () => {
      V.removed = [{ slug: "c", name_zh: "深蹲", name_en: "Squat" }];
      await window.BodyTrainingDay.mount(el, ctx);
      click("data-plan-toggle");
      const html = el.innerHTML;
      el.onclick({ target: { closest: (sel) => (sel === "[data-restore]" ? { dataset: { restore: "c" } } : null) } });
      await new Promise((r) => setTimeout(r, 10));
      console.log(JSON.stringify({ html, post: calls.find((c) => c.method === "POST") }));
    })()""")
    assert "已移除" in out["html"] and 'data-restore="c"' in out["html"] and "深蹲" in out["html"]
    assert out["post"]["url"].endswith("/training/plan/restore-exercise") and out["post"]["body"] == {"exercise": "c", "day": "2026-10-22"}


def test_remove_sheet_day_has_undo_and_template_asks_twice():
    out = harness("""(async () => {
      let root = null;
      const mkRoot = () => { const r = { html: "", btn: {}, get innerHTML() { return r.html; }, set innerHTML(v) { r.html = v; },
        querySelector: (sel) => { const key = sel.slice(1, -1); return r.html.includes(key) ? (r.btn[key] ??= {}) : null; } }; return r; };
      ctx.openSheet = (title, html, bind) => { root = mkRoot(); root.html = html; bind(root); };
      const info = { slug: "a", name: "卧推", date: V.date, isToday: true, dayOfWeek: 4, sessionName: "上肢 B", addedForDay: false };
      const done = [];
      // this day only
      window.BodyTrainingDay.askRemove(ctx, info, (k) => done.push(k));
      const first = root.html;
      await root.btn["data-rm-day"].onclick();
      const undo = toasts[toasts.length - 1];
      await undo[2]();
      // never again: the first tap only shows the confirmation, nothing is written yet
      window.BodyTrainingDay.askRemove(ctx, info, (k) => done.push(k));
      const before = calls.length;
      root.btn["data-rm-tpl"].onclick();
      const second = root.html, wrote = calls.length - before;
      await root.btn["data-rm-tpl-ok"].onclick();
      console.log(JSON.stringify({ first, second, wrote, done, undoLabel: undo[1], posts: calls.filter((c) => c.method === "POST") }));
    })()""")
    assert "只删今天的" in out["first"] and "以后都不要这个动作" in out["first"]
    assert out["second"] != out["first"] and "以后每周都不再出现" in out["second"] and out["wrote"] == 0   # nothing written before the 2nd tap
    assert out["undoLabel"] == "撤销" and out["done"] == ["day", "restore", "template"]
    day, restore, tpl = out["posts"]
    assert day["url"].endswith("/plan/remove-exercise") and day["body"] == {"exercise": "a", "scope": "day", "day": "2026-10-22"}
    assert restore["url"].endswith("/plan/restore-exercise")
    assert tpl["body"] == {"exercise": "a", "scope": "template", "confirm": True, "day_of_week": 4, "session_name": "上肢 B"}


def test_template_option_is_not_offered_for_a_day_added_exercise():
    out = harness("""(async () => {
      let html = "";
      ctx.openSheet = (t, h) => { html = h; };
      window.BodyTrainingDay.askRemove(ctx, { slug: "a", name: "x", date: V.date, isToday: false, dayOfWeek: null, sessionName: "加入的动作", addedForDay: true }, () => {});
      console.log(JSON.stringify({ html }));
    })()""")
    assert "data-rm-day" in out["html"] and "data-rm-tpl" not in out["html"] and "只删这一天的" in out["html"]


# ---------------------------------------------------------------- the day card (app.js)
def test_day_card_shows_only_what_was_trained():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    day = app[app.index("async function daySheet"):app.index("// ------------------------------------------------------------------ Ask the butler")]
    assert "planned" not in day and "原计划" not in day and 'tr("计划")' not in day and "exerciseRow" not in day   # no plan in the day card
    assert "sessionHtml" in day and "daySheet(iso, planDays" not in app                                          # the actual sessions, no plan argument
    assert "tr(\"已练\")" not in day
    # a future day still shows its plan (with remove buttons); the plan card of today is in training-day.js
    assert "function planSheet" in app and "askRemove" in app
    # the session card shows exercises with their sets (reps x weight) and the volume
    sess = app[app.index("function sessionHtml"):app.index("async function daySheet")]
    assert "容量" in sess and "weight_kg" in sess
