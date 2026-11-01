"""Open sign-up (account/open_signup.py, flag open_signup): new accounts open without an invite code below the account cap,
waiting accounts are opened on their next request, the cap / per-IP / identity limits put people back on the invite step,
codes still record the inviter, and the admin views (status, list, fallback-AI cost).

Uses its own test database (body_test_open_signup), dropped and recreated on every run; skipped if Postgres is unreachable.
Nothing leaves the machine: e-mail goes to the console outbox, Apple / OW / SparkyFitness are fakes (from test_account).
"""
import base64

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api
from body_engine.account import api as account_api, invites, mailer, open_signup, open_signup_cli, params, provision, signin
from body_engine.account import stats as user_stats
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.profile import consent
from body_engine.rollout import flags
from body_engine.web import login_limit
from test_account import AUD, FakeOW, FakeSparky, _jwks, apple_token

TEST_DB = "body_test_open_signup"


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
        conn.execute("INSERT INTO users (display_name, role) VALUES ('Owner', 'owner')")
    return dsn


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    login_limit.reset()
    signin.reset_jwks_cache()
    mailer.OUTBOX.clear()
    monkeypatch.setenv("BODY_MAIL_MODE", "console")
    monkeypatch.setenv("BODY_APPLE_AUDIENCES", AUD)
    master = tmp_path / "ai_master_key"
    master.write_text(base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("BODY_AI_MASTER_KEY_FILE", str(master))
    monkeypatch.setattr(signin, "_fetch_jwks", _jwks)
    ow, sparky = FakeOW(), FakeSparky()
    monkeypatch.setattr(provision, "OWClient", lambda **kw: ow)
    monkeypatch.setattr(provision, "SparkySignup", lambda **kw: sparky)
    monkeypatch.setattr(account_api, "start_provisioning", account_api._provision_in_background)   # synchronous in tests
    monkeypatch.setattr(params, "OPEN_SIGNUP_PER_IP_DAY", 1000)   # every test sign-up comes from the same test client IP
    flags.invalidate()
    yield {"ow": ow, "sparky": sparky}
    login_limit.reset()
    flags.invalidate()


def _flag(dsn, state):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, params.OPEN_SIGNUP_FLAG, state, actor="test")


def _active(dsn) -> int:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return open_signup.active_count(conn)


def _signup(client, email, kind="app"):
    client.cookies.clear()
    assert client.post("/v1/auth/email/start", json={"email": email}).status_code == 200
    code = mailer.OUTBOX[-1][1].rsplit(" ", 1)[1]
    r = client.post("/v1/auth/email/verify", json={"email": email, "code": code, "client": kind, "device": "iPhone"})
    assert r.status_code == 200, r.text
    out = r.json()
    if kind == "app":
        out["h"] = {"Authorization": f"Bearer {out['token']}"}
    return out


