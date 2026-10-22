"""Offline use of the Today page (rollout flag offline_mode, issue #77, docs/offline-plan.md).

- offline.js core (read cache + write queue) run in node with its in-memory store (skipped without node): only own data is
  kept, copies expire and are capped, another account wipes them, the queue sends in order, a resend is idempotent
  (same client_id), a group stops at its first rejection, conflicts resend with their patch, the banner text.
- the page wiring, checked on the source: nothing offline runs while the flag is off, logout wipes the copies, the scripts
  are loaded and cached, the limits agree with offline/params.py.
- POST /v1/users/{user}/weight (offline/api.py): 404 while the flag is off, idempotent on (date, value), 409 on a different
  value unless overwrite. Separate test database BODY_TEST_DB_NAME (default body_test_offline); skipped without Postgres.
"""
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
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.offline import params as P
from body_engine.rollout import flags, registry

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
OFFLINE_JS = (STATIC / "offline.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_offline")


def run_js(body: str):
    """Run offline.js (no IndexedDB, no document: only BodyOfflineLib is defined) and then `body`, an async block that
    returns a JSON-able value."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = OFFLINE_JS + "\nconst L = globalThis.BodyOfflineLib;\n(async () => {\n" + body + \
        "\n})().then((v) => console.log(JSON.stringify(v)), (e) => { console.error(e); process.exit(1); });"
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ---------------------------------------------------------------- read cache
def test_cache_keeps_own_data_only():
    got = run_js("""
      const paths = ["/v1/web/me", "/v1/users/me/today", "/v1/users/me/trends?days=30", "/v1/users/me/water?date=2026-10-22",
        "/v1/users/7/today", "/v1/friends/cards", "/v1/users/me/ai-providers", "/v1/users/me/assistant/chat",
        "/v1/users/me/ecg/3", "/v1/assistant/chat", "/v1/web/me/x"];
      return Object.fromEntries(paths.map((p) => [p, L.createOffline().cacheable(p)]));""")
    assert [p for p, ok in got.items() if ok] == [
        "/v1/web/me", "/v1/users/me/today", "/v1/users/me/trends?days=30", "/v1/users/me/water?date=2026-10-22"]


def test_cache_roundtrip_expiry_cap_and_accounts():
    got = run_js("""
      let t = Date.parse("2026-10-22T08:00:00Z");
      const store = L.memStore(), o = L.createOffline({ store, now: () => t });
      const r = {};
      await o.put("/v1/users/me/today", "zh", { a: 1 });          // nobody signed in yet: not kept
      r.noUser = await o.get("/v1/users/me/today", "zh");
      await o.setUser(1);
      await o.put("/v1/users/me/today", "zh", { a: 1 });
      await o.put("/v1/users/7/today", "zh", { other: 1 });       // someone else's: never kept
      r.hit = await o.get("/v1/users/me/today", "zh");
      r.otherLang = await o.get("/v1/users/me/today", "en");
      r.other = await o.get("/v1/users/7/today", "zh");
      t += 15 * 86400000;                                          // older than 14 days: not shown
      r.expired = await o.get("/v1/users/me/today", "zh");
      L.LIMITS.entries = 3;                                        // cap: the oldest goes first
      for (let i = 0; i < 5; i++) { t += 1000; await o.put(`/v1/users/me/x${i}`, "zh", i); }
      r.capped = [];
      for (let i = 0; i < 5; i++) r.capped.push((await o.get(`/v1/users/me/x${i}`, "zh"))?.data ?? null);
      // a cold start reads the same store
      const o2 = L.createOffline({ store, now: () => t });
      r.restored = await o2.restore();
      r.coldHit = (await o2.get("/v1/users/me/x4", "zh"))?.data;
      // another account signs in on this device: everything of the first one is gone
      await o2.setUser(2);
      r.afterSwitch = await o2.get("/v1/users/me/x4", "zh");
      await o2.put("/v1/users/me/x4", "zh", "mine");
      await o2.clearCache();                                       // 401: copies gone
      r.after401 = await o2.get("/v1/users/me/x4", "zh");
      await o2.clear();                                            // logout
      r.afterLogout = await L.createOffline({ store }).restore();
      return r;""")
    assert got["noUser"] is None
    assert got["hit"]["data"] == {"a": 1} and got["hit"]["at"] == "2026-10-22T08:00:00.000Z"
    assert got["otherLang"] is None and got["other"] is None and got["expired"] is None
    assert got["capped"] == [None, None, 2, 3, 4]
    assert got["restored"] == 1 and got["coldHit"] == 4
    assert got["afterSwitch"] is None and got["after401"] is None and got["afterLogout"] is None


# ---------------------------------------------------------------- write queue
QUEUE_SETUP = """
  const store = L.memStore(), o = L.createOffline({ store });
  await o.setUser(1);
  const sent = [];
  // a fake server: water is idempotent on client_id like water/store.py
  const seen = new Set();
  let online = false, reject = null;
  const send = async (it) => {
    if (!online) return { ok: false, status: 0 };
    sent.push(`${it.method} ${it.path}`);
    if (reject && reject(it)) return reject(it);
    if (it.kind === "water") { const dup = seen.has(it.body.client_id); seen.add(it.body.client_id); return { ok: true, status: 200, data: { duplicate: dup } }; }
    return { ok: true, status: 200, data: {} };
  };
