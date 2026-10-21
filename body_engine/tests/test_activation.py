"""Reusable activation codes (web/activation.py): redeem many times, revoke / expiry, OW invitation minting (HTTP mocked),
the JWT signature, behaviour without OW config, the per-IP failure limit, and the activation page under the CSP.

Uses its own test database (body_test_activation), dropped and recreated on every run; skipped if Postgres is unreachable.
No real Open Wearables server is contacted.
"""
import base64
import hashlib
import hmac
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.web import activation, login_codes, login_limit

TEST_DB = "body_test_activation"
OW_USER = "0b7a3c52-6f0e-4b8e-9a51-3f2d1c0e9b77"
DEV_ID = "5d1e2f3a-4b5c-4d6e-8f70-819293a4b5c6"
SECRET = "test-ow-secret-key"


@pytest.fixture(scope="module")
def dsn():
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


@pytest.fixture(scope="module")
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        synced = conn.execute("INSERT INTO users (display_name, ow_user_id) VALUES ('S', %s) RETURNING id",
                              (OW_USER,)).fetchone()["id"]
        plain = conn.execute("INSERT INTO users (display_name) VALUES ('P') RETURNING id").fetchone()["id"]
    return {"synced": synced, "plain": plain}


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    login_limit.reset()
    monkeypatch.setenv("BODY_PUBLIC_URL", "https://body.example.com/")
    monkeypatch.setenv("BODY_OW_PUBLIC_URL", "https://sync.example.com")
    monkeypatch.delenv("BODY_OW_INVITE_SIGNING_FILE", raising=False)
    yield
    login_limit.reset()


@pytest.fixture
def ow(monkeypatch, tmp_path):
    """OW signing configured; the HTTP call is captured and answers like OW (201 UserInvitationCodeRead)."""
    f = tmp_path / "ow_invite_signing.json"
    f.write_text(json.dumps({"secret_key": SECRET, "algorithm": "HS256", "developer_id": DEV_ID}))
    monkeypatch.setenv("BODY_OW_INVITE_SIGNING_FILE", str(f))
    calls = []
    codes = iter(["ABCDEFGH", "JKLMNPQR", "STUVWXYZ", "23456789"])

    def fake_post(url, headers, timeout):
        calls.append({"url": url, "headers": headers})
        return {"id": "x", "code": next(codes), "user_id": OW_USER, "expires_at": "2026-10-26T00:00:00Z",
                "created_at": "2026-10-19T00:00:00Z"}
    monkeypatch.setattr(activation, "_post_json", fake_post)
    return calls


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _params(link: str) -> dict:
    assert link.startswith("watch2whoop://setup?")
    return dict(urllib.parse.parse_qsl(link.split("?", 1)[1]))


def _new(dsn, user_id, **kw) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return activation.create(conn, user_id, **kw)


def test_redeem_many_times_with_ow(client, users, dsn, ow):
    res = _new(dsn, users["synced"], label="Mum")
    code = res["code"]
    assert len(code) == activation.LENGTH and set(code) <= set(activation.ALPHABET)
    logins = []
    for i in range(3):
        pretty = f"{code[:4].lower()}-{code[4:8]} {code[8:]}"
        r = client.post("/v1/web/activate", json={"code": pretty if i == 1 else code})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sync"] is True and body["note"] is None
        p = _params(body["setup_link"])
        assert p["today"] == "https://body.example.com" and p["ow"] == "https://sync.example.com"
        assert p["code"] == ["ABCDEFGH", "JKLMNPQR", "STUVWXYZ"][i]
        assert len(p["login"]) == login_codes.LENGTH
        logins.append(p["login"])
    assert len(set(logins)) == 3
    assert r.headers["cache-control"] == "private, no-store"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT uses, last_used_at FROM activation_codes WHERE id = %s", (res["id"],)).fetchone()
        assert row["uses"] == 3 and row["last_used_at"] is not None
        lc = conn.execute("SELECT label, expires_at - created_at AS ttl FROM web_login_codes WHERE code_sha256 = %s",
                          (login_codes._sha(logins[0]),)).fetchone()
        assert lc["label"] == "activation:Mum" and lc["ttl"].total_seconds() <= 3600
    # The minted login code works like any one-time login code
    client.cookies.clear()
    assert client.post("/v1/web/login-code", json={"code": logins[-1]}).status_code == 200
    assert client.get("/v1/web/me").json()["me"]["id"] == users["synced"]
    client.cookies.clear()

    # The OW call: right URL, developer JWT verifiable with the OW secret, short-lived
    assert ow[0]["url"].endswith(f"/api/v1/users/{OW_USER}/invitation-code")
    token = ow[0]["headers"]["Authorization"].removeprefix("Bearer ")
    head, payload, sig = token.split(".")
    expected = hmac.new(SECRET.encode(), f"{head}.{payload}".encode(), hashlib.sha256).digest()
    assert hmac.compare_digest(_b64d(sig), expected)
    assert json.loads(_b64d(head)) == {"alg": "HS256", "typ": "JWT"}
    claims = json.loads(_b64d(payload))
    assert claims["sub"] == DEV_ID and 0 < claims["exp"] - time.time() <= activation.OW_JWT_TTL_SECONDS


