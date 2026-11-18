"""Apple Watch hold-to-talk backend (docs/watch-app-plan.md 2.2, 2.3, 4.3–4.5; account/watch.py, ai/watch.py, migration 1190).

The phone asks for a one-time watch code, the watch redeems it for its own narrow device token, and that token works on
transcribe, chat, chat/confirm and chat events only, for the conversations its watch started. Covered here: pairing,
what the token may never do (negative tests, a sweep over every route), revocation with the phone, short answers for
every provider path (own key and the fallback AI), the device marked in usage and audit, the shared voice allowance,
the audio a watch sends, and a meal said by voice up to its receipt and undo.

Separate test database BODY_TEST_DB_NAME_WATCH (default body_test_watch_voice), dropped and recreated every run; skipped
without Postgres. No paid API: every provider kind is FakeAdapter, the ASR call and DeepSeek's balance are faked.

Users: W (own anthropic_api provider; phone tokens rw and read-only), F (no AI of their own -> the fallback AI),
S (Codex subscription), X (someone else), P (pending account).
"""
import io
import json
import math
import os
import re
import struct
import wave
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth, queries
from body_engine.account import params as account_params
from body_engine.account import watch as watch_tokens
from body_engine.ai import adapters, asr, cards, crypto, fallback, watch
from body_engine.ai import fallback_params as fp
from body_engine.ai import tokens as ai_tokens
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.assistant import scene_tools, toolsets
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.web import login_codes, login_limit
from body_engine.web import session as web_session

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_WATCH", "body_test_watch_voice")
KEY_W = "sk-ant-test-WWWW-3333-secretW"
DS_KEY = "test-fallback-key-not-real-0000"
CSRF = {"X-Body-Web": "1"}
VOICE = {("POST", "/v1/assistant/transcribe"), ("POST", "/v1/assistant/chat"), ("POST", "/v1/assistant/chat/confirm"),
         ("GET", "/v1/assistant/chat/{conv_id}/events")}


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


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    d = tmp_path_factory.mktemp("watch")
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(d / "ai_master_key"))
    crypto.init_master_key()
    (d / "deepseek_api_key").write_text(DS_KEY + "\n")
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(d / "deepseek_api_key"))
    yield d
    mp.undo()


@pytest.fixture(scope="module")
def world(dsn, keys):
    now = datetime.now(timezone.utc)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {}
        for n, status in (("W", "active"), ("F", "active"), ("S", "active"), ("X", "active"), ("P", "pending")):
            ids[n] = conn.execute("""INSERT INTO users (display_name, role, consent_at, timezone, status)
                                     VALUES (%s, 'member', %s, 'Australia/Sydney', %s) RETURNING id""",
                                  (n, now, status)).fetchone()["id"]
        tok = {n: auth.create_token(conn, ids[n], "ios:signin:iPhone", ["read", "write"]) for n in ids}
        tok["W_ro"] = auth.create_token(conn, ids["W"], "ios:signin:iPad", ["read"])
        tok["W_mcp"] = ai_tokens.create(conn, ids["W"], "claude-code")["token"]
        tok["W_ingest"] = auth.create_token(conn, ids["W"], "app:ingest:iPhone", [auth.INGEST_SCOPE])
        for f in ("own_food_only", cards.FLAG, cards.FLAG_V2):   # meals in Body Engine's own tables, meal cards with undo
            flags.set_flag(conn, f, "all", actor="test")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver") as c:
        ids, tok = world
        r = c.post("/v1/users/me/ai-providers", headers=H(tok["W"]),
                   json={"kind": "anthropic_api", "api_key": KEY_W, "monthly_budget_usd": 5})
        assert r.status_code == 201, r.text
        assert c.post("/v1/users/me/ai-providers", headers=H(tok["S"]), json={"kind": "codex_plan_mcp"}).status_code == 201
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    for kind in ("anthropic_api", "openai_api", "gemini_api", "openai_compatible", "system_fallback"):
        monkeypatch.setitem(adapters.ADAPTERS, kind, lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 50.0)
    login_limit.reset()   # the negative tests count as failed logins from one address
    yield
    login_limit.reset()


