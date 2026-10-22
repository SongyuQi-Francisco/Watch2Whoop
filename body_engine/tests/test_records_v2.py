"""Rollout flag records_v2: the 记录 page loses its 日记 segment (训练 | 饮食); the journal moves to the Today page (a 日记 card that
opens the day's editor sheet) and Trends (the 行为影响 card). Flag off: 日记 | 训练 | 饮食 exactly as before.

The page scripts have no build step and no DOM library in CI, so the behaviour is run in node: the segment bar is app.js's own
`recSeg` (sliced out of the file and run against stubs), and the editor sheet is journal.js's `openDay` driven through a tiny fake
sheet (it finds the tags in the HTML it was given, "taps" them and records the API calls it makes).
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
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.explain import glossary_trends
from body_engine.records import defaults
from body_engine.rollout import registry

STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_RECORDS_V2", "body_test_records_v2")


def node_run(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    prelude = ("globalThis.I18N = { t: (k, p) => String(k).replace(/\\{(\\w+)\\}/g, (m, n) => (p && n in p ? p[n] : m)), lang: 'zh' };\n"
               f"const J = require({json.dumps(str(STATIC / 'journal.js'))});\n")
    out = subprocess.run([node, "-e", prelude + script], capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


# ---------------------------------------------------------------- the flag
def test_flag_declared_and_the_scripts_are_shipped():
    assert "records_v2" in registry.FLAGS
    assert 'src="journal.js"' in (STATIC / "index.html").read_text(encoding="utf-8")
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '"journal.js"' in sw
    assert "records_v2" in APP and "const recordsV2 = () =>" in APP


# ---------------------------------------------------------------- 记录 page: two segments with the flag, three without
def seg_labels(v2: bool, friends: bool, rec_sub: str = "training", tab: str = "record"):
    start = APP.index("const recCur = () => {")   # recCur (which segment is lit) + recSeg
    code = APP[start:APP.index("function bindRecSeg", start)].rstrip().rstrip(";")
    return node_run(f"""
      const S = {{ tab: {json.dumps(tab)}, recSub: {json.dumps(rec_sub)} }};
      const tr = (k) => k, recordsV2 = () => {str(v2).lower()}, friendsOn = () => {str(friends).lower()};
      {code};
      const html = recSeg();
      console.log(JSON.stringify({{ html, labels: [...html.matchAll(/data-rsub="(\\w+)" aria-pressed="(\\w+)">([^<]+)</g)].map((m) => [m[1], m[2], m[3]]) }}));
    """)


def test_flag_on_records_has_only_training_and_food():
    out = seg_labels(v2=True, friends=True)
    assert [l[0] for l in out["labels"]] == ["training", "food"]
    assert [l[2] for l in out["labels"]] == ["训练", "饮食"]
    assert out["labels"][0][1] == "true"                              # 训练 is the selected one
    # a saved 日记 segment (localStorage) can never be the selected one
    assert seg_labels(v2=True, friends=True, rec_sub="journal")["labels"][0][:2] == ["training", "true"]
    # the food page (its own tab under the Friends flag) lights 饮食, not 训练
    food = seg_labels(v2=True, friends=True, tab="food")["labels"]
    assert [l[:2] for l in food] == [["training", "false"], ["food", "true"]]


def test_flag_on_without_friends_has_a_single_page_and_no_switch():
    assert seg_labels(v2=True, friends=False)["html"] == ""


def test_flag_off_records_is_unchanged():
    assert [l[0] for l in seg_labels(v2=False, friends=True)["labels"]] == ["journal", "training", "food"]
    off = seg_labels(v2=False, friends=False, rec_sub="food")
    assert [l[0] for l in off["labels"]] == ["journal", "training"] and off["labels"][0][1] == "true"
    # the old journal page and its handlers are still there for the flag-off path
    assert "const journalPageHtml = async" in APP and "function afterRecord" in APP
    render = APP[APP.index("const RENDER_record"):APP.index("const journalPageHtml")]
    assert 'S.recSub === "training" || recordsV2()' in render


def test_old_links_to_the_journal_segment_are_redirected():
    # setTab: a saved / linked 记录 → 日记 becomes 训练; Jarvis' page id record.journal opens the editor
    set_tab = APP[APP.index("function setTab"):APP.index("for (const b of $$(\".tab\"))")]
    assert 'recordsV2() && S.recSub === "journal") S.recSub = "training"' in set_tab
    go = APP[APP.index("function goPage"):APP.index("window.BodyCharts")]
    assert 'sub === "journal" && recordsV2()' in go and "openDay(journalCtx(), viewDay())" in go
    ai = (STATIC / "ai.js").read_text(encoding="utf-8")
    assert 'BodyFlags?.on("records_v2")' in ai[ai.index("function quickList"):ai.index("function drawQuick")]


def test_explain_text_no_longer_points_at_the_record_page():
    tips = glossary_trends.TREND_METRICS["behavior"]["tips"]
    assert tips and all("「记录」" not in t for t in tips)


# ---------------------------------------------------------------- Trends: the 行为影响 card
ANALYSIS = [
    {"item": {"key": "alcohol", "label_zh": "喝酒"}, "outcomes": {"next_recovery": {"status": "ok", "diff_yes_minus_no": -12.4, "direction": "worse", "reliable": True}}},
    {"item": {"key": "sauna", "label_zh": "桑拿"}, "outcomes": {"next_recovery": {"status": "ok", "diff_yes_minus_no": 4.1, "direction": "better", "reliable": False}}},
    {"item": {"key": "coffee", "label_zh": "咖啡"}, "outcomes": {"next_recovery": {"status": "insufficient"}}},
]


def test_behavior_card_is_a_trend_card_sorted_by_effect():
    out = node_run(f"""
      const ctx = {{ esc: (s) => s, signed: (v, d) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d),
                    xpLabel: (key, label) => `<button class="trend-label xp-label" data-key="${{key}}">${{label}}</button>` }};
      const html = J.behaviorCardHtml(ctx, {json.dumps(ANALYSIS)});
      console.log(JSON.stringify({{ html, model: J.behaviorModel({json.dumps(ANALYSIS)}), none: J.behaviorCardHtml(ctx, null) }}));
    """)
    html = out["html"]
    assert 'class="card trend trend-wide"' in html and 'data-key="behavior"' in html and "行为影响" in html
    assert html.index("喝酒") < html.index("桑拿") and "咖啡" not in html          # biggest effect first; no result = not listed
    assert "−12" in html and "+4" in html and "1 项样本不足" in html
    assert [r["diff"] for r in out["model"]["rows"]] == [-12.4, 4.1] and out["model"]["lack"] == 1
    assert out["none"] == ""                                                       # no journal access: no card


def test_trends_page_draws_the_card_only_with_the_flag():
    metrics = APP[APP.index("async function metricsHtml"):APP.index("function regularityHtml")]
    assert 'recordsV2() ? optional(`journal-an' in metrics and "journal/analysis?days=90" in metrics
    assert 'behavior: [recordsV2() ? window.BodyJournal.behaviorCardHtml(journalCtx(), ja) : ""]' in metrics
    assert '["behavior", tr("行为")]' in APP


# ---------------------------------------------------------------- Today: the 日记 card → editor sheet → save
def test_pure_helpers():
    out = node_run("""
      const items = [{ key: "alcohol", kind: "bool", label_zh: "喝酒" }, { key: "water_l", kind: "number", unit: "L", label_zh: "喝水" },
                     { key: "late", kind: "bool", label_zh: "晚饭太晚" }];
      const entries = [{ date: "2026-10-22", key: "alcohol", value: 1 }, { date: "2026-10-22", key: "late", value: 0 }, { date: "2026-10-21", key: "water_l", value: 2 }];
      const v = J.valuesOf(entries, "2026-10-22");
      console.log(JSON.stringify({
        v, logged: J.loggedItems(items, v).map((i) => i.key),
        put: J.writeRequest("2026-10-22", "water_l", 2.5), del: J.writeRequest("2026-10-22", "a/b", null),
        applied: J.applyWrite(entries, "2026-10-22", "alcohol", null), set: J.applyWrite(entries, "2026-10-22", "water_l", 2.5),
        label: J.shortLabel({ label_zh: "吃了红肉（牛羊猪）" }), same: entries.length,
      }));
    """)
    assert out["v"] == {"alcohol": 1, "late": 0}
    assert out["logged"] == ["alcohol"]                    # "no" (0) is a record but not listed on the card
    assert out["put"] == {"method": "PUT", "path": "/v1/users/me/journal/2026-10-22/water_l", "body": {"value": 2.5}}
    assert out["del"] == {"method": "DELETE", "path": "/v1/users/me/journal/2026-10-22/a%2Fb"}
    assert [e["key"] for e in out["applied"]] == ["late", "water_l"] and out["same"] == 3
    assert {"date": "2026-10-22", "key": "water_l", "value": 2.5} in out["set"]
    assert out["label"] == "红肉"


FAKE_SHEET = r"""
// A fake sheet: the HTML it is given is searched for the tags, a "tap" runs the onclick journal.js attached to them.
function makeEnv({ self = true, entries = [], day = "2026-10-22" } = {}) {
  const log = { api: [], toasts: [], invalidated: 0, cache: {}, sheets: [] };
  const items = [{ key: "alcohol", kind: "bool", label_zh: "喝酒", category: "lifestyle" },
                 { key: "sauna", kind: "bool", label_zh: "桑拿", category: "lifestyle" },
                 { key: "creatine", kind: "bool", label_zh: "肌酸", category: "supplement" },
                 { key: "water_l", kind: "number", unit: "L", label_zh: "喝水", category: "diet" }];
  let current = null;
  const tagsIn = (html, attr) => [...html.matchAll(new RegExp(`<button class="tag ([^"]*)" ${attr}="([^"]+)"`, "g"))]
    .map((m) => { const el = { dataset: { [attr === "data-jkey" ? "jkey" : "num"]: m[2] }, onclick: null, classList: { contains: (c) => m[1].split(" ").includes(c), add() {}, remove() {} } }; return el; });
  const sheet = (html) => ({
    html,
    querySelectorAll: (sel) => (sel === "[data-jkey]" ? (current.jk ||= tagsIn(html, "data-jkey")) : sel === "[data-num]" ? (current.nm ||= tagsIn(html, "data-num")) : []),
    querySelector: (sel) => { const id = sel.slice(1); if (!html.includes(`id="${id}"`)) return null; return (current.ids[id] ||= { onclick: null, focus() {}, value: "" }); },
  });
  globalThis.document = { getElementById: (id) => (current && current.html.includes(`id="${id}"`) ? { innerHTML: "" } : null) };
  globalThis.setTimeout = (fn) => 0;
  const ctx = {
    api: async (path, o = {}) => { log.api.push({ path, method: o.method || "GET", body: o.body ?? null }); return null; },
    load: async (key, path) => {
      if (key === "jitems") return items;
      if (key === "jfreq") return items.slice(0, 3);
      if (key.startsWith("jday")) return entries;
      throw new Error(key);
    },
    setCache: (k, v) => { log.cache[k] = v; }, invalidate: () => { log.invalidated++; },
    ownerPath: () => "me", isSelf: () => self, esc: (s) => String(s), shortDay: (d) => d.slice(5).replace("-", "/"),
    categories: { supplement: "补剂", lifestyle: "生活", diet: "饮食" }, fmtNum: (v) => String(v),
    icon: { chev: "", pulse: "", jarvis: "" },
    sheetAttr: (fn) => { log.card = fn; return 'data-sheet="s1"'; },
    openSheet: (title, html, after) => { current = { title, html, ids: {} }; log.sheets.push({ title, html }); if (after) after(sheet(html)); },
    closeSheet() {}, setBack: (fn) => { log.back = fn; }, toast: (msg, label, fn) => { log.toasts.push({ msg, label, fn }); },
    openEvent: () => { log.event = true; }, openAssistant: () => {},
  };
  return { ctx, log, items, day, cur: () => current, tap: (el) => el.onclick({}) };
}
"""


def test_today_card_opens_the_editor_for_that_day():
    out = node_run(FAKE_SHEET + r"""
      (async () => {
        const entries = [{ date: "2026-10-22", key: "alcohol", value: 1 }, { date: "2026-10-21", key: "sauna", value: 1 }];
        const e = makeEnv({ entries });
        const card = J.cardHtml(e.ctx, e.day, { items: e.items, entries });
        await e.log.card();                                              // a tap on the card
        const sheet = e.log.sheets[e.log.sheets.length - 1];
        console.log(JSON.stringify({ card, empty: J.cardHtml(e.ctx, e.day, { items: e.items, entries: [] }), none: J.cardHtml(e.ctx, e.day, null),
          title: sheet.title, html: sheet.html, calls: e.log.api }));
      })();
    """)
    assert 'id="journal-card"' in out["card"] and "已记录 1 项" in out["card"] and "喝酒" in out["card"] and "桑拿" not in out["card"]   # only that day's
    assert "未记录" in out["empty"] and out["none"] == ""
    assert out["title"] == "日记 · 10/22"
    assert 'data-jkey="alcohol"' in out["html"] and 'aria-pressed="true"' in out["html"] and "记身体事件" in out["html"]
    assert out["calls"] == []                                            # opening only reads (through ctx.load)


def test_editor_saves_a_tick_undo_and_untick():
    out = node_run(FAKE_SHEET + r"""
      (async () => {
        const e = makeEnv({ entries: [{ date: "2026-10-22", key: "alcohol", value: 1 }] });
        await J.openDay(e.ctx, e.day, { showAll: true });
        const jk = e.cur().jk, nm = e.cur().nm;
        const byKey = (k) => jk.find((t) => t.dataset.jkey === k);
        const pressed = { alcohol: byKey("alcohol").classList.contains("on"), sauna: byKey("sauna").classList.contains("on") };
        await byKey("sauna").onclick();                                  // tick 桑拿
        const afterTick = { calls: [...e.log.api], toast: e.log.toasts[0].msg, cache: e.log.cache["jday2026-10-22"], inv: e.log.invalidated };
        await e.log.toasts[0].fn();                                      // 撤销
        const afterUndo = e.log.api[e.log.api.length - 1];
        await byKey("alcohol").onclick();                                // 喝酒 was on -> off
        const afterOff = e.log.api[e.log.api.length - 1];
        nm[0].onclick();                                                 // a number item opens its form in the same sheet
        const form = e.log.sheets[e.log.sheets.length - 1];
        console.log(JSON.stringify({ pressed, afterTick, afterUndo, afterOff, form, back: typeof e.log.back }));
      })();
    """)
    assert out["pressed"] == {"alcohol": True, "sauna": False}
    assert out["afterTick"]["calls"] == [{"path": "/v1/users/me/journal/2026-10-22/sauna", "method": "PUT", "body": {"value": 1}}]
    assert out["afterTick"]["toast"] == "已记录 · 桑拿" and out["afterTick"]["inv"] == 1
    assert {"date": "2026-10-22", "key": "sauna", "value": 1} in out["afterTick"]["cache"]
    assert out["afterUndo"] == {"path": "/v1/users/me/journal/2026-10-22/sauna", "method": "DELETE", "body": None}
    assert out["afterOff"] == {"path": "/v1/users/me/journal/2026-10-22/alcohol", "method": "DELETE", "body": None}
    assert out["form"]["title"] == "喝水" and 'id="num-in"' in out["form"]["html"] and out["back"] == "function"


def test_editor_has_supplements_and_saves_a_number():
    out = node_run(FAKE_SHEET + r"""
      (async () => {
        const e = makeEnv();
        await J.openDay(e.ctx, e.day, { showAll: true });
        const html = e.cur().html;                                       // 全部: grouped by category, supplements included
        e.cur().nm[0].onclick();                                         // 喝水
        const form = e.cur();
        form.ids["num-in"].value = "2.5";
        form.ids["num-save"].onclick();
        await new Promise((r) => setImmediate(r));
        console.log(JSON.stringify({ html, calls: e.log.api, toast: e.log.toasts[e.log.toasts.length - 1].msg, after: e.cur().title }));
      })();
    """)
    assert "补剂" in out["html"] and 'data-jkey="creatine"' in out["html"]
    assert out["calls"] == [{"path": "/v1/users/me/journal/2026-10-22/water_l", "method": "PUT", "body": {"value": 2.5}}]
    assert out["toast"] == "已记录 2.5L" and out["after"] == "日记 · 10/22"   # back to the list after saving


def test_viewing_someone_else_is_read_only():
    out = node_run(FAKE_SHEET + r"""
      (async () => {
        const e = makeEnv({ self: false });
        await J.openDay(e.ctx, e.day);
        console.log(JSON.stringify({ html: e.cur().html, jk: (e.cur().jk || []).length, calls: e.log.api }));
      })();
    """)
    assert "disabled" in out["html"] and "add-event" not in out["html"] and out["calls"] == []


def test_today_page_draws_the_card_only_with_the_flag():
    today = APP[APP.index("const RENDER_today"):APP.index("function activityHtml")]
    assert "const v2 = recordsV2();" in today
    assert 'v2 ? optional("jitems"' in today and "journal?start=${day}&end=${day}" in today
    assert "window.BodyJournal.cardHtml(journalCtx(), day," in today
    # off: the two requests are not made at all (null placeholders in the Promise.all)
    assert today.count(": null,") >= 2 or today.count(": null") >= 2
    assert "openEvent: eventSheet" in APP and "openAssistant" in APP[APP.index("function journalCtx"):APP.index("function activityHtml")]


# ---------------------------------------------------------------- server: the card's item names follow the page language
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


def test_analysis_items_carry_a_label_in_the_request_language(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("INSERT INTO users (display_name, role) VALUES ('A', 'owner') RETURNING id").fetchone()["id"]
        token = auth.create_token(conn, uid, "web", ["read", "write"])
        defaults.init_journal_items(conn, uid)
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    try:
        client = TestClient(api.app, base_url="https://testserver")
        got = {}
        for lang in ("zh", "en"):
            r = client.get("/v1/users/me/journal/analysis?days=90", headers={"Authorization": f"Bearer {token}", "X-Body-Lang": lang})
            assert r.status_code == 200, r.text
            got[lang] = {a["item"]["key"]: a["item"]["label"] for a in r.json()}
        assert got["zh"]["alcohol"] == "喝酒" and got["en"]["alcohol"] == "Alcohol"
        assert all(a for a in got["en"].values())
    finally:
        api.pool.close()