def test_revoked_and_expired_are_401(client, users, dsn):
    revoked = _new(dsn, users["plain"], label="old")
    expired = _new(dsn, users["plain"], days=1)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert activation.revoke(conn, revoked["id"]) is True
        assert activation.revoke(conn, revoked["id"]) is False
        conn.execute("UPDATE activation_codes SET expires_at = now() - interval '1 minute' WHERE id = %s", (expired["id"],))
        listed = {r["id"]: r for r in activation.list_codes(conn, users["plain"])}
        assert listed[revoked["id"]]["active"] is False and listed[expired["id"]]["active"] is False
        assert "code_sha256" not in listed[revoked["id"]]
    for c in (revoked["code"], expired["code"], "ABCDEFGHJKLMNPQR", "SHORT123"):
        r = client.post("/v1/web/activate", json={"code": c})
        assert r.status_code == 401 and r.json()["detail"] == "激活码无效或已停用"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT uses FROM activation_codes WHERE id = %s", (revoked["id"],)).fetchone()["uses"] == 0
    with pytest.raises(ValueError), psycopg.connect(dsn, row_factory=dict_row) as conn:
        activation.create(conn, users["plain"], days=0)


def test_without_ow_link_has_no_code(client, users, dsn, monkeypatch):
    """No ow_user_id or no signing file: still a valid link (today + ow + login), without `code`; the app asks for it later."""
    no_user = _new(dsn, users["plain"])["code"]
    r = client.post("/v1/web/activate", json={"code": no_user})
    assert r.status_code == 200
    assert r.json()["sync"] is False and r.json()["note"] == "no_ow_user"
    p = _params(r.json()["setup_link"])
    assert "code" not in p and p["login"] and p["ow"] == "https://sync.example.com"

    unsigned = _new(dsn, users["synced"])["code"]
    r = client.post("/v1/web/activate", json={"code": unsigned})
    assert r.json()["sync"] is False and r.json()["note"] == "ow_signing_not_configured"
    assert "code" not in _params(r.json()["setup_link"])

    # Without BODY_PUBLIC_URL the request's own address is used for `today`
    monkeypatch.delenv("BODY_PUBLIC_URL")
    monkeypatch.delenv("BODY_OW_PUBLIC_URL")
    p = _params(client.post("/v1/web/activate", json={"code": unsigned}).json()["setup_link"])
    assert p["today"] == "https://testserver" and "ow" not in p