@pytest.fixture()
def asr_on(monkeypatch):
    monkeypatch.setenv("BODY_ASR_URL", "http://asr.test:8000")
    sent = []

    def fake_post(data):
        sent.append(len(data))
        return {"text": "午饭一碗牛肉面加一个蛋", "seconds": 2.0, "ms": 120}
    monkeypatch.setattr(asr, "_post", fake_post)
    return sent


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def wav(seconds: float, rate: int = 16000, fmt_tag: int = 1, bits: int = 16, channels: int = 1,
        extra: bytes = b"", silent: bool = False) -> bytes:
    """A WAV file as a watch app may write it: plain PCM, WAVE_FORMAT_EXTENSIBLE (AVAudioFile / AVAudioRecorder), or with
    extra chunks before the data (Apple writes FLLR padding; LIST metadata). silent: zeros (fast, for sizes)."""
    n, width = int(seconds * rate), bits // 8
    if bits == 16 and not silent:
        data = b"".join(struct.pack("<h", int(6000 * math.sin(i / 9))) for i in range(n)) * channels
    else:
        data = bytes(n * channels * width)
    block = channels * width
    fmt = struct.pack("<HHIIHH", fmt_tag, channels, rate, rate * block, block, bits)
    if fmt_tag == 0xFFFE:   # cbSize 22, valid bits, channel mask, KSDATAFORMAT_SUBTYPE_PCM
        fmt += struct.pack("<HHI", 22, bits, 4) + b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    elif fmt_tag == 3:
        fmt += struct.pack("<H", 0)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def pair(client, phone: str, device: str | None = None) -> str:
    """The phone asks for a watch code, the watch redeems it: the watch's token."""
    r = client.post("/v1/account/watch-code", headers=H(phone), json={"device": device} if device else None)
    assert r.status_code == 200, r.text
    r = client.post("/v1/web/login-code", json={"code": r.json()["code"], "client": "watch"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def token_row(dsn, token: str) -> dict:
    with db(dsn) as c:
        return c.execute("SELECT * FROM api_tokens WHERE token_sha256 = %s", (auth.hash_token(token),)).fetchone()


def fresh_phone(dsn, uid: int, scopes=("read", "write"), name="ios:signin:iPhone") -> str:
    with db(dsn) as c:
        return auth.create_token(c, uid, name, list(scopes))


def reset_ai(dsn, uid: int) -> None:
    with db(dsn) as c:
        c.execute("DELETE FROM ai_usage WHERE user_id = %s", (uid,))
        c.execute("DELETE FROM ai_fallback_quota WHERE user_id = %s", (uid,))
        c.execute("UPDATE ai_fallback_state SET enabled = true, balance_cny = NULL, balance_checked_at = NULL, balance_error = NULL")


# ---------------------------------------------------------------- pairing
def test_pairing_gives_the_watch_its_own_narrow_token(client, world, dsn):
    ids, _ = world
    phone = fresh_phone(dsn, ids["W"])
    r = client.post("/v1/account/watch-code", headers=H(phone), json={"device": "小林的 Apple Watch <script>"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert set(got) == {"code", "expires_in", "scopes"} and got["expires_in"] == account_params.WATCH_CODE_MINUTES * 60
    assert got["scopes"] == ["read", "write"] and len(login_codes.normalize(got["code"])) == login_codes.LENGTH
    r = client.post("/v1/web/login-code", json={"code": got["code"], "client": "watch"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["id"] == ids["W"] and me["token"].startswith(auth.TOKEN_PREFIX) and "set-cookie" not in r.headers
    row = token_row(dsn, me["token"])
    assert row["scopes"] == [auth.WATCH_SCOPE, "read", "write"] and row["parent_token_id"] == token_row(dsn, phone)["id"]
    assert row["name"] == "watch:小林的 Apple Watch script"   # only letters, digits, spaces, . and -
    # one use only
    assert client.post("/v1/web/login-code", json={"code": got["code"], "client": "watch"}).status_code == 401
    lst = client.get("/v1/account/watch-tokens", headers=H(phone)).json()["watches"]
    assert [(w["id"], w["device"], w["scopes"], w["this_phone"]) for w in lst] == \
        [(row["id"], "小林的 Apple Watch script", ["read", "write"], True)]
    other_phone = fresh_phone(dsn, ids["W"], name="ios:signin:iPhone2")
    assert client.get("/v1/account/watch-tokens", headers=H(other_phone)).json()["watches"][0]["this_phone"] is False
    with db(dsn) as c:   # the code row points at the token it became
        code_row = c.execute("SELECT * FROM web_login_codes WHERE token_id = %s", (row["id"],)).fetchone()
        assert code_row["purpose"] == "watch" and code_row["used_at"] is not None


def test_watch_codes_and_sign_in_codes_never_cross(client, world, dsn):
    ids, tok = world
    phone = fresh_phone(dsn, ids["W"])
    code = client.post("/v1/account/watch-code", headers=H(phone)).json()["code"]
    for c in ("web", "app"):   # a watch code never signs anyone in ...
        r = client.post("/v1/web/login-code", json={"code": code, "client": c})
        assert r.status_code == 401 and "set-cookie" not in r.headers and "token" not in r.text
    # ... nor moves a pending sign-in to this account (redeem-invite takes an existing account's login code)
    r = client.post("/v1/account/redeem-invite", headers=H(tok["P"]), json={"code": code})
    assert r.status_code == 401 and "token" not in r.json()
    # still unused after the wrong tries: the watch can redeem it
    assert client.post("/v1/web/login-code", json={"code": code, "client": "watch"}).status_code == 200
    # a sign-in code never gives a watch token, and stays good for signing in
    with db(dsn) as c:
        login = login_codes.create(c, ids["W"], "ipad", scopes=["read", "write"], minutes=10)
    assert client.post("/v1/web/login-code", json={"code": login, "client": "watch"}).status_code == 401
    r = client.post("/v1/web/login-code", json={"code": login, "client": "app"})
    assert r.status_code == 200 and auth.WATCH_SCOPE not in r.json()["token_scopes"]
    # expired, malformed, unknown
    code = client.post("/v1/account/watch-code", headers=H(phone)).json()["code"]
    with db(dsn) as c:
        c.execute("UPDATE web_login_codes SET expires_at = now() - interval '1 second' WHERE code_sha256 = %s",
                  (login_codes._sha(code),))
    for bad in (code, "ABCDEFGH", "ABCD-EFGH-JKLM"):
        assert client.post("/v1/web/login-code", json={"code": bad, "client": "watch"}).status_code == 401
    # a new code from the same phone voids its earlier unused one
    first = client.post("/v1/account/watch-code", headers=H(phone)).json()["code"]
    second = client.post("/v1/account/watch-code", headers=H(phone)).json()["code"]
    assert client.post("/v1/web/login-code", json={"code": first, "client": "watch"}).status_code == 401
    assert client.post("/v1/web/login-code", json={"code": second, "client": "watch"}).status_code == 200


def test_only_a_phone_token_may_ask_and_never_for_more(client, world, dsn):
    ids, tok = world
    # read-only phone -> read-only watch
    r = client.post("/v1/account/watch-code", headers=H(tok["W_ro"]))
    assert r.status_code == 200 and r.json()["scopes"] == ["read"]
    ro = client.post("/v1/web/login-code", json={"code": r.json()["code"], "client": "watch"}).json()["token"]
    assert token_row(dsn, ro)["scopes"] == [auth.WATCH_SCOPE, "read"]
    # a watch cannot mint another watch, nor any other code
    w = pair(client, fresh_phone(dsn, ids["W"]))
    for path in ("/v1/account/watch-code", "/v1/account/web-login-code", "/v1/web/ingest-token"):
        assert client.post(path, headers=H(w), json={"device": "x"}).status_code == 401, path
    # AI-client (MCP) and upload-only tokens are refused like everywhere else
    for t in (tok["W_mcp"], tok["W_ingest"]):
        assert client.post("/v1/account/watch-code", headers=H(t)).status_code == 401
    # a cookie session (the Today page in a browser) has no watch to pair: 403 bearer_only
    login = client.post("/v1/web/login", json={"token": fresh_phone(dsn, ids["W"])})
    assert login.status_code == 200
    r = client.post("/v1/account/watch-code", headers=CSRF)
    assert r.status_code == 403 and r.json()["detail"] == "bearer_only"
    client.cookies.clear()
    # a pending account: 403 account_pending
    r = client.post("/v1/account/watch-code", headers=H(tok["P"]))
    assert r.status_code == 403 and r.json()["detail"] == "account_pending"
    # a hand-made token without read / write (CLI token-create) gets nothing to pass on
    with db(dsn) as c:
        odd = auth.create_token(c, ids["W"], "odd", ["profile"])
    r = client.post("/v1/account/watch-code", headers=H(odd))
    assert r.status_code == 403 and r.json()["detail"] == "no_scope"
    # the code cannot carry more than the phone had, whatever the request says
    assert client.post("/v1/account/watch-code", headers=H(tok["W_ro"]), json={"scopes": ["write"]}).status_code == 422


def test_watch_token_is_refused_everywhere_but_the_voice_endpoints(client, world, dsn):
    ids, _ = world
    w = pair(client, fresh_phone(dsn, ids["W"]))
    for method, path in (("GET", "/v1/me"), ("GET", "/v1/web/me"), ("GET", "/v1/users/me/today"),
                         ("GET", "/v1/assistant/conversations"), ("GET", "/v1/users/me/ai-providers"),
                         ("GET", "/v1/users/me/ai-choice"), ("POST", "/v1/assistant/voice-fix"),
                         ("GET", "/v1/account/watch-tokens"), ("POST", "/v1/account/logout"),
                         ("DELETE", "/v1/users/me/meal-logs/1"), ("DELETE", "/v1/users/me/nutrition/entries/x?date=2026-11-18"),
                         ("GET", f"/v1/users/{ids['W']}/sleep"), ("GET", f"/v1/users/{ids['X']}/sleep")):
        r = client.request(method, path, headers=H(w), json={"text": "x"} if method == "POST" else None)
        assert r.status_code == 401, (method, path, r.status_code)
        assert "x-body-user" not in r.headers
    # no Today page cookie from it, no uploads, no MCP
    r = client.post("/v1/web/login", json={"token": w})
    assert r.status_code == 401 and "set-cookie" not in r.headers
    with db(dsn) as c:
        assert auth.user_for_token(c, w) is None and auth.user_for_ingest_token(c, w) is None
        assert web_session.login(c, w) is None and ai_tokens.user_for_mcp_token(c, w) is None
        assert auth.user_for_watch_token(c, w)["id"] == ids["W"]
    r = client.post("/mcp", headers={**H(w), "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def all_routes(app) -> set[tuple[str, str]]:
    """(method, path) of every HTTP route: plain routes, routers FastAPI includes lazily (effective_candidates, 0.14x),
    and, whatever the version does, every path of the OpenAPI schema."""
    out = set()
    for r in app.routes:
        for c in (r.effective_candidates() if hasattr(r, "effective_candidates") else [r]):
            if getattr(c, "methods", None) and isinstance(getattr(c, "path", None), str):
                out |= {(m, c.path) for m in c.methods}
    for path, item in app.openapi()["paths"].items():
        out |= {(m.upper(), path) for m in item if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE")}
    return {(m, p) for m, p in out if m not in ("HEAD", "OPTIONS")}


def test_route_sweep_no_other_endpoint_authenticates_the_watch(client, world, dsn):
    """Every route of the app, called with a watch token: only the four voice endpoints may authenticate it (the response
    then carries X-Body-User, obs.py). Public routes answer as they would anyone, without a user."""
    ids, _ = world
    w = pair(client, fresh_phone(dsn, ids["W"]))
    seen = 0
    sweep = TestClient(api.app, base_url="https://testserver", raise_server_exceptions=False)   # same pool, no lifespan
    routes = all_routes(api.app)
    assert VOICE <= routes
    for method, route in sorted(routes - VOICE):
        path = re.sub(r"\{user\}", "me", route)
        path = re.sub(r"\{[^}]+:path\}", "x", path)
        path = re.sub(r"\{[^}]+\}", "1", path)
        r = sweep.request(method, path, headers=H(w))
        seen += 1
        assert r.headers.get("x-body-user") is None, (method, route, r.status_code)
    assert seen > 200
    # the four voice endpoints do authenticate it
    r = sweep.get("/v1/assistant/chat/999999/events", headers=H(w))
    assert r.status_code == 404 and r.headers.get("x-body-user") == str(ids["W"])


# ---------------------------------------------------------------- revocation follows the phone
def test_signing_out_on_the_phone_ends_the_watch(client, world, dsn, asr_on):
    ids, _ = world
    phone = fresh_phone(dsn, ids["W"])
    w = pair(client, phone)
    assert client.post("/v1/assistant/transcribe", headers={**H(w), "Content-Type": "audio/wav"}, content=wav(1)).status_code == 200
    assert client.post("/v1/account/logout", headers=H(phone)).status_code == 204
    r = client.post("/v1/assistant/transcribe", headers={**H(w), "Content-Type": "audio/wav"}, content=wav(1))
    assert r.status_code == 401
    assert token_row(dsn, w)["revoked_at"] is not None


def test_the_watch_ends_however_the_phone_token_ends(client, world, dsn):
    ids, _ = world
    # revoked by name (CLI token-revoke): the watch row itself is untouched, the parent check refuses it
    phone = fresh_phone(dsn, ids["W"], name="ios:signin:old-iPhone")
    w = pair(client, phone)
    with db(dsn) as c:
        auth.revoke_tokens(c, ids["W"], name="ios:signin:old-iPhone")
        assert auth.user_for_watch_token(c, w) is None
    assert token_row(dsn, w)["revoked_at"] is None
    assert client.post("/v1/assistant/chat", headers=H(w), json={"message": "hi", "page": "watch"}).status_code == 401
    listed = client.get("/v1/account/watch-tokens", headers=H(fresh_phone(dsn, ids["W"]))).json()["watches"]
    assert token_row(dsn, w)["id"] not in [x["id"] for x in listed]
    # an unused code of a revoked phone does not redeem either
    phone = fresh_phone(dsn, ids["W"], name="ios:signin:gone")
    code = client.post("/v1/account/watch-code", headers=H(phone)).json()["code"]
    with db(dsn) as c:
        auth.revoke_tokens(c, ids["W"], name="ios:signin:gone")
    assert client.post("/v1/web/login-code", json={"code": code, "client": "watch"}).status_code == 401
    # a parent token that expires (an OAuth-style expiry) ends the watch too
    phone = fresh_phone(dsn, ids["W"])
    w = pair(client, phone)
    with db(dsn) as c:
        c.execute("UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE token_sha256 = %s",
                  (auth.hash_token(phone),))
        assert auth.user_for_watch_token(c, w) is None
    # pairing again from the same phone replaces that phone's earlier watch
    phone = fresh_phone(dsn, ids["W"])
    first = pair(client, phone)
    second = pair(client, phone)
    with db(dsn) as c:
        assert auth.user_for_watch_token(c, first) is None and auth.user_for_watch_token(c, second)["id"] == ids["W"]


def test_the_user_lists_and_revokes_watches(client, world, dsn):
    ids, tok = world
    phone = fresh_phone(dsn, ids["W"])
    w = pair(client, phone)
    wid = token_row(dsn, w)["id"]
    # someone else cannot end it, and this endpoint never ends a phone token
    assert client.delete(f"/v1/account/watch-tokens/{wid}", headers=H(tok["X"])).status_code == 404
    assert client.delete(f"/v1/account/watch-tokens/{token_row(dsn, phone)['id']}", headers=H(phone)).status_code == 404
    assert client.get("/v1/me", headers=H(phone)).status_code == 200
    # a read-only login of the same user may end it (it only takes access away)
    assert client.delete(f"/v1/account/watch-tokens/{wid}", headers=H(tok["W_ro"])).status_code == 204
    with db(dsn) as c:
        assert auth.user_for_watch_token(c, w) is None
    assert client.delete(f"/v1/account/watch-tokens/{wid}", headers=H(phone)).status_code == 404
    assert wid not in [x["id"] for x in client.get("/v1/account/watch-tokens", headers=H(phone)).json()["watches"]]
    # revoke-everything (admin) ends watches and unused watch codes
    w = pair(client, phone)
    code = client.post("/v1/account/watch-code", headers=H(phone)).json()["code"]
    with db(dsn) as c:
        x = auth.create_token(c, ids["X"], "ios:signin:x", ["read", "write"])
    wx = pair(client, x)
    xcode = client.post("/v1/account/watch-code", headers=H(x)).json()["code"]
    with db(dsn) as c:
        auth.revoke_tokens(c, ids["X"], everything=True)
        assert auth.user_for_watch_token(c, wx) is None
        assert auth.user_for_watch_token(c, w)["id"] == ids["W"]   # another user's: nothing of W's changes
    assert client.post("/v1/web/login-code", json={"code": xcode, "client": "watch"}).status_code == 401
    assert client.post("/v1/web/login-code", json={"code": code, "client": "watch"}).status_code == 200


# ---------------------------------------------------------------- voice -> text
def test_watch_audio_formats():
    """What a watch recording may look like (docs/watch-app-plan.md 4.3): 16 kHz mono PCM16 WAV, ≤ 30 s (4.7)."""
    thirty = wav(30)
    assert len(thirty) < 1_000_000 and asr.check_wav(thirty) == pytest.approx(30)
    assert asr.check_wav(wav(2, fmt_tag=0xFFFE)) == pytest.approx(2)        # WAVE_FORMAT_EXTENSIBLE header
    fllr = b"FLLR" + struct.pack("<I", 12) + bytes(12)
    lst = b"LIST" + struct.pack("<I", 4) + b"INFO"
    assert asr.check_wav(wav(2, extra=fllr + lst)) == pytest.approx(2)     # Apple's padding chunk, metadata
    assert asr.check_wav(wav(2, rate=48000)) == pytest.approx(2)
    assert len(wav(30, rate=48000, silent=True)) <= asr.MAX_BYTES
    for bad in (wav(2, fmt_tag=3, bits=32), wav(2, channels=2), wav(0.1), b"\x00" * 64):
        with pytest.raises(asr.AsrError) as e:
            asr.check_wav(bad)
        assert e.value.code in ("bad_audio", "too_short")
    with wave.open(io.BytesIO(wav(1))) as w:   # the helper writes what the recogniser container reads (deploy/asr/server.py)
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)


def test_transcribe_from_the_watch_shares_the_phones_allowance(client, world, dsn, asr_on, monkeypatch):
    ids, tok = world
    w = pair(client, fresh_phone(dsn, ids["W"]))
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage")
    A = {"Content-Type": "audio/wav"}
    r = client.post("/v1/assistant/transcribe", headers={**H(tok["W"]), **A}, content=wav(2))
    assert r.status_code == 200 and r.json()["used_today"] == 1
    r = client.post("/v1/assistant/transcribe?fix=1", headers={**H(w), **A}, content=wav(2, fmt_tag=0xFFFE))
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "午饭一碗牛肉面加一个蛋", "seconds": 2.0, "used_today": 2, "per_day": asr.ASR_PER_DAY}
    with db(dsn) as c:
        rows = c.execute("SELECT * FROM asr_usage WHERE user_id = %s ORDER BY id", (ids["W"],)).fetchall()
        assert [r["client"] for r in rows] == [None, "watch"]
        assert set(rows[0]) == {"id", "user_id", "at", "audio_ms", "proc_ms", "ok", "error", "client"}   # no audio, no text
        # the phone used up today's allowance: the watch gets the same 429
        c.execute("""INSERT INTO asr_usage (user_id, audio_ms, proc_ms, ok) SELECT %s, 1000, 100, true
                     FROM generate_series(1, %s)""", (ids["W"], asr.ASR_PER_DAY))
    r = client.post("/v1/assistant/transcribe", headers={**H(w), **A}, content=wav(2))
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limit"
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage")
    # too big for the endpoint at all: 413 before anything is read
    big = wav(asr.MAX_SECONDS + 10, rate=48000, silent=True)
    r = client.post("/v1/assistant/transcribe", headers={**H(w), **A}, content=big)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"
    assert asr_on == [len(wav(2)), len(wav(2, fmt_tag=0xFFFE))]   # only valid audio within the limits reached the recogniser


def test_transcribe_off_or_down_tells_the_watch_why(client, world, dsn, monkeypatch):
    ids, _ = world
    w = pair(client, fresh_phone(dsn, ids["W"]))
    monkeypatch.delenv("BODY_ASR_URL", raising=False)
    r = client.post("/v1/assistant/transcribe", headers={**H(w), "Content-Type": "audio/wav"}, content=wav(1))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "asr_off"


# ---------------------------------------------------------------- chat: short answers, device marked, own conversations
def _audit(dsn, uid: int, tool: str) -> dict:
    with db(dsn) as c:
        return c.execute("SELECT channel FROM assistant_audit WHERE user_id = %s AND tool = %s ORDER BY id DESC LIMIT 1",
                         (uid, tool)).fetchone()


def test_watch_chat_answers_short_and_is_marked_as_the_watch(client, world, dsn):
    ids, tok = world
    reset_ai(dsn, ids["W"])
    w = pair(client, fresh_phone(dsn, ids["W"]))
    FakeAdapter.script = [{"tool_calls": [{"id": "t1", "name": "get_scores", "args": {}}]}, {"text": "可以练"}]
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "今天适合练腿吗", "page": "watch"})
    assert r.status_code == 200, r.text
    evs = sse(r.text)
    conv = next(d for e, d in evs if e == "conversation")["id"]
    assert evs[-1][0] == "done"
    assert all(watch.PROMPT in call["system"] for call in FakeAdapter.calls) and len(FakeAdapter.calls) == 2
    watch_tools = FakeAdapter.calls[0]["tools"]   # the same tools as the phone's (below)
    assert _audit(dsn, ids["W"], "get_scores")["channel"] == "api:anthropic_api:watch"
    with db(dsn) as c:
        assert c.execute("SELECT client FROM ai_conversations WHERE id = %s", (conv,)).fetchone()["client"] == "watch"
        assert {r["client"] for r in c.execute("SELECT client FROM ai_usage WHERE conversation_id = %s", (conv,))} == {"watch"}
    # without the page field the watch's token still means the watch
    FakeAdapter.script, FakeAdapter.calls = [{"text": "好"}], []
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "然后呢", "conversation_id": conv})
    assert r.status_code == 200 and watch.PROMPT in FakeAdapter.calls[0]["system"]
    # the phone: no page -> usual answers, unmarked
    FakeAdapter.script, FakeAdapter.calls = [{"tool_calls": [{"id": "t2", "name": "get_scores", "args": {}}]}, {"text": "好"}], []
    r = client.post("/v1/assistant/chat", headers=H(tok["W"]), json={"message": "今天适合练腿吗"})
    pconv = next(d for e, d in sse(r.text) if e == "conversation")["id"]
    assert watch.PROMPT not in FakeAdapter.calls[0]["system"] and FakeAdapter.calls[0]["tools"] == watch_tools
    assert _audit(dsn, ids["W"], "get_scores")["channel"] == "api:anthropic_api"
    with db(dsn) as c:
        assert c.execute("SELECT client FROM ai_conversations WHERE id = %s", (pconv,)).fetchone()["client"] is None
        assert {r["client"] for r in c.execute("SELECT client FROM ai_usage WHERE conversation_id = %s", (pconv,))} == {None}
    # the phone may ask for watch-style answers (page "watch"), but the device is taken from the token, never the request
    FakeAdapter.script, FakeAdapter.calls = [{"text": "好"}], []
    r = client.post("/v1/assistant/chat", headers=H(tok["W"]), json={"message": "hi", "page": "watch"})
    c2 = next(d for e, d in sse(r.text) if e == "conversation")["id"]
    assert watch.PROMPT in FakeAdapter.calls[0]["system"]
    with db(dsn) as c:
        assert c.execute("SELECT client FROM ai_conversations WHERE id = %s", (c2,)).fetchone()["client"] is None
    # the phone may continue the watch's conversation ("continue on the phone", J4): its own answers again
    FakeAdapter.script, FakeAdapter.calls = [{"text": "详细说"}], []
    r = client.post("/v1/assistant/chat", headers=H(tok["W"]), json={"message": "详细说说", "conversation_id": conv})
    assert r.status_code == 200 and watch.PROMPT not in FakeAdapter.calls[0]["system"]


def test_the_watch_reaches_only_its_own_conversations(client, world, dsn):
    ids, tok = world
    reset_ai(dsn, ids["W"])
    w = pair(client, fresh_phone(dsn, ids["W"]))
    # the phone has a conversation with a pending delete
    FakeAdapter.script = [{"tool_calls": [{"id": "d1", "name": "undo_sparky_entry", "args": {
        "kind": "food", "date": "2026-11-18", "id": "abc", "meal": None, "food": None, "time": None}}]}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["W"]), json={"message": "删掉早饭"}).text)
    pconv = next(d for e, d in evs if e == "conversation")["id"]
    call_id = next(d for e, d in evs if e == "confirm")["calls"][0]["call_id"]
    FakeAdapter.calls = []
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "继续", "conversation_id": pconv, "page": "watch"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    r = client.post("/v1/assistant/chat/confirm", headers=H(w), json={"call_id": call_id, "approve": True})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    assert client.get(f"/v1/assistant/chat/{pconv}/events?after=0", headers=H(w)).status_code == 404
    assert not FakeAdapter.calls
    with db(dsn) as c:
        assert c.execute("SELECT status FROM ai_tool_calls WHERE id = %s", (call_id,)).fetchone()["status"] == "pending"
    # another user's conversation: the same 404
    with db(dsn) as c:
        xconv = c.execute("""INSERT INTO ai_conversations (user_id, title, system_prompt, client)
                             VALUES (%s, 'x', 's', 'watch') RETURNING id""", (ids["X"],)).fetchone()["id"]
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "hi", "conversation_id": xconv})
    assert r.status_code == 404
    # its own conversation resumes fine
    FakeAdapter.script = [{"text": "好"}]
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "hi", "page": "watch"})
    own = next(d for e, d in sse(r.text) if e == "conversation")["id"]
    r = client.get(f"/v1/assistant/chat/{own}/events?after=0", headers=H(w))
    assert r.status_code == 200 and sse(r.text)[-1][0] in ("done", "resync")