def _batch(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return invites.create_batch(conn, 1, "test")["codes"][0]


def test_flag_is_declared_and_off_by_default(client, dsn):
    assert params.OPEN_SIGNUP_FLAG in flags.FLAGS
    s = _signup(client, "off@example.com")
    assert s["status"] == "pending"
    acct = client.get("/v1/account", headers=s["h"]).json()
    assert acct["invite_required"] is True and acct["invite_optional"] is False
    assert client.get("/v1/auth/methods").json()["open_signup"] is False
    _flag(dsn, "cohort")   # only `all` turns it on
    assert client.get("/v1/account", headers=s["h"]).json()["status"] == "pending"
    _flag(dsn, "off")


def test_new_account_opens_like_a_redeemed_code(client, dsn, _env):
    _flag(dsn, "all")
    assert client.get("/v1/auth/methods").json()["open_signup"] is True
    s = _signup(client, "open-one@example.com")
    assert s["status"] == "active" and s["new_user"] is True and s["me"]["status"] == "active"
    uid = s["me"]["id"]
    acct = client.get("/v1/account", headers=s["h"]).json()
    assert acct["invite_required"] is False and acct["invite_optional"] is True
    assert acct["provisioning"]["state"] == "done"
    assert client.get("/v1/users/me/journal/frequent", headers=s["h"]).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # the same as invites.redeem + the redeem-invite endpoint: active, provisioned (OW, SparkyFitness, journal, memory)
        u = conn.execute("SELECT * FROM users WHERE id = %s", (uid,)).fetchone()
        assert u["status"] == "active" and u["ow_user_id"] and u["sparky_user_id"] == f"sparky-w2w-u{uid}"
        p = conn.execute("SELECT * FROM user_provisioning WHERE user_id = %s", (uid,)).fetchone()
        assert p["state"] == "done" and set(p["steps"]) == set(provision.STEPS)
        assert conn.execute("SELECT count(*) AS n FROM journal_items WHERE user_id = %s", (uid,)).fetchone()["n"] > 0
        row = conn.execute("SELECT * FROM open_signups WHERE user_id = %s", (uid,)).fetchone()
        assert (row["decision"], row["via"]) == ("activated", "signup") and row["activated_at"] and len(row["ip_sha256"]) == 64
        assert conn.execute("SELECT hint FROM open_signup_identities WHERE provider = 'email'").fetchall() == [
            {"hint": "o***@e***.com"}]
        # the consent still comes first: nothing about it changed
        assert consent.needs_consent(conn, uid) and u["consent_version"] is None
    assert len(_env["ow"].created) == 1 and len(_env["sparky"].accounts) == 1
    # Today page (cookie) sign-in: the page boots as active, no invite step
    w = _signup(client, "open-web@example.com", "web")
    assert w["status"] == "active" and client.get("/v1/web/me").json()["me"]["status"] == "active"
    client.cookies.clear()
    # Sign in with Apple
    r = client.post("/v1/auth/apple", json={"identity_token": apple_token(sub="open-apple", email="relay-x@example.com"),
                                            "nonce": "raw-nonce", "client": "app"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    _flag(dsn, "off")


def test_waiting_account_opens_on_next_request(client, dsn):
    _flag(dsn, "off")
    s = _signup(client, "waiting@example.com")
    assert s["status"] == "pending"
    assert client.get("/v1/users/me/journal/frequent", headers=s["h"]).status_code == 403
    _flag(dsn, "all")
    # any request: the app's resume (GET /v1/account) or a normal endpoint
    r = client.get("/v1/account", headers=s["h"]).json()
    assert r["status"] == "active" and r["invite_required"] is False and r["provisioning"]["state"] == "done"
    assert client.get("/v1/users/me/journal/frequent", headers=s["h"]).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT via, ip_sha256 FROM open_signups WHERE user_id = %s", (s["me"]["id"],)).fetchone() == {
            "via": "pending", "ip_sha256": None}
    _flag(dsn, "off")


def test_cap_puts_new_accounts_back_on_the_invite_step(client, dsn, monkeypatch):
    _flag(dsn, "all")
    monkeypatch.setattr(params, "OPEN_SIGNUP_MAX_ACTIVE", _active(dsn) + 1)
    assert _signup(client, "last-place@example.com")["status"] == "active"
    assert client.get("/v1/auth/methods").json()["open_signup"] is False
    full = _signup(client, "too-late@example.com")
    assert full["status"] == "pending"
    assert client.get("/v1/account", headers=full["h"]).json()["invite_required"] is True
    # codes still work at the cap
    r = client.post("/v1/account/redeem-invite", headers=full["h"], json={"code": _batch(dsn)})
    assert r.status_code == 200 and r.json()["status"] == "active"
    # demo accounts do not count: put one in the excluded cohort and a place opens again
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.cohort_add(conn, full["me"]["id"], params.OPEN_SIGNUP_EXCLUDE_COHORTS[0], actor="test")
        flags.cohort_add(conn, 1, params.OPEN_SIGNUP_EXCLUDE_COHORTS[0], actor="test")
    waiting = _signup(client, "after-demo@example.com")
    assert waiting["status"] == "active"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for uid in (full["me"]["id"], 1):
            flags.cohort_remove(conn, uid, params.OPEN_SIGNUP_EXCLUDE_COHORTS[0], actor="test")
    _flag(dsn, "off")


def test_per_ip_limit_holds_the_account_for_good(client, dsn, monkeypatch):
    _flag(dsn, "all")
    monkeypatch.setattr(params, "OPEN_SIGNUP_PER_IP_DAY", 0)   # every earlier sign-up from this test IP is over it
    s = _signup(client, "same-ip@example.com")
    assert s["status"] == "pending"
    monkeypatch.setattr(params, "OPEN_SIGNUP_PER_IP_DAY", 100)
    assert client.get("/v1/account", headers=s["h"]).json()["status"] == "pending"   # held: not opened later
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT decision FROM open_signups WHERE user_id = %s", (s["me"]["id"],)).fetchone()["decision"] \
            == "ip_limit"
        assert [r["user_id"] for r in open_signup.list_opened(conn, all_rows=True) if r["decision"] == "ip_limit"] == [s["me"]["id"]]
    assert client.post("/v1/account/redeem-invite", headers=s["h"], json={"code": _batch(dsn)}).json()["status"] == "active"
    # the real rule: N per IP in 24 hours, other addresses unaffected, the IP kept only as a hash
    monkeypatch.setattr(params, "OPEN_SIGNUP_PER_IP_DAY", 2)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = [signin.find_or_create_user(conn, "email", f"ip{i}@example.com", f"ip{i}@example.com", True)[0]["id"]
               for i in range(4)]
        got = [open_signup.try_activate(conn, uid, ip=ip, new=True)
               for uid, ip in zip(ids, ["203.0.113.9", "203.0.113.9", "203.0.113.9", "198.51.100.7"])]
        assert got == ["activated", "activated", "ip_limit", "activated"]
        assert not conn.execute("SELECT 1 FROM open_signups WHERE ip_sha256 LIKE '%203%'").fetchone()
        assert open_signup.try_activate(conn, ids[2]) == "ip_limit"   # held for good, also without an IP
        conn.rollback()
    _flag(dsn, "off")


def test_identity_opens_one_free_account_ever(client, dsn):
    _flag(dsn, "all")
    s = _signup(client, "again@example.com")
    assert s["status"] == "active"
    r = client.request("DELETE", "/v1/account", headers=s["h"], json={"confirm": "DELETE"})
    assert r.status_code == 200, r.text
    login_limit.reset()
    again = _signup(client, "again@example.com")
    assert again["new_user"] is True and again["status"] == "pending"
    # and no invite code opens it either (same rule as codes)
    assert client.post("/v1/account/redeem-invite", headers=again["h"], json={"code": _batch(dsn)}).status_code == 401
    _flag(dsn, "off")


def test_store_signup_has_no_cap_but_keeps_the_other_limits(client, dsn, monkeypatch, capsys):
    """flag store_signup (App Store): every new account opens, also past OPEN_SIGNUP_MAX_ACTIVE; per-IP and identity rules stay."""
    assert params.STORE_SIGNUP_FLAG in flags.FLAGS
    m = client.get("/v1/auth/methods").json()
    assert m["store_signup"] is False and m["invite_required"] is True    # default off
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, params.STORE_SIGNUP_FLAG, "cohort", actor="test")   # only `all` turns it on
    assert _signup(client, "store-cohort@example.com")["status"] == "pending"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, params.STORE_SIGNUP_FLAG, "all", actor="test")
    monkeypatch.setattr(params, "OPEN_SIGNUP_MAX_ACTIVE", 0)            # the open_signup cap does not apply
    m = client.get("/v1/auth/methods").json()
    assert m["store_signup"] is True and m["open_signup"] is True and m["invite_required"] is False
    s = _signup(client, "store-one@example.com")
    assert s["status"] == "active"
    acct = client.get("/v1/account", headers=s["h"]).json()
    assert acct["invite_required"] is False and acct["invite_optional"] is True   # a code can still be added
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT mode FROM open_signups WHERE user_id = %s", (s["me"]["id"],)).fetchone()["mode"] == "store"
        st = open_signup.status(conn)
        assert st["store"] is True and st["open"] is True and st["opened_store"] >= 1
        assert next(u for u in user_stats.collect(conn, 30)["users"] if u["id"] == s["me"]["id"])["method"] == "store"
        # the per-IP limit still holds
        monkeypatch.setattr(params, "OPEN_SIGNUP_PER_IP_DAY", 1)
        ids = [signin.find_or_create_user(conn, "email", f"store-ip{i}@example.com", f"store-ip{i}@example.com", True)[0]["id"]
               for i in range(2)]
        assert [open_signup.try_activate(conn, uid, ip="203.0.113.77", new=True) for uid in ids] == ["activated", "ip_limit"]
        conn.rollback()
    # an identity that already opened an account is not opened again without a code
    r = client.request("DELETE", "/v1/account", headers=s["h"], json={"confirm": "DELETE"})
    assert r.status_code == 200, r.text
    login_limit.reset()
    assert _signup(client, "store-one@example.com")["status"] == "pending"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        open_signup_cli.run(conn, open_signup_cli.argparse.Namespace(open_signup_cmd="status", refresh=False, json=False))
        flags.set_flag(conn, params.STORE_SIGNUP_FLAG, "off", actor="test")
    assert "App Store" in capsys.readouterr().out