"""


def test_queue_waits_offline_then_sends_in_order_once():
    got = run_js(QUEUE_SETUP + """
      await o.enqueue({ kind: "water", method: "POST", path: "/v1/users/me/water", body: { ml: 250, client_id: "c1" }, label: "w1" });
      await o.enqueue({ kind: "training-save", method: "PUT", path: "/v1/users/me/training/session/2026-10-22", body: { exercises: [1] }, group: "t:2026-10-22", coalesce: "ts:2026-10-22" });
      await o.enqueue({ kind: "training-save", method: "PUT", path: "/v1/users/me/training/session/2026-10-22", body: { exercises: [1, 2] }, group: "t:2026-10-22", coalesce: "ts:2026-10-22" });
      await o.enqueue({ kind: "training-submit", method: "POST", path: "/v1/users/me/training/session/2026-10-22/submit", body: {}, group: "t:2026-10-22" });
      await o.enqueue({ kind: "water", method: "POST", path: "/v1/users/me/water", body: { ml: 500, client_id: "c2" } });
      const r = {};
      r.mustQueue = o.mustQueue();
      r.offline = await o.flush(send);                      // still offline: nothing sent, nothing lost
      r.kept = o.items().map((it) => [it.kind, it.body]);
      // a resend of an item already stored (the app was killed after the server answered) is stored once
      seen.add("c1");
      online = true;
      r.online = await o.flush(send);
      r.sent = sent;
      r.left = o.items().length;
      r.mustQueueAfter = o.mustQueue();
      // the queue survives a reload of the page
      await o.enqueue({ kind: "water", method: "POST", path: "/v1/users/me/water", body: { ml: 100, client_id: "c3" } });
      r.reloaded = (await L.createOffline({ store }).restore(), (await store.get("queue")).length);
      return r;""")
    assert got["mustQueue"] is True
    assert got["offline"] == {"sent": 0, "failed": 0, "left": 4}
    # the second draft of the day replaced the first one (coalesced); order kept
    assert got["kept"] == [["water", {"ml": 250, "client_id": "c1"}], ["training-save", {"exercises": [1, 2]}],
                           ["training-submit", {}], ["water", {"ml": 500, "client_id": "c2"}]]
    assert got["online"] == {"sent": 4, "failed": 0, "left": 0}
    assert got["sent"] == ["POST /v1/users/me/water", "PUT /v1/users/me/training/session/2026-10-22",
                           "POST /v1/users/me/training/session/2026-10-22/submit", "POST /v1/users/me/water"]
    assert got["left"] == 0 and got["mustQueueAfter"] is False and got["reloaded"] == 1


def test_queue_rejections_conflicts_and_limits():
    got = run_js(QUEUE_SETUP + """
      online = true;
      // the day's draft is rejected (422): its submit waits behind it; water of another group still goes
      reject = (it) => (it.kind === "training-save" ? { ok: false, status: 422, data: { detail: "最多 30 个动作" } }
        : it.kind === "weight" && !it.body.overwrite ? { ok: false, status: 409, data: { detail: { conflict: "weight", message: "已经记了 88 kg" } } }
        : it.kind === "training-submit" && !it.body.allow_duplicate ? { ok: true, status: 200, data: { duplicate: true } } : null);
      const day = "t:2026-10-21";
      await o.enqueue({ kind: "training-save", method: "PUT", path: "/s/21", body: {}, group: day, coalesce: "ts:21" });
      await o.enqueue({ kind: "training-submit", method: "POST", path: "/s/21/submit", body: {}, group: day });
      await o.enqueue({ kind: "water", method: "POST", path: "/w", body: { ml: 250, client_id: "a" } });
      await o.enqueue({ kind: "weight", method: "POST", path: "/weight", body: { weight_kg: 87.5 }, group: "w:22", coalesce: "w:22" });
      const r = {};
      r.first = await o.flush(send);
      r.state = o.items().map((it) => [it.kind, it.status, it.error, !!it.conflict]);
      // the user overrides the weight conflict, fixes and retries the draft
      const w = o.items().find((it) => it.kind === "weight"), d = o.items().find((it) => it.kind === "training-save");
      await o.retry(w.id, true);
      await o.retry(d.id);
      reject = (it) => (it.kind === "training-submit" && !it.body.allow_duplicate ? { ok: true, status: 200, data: { duplicate: true } } : null);
      r.second = await o.flush(send);
      r.state2 = o.items().map((it) => [it.kind, it.status, !!it.conflict]);
      const sub = o.items()[0];
      await o.retry(sub.id, true);                       // "submit anyway"
      r.third = await o.flush(send);
      r.lastBody = sent.length;
      // server trouble: stop and keep, no item marked failed
      await o.enqueue({ kind: "water", method: "POST", path: "/w", body: { ml: 1, client_id: "b" } });
      reject = () => ({ ok: false, status: 503 });
      r.server = await o.flush(send);
      r.dropped = (await o.drop(o.items()[0].id), o.items().length);
      // full queue refuses
      L.LIMITS.queue = 2;
      await o.enqueue({ kind: "water", method: "POST", path: "/w", body: { ml: 1, client_id: "x" } });
      await o.enqueue({ kind: "water", method: "POST", path: "/w", body: { ml: 1, client_id: "y" } });
      r.full = await o.enqueue({ kind: "water", method: "POST", path: "/w", body: { ml: 1, client_id: "z" } });
      return r;""")
    assert got["first"] == {"sent": 1, "failed": 2, "left": 1}
    assert got["state"] == [["training-save", "failed", "最多 30 个动作", False], ["training-submit", "pending", None, False],
                            ["weight", "failed", "已经记了 88 kg", True]]
    assert got["second"] == {"sent": 2, "failed": 1, "left": 0}          # draft + overwritten weight; submit was a duplicate
    assert got["state2"] == [["training-submit", "failed", True]]
    assert got["third"] == {"sent": 1, "failed": 0, "left": 0}
    assert got["server"] == {"sent": 0, "failed": 0, "left": 1}
    assert got["dropped"] == 0 and got["full"] is None


def test_banner_text():
    got = run_js("""
      const tr = (k, p) => k.replace(/\\{(\\w+)\\}/g, (_, n) => p[n]);
      const when = (d) => d.toISOString().slice(11, 16);
      return [
        L.bannerParts({ offline: true, at: "2026-10-22T07:41:00.000Z", waiting: 2, failed: 0 }, tr, when),
        L.bannerParts({ offline: true, at: "", waiting: 0, failed: 0 }, tr, when),
        L.bannerParts({ offline: false, at: null, waiting: 1, failed: 1 }, tr, when),
        L.bannerParts({ offline: false, at: null, waiting: 0, failed: 0 }, tr, when),
      ];""")
    assert got == [["离线 · 数据更新于 07:41", "2 条待同步"], ["离线"], ["正在同步 1 条", "1 条没提交"], []]


# ---------------------------------------------------------------- page wiring (source)
def test_flag_off_leaves_the_page_as_before():
    assert "offline_mode" in registry.FLAGS
    # every offline action in api() goes through OFF(), which is null unless the flag (or a copy it left) is on
    m = re.search(r"async function api\(path.*?\n  \}\n", APP, re.S)
    body = m.group(0)
    assert "const off = OFF();" in body
    assert not re.search(r"BodyOffline", body), "api() must only reach offline.js through OFF()"
    assert re.search(r'const OFF = \(\) => \(S\.offlineOn && window\.BodyOffline', APP)
    assert 'S.offlineOn = !!lib && S.flags.includes("offline_mode")' in APP
    # the weight entry and water's `at` only exist with the flag
    assert 'const weightOn = () => isSelf() && (S.flags || []).includes("offline_mode")' in APP
    water = (STATIC / "water.js").read_text(encoding="utf-8")
    assert '...(offlineOn() ? { at: localAt() } : {})' in water
    # the banner keeps its old texts while the flag is off
    assert 'tr("banner.offlineAt"' in APP and 'tr("banner.offline")' in APP


def test_logout_wipes_and_scripts_are_cached():
    m = re.search(r"async function logout\(\) \{.*?\n  \}\n", APP, re.S)
    assert "BodyOffline?.clear()" in m.group(0) and 'caches.delete("be-data")' in m.group(0)
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="offline.js"') < html.index('src="app.js"')
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '"offline.js"' in sw
    limits = json.loads(re.sub(r"(\w+):", r'"\1":', re.search(r"const LIMITS = (\{.*?\});", OFFLINE_JS).group(1)))
    assert limits == {"entries": P.CACHE_MAX_ENTRIES, "bytes": P.CACHE_MAX_BYTES, "ageDays": P.CACHE_MAX_AGE_DAYS,
                      "queue": P.QUEUE_MAX_ITEMS}


def test_offline_is_not_no_data():
    """#77: a failed load says "offline, retry" instead of "no data" in the page and in the on-demand sheets."""
    assert APP.count("offlineInto($(\"#sheet-body\")") >= 4   # labs, recipe, training day, exercise library
    assert "err.status === 0 ? offlineHtml()" in APP and "list.error.status === 0 ? offlineHtml()" in APP
    assert 'e.status === 0 ? offlineHtml() : `<div class="cal-note">' in APP