def test_fallback_ai_on_the_watch_keeps_its_rules(client, world, dsn):
    ids, tok = world
    reset_ai(dsn, ids["F"])
    w = pair(client, fresh_phone(dsn, ids["F"]))
    # the fallback's first-time consent happens on the phone only: the watch is told to go there
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "今天怎么样", "page": "watch"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "fallback_consent"
    v = client.get("/v1/users/me/ai-fallback", headers=H(tok["F"])).json()["consent_version"]
    assert client.post("/v1/users/me/ai-fallback/consent", headers=H(w), json={"version": v, "agree": True}).status_code == 401
    assert client.post("/v1/users/me/ai-fallback/consent", headers=H(tok["F"]), json={"version": v, "agree": True}).status_code == 200
    FakeAdapter.script = [{"tool_calls": [{"id": "g1", "name": "get_profile", "args": {"user": str(ids["X"])}},
                                          {"id": "g2", "name": "get_playbook", "args": {"name": "plan_week"}}]},
                          {"text": "还能吃 680 kcal", "usage": {"input_tokens": 1000, "output_tokens": 50}}]
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "还能吃多少", "page": "watch"})
    assert r.status_code == 200, r.text
    evs = sse(r.text)
    conv = next(d for e, d in evs if e == "conversation")
    assert conv["provider"] == fallback.KIND and conv["quota"]["quota_cny"] == fp.MONTHLY_QUOTA_CNY
    first = FakeAdapter.calls[0]
    assert "# 身体管家（精简版）" in first["system"] and watch.PROMPT in first["system"]
    assert first["tools"] == [t.name for t in toolsets.for_page(fallback.tools(), None)] + [cards.NAME]   # same whitelist
    results = {b["id"]: b for b in FakeAdapter.calls[1]["history"][-1]["content"]}
    assert '"F"' in results["g1"]["content"] and '"X"' not in results["g1"]["content"]   # own data only, as ever
    assert _audit(dsn, ids["F"], "get_profile")["channel"] == "api:system_fallback:watch"
    with db(dsn) as c:
        rows = c.execute("SELECT client, cost_cny FROM ai_usage WHERE user_id = %s", (ids["F"],)).fetchall()
        assert rows and all(r["client"] == "watch" for r in rows)
        assert sum(float(r["cost_cny"]) for r in rows) > 0   # counts against the same allowance
    # the fallback's playbooks are chosen by channel: the watch suffix keeps it the fallback
    assert scene_tools._is_fallback({"_channel": "api:system_fallback:watch"})
    assert not scene_tools._is_fallback({"_channel": "api:anthropic_api:watch"})
    # the allowance used up: the watch gets the same 402
    with db(dsn) as c:
        c.execute("""INSERT INTO ai_usage (user_id, kind, model, cost_usd, cost_cny, client)
                     VALUES (%s, %s, %s, 0, %s, 'watch')""", (ids["F"], fallback.KIND, fp.MODEL, fp.MONTHLY_QUOTA_CNY))
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "再问一句", "page": "watch"})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "quota"