def test_ow_failure_still_gives_a_link(client, users, dsn, ow, monkeypatch):
    def down(url, headers, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(activation, "_post_json", down)
    code = _new(dsn, users["synced"])["code"]
    r = client.post("/v1/web/activate", json={"code": code})
    assert r.status_code == 200 and r.json()["sync"] is False and r.json()["note"] == "ow_unavailable"


def test_failures_are_rate_limited(client, users, dsn, monkeypatch):
    monkeypatch.setattr(login_limit, "MAX_FAILURES", 3)
    good = _new(dsn, users["plain"])["code"]
    for _ in range(3):
        assert client.post("/v1/web/activate", json={"code": "ABCDEFGHJKLMNPQR"}).status_code == 401
    r = client.post("/v1/web/activate", json={"code": good})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # Token login shares the same per-IP counter
    assert client.post("/v1/web/login", json={"token": "be_wrong_wrong"}).status_code == 429


def test_success_does_not_reset_failures(client, users, dsn, monkeypatch):
    monkeypatch.setattr(login_limit, "MAX_FAILURES", 3)
    good = _new(dsn, users["plain"])["code"]
    for _ in range(2):
        assert client.post("/v1/web/activate", json={"code": "ABCDEFGHJKLMNPQR"}).status_code == 401
    assert client.post("/v1/web/activate", json={"code": good}).status_code == 200
    assert client.post("/v1/web/activate", json={"code": "ABCDEFGHJKLMNPQR"}).status_code == 401
    assert client.post("/v1/web/activate", json={"code": good}).status_code == 429


def test_token_revoke_all_revokes_activation_codes(client, users, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("INSERT INTO users (display_name) VALUES ('R') RETURNING id").fetchone()["id"]
        code = activation.create(conn, uid)["code"]
        assert auth.revoke_tokens(conn, uid, everything=True)["activation_codes"] == 1
    assert client.post("/v1/web/activate", json={"code": code}).status_code == 401


def test_activation_page_served_with_csp(client):
    r = client.get("/app/activate.html")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    html = r.text
    assert "激活 Watch2Whoop" in html and "打开 App 完成设置" in html
    assert '<script src="activate.js"></script>' in html
    assert 'href="handbook/"' in html   # the handbook is one tap away
    assert "<script>" not in html                     # no inline script (blocked by the CSP)
    js = client.get("/app/activate.js")
    assert js.status_code == 200 and "/v1/web/activate" in js.text and "location.hash" in js.text


def test_sign_jwt_rejects_unknown_algorithm():
    with pytest.raises(ValueError):
        activation.sign_jwt({"sub": "x"}, "k", "RS256")


def test_login_box_takes_activation_code(client, users, dsn):
    """Issue #12: the Today page login box redeems an activation code the way app.js does it: /v1/web/activate →
    `login` from the setup link → /v1/web/login-code. Each step fails with its own message."""
    act = _new(dsn, users["plain"], label="box")
    client.cookies.clear()
    pretty = f"{act['code'][:4]}-{act['code'][4:8]}-{act['code'][8:12]}-{act['code'][12:]}".lower()
    code = pretty.replace("-", "").upper()                      # loginKind() strips dashes / spaces, upper-cases
    r = client.post("/v1/web/activate", json={"code": code})
    assert r.status_code == 200, r.text
    login = urllib.parse.unquote(re.search(r"[?&]login=([^&]+)", r.json()["setup_link"]).group(1))   # same regex as app.js
    assert client.post("/v1/web/login-code", json={"code": login}).status_code == 200
    assert client.get("/v1/web/me").json()["me"]["id"] == users["plain"]
    client.cookies.clear()
    # the one-time login code is spent; a token-looking value goes to /v1/web/login: distinct server answers
    r = client.post("/v1/web/login-code", json={"code": login})
    assert r.status_code == 401 and "已用过" in r.json()["detail"]
    assert client.post("/v1/web/login", json={"token": "be_not-a-real-token"}).json()["detail"] == "token 无效或已吊销"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        activation.revoke(conn, act["id"])
    assert client.post("/v1/web/activate", json={"code": code}).json()["detail"] == "激活码无效或已停用"


def test_today_page_login_kinds_and_app_bridge(client):
    """app.js: loginKind() tells activation codes (16) from login codes (12) and tokens; distinct Chinese errors per step;
    the page reports logged-in / login-failed to the iOS app so it keeps its login code until then."""
    js = client.get("/app/app.js").text + client.get("/app/i18n.js").text   # error texts live in the i18n tables
    for s in ('"/v1/web/activate"', 'tellApp("logged-in")', 'tellApp("login-failed")', "window.Watch2Whoop.postMessage",
              '"hashchange"', 'tr("login.badActivation")', "激活码无效或已停用", "登录码已用过或已过期", "token 不对或已吊销"):
        assert s in js, s
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    fn = re.search(r"  function loginKind\(v\) \{.*?\n  \}\n", js, re.S).group(0)
    probe = fn + """
const cases = ["ABCD-EFGH-JKMN-PQRS", " abcdefghjkmnpqrs ", "ABCD-EFGH-JKMN", "abcdefghjkmn", "be_abcdefghjkmnpqrs",
               "ABCDEFGHJKMNPQR0", "ABCDEFGHJKMNPQ"];
console.log(JSON.stringify(cases.map(loginKind)));"""
    out = json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)
    assert [o["kind"] for o in out] == ["activation", "activation", "code", "code", "token", "token", "token"]
    assert out[0]["code"] == "ABCDEFGHJKMNPQRS" and out[3]["code"] == "ABCDEFGHJKMN"
    assert out[4]["code"] == "be_abcdefghjkmnpqrs"


ACTIVATE_HARNESS = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");
const IPHONE = { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) Mobile/15E148 Safari/604.1", platform: "iPhone", maxTouchPoints: 5 };
const IPADOS = { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15", platform: "MacIntel", maxTouchPoints: 5 };
const MAC = { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/140", platform: "MacIntel", maxTouchPoints: 0 };
async function scenario(hash, opts) {
  const els = {}, calls = [], timers = [], docL = {}, winL = {};
  const startHidden = ["form", "choose", "install", "open-app", "open-web"];
  const el = (id) => els[id] || (els[id] = { id, hidden: startHidden.includes(id), textContent: "", disabled: false, value: "",
    _l: {}, addEventListener(t, f) { this._l[t] = f; }, focus() {} });
  const document = { hidden: false, getElementById: el, addEventListener: (t, f) => { docL[t] = f; } };
  const location = { hash, _href: null, set href(v) { this._href = v; calls.push(["open", v]);
      if (opts.appOpens) { document.hidden = true; docL.visibilitychange(); } },
    get href() { return this._href; }, replace(v) { calls.push(["replace", v]); } };
  const fetch = async (url, init) => {
    calls.push(["fetch", url, JSON.parse(init.body)]);
    if (url === "/v1/web/activate") return opts.valid
      ? { ok: true, status: 200, json: async () => ({ setup_link: "watch2whoop://setup?today=x&login=LOGIN" + calls.length }) }
      : { ok: false, status: 401, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => ({}) };
  };
  const window = { addEventListener: (t, f) => { winL[t] = f; } };
  const setTimeout = (f) => { timers.push(f); return timers.length; };
  const tick = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => global.setTimeout(r, 0)); };
  new Function("window", "document", "location", "fetch", "setTimeout", "clearTimeout", "navigator", src)(
    window, document, location, fetch, setTimeout, () => { timers.length = 0; }, opts.device || MAC);
  await tick();
  if (opts.typed) { els.code.value = opts.typed; els.form._l.submit({ preventDefault() {} }); await tick(); }
  for (const f of timers.splice(0)) f();   // FALLBACK_MS elapses (and the button re-enable)
  await tick();
  const h = (id) => els[id].hidden;
  return { calls: calls.map((c) => (c[0] === "fetch" ? [c[1], c[2]] : [c[0], c[1]])), form: h("form"), choose: h("choose"),
           guide: h("install"), app: h("open-app"), web: h("open-web"), err: els.err.textContent };
}
(async () => {
  console.log(JSON.stringify({
    desktop: await scenario("#abcd-efgh-jkmn-pqrs", { valid: true }),
    iphoneNoApp: await scenario("#ABCDEFGHJKMNPQRS", { valid: true, device: IPHONE }),
    iphoneApp: await scenario("#ABCDEFGHJKMNPQRS", { valid: true, device: IPHONE, appOpens: true }),
    ipados: await scenario("#ABCDEFGHJKMNPQRS", { valid: true, device: IPADOS }),
    invalid: await scenario("#ABCDEFGHJKMNPQRS", { valid: false, device: IPHONE }),
    noCode: await scenario("", { valid: true }),
    typed: await scenario("", { valid: true, typed: "abcd efgh jkmn pqrs" }),
  }));
})();
"""


def test_activation_page_flow(client, tmp_path):
    """activate.js with a stub DOM: a code in the link never shows the input. iPhone / iPad (also iPadOS posing as a Mac):
    open the app only; if it does not open, the install guide (no web sign-in); a computer signs in on the web with its
    own web=true login code. An invalid code says so with no input, no buttons."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = tmp_path / "activate.js"
    src.write_text(client.get("/app/activate.js").text)
    out = json.loads(subprocess.run([node, "-e", ACTIVATE_HARNESS, str(src)], capture_output=True, text=True, check=True).stdout)
    code = "ABCDEFGHJKMNPQRS"
    d = out["desktop"]
    assert d["form"] is True and d["choose"] is False and d["web"] is False and d["app"] is True and d["guide"] is True
    assert [c[0] for c in d["calls"]] == ["/v1/web/activate", "/v1/web/login-code", "replace"], d["calls"]
    assert d["calls"][0][1] == {"code": code, "web": True} and d["calls"][1][1]["code"].startswith("LOGIN") and d["calls"][2][1] == "./"
    for name in ("iphoneNoApp", "ipados"):
        o = out[name]
        assert o["form"] is True and o["app"] is False and o["web"] is True, name          # only 「在 App 里打开」
        assert [c[0] for c in o["calls"]] == ["/v1/web/activate", "open"], o["calls"]       # never a web sign-in
        assert o["calls"][0][1] == {"code": code} and o["guide"] is False, name           # install guide shown
    a = out["iphoneApp"]
    assert [c[0] for c in a["calls"]] == ["/v1/web/activate", "open"] and a["guide"] is True   # the app came up
    bad = out["invalid"]
    assert bad["err"] == "activate.invalidAsk" and bad["choose"] is True and bad["form"] is True and bad["guide"] is True
    assert out["noCode"]["form"] is False and out["noCode"]["calls"] == []
    t = out["typed"]
    assert t["form"] is True and t["calls"][0][1] == {"code": code, "web": True} and t["calls"][-1][0] == "replace"


IOS_WEB_HARNESS = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");
async function run(device, mode, inApp) {
  const body = { first: null, prepend(n) { this.first = n; } };
  const root = { style: { visibility: "" } };
  const location = { replaced: null, replace(v) { this.replaced = v; } };
  const window = inApp ? { Watch2Whoop: {} } : {};
  const fetches = [];
  const fetch = async (url) => { fetches.push(url); return { ok: true, json: async () => ({ ios_web: mode }) }; };
  const el = () => { const n = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, _kids: { span: { textContent: "" }, a: { textContent: "" } },
    set innerHTML(v) { this.html = v; }, querySelector(s) { return this._kids[s]; } }; return n; };
  const document = { documentElement: root, body, createElement: el };
  new Function("window", "document", "location", "fetch", "navigator", "setTimeout", src)(window, document, location, fetch, device, () => 0);
  for (let i = 0; i < 6; i++) await new Promise((r) => global.setTimeout(r, 0));
  return { fetched: fetches.length, banner: body.first ? body.first._kids.span.textContent : null, href: body.first ? body.first.html : null,
           replaced: location.replaced, hidden: root.style.visibility === "hidden" };
}
const IPHONE = { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X)", platform: "iPhone", maxTouchPoints: 5 };
const MAC = { userAgent: "Mozilla/5.0 (Macintosh)", platform: "MacIntel", maxTouchPoints: 0 };
(async () => console.log(JSON.stringify({
  warn: await run(IPHONE, "warn"), off: await run(IPHONE, "off"), app: await run(IPHONE, "off", true), mac: await run(MAC, "off"),
})))();
"""


def test_ios_safari_today_page_modes(client, tmp_path, monkeypatch):
    """BODY_IOS_WEB: /v1/auth/methods reports warn (default) / off; ios-web.js shows the banner (warn) or goes to the install
    guide (off) in iOS Safari, and does nothing inside the app's WebView or on a computer."""
    monkeypatch.delenv("BODY_IOS_WEB", raising=False)
    assert client.get("/v1/auth/methods").json()["ios_web"] == "warn"
    monkeypatch.setenv("BODY_IOS_WEB", "off")
    assert client.get("/v1/auth/methods").json()["ios_web"] == "off"
    monkeypatch.setenv("BODY_IOS_WEB", "nonsense")
    assert client.get("/v1/auth/methods").json()["ios_web"] == "warn"
    html = client.get("/app/").text
    assert html.index('src="i18n-en.js"') < html.index('src="ios-web.js"') < html.index('src="account.js"')
    guide = client.get("/app/install.html")
    assert guide.status_code == 200 and "TestFlight" in guide.text and "<script>" not in guide.text
    assert "apps.apple.com/app/testflight" in guide.text and "activate.noInvite" in guide.text
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = tmp_path / "ios-web.js"
    src.write_text(client.get("/app/ios-web.js").text)
    out = json.loads(subprocess.run([node, "-e", IOS_WEB_HARNESS, str(src)], capture_output=True, text=True, check=True).stdout)
    assert out["warn"]["banner"] == "activate.iosBanner" and 'href="install.html"' in out["warn"]["href"] and not out["warn"]["hidden"]
    assert out["off"]["replaced"] == "install.html" and out["off"]["banner"] is None
    for name in ("app", "mac"):
        assert out[name] == {"fetched": 0, "banner": None, "href": None, "replaced": None, "hidden": False}, name


def test_web_activation_mints_no_ow_code(client, users, dsn, ow):
    """activate.html / the login box signing in on the web (web=true): a login code only, no OW invitation, so an app that
    is still opening an earlier link keeps a valid sync code."""
    code = _new(dsn, users["synced"], label="Web")["code"]
    before = len(ow)
    r = client.post("/v1/web/activate", json={"code": code, "web": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sync"] is False and body["note"] == "web" and "code" not in _params(body["setup_link"])
    assert len(ow) == before                                                           # OW was not called
    client.cookies.clear()
    assert client.post("/v1/web/login-code", json={"code": _params(body["setup_link"])["login"]}).status_code == 200
    assert client.get("/v1/web/me").json()["me"]["id"] == users["synced"]
    client.cookies.clear()
    assert client.post("/v1/web/activate", json={"code": "ABCDEFGHJKMNPQRS", "web": True}).status_code == 401