# ---------------------------------------------------------------- POST /weight
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
        ids = {"a": add("a", "owner"), "b": add("b")}
        conn.commit()
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


class FakeSparky:
    """The check-ins of one user, like SparkyFitness /measurements/check-in."""
    def __init__(self):
        self.days, self.posts = {}, 0

    def get(self, path, **_):
        return self.days.get(path.rsplit("/", 1)[1], {})

    def request(self, method, path, body):
        self.posts += 1
        self.days[body["entry_date"]] = {**self.days.get(body["entry_date"], {}), **body}


def H(t):
    return {"Authorization": f"Bearer {t}"}


def set_flag(dsn, state):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, P.FEATURE, state, actor="test")
        conn.commit()
    flags.invalidate()


def test_weight_endpoint(client, users, dsn, monkeypatch):
    from body_engine import sparky_client
    from body_engine.records import api as records_api   # noqa: F401  (router dependencies)
    ids, tok = users
    fake = FakeSparky()
    monkeypatch.setattr(sparky_client, "write_client_for_user", lambda conn, uid: fake)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        from body_engine.assistant.sparky_write import user_today
        day = user_today(conn, ids["a"]).isoformat()
    body = {"date": day, "weight_kg": 88.4, "client_id": "q1"}

    set_flag(dsn, "off")
    assert client.post("/v1/users/me/weight", json=body, headers=H(tok["a"])).status_code == 404   # flag off: no endpoint

    set_flag(dsn, "all")
    r = client.post("/v1/users/me/weight", json=body, headers=H(tok["a"]))
    assert r.status_code == 200 and r.json()["written"] is True and fake.posts == 1
    r = client.post("/v1/users/me/weight", json=body, headers=H(tok["a"]))     # the queue resends it: nothing new
    assert r.status_code == 200 and r.json()["unchanged"] is True and fake.posts == 1
    r = client.post("/v1/users/me/weight", json={**body, "weight_kg": 87.9}, headers=H(tok["a"]))
    assert r.status_code == 409 and r.json()["detail"]["conflict"] == "weight" and r.json()["detail"]["existing_kg"] == 88.4
    r = client.post("/v1/users/me/weight", json={**body, "weight_kg": 87.9, "overwrite": True}, headers=H(tok["a"]))
    assert r.status_code == 200 and fake.days[day]["weight"] == 87.9 and fake.posts == 2
    # only for oneself; plausible values only
    assert client.post(f"/v1/users/{ids['a']}/weight", json=body, headers=H(tok["b"])).status_code in (403, 404)
    assert client.post("/v1/users/me/weight", json={**body, "weight_kg": 900}, headers=H(tok["a"])).status_code == 422
    assert client.post("/v1/users/me/weight", json={**body, "date": "2099-01-01"}, headers=H(tok["a"])).status_code == 422


