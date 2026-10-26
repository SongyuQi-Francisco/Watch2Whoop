"""Slim-muscle mode (issue #126, flag slim_mode, migration 900): the saved choice (theme + daily quote) and its endpoints,
the theme / quote script (slim.js, run in node with a fake page; skipped without node) and the quote data file
(slim-quotes.js): every shown quote is a post of a real person copied word for word, with a link to the post.

Separate test database BODY_TEST_DB_NAME_SLIM (default body_test_slim), recreated each run; the database tests are skipped
without Postgres."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.appearance import store
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags, registry

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
SLIM = STATIC / "slim.js"
QUOTES = STATIC / "slim-quotes.js"
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_SLIM", "body_test_slim")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


# ---------------------------------------------------------------- the flag
def test_flag_is_declared_and_defaults_off():
    assert "slim_mode" in registry.FLAGS
    assert flags.STATES[0] == "off"   # a declared key has no row until it is listed or set: off


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name, role="member"):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, %s, 'Australia/Sydney', now(), '2026-10-22.2') RETURNING id""",
                                (name, role)).fetchone()["id"]
        ids = {"owner": add("owner", "owner"), "other": add("other")}
        conn.commit()
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
        tok["owner_ro"] = auth.create_token(conn, ids["owner"], "ro", ["read"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(t):
    return {"Authorization": f"Bearer {t}"}


def set_flag(dsn, state, cohort=None, user_id=None):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if user_id:
            conn.execute("INSERT INTO user_cohorts (user_id, cohort) VALUES (%s, 'owner') ON CONFLICT DO NOTHING", (user_id,))
        flags.set_flag(conn, "slim_mode", state, cohort=cohort, actor="test")
        conn.commit()
    flags.invalidate()


def test_off_by_default_everything_404_and_not_in_web_me(client, users, dsn):
    ids, tok = users
    set_flag(dsn, "off")
    assert client.get("/v1/users/me/appearance", headers=H(tok["owner"])).status_code == 404
    assert client.put("/v1/users/me/appearance", json={"theme": "slim"}, headers=H(tok["owner"])).status_code == 404
    me = client.get("/v1/web/me", headers=H(tok["owner"])).json()
    assert "appearance" not in me and "slim_mode" not in me["flags"]


def test_cohort_owner_only(client, users, dsn):
    """Opened for the cohort 'owner' (the deployer), the other user still sees nothing."""
    ids, tok = users
    set_flag(dsn, "cohort", cohort="owner", user_id=ids["owner"])
    me = client.get("/v1/web/me", headers=H(tok["owner"])).json()
    assert "slim_mode" in me["flags"] and me["appearance"] == {"theme": "default", "quotes": True}
    other = client.get("/v1/web/me", headers=H(tok["other"])).json()
    assert "appearance" not in other and "slim_mode" not in other["flags"]
    assert client.get("/v1/users/me/appearance", headers=H(tok["other"])).status_code == 404


def test_save_follows_the_account_and_is_validated(client, users, dsn):
    ids, tok = users
    set_flag(dsn, "cohort", cohort="owner", user_id=ids["owner"])
    r = client.put("/v1/users/me/appearance", json={"theme": "slim"}, headers=H(tok["owner"]))
    assert r.status_code == 200 and r.json() == {"theme": "slim", "quotes": True}
    r = client.put("/v1/users/me/appearance", json={"quotes": False}, headers=H(tok["owner"]))   # one field leaves the other
    assert r.json() == {"theme": "slim", "quotes": False}
    assert client.get("/v1/users/me/appearance", headers=H(tok["owner"])).json() == {"theme": "slim", "quotes": False}
    assert client.get("/v1/web/me", headers=H(tok["owner"])).json()["appearance"] == {"theme": "slim", "quotes": False}
    assert client.put("/v1/users/me/appearance", json={"theme": "neon"}, headers=H(tok["owner"])).status_code == 422
    assert client.put("/v1/users/me/appearance", json={"colour": "red"}, headers=H(tok["owner"])).status_code == 422
    assert client.put("/v1/users/me/appearance", json={"theme": "default"}, headers=H(tok["owner_ro"])).status_code == 403   # read-only token
    # only one's own: someone else's id is refused
    assert client.get(f"/v1/users/{ids['other']}/appearance", headers=H(tok["owner"])).status_code == 403
    assert client.put(f"/v1/users/{ids['other']}/appearance", json={"theme": "slim"}, headers=H(tok["owner"])).status_code == 403
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert store.get(conn, ids["other"]) == {"theme": "default", "quotes": True}


def test_turning_the_flag_off_again_hides_the_choice_but_keeps_it(client, users, dsn):
    ids, tok = users
    set_flag(dsn, "cohort", cohort="owner", user_id=ids["owner"])
    client.put("/v1/users/me/appearance", json={"theme": "slim", "quotes": True}, headers=H(tok["owner"]))
    set_flag(dsn, "off")
    me = client.get("/v1/web/me", headers=H(tok["owner"])).json()
    assert "appearance" not in me   # the page then shows the normal look
    set_flag(dsn, "cohort", cohort="owner", user_id=ids["owner"])
    assert client.get("/v1/users/me/appearance", headers=H(tok["owner"])).json()["theme"] == "slim"


def test_deleting_the_account_removes_the_row(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("INSERT INTO users (display_name, role) VALUES ('gone', 'member') RETURNING id").fetchone()["id"]
        store.update(conn, uid, "slim", False)
        assert conn.execute("SELECT count(*) AS n FROM user_appearance WHERE user_id = %s", (uid,)).fetchone()["n"] == 1
        conn.execute("DELETE FROM users WHERE id = %s", (uid,))
        assert conn.execute("SELECT count(*) AS n FROM user_appearance WHERE user_id = %s", (uid,)).fetchone()["n"] == 0
        conn.rollback()


# ---------------------------------------------------------------- the quote data file
def load_quotes() -> dict:
    out = subprocess.run([NODE, "-e", "globalThis.window = globalThis; eval(require('fs').readFileSync(process.argv[1], 'utf8')); console.log(JSON.stringify(window.BodySlimQuotes))",
                          str(QUOTES)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@needs_node
def test_every_shown_quote_has_a_primary_source():
    """`items` (what the page shows) are posts of the person under their own account, each with the link to the post;
    nothing unverified or without a link gets in. `unverified` is never shown."""
    data = load_quotes()
    items, rumour = data["items"], data["unverified"]
    assert len(items) >= 10
    ids = [q["id"] for q in items + rumour]
    assert len(ids) == len(set(ids))
    # the account each person posts from; the post id in the link is the one in the item id
    accounts = {"邵艾伦": "AlanShao111", "孙宇晨": "justinsuntron", "峰哥亡命天涯": "zlf86"}
    for q in items:
        assert set(q) == {"id", "person", "text", "lang", "source_url", "source_desc", "posted", "verified"}, q
        assert q["verified"] is True and q["lang"] in ("zh", "en")
        assert q["person"] in accounts, q
        m = re.fullmatch(rf"https://x\.com/{accounts[q['person']]}/status/(\d+)", q["source_url"])
        assert m and q["id"] == f"{accounts[q['person']].lower()}-{m.group(1)}", q
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", q["posted"]) and q["text"].strip() == q["text"] and q["text"]
        assert len(q["text"]) <= 140   # a card line, not an essay
    assert {q["person"] for q in items} == set(accounts)
    for q in rumour:
        assert q["verified"] is False and q["source_desc"] and q["person"] in accounts
    # no picture of anyone, in any field
    assert not re.search(r"\.(png|jpe?g|webp|gif)\b|avatar|photo|portrait", json.dumps(data), re.I)


# ---------------------------------------------------------------- slim.js in node with a fake page
HARNESS = r"""
const fs = require("fs");
const store = %STORE%;
const saved = [];
const meta = [{ media: "(prefers-color-scheme: light)", c: "#ffffff" }, { media: "(prefers-color-scheme: dark)", c: "#000000" }]
  .map((m) => { const o = { dataset: {}, getAttribute: (k) => (k === "media" ? m.media : o.c), setAttribute: (k, v) => { o.c = v; } }; o.c = m.c; return o; });
const styles = [];
globalThis.window = globalThis;
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
globalThis.document = {
  documentElement: { dataset: {} }, head: { appendChild: (e) => styles.push(e) },
  querySelector: (s) => (s === "#slim-css" ? styles.find((e) => e.id === "slim-css") || null : null),
  createElement: () => ({}), querySelectorAll: () => meta,
};
window.I18N = { t: (k) => k };
window.BodyPlugins = [];
window.BodyLazy = { script: async () => { eval(fs.readFileSync(process.argv[1], "utf8")); return true; } };
const calls = [];
const C = { esc: (s) => String(s), toast() {}, openSheet() {}, api: async (p, o) => { calls.push(`${o.method} ${p} ${JSON.stringify(o.body)}`); return {}; } };
require(process.argv[2]);
const P = window.BodyPlugins[0];
const cfg = JSON.parse(process.argv[3]);
(async () => {
%BODY%
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run_js(body, store_init="{}", me=None):
    if NODE is None:
        pytest.skip("node not installed")
    script = HARNESS.replace("%STORE%", store_init).replace("%BODY%", body)
    p = subprocess.run([NODE, "-e", script, str(QUOTES), str(SLIM), json.dumps(me or {})], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


ME_ON = {"flags": ["slim_mode"], "today": "2026-10-27", "appearance": {"theme": "slim", "quotes": True}}


@needs_node
def test_flag_off_changes_nothing():
    out = run_js("""
      P.init(C, cfg);
      console.log(JSON.stringify({ theme: document.documentElement.dataset.theme || null, style: styles.length, row: P.meItem("›"),
        top: window.BodySlim.top(), bar: meta.map((m) => m.c), stored: store["be.theme"] || null }));""",
                 me={"flags": ["friends"], "today": "2026-10-27", "appearance": {"theme": "slim", "quotes": True}})
    assert out == {"theme": None, "style": 0, "row": "", "top": "", "bar": ["#ffffff", "#000000"], "stored": None}


@needs_node
def test_flag_off_removes_a_theme_left_from_before():
    """The flag is the kill switch: a device that last started in slim, now with the flag off, goes back to the normal look."""
    out = run_js("""
      P.init(C, cfg);
      console.log(JSON.stringify({ theme: document.documentElement.dataset.theme || null, stored: store["be.theme"] || null, bar: meta.map((m) => m.c) }));""",
                 store_init='{"be.theme": "slim"}', me={"flags": [], "today": "2026-10-27"})
    assert out == {"theme": None, "stored": None, "bar": ["#ffffff", "#000000"]}


@needs_node
def test_slim_theme_and_row_when_on():
    out = run_js("""
      P.init(C, cfg);
      console.log(JSON.stringify({ theme: document.documentElement.dataset.theme, style: styles.length, stored: store["be.theme"],
        bar: meta.map((m) => m.c), row: P.meItem("›"), top: window.BodySlim.top() }));""", me=ME_ON)
    assert out["theme"] == "slim" and out["style"] == 1 and out["stored"] == "slim"
    assert out["bar"] == ["#fbfaf7", "#0a0a0b"]
    assert 'id="me-appearance"' in out["row"] and "薄肌" in out["row"] and 'class="slimq"' in out["top"]


@needs_node
def test_default_theme_and_quote_off_when_chosen():
    out = run_js("""
      P.init(C, cfg);
      console.log(JSON.stringify({ theme: document.documentElement.dataset.theme || null, row: P.meItem("›"), top: window.BodySlim.top() }));""",
                 me={**ME_ON, "appearance": {"theme": "default", "quotes": True}})
    assert out["theme"] is None and "默认" in out["row"] and out["top"] == ""   # the quote belongs to the slim look
    out = run_js("""
      P.init(C, cfg);
      console.log(JSON.stringify({ theme: document.documentElement.dataset.theme, top: window.BodySlim.top() }));""",
                 me={**ME_ON, "appearance": {"theme": "slim", "quotes": False}})
    assert out == {"theme": "slim", "top": ""}


@needs_node
def test_quote_changes_daily_taps_to_next_and_turns_off():
    out = run_js("""
      P.init(C, cfg);
      const q = window.BodySlimQuotes ? 1 : 0;   // not loaded yet: loaded on demand
      const box = { isConnected: true, hidden: true, innerHTML: "", btn: {}, x: {} };
      box.querySelector = (s) => (s === "#slimq-next" ? box.btn : s === "#slimq-x" ? box.x : s === "#slimq-txt" ? { classList: { add() {} } } : null);
      const view = { querySelector: (s) => (s === "#slimq" ? box : null) };
      window.BodySlim.bind(view);
      await new Promise((r) => setTimeout(r, 20));
      const first = box.innerHTML, shown = !box.hidden;
      box.btn.onclick();
      await new Promise((r) => setTimeout(r, 300));
      const second = box.innerHTML;
      box.x.onclick();
      await new Promise((r) => setTimeout(r, 20));
      console.log(JSON.stringify({ q, shown, changed: first !== second, hiddenAfterX: box.hidden, saved: calls, stored: store["be.slimq"] || null,
        top: window.BodySlim.top() }));""", me=ME_ON)
    assert out["q"] == 0 and out["shown"] and out["changed"] and out["hiddenAfterX"]
    assert out["saved"] == ['PUT /v1/users/me/appearance {"quotes":false}']
    assert json.loads(out["stored"]) == {"day": "2026-10-27", "n": 1}
    assert out["top"] == ""


@needs_node
def test_pick_is_stable_per_day_and_uses_verified_items_only():
    out = run_js("""
      const S = require(process.argv[2]);
      eval(fs.readFileSync(process.argv[1], "utf8"));
      const items = window.BodySlimQuotes.items;
      const bad = [{ id: "x-1", person: "p", text: "t", source_url: "https://x.com/a/status/1", verified: false }, ...items];
      const a = S.pick(items, "2026-10-27"), b = S.pick(bad, "2026-10-27"), nextDay = S.pick(items, "2026-10-28"), tap = S.pick(items, "2026-10-27", 1);
      const month = new Set(); for (let d = 1; d <= 28; d++) month.add(S.pick(items, `2026-11-${String(d).padStart(2, "0")}`).id);
      console.log(JSON.stringify({ same: a.id === b.id, nextDifferent: a.id !== nextDay.id, tapDifferent: a.id !== tap.id, tapIsNextDay: tap.id === nextDay.id,
        n: items.length, distinct28: month.size, empty: S.pick([], "2026-10-27"), day: S.dayNumber("1970-01-02") }));""")
    assert out["same"] and out["nextDifferent"] and out["tapDifferent"] and out["tapIsNextDay"] and out["empty"] is None and out["day"] == 1
    assert out["distinct28"] == min(28, out["n"])   # a different quote every day until the list runs out


# ---------------------------------------------------------------- css, wiring
def test_both_colour_schemes_set_the_same_variables_as_the_normal_page():
    css = re.search(r"const CSS = `(.*?)`;", SLIM.read_text(encoding="utf-8"), re.S).group(1)
    light = re.search(r'html\[data-theme="slim"\] \{(.*?)\n\}', css, re.S).group(1)
    dark = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*html\[data-theme="slim"\] \{(.*?)\n  \}', css, re.S).group(1)
    names = lambda block: set(re.findall(r"(--[\w-]+):", block))
    assert names(light) == names(dark)
    base = (STATIC / "app.css").read_text(encoding="utf-8")
    root = names(re.search(r":root \{(.*?)\n\}", base, re.S).group(1)) - {"--radius", "--tabbar-h", "--ask-h", "--ease"}
    assert root <= names(light), sorted(root - names(light))   # every colour variable of the normal page is re-set
    assert {"--accent", "--on-accent"} <= names(light)
    assert "grid" not in css and "position:" not in css and "padding" not in light and "margin" not in light   # colours only for the theme


def test_page_wiring():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    # slim.js is only fetched when the flag is on for the account (never at start for everybody)
    assert 'if (S.flags.includes("slim_mode")) await lazyScript("slim.js");' in app
    assert 'window.BodySlim?.top() || ""' in app and "window.BodySlim?.bind(view)" in app and "window.BodySlim?.reset()" in app
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<script type="w2w/lazy" src="slim.js"></script>' in html and '<script type="w2w/lazy" src="slim-quotes.js"></script>' in html
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '"slim.js"' in sw and '"slim-quotes.js"' in sw
    assert "BodySlim" not in html.split("<script")[0]   # nothing of it in the first part of the page
