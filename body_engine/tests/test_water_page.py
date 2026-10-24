"""Water card on the 饮食 page (water.js) when looking back at a past day (issue: the card showed today's water).

water.js runs in node with a small fake page (skipped without node): the card must ask for the day being looked at, show that
day's total read-only, and a drink sent for one day must never end up on another day's card. Checked with the page's
rollout flags all on and all off (offline_mode, optimistic_writes; boot_api / tab_prefetch / cached_first_paint only add
requests and copies keyed by the full path, which the path test below pins). Static checks on app.js for the call site.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
WATER = (STATIC / "water.js").read_text(encoding="utf-8")

HARNESS = r"""
const flags = new Set(%FLAGS%);
const calls = [];
const days = {
  "2026-10-25": { date: "2026-10-25", total_ml: 250, goal: { ml: 2500, source: "default", extras: [] }, percent: 10, entries: [{ id: 9, ml: 250 }], undo: { id: 9, ml: 250 }, quick_ml: [250, 500] },
  "2026-10-23": { date: "2026-10-23", total_ml: 1800, goal: { ml: 2500, source: "default", extras: [] }, percent: 72, entries: [{ id: 5, ml: 1800 }], undo: { id: 5, ml: 1800 }, quick_ml: [250, 500] },
};
let hold = null;   // a write waits here until the test lets it go
const mkBox = () => {
  const box = { innerHTML: "", hidden: true, isConnected: true };
  box.querySelectorAll = (sel) => {
    if (sel !== "[data-water-add]") return [];
    return [...box.innerHTML.matchAll(/data-water-add="(\d+)"/g)].map((m) => (box[`b${m[1]}`] = box[`b${m[1]}`] || { dataset: { waterAdd: m[1] } }));
  };
  box.querySelector = () => null;
  return box;
};
const mkView = (box) => ({ querySelector: (s) => (s === "#water-box" ? box : null) });
globalThis.window = {
  I18N: { t: (k, p) => k.replace(/\{(\w+)\}/g, (_, n) => p?.[n] ?? ""), num: (v) => String(v) },
  BodyFlags: { on: (n) => flags.has(n) }, BodyOffline: { pending: () => [] },
};
globalThis.document = { querySelector: () => ({}), createElement: () => ({}), head: { appendChild() {} } };
const C = {
  esc: (s) => String(s), toast() {},
  api: async (path, o = {}) => {
    calls.push(`${o.method || "GET"} ${path}`);
    if (o.method === "POST") { if (hold) await hold; return { entry: {}, day: { ...days["2026-10-25"], total_ml: 500, entries: [{ id: 9 }, { id: 10 }] } }; }
    const d = /date=([\d-]+)/.exec(path)[1];
    return days[d];
  },
};
require_plugin();
const P = window.BodyPlugins[0];
P.init(C);
const info = (date, past) => ({ owner: "me", self: true, date, past });
"""


def run(flag_list, body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    # water.js is an IIFE that registers itself in window.BodyPlugins
    js = HARNESS.replace("%FLAGS%", json.dumps(flag_list)).replace(
        "require_plugin();", "window.BodyPlugins = []; (function(){" + WATER + "\n}).call(globalThis);")
    js += "\n(async () => {\n" + body + "\n})().then((v) => console.log(JSON.stringify(v)), (e) => { console.error(e); process.exit(1); });"
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


FLAG_SETS = [["water"], ["water", "offline_mode", "optimistic_writes", "boot_api", "tab_prefetch", "cached_first_paint"]]


@pytest.mark.parametrize("flag_list", FLAG_SETS)
def test_past_day_shows_that_day_read_only(flag_list):
    got = run(flag_list, """
      const box = mkBox();
      P.foodDiet(info("2026-10-23", true));
      await P.bindFoodDiet(mkView(box));
      return { calls, html: box.innerHTML, shown: !box.hidden };""")
    assert got["calls"] == ["GET /v1/users/me/water?date=2026-10-23"]
    assert got["shown"] and "1800" in got["html"]
    assert "250<small>" not in got["html"].replace("\n", "")      # not today's total
    assert "data-water-add" not in got["html"] and "data-water-undo" not in got["html"]


@pytest.mark.parametrize("flag_list", FLAG_SETS)
def test_today_has_buttons(flag_list):
    got = run(flag_list, """
      const box = mkBox();
      P.foodDiet(info("2026-10-25", false));
      await P.bindFoodDiet(mkView(box));
      return { calls, html: box.innerHTML };""")
    assert got["calls"] == ["GET /v1/users/me/water?date=2026-10-25"]
    assert "data-water-add" in got["html"] and "data-water-undo" in got["html"]


def test_drink_in_flight_leaves_the_viewed_day_alone():
    """optimistic_writes: tap +250 on today, move to a past day before the answer: the past card keeps its own total."""
    got = run(["water", "optimistic_writes"], """
      const todayBox = mkBox();
      P.foodDiet(info("2026-10-25", false));
      await P.bindFoodDiet(mkView(todayBox));
      let release; hold = new Promise((r) => (release = r));
      todayBox.b250.onclick();                       // drink sent for today, answer pending
      const pastBox = mkBox();
      P.foodDiet(info("2026-10-23", true));          // the page moved to a past day
      await P.bindFoodDiet(mkView(pastBox));
      release(); await new Promise((r) => setTimeout(r, 20));
      const html = pastBox.innerHTML;
      todayBox.isConnected = false;
      return { html, oldBox: todayBox.innerHTML };""")
    assert "1800" in got["html"] and "2050" not in got["html"]
    assert "data-water-add" not in got["html"]


def test_page_asks_for_the_viewed_day():
    """app.js hands water.js the day being looked at (viewDay), never always today's date."""
    m = re.search(r"p\.foodDiet\?\.\(\{[^}]*\}\)", APP)
    assert m and "date: viewDay()" in m.group(0) and "past" in m.group(0)
    assert "p.foodDiet?.({ owner: ownerPath(), self: isSelf(), date: S.todayDate" not in APP


def test_changing_day_drops_the_start_recordings():
    """boot_api / tab_prefetch record the paths a start or a tab asked for; another day's paths must not become templates."""
    m = re.search(r"function setViewDate\(.*?\n  \}\n", APP, re.S)
    assert "rec = null; tabRec = null;" in m.group(0)