def test_what_the_watch_hears_when_there_is_no_ai_for_it(client, world, dsn):
    ids, _ = world
    w = pair(client, fresh_phone(dsn, ids["S"]))   # a subscription (MCP) user: Jarvis lives in another app
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "hi", "page": "watch"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "subscription"
    with db(dsn) as c:   # the user's own key over budget
        pid = c.execute("SELECT id FROM ai_providers WHERE user_id = %s", (ids["W"],)).fetchone()["id"]
        c.execute("""INSERT INTO ai_usage (user_id, provider_id, kind, model, cost_usd) VALUES (%s, %s, 'anthropic_api', 'm', 99)""",
                  (ids["W"], pid))
    ww = pair(client, fresh_phone(dsn, ids["W"]))
    r = client.post("/v1/assistant/chat", headers=H(ww), json={"message": "hi", "page": "watch"})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "budget"
    reset_ai(dsn, ids["W"])


# ---------------------------------------------------------------- a meal by voice: written, receipt, undo
def test_meal_by_voice_receipt_and_undo(client, world, dsn):
    ids, tok = world
    reset_ai(dsn, ids["W"])
    w = pair(client, fresh_phone(dsn, ids["W"]))
    with db(dsn) as c:
        today = queries.local_today(c, ids["W"]).isoformat()
    item = {"date": today, "meal": "lunch", "time": "12:30", "carbs_g": 60, "fat_g": 12, "saturated_fat_g": 4,
            "alcohol_g": None, "sugar_g": None, "added_sugar_g": None, "fiber_g": None, "sodium_mg": None,
            "sugary_drink": None, "notes": None}
    FakeAdapter.script = [
        {"tool_calls": [{"id": "f1", "name": "log_food", "args": {"allow_duplicates": False, "items": [
            {**item, "food": "牛肉面", "amount": "1 碗", "calories": 520, "protein_g": 25},
            {**item, "food": "鸡蛋", "amount": "1 个", "calories": 78, "protein_g": 6.3, "carbs_g": 0.6, "fat_g": 5.3,
             "saturated_fat_g": 1.6}]}}]},
        {"text": "记到午餐：牛肉面 + 鸡蛋，598 kcal"}]
    r = client.post("/v1/assistant/chat", headers=H(w), json={"message": "午饭一碗牛肉面加一个蛋", "page": "watch"})
    assert r.status_code == 200, r.text
    evs = sse(r.text)
    assert any(e == "tool" and d["name"] == "log_food" and d["status"] == "done" for e, d in evs)
    card = next(d["card"] for e, d in evs if e == "card")
    assert card["type"] == "meal" and card["date"] == today and card["totals"]["calories"] == pytest.approx(598)
    (written,) = card["written"]
    assert written["tool"] == "log_food" and written["meal"] == "lunch" and written["foods"] == ["牛肉面", "鸡蛋"]
    assert written["calories"] == 598 and written["protein_g"] == 31.3 and len(written["entry_ids"]) == 2
    assert written["date"] == today and written["log_id"] is None
    assert {"kind": "send", "key": "undo_meal"} in card["actions"]
    assert evs[-1][0] == "done"
    conv = next(d for e, d in evs if e == "conversation")["id"]
    assert _audit(dsn, ids["W"], "log_food")["channel"] == "api:anthropic_api:watch"
    with db(dsn) as c:   # written once, by the watch's chat (write log says so)
        assert c.execute("SELECT count(*) AS n FROM ai_write_log WHERE user_id = %s AND tool = 'log_food' AND channel = %s",
                         (ids["W"], "api:anthropic_api:watch")).fetchone()["n"] == 2
    # undo, way 1 (the plan's: records go through the phone): the phone deletes with the written ids; the watch cannot
    e1, e2 = written["entry_ids"]
    assert client.delete(f"/v1/users/me/nutrition/entries/{e1}?date={today}", headers=H(w)).status_code == 401
    r = client.delete(f"/v1/users/me/nutrition/entries/{e1}?date={today}", headers=H(tok["W"]))
    assert r.status_code == 200, r.text
    # undo, way 2 (no phone around): say it to Jarvis; deleting waits for the confirmation, which the watch gives (M4)
    FakeAdapter.script = [{"tool_calls": [{"id": "u1", "name": "undo_sparky_entry", "args": {
        "kind": "food", "date": today, "id": e2, "meal": None, "food": None, "time": None}}]}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(w),
                          json={"message": "撤销刚才的蛋", "conversation_id": conv, "page": "watch"}).text)
    pending = next(d for e, d in evs if e == "confirm")["calls"]
    assert [p["tool"] for p in pending] == ["undo_sparky_entry"]
    FakeAdapter.script = [{"text": "已撤销"}]
    r = client.post("/v1/assistant/chat/confirm", headers=H(w), json={"call_id": pending[0]["call_id"], "approve": True})
    assert r.status_code == 200, r.text
    evs = sse(r.text)
    assert any(e == "tool" and d["name"] == "undo_sparky_entry" and d["status"] == "done" for e, d in evs)
    assert watch.PROMPT in FakeAdapter.calls[-1]["system"]   # the answer after a confirmation is short too
    with db(dsn) as c:
        left = c.execute("SELECT count(*) AS n FROM food_entries WHERE user_id = %s AND date = %s",
                         (ids["W"], today)).fetchone()["n"]
    assert left == 0