# ---------------------------------------------------------------- the app's offline shell (iOS 1.0.7)
def test_offline_shell_inlines_the_page():
    from body_engine.web import api as web_api
    html = web_api.offline_shell_html()
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    # every local script and stylesheet is inside, in the same order; nothing is loaded from the server
    names = re.findall(r'<script src="([\w.-]+\.js)"', index)
    assert names and not re.search(r'<script src="[\w.-]+\.js"', html) and 'rel="stylesheet"' not in html
    starts = [html.index((STATIC / n).read_text(encoding="utf-8")[:200].replace("</script", "<\\/script")) for n in names]
    assert starts == sorted(starts)
    assert html.count("<script>") == len(names) + 1          # + the W2W_SHELL marker
    assert f'window.W2W_SHELL = "{web_api.WEB_VERSION}"' in html
    assert html.index("W2W_SHELL") < html.index("</head>")
    assert html.count("</script>") == html.count("<script")  # no closing tag from inside a script ends it early


def test_offline_shell_endpoint_and_page_bridge():
    from body_engine import api as root_api
    r = TestClient(root_api.app, base_url="https://testserver").get("/app/offline-shell.html")
    assert r.status_code == 200 and "W2W_SHELL" in r.text and r.headers["content-type"].startswith("text/html")
    # the page tells the app when to keep / drop the copy, sends nothing from the copy and asks for the real page online
    assert 'tellApp(S.offlineOn ? "offline:on" : "offline:off")' in APP and 'tellApp("offline:off")' in APP
    assert 'if (window.W2W_SHELL) throw new Error("offline shell")' in APP
    assert 'if (window.W2W_SHELL) { tellApp("reload"); return; }' in APP