def test_codes_still_record_the_inviter(client, dsn, monkeypatch):
    monkeypatch.setenv(params.REFERRALS_ENV, "1")
    inviter = _signup(client, "inviter-open@example.com")
    client.post("/v1/account/redeem-invite", headers=inviter["h"], json={"code": _batch(dsn)})
    ref = client.get("/v1/users/me/invite-code", headers=inviter["h"]).json()["code"]
    _flag(dsn, "all")
    friend = _signup(client, "friend-open@example.com")
    assert friend["status"] == "active"
    r = client.post("/v1/account/redeem-invite", headers=friend["h"], json={"code": ref})
    assert r.status_code == 200 and r.json()["status"] == "active"
    fid = friend["me"]["id"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        red = conn.execute("SELECT * FROM invite_redemptions WHERE user_id = %s", (fid,)).fetchone()
        assert red["inviter_user_id"] == inviter["me"]["id"]
        assert conn.execute("SELECT 1 FROM invite_rewards WHERE redemption_id = %s", (red["id"],)).fetchone()
        assert conn.execute("SELECT 1 FROM invite_redemption_identities WHERE redemption_id = %s", (red["id"],)).fetchone()
        uses = conn.execute("SELECT uses FROM invite_codes WHERE id = %s", (red["code_id"],)).fetchone()["uses"]
    # one code only: the window closes, a second code is not consumed
    assert client.get("/v1/account", headers=friend["h"]).json()["invite_optional"] is False
    other = _batch(dsn)
    assert client.post("/v1/account/redeem-invite", headers=friend["h"], json={"code": other}).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM invite_redemptions WHERE user_id = %s", (fid,)).fetchone()["n"] == 1
        assert conn.execute("SELECT uses FROM invite_codes WHERE id = %s", (red["code_id"],)).fetchone()["uses"] == uses
        # outside the window an open-signup account is left alone like any active one
        conn.execute("UPDATE open_signups SET activated_at = now() - make_interval(days => %s) WHERE user_id = %s",
                     (params.OPEN_SIGNUP_CODE_DAYS + 1, fid))
        assert not open_signup.code_window(conn, fid)
    _flag(dsn, "off")


def test_admin_status_list_cost_and_stats(client, dsn, monkeypatch, capsys):
    from body_engine.ai import fallback, fallback_params as fp
    _flag(dsn, "all")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        s = open_signup.status(conn)
        assert s["on"] and s["cap"] == params.OPEN_SIGNUP_MAX_ACTIVE and s["active"] == open_signup.active_count(conn)
        assert s["opened"] >= 4 and s["held_ip_limit"] == 1 and s["per_ip_day"] == params.OPEN_SIGNUP_PER_IP_DAY
        f = s["fallback"]
        members = conn.execute("SELECT count(*) AS n FROM users WHERE status = 'active' AND role <> 'owner'").fetchone()["n"]
        assert f["month_max_now_cny"] == pytest.approx(members * fp.MONTHLY_QUOTA_CNY)
        assert f["month_max_at_cap_cny"] == pytest.approx(f["month_max_now_cny"] + s["room"] * fp.MONTHLY_QUOTA_CNY)
        assert f["runway_months_now"] is None   # no balance known yet
        conn.execute("""INSERT INTO ai_fallback_state (id, balance_cny) VALUES (1, 302) ON CONFLICT (id)
                        DO UPDATE SET balance_cny = 302""")
        f = open_signup.fallback_cost(conn)
        assert f["runway_months_at_cap"] == pytest.approx(round(300 / f["month_max_at_cap_cny"], 1))
        text = open_signup_cli.render_status(open_signup.status(conn))
        assert "开放注册：开着" in text and "满额" in text and "¥302.00" in text
        rows = open_signup.list_opened(conn)
        assert rows and all(r["decision"] == "activated" for r in rows)
        assert all("@example.com" not in i["hint"] for r in rows for i in r["identities"])   # masked
        assert open_signup.list_opened(conn, days=1)
        assert fallback.admin_status(conn, refresh=False)["open_signup"]["on"] is True
        st = user_stats.collect(conn, 7)
        opened_ids = {r["user_id"] for r in rows}
        assert {u["method"] for u in st["users"] if u["id"] in opened_ids} == {"open"}
        assert all(u["activated_at"] for u in st["users"] if u["id"] in opened_ids)
    _flag(dsn, "off")