def test_a_read_only_watch_cannot_write(client, world, dsn):
    ids, tok = world
    reset_ai(dsn, ids["W"])
    ro = pair(client, tok["W_ro"])
    FakeAdapter.script = [{"tool_calls": [{"id": "x1", "name": "log_weight", "args": {"date": None, "weight_kg": 80.0}}]},
                          {"text": "没记"}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(ro), json={"message": "体重 80", "page": "watch"}).text)
    assert ("tool", {"name": "log_weight", "status": "error"}) in evs
    FakeAdapter.script = [{"tool_calls": [{"id": "x2", "name": "undo_sparky_entry", "args": {
        "kind": "food", "date": "2026-11-18", "id": "abc", "meal": None, "food": None, "time": None}}]}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(ro), json={"message": "删掉", "page": "watch"}).text)
    confirm = [d for e, d in evs if e == "confirm"]
    if confirm:   # a read-only login cannot approve a pending delete either
        r = client.post("/v1/assistant/chat/confirm", headers=H(ro), json={"call_id": confirm[0]["calls"][0]["call_id"], "approve": True})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "read_only"
    else:
        assert ("tool", {"name": "undo_sparky_entry", "status": "error"}) in evs


def test_watch_prompt_stays_short_and_chinese():
    """AI-facing text in Chinese (project convention), with the limits from params, and small: it rides on every call."""
    from body_engine.ai import params as P
    assert re.search(r"[一-鿿]", watch.PROMPT)
    assert f"{P.WATCH_MAX_SENTENCES} 句" in watch.PROMPT and f"{P.WATCH_MAX_CHARS_ZH} 个字" in watch.PROMPT
    assert len(watch.PROMPT) < 600
    assert watch.for_request({"id": 1}, "watch")["short_answer"] and not watch.short(watch.for_request({"id": 1}, "today"))
    assert watch.short({"id": 1, "client": "watch"}) and watch.client_of({"id": 1}) is None
    assert watch.channel("api:openai_api", {"client": "watch"}) == "api:openai_api:watch"
    assert watch_tokens.device_label("  ") == account_params.WATCH_DEVICE_DEFAULT
    assert len(watch_tokens.device_label("x" * 99)) == account_params.WATCH_DEVICE_MAX_LEN
    assert timedelta(minutes=account_params.WATCH_CODE_MINUTES) <= timedelta(minutes=10)
