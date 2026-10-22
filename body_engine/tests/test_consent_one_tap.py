"""One-tap consent (rollout flag consent_one_tap; profile/consent.py, profile/api.py, ai/fallback.py, migration 665).

One 「开始使用」 tap records the data consent (method one_tap, current version, the page's language) and the Jarvis
(fallback AI) consent (method one_tap, current CONSENT_VERSION) in one transaction. Covered here: the flag off keeps the old
behaviour; `--state all` reaches people who never consented (they are in no cohort); the data gate (imports, history
import, raw data) stays shut until the tap and opens after it; all or nothing; no fallback on the server = the data consent
alone; reconfirming is one tap and never re-grants a withdrawn Jarvis consent; owner and paper consent unchanged; a new
account from a circle activation code joins its circle and sees the Friends page when `friends` asks for 2026-10-22.2;
the one-tap Jarvis sheet; the admin status shows the method.

Test database body_test_consent_one_tap (BODY_TEST_DB_NAME_ONE_TAP), recreated on every run; skipped without Postgres.
No paid API: DeepSeek's balance endpoint is monkeypatched and the key is a temp file. Names and codes are made up.
"""
import os
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.account import api as account_api, signin
from body_engine.ai import fallback, fallback_cli
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import admin, api as friends_api, codes, params as FP
from body_engine.profile import consent
from body_engine.rawdata import ingest as raw_ingest
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_ONE_TAP", "body_test_consent_one_tap")
FLAG = "consent_one_tap"
BATCH = "00000000-0000-4000-8000-0000000000a1"   # an empty raw-data batch (rawdata/ingest.py)


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_accept", ""):
        pytest.exit("the test DB must not be a real DB")
    try:
        admin_conn = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin_conn:
        admin_conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin_conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def key_file(tmp_path_factory):
    f = tmp_path_factory.mktemp("one_tap") / "deepseek_api_key"
    f.write_text("test-fallback-key-not-real-0000\n")
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(f))
    yield f
    mp.undo()


@pytest.fixture(scope="module")
def client(dsn, key_file):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


@pytest.fixture(autouse=True)
def clean(dsn, monkeypatch):
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 50.0)
    monkeypatch.setattr(account_api, "start_provisioning", lambda uid: None)   # no OW / SparkyFitness here
    friends_api._failures._events.clear()
    from body_engine.web import login_limit
    for lim in vars(login_limit).values():
        if isinstance(lim, login_limit.Limiter):
            lim._events.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM circles")
        conn.execute("DELETE FROM shares")
        conn.execute("DELETE FROM users")
        conn.execute("UPDATE ai_fallback_state SET enabled = true, balance_cny = NULL, balance_checked_at = NULL, balance_error = NULL")
        flags.set_flag(conn, FLAG, "off", actor="test")
        flags.set_flag(conn, "friends", "off", clear_min_consent=True, actor="test")
    yield


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def h(tok, **extra):
    return {"Authorization": f"Bearer {tok}", **extra}


def person(conn, name, role="member", agreed: str | None = None):
    """A user and a read/write token; agreed = a consent version given in the app (None = never consented)."""
    uid = conn.execute("INSERT INTO users (display_name, role, timezone) VALUES (%s, %s, 'Australia/Sydney') RETURNING id",
                       (name, role)).fetchone()["id"]
    if agreed:
        conn.execute("UPDATE users SET consent_at = now(), consent_version = %s WHERE id = %s", (agreed, uid))
    return uid, auth.create_token(conn, uid, "app", ["read", "write"])


def one_tap(client, tok, c=None, **extra):
    c = c or client.get("/v1/consent", headers=h(tok)).json()
    body = {"version": c["version"], "agree": True, "language": c["language"], "method": "one_tap",
            **({"ai_version": c["ai"]["version"]} if c.get("ai") else {}), **extra}
    return client.post("/v1/users/me/consent", json=body, headers=h(tok, **{"User-Agent": "Watch2Whoop test"}))


def rows(conn, table, uid):
    return conn.execute(f"SELECT * FROM {table} WHERE user_id = %s ORDER BY id", (uid,)).fetchall()


# ---------------------------------------------------------------- flag off: as before
def test_flag_off_is_the_old_sheet(client, dsn):
    with db(dsn) as conn:
        uid, tok = person(conn, "Nia")
    st = client.get("/v1/users/me/profile", headers=h(tok)).json()["consent"]
    assert st["required"] is True and st["one_tap"] is False and st["accepted_method"] is None
    c = client.get("/v1/consent", headers=h(tok)).json()
    assert c["ai"] is None and c["version"] == consent.version()
    assert one_tap(client, tok, c).status_code == 404
    v = client.get("/v1/users/me/ai-fallback", headers=h(tok)).json()["consent_version"]
    assert client.post("/v1/users/me/ai-fallback/consent", headers=h(tok),
                       json={"version": v, "agree": True, "method": "one_tap"}).status_code == 404
    with db(dsn) as conn:
        assert consent.needs_consent(conn, uid) and not rows(conn, "user_consents", uid) and not rows(conn, "ai_fallback_consents", uid)
    # the tick-box consent works as before and records only the data consent
    r = client.post("/v1/users/me/consent", json={"version": c["version"], "agree": True}, headers=h(tok))
    assert r.status_code == 200 and r.json()["consent"]["accepted_method"] == "app"
    with db(dsn) as conn:
        assert [x["method"] for x in rows(conn, "user_consents", uid)] == ["app"] and not rows(conn, "ai_fallback_consents", uid)


def test_methods_allowed_by_the_database(dsn):
    with db(dsn) as conn:
        uid, _ = person(conn, "Ola")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO user_consents (user_id, version, method) VALUES (%s, 'x', 'tap')", (uid,))
        conn.execute("INSERT INTO ai_fallback_consents (user_id, version) VALUES (%s, 'x')", (uid,))
        assert rows(conn, "ai_fallback_consents", uid)[0]["method"] == "app"   # rows from before migration 665
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO ai_fallback_consents (user_id, version, method) VALUES (%s, 'x', 'paper')", (uid,))
        with pytest.raises(ValueError):
            consent.accept(conn, uid, consent.version(), "tap")


def test_state_all_reaches_people_who_never_consented(dsn):
    """New users are in no cohort and have agreed to nothing: only `all` (no min consent) turns it on for them."""
    with db(dsn) as conn:
        new, _ = person(conn, "Pia")
        beta, _ = person(conn, "Bo", agreed=consent.version())
        owner, _ = person(conn, "Own", role="owner")
        flags.cohort_add(conn, beta, actor="test")
        flags.set_flag(conn, FLAG, "cohort", actor="test")
        assert flags.enabled(conn, beta, FLAG) and not flags.enabled(conn, new, FLAG)
        flags.set_flag(conn, FLAG, "all", actor="test")
        assert flags.enabled(conn, new, FLAG) and FLAG in flags.enabled_keys(conn, new)
        assert consent.status(conn, new)["one_tap"] is True and consent.status(conn, new)["required"] is True
        assert consent.status(conn, owner)["one_tap"] is False                     # the owner is never asked
        assert FLAG not in flags.consent_versions_needed(conn, new) and consent.required_version(conn, new) == consent.version()


# ---------------------------------------------------------------- the tap
def test_one_tap_records_both_and_opens_the_gate(client, dsn, tmp_path, monkeypatch):
    with db(dsn) as conn:
        flags.set_flag(conn, FLAG, "all", actor="test")
        uid, tok = person(conn, "Quin")
    # before the tap: nothing is processed (imports / scores skip, history import refused, raw data refused)
    with db(dsn) as conn:
        assert consent.gate(conn, uid) and consent.needs_consent(conn, uid)
        with pytest.raises(PermissionError):
            raw_ingest.ingest(conn, uid, {"batch_id": BATCH})
    monkeypatch.setenv("BODY_IMPORT_DIR", str(tmp_path))
    monkeypatch.setenv("BODY_IMPORT_MIN_FREE_MB", "1")
    assert client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 10},
                       headers=h(tok)).status_code == 403
    assert client.get("/v1/users/me/ai-fallback", headers=h(tok)).json()["reason"] == "needs_consent"

    p = client.get("/v1/users/me/profile", headers=h(tok)).json()["consent"]
    assert p["required"] is True and p["one_tap"] is True
    c = client.get("/v1/consent", headers=h(tok, **{"X-Body-Lang": "en"})).json()
    assert c["language"] == "en" and c["version"] == consent.version()
    assert c["ai"]["version"] == fallback.CONSENT_VERSION and c["ai"]["translated"] is True
    assert len(c["ai"]["points"]) == len(fallback.CONSENT_POINTS) and any("DeepSeek" in x for x in c["ai"]["points"])
    assert client.get("/v1/consent", headers=h(tok)).json()["ai"]["points"][0].startswith("托底 AI")

    r = one_tap(client, tok, c)
    assert r.status_code == 200, r.text
    st = r.json()["consent"]
    assert st["required"] is False and st["reconfirm"] is False
    assert (st["accepted_version"], st["accepted_method"], st["accepted_language"]) == (consent.version(), "one_tap", "en")
    with db(dsn) as conn:
        [u] = rows(conn, "user_consents", uid)
        assert (u["version"], u["method"], u["language"], u["user_agent"]) == (consent.version(), "one_tap", "en", "Watch2Whoop test")
        [a] = rows(conn, "ai_fallback_consents", uid)
        assert (a["version"], a["method"], a["revoked_at"], a["user_agent"]) == \
            (fallback.CONSENT_VERSION, "one_tap", None, "Watch2Whoop test")
        # after the tap: the same as after the electronic consent
        assert consent.gate(conn, uid) is None and not consent.needs_consent(conn, uid)
        assert consent.agreed_at_least(conn, uid, raw_ingest.RAW_CONSENT_VERSION)
        raw_ingest.ingest(conn, uid, {"batch_id": BATCH})
        fallback.require_available(conn, uid)                                # Jarvis does not ask again
    r = client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 10}, headers=h(tok))
    assert r.status_code == 200
    client.delete(f"/v1/users/me/history-import/{r.json()['job']['id']}", headers=h(tok))
    f = client.get("/v1/users/me/ai-fallback", headers=h(tok)).json()
    assert f["consent"] is True and f["available"] is True and f["reason"] is None
    assert client.get("/v1/consent", headers=h(tok)).json()["ai"] is None   # nothing left to agree to
    # tapping again (e.g. a second device) adds no second Jarvis consent
    assert one_tap(client, tok, c).status_code == 200
    with db(dsn) as conn:
        assert len(rows(conn, "ai_fallback_consents", uid)) == 1
        # the admin status (ai-fallback status, read by the server doctor) shows how it was given
        s = fallback.admin_status(conn, refresh=False)
        me = next(x for x in s["users"] if x["user_id"] == uid)
        assert me["consent"] is True and me["consent_method"] == "one_tap"
        assert "一键" in fallback_cli._table(s)


def test_one_tap_is_all_or_nothing(client, dsn):
    with db(dsn) as conn:
        flags.set_flag(conn, FLAG, "all", actor="test")
        uid, tok = person(conn, "Rex")
    c = client.get("/v1/consent", headers=h(tok)).json()
    assert one_tap(client, tok, c, ai_version="2000-01-01").status_code == 409     # the Jarvis terms changed meanwhile
    assert one_tap(client, tok, c, version="2000-01-01").status_code == 409        # the consent form changed meanwhile
    with db(dsn) as conn:
        assert consent.needs_consent(conn, uid)
        assert not rows(conn, "user_consents", uid) and not rows(conn, "ai_fallback_consents", uid)
    # only for yourself, and not with a read-only login
    with db(dsn) as conn:
        other, _ = person(conn, "Sue")
        ro = auth.create_token(conn, uid, "ro", ["read"])
    assert client.post(f"/v1/users/{other}/consent", headers=h(tok),
                       json={"version": c["version"], "agree": True, "method": "one_tap"}).status_code == 403
    assert one_tap(client, ro, c).status_code == 403


def test_without_the_fallback_only_the_data_consent(client, dsn, monkeypatch, tmp_path):
    with db(dsn) as conn:
        flags.set_flag(conn, FLAG, "all", actor="test")
        uid, tok = person(conn, "Tia")
        zero, ztok = person(conn, "Zed")
        fallback.set_quota(conn, zero, 0)                     # may not use the fallback
    assert client.get("/v1/consent", headers=h(ztok)).json()["ai"] is None
    monkeypatch.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(tmp_path / "missing"))   # this server has no fallback AI
    c = client.get("/v1/consent", headers=h(tok)).json()
    assert c["ai"] is None
    r = one_tap(client, tok, c, ai_version=fallback.CONSENT_VERSION)   # an older page still sends it: not recorded
    assert r.status_code == 200, r.text
    with db(dsn) as conn:
        assert [x["method"] for x in rows(conn, "user_consents", uid)] == ["one_tap"]
        assert not rows(conn, "ai_fallback_consents", uid) and consent.gate(conn, uid) is None


def test_reconfirm_is_one_tap_and_never_regrants_a_withdrawal(client, dsn):
    old = "2026-10-18"
    with db(dsn) as conn:
        flags.set_flag(conn, FLAG, "all", actor="test")
        gone, gtok = person(conn, "Uma", agreed=old)            # agreed to Jarvis once, then withdrew
        fallback.accept_consent(conn, gone, fallback.CONSENT_VERSION)
        fallback.revoke_consent(conn, gone)
        stale, stok = person(conn, "Vic", agreed=old)           # agreed to older Jarvis terms
        conn.execute("INSERT INTO ai_fallback_consents (user_id, version) VALUES (%s, '2026-10-18')", (stale,))
        for u in (gone, stale):
            st = consent.status(conn, u)
            assert st["reconfirm"] is True and st["required"] is False and st["one_tap"] is True
            assert consent.gate(conn, u) is None                # reconfirming never blocks data processing
    c = client.get("/v1/consent", headers=h(gtok)).json()
    assert c["ai"] is None                                      # the withdrawal stands
    r = one_tap(client, gtok, c, ai_version=fallback.CONSENT_VERSION)
    assert r.status_code == 200 and r.json()["consent"]["reconfirm"] is False
    assert r.json()["consent"]["accepted_version"] == consent.version()
    c = client.get("/v1/consent", headers=h(stok)).json()
    assert c["ai"]["version"] == fallback.CONSENT_VERSION
    assert one_tap(client, stok, c).status_code == 200
    with db(dsn) as conn:
        assert [x["revoked_at"] is not None for x in rows(conn, "ai_fallback_consents", gone)] == [True]
        assert fallback.consent(conn, gone) is None
        assert [x["method"] for x in rows(conn, "ai_fallback_consents", stale)] == ["app", "one_tap"]
        assert fallback.consent(conn, stale)["method"] == "one_tap"


def test_owner_and_paper_consent_unchanged(client, dsn):
    with db(dsn) as conn:
        flags.set_flag(conn, FLAG, "all", actor="test")
        owner, otok = person(conn, "Own", role="owner")
        paper, ptok = person(conn, "Pat")
        consent.accept(conn, paper, "paper", "paper", at=date(2026, 10, 20))   # recorded by the admin (onboarding)
    o = client.get("/v1/users/me/profile", headers=h(otok)).json()["consent"]
    assert (o["required"], o["reconfirm"], o["one_tap"]) == (False, False, False)
    assert client.get("/v1/consent", headers=h(otok)).json()["ai"] is None   # the owner needs no Jarvis consent
    p = client.get("/v1/users/me/profile", headers=h(ptok)).json()["consent"]
    assert (p["required"], p["reconfirm"], p["accepted_method"]) == (False, False, "paper")   # never asked again in the app
    with db(dsn) as conn:
        assert consent.gate(conn, owner) is None and consent.gate(conn, paper) is None
        assert not consent.agreed_at_least(conn, paper, FP.CONSENT_VERSION)   # paper still does not reach the circle clause


def test_jarvis_sheet_is_one_tap_too(client, dsn):
    """People who consented before the flag (or whose Jarvis terms were revised) agree to Jarvis with one tap as well."""
    with db(dsn) as conn:
        uid, tok = person(conn, "Wen", agreed=consent.version())
    v = client.get("/v1/users/me/ai-fallback", headers=h(tok)).json()
    assert v["reason"] == "fallback_consent"
    body = {"version": v["consent_version"], "agree": True, "method": "one_tap"}
    assert client.post("/v1/users/me/ai-fallback/consent", headers=h(tok), json=body).status_code == 404   # flag off
    with db(dsn) as conn:
        flags.set_flag(conn, FLAG, "all", actor="test")
    r = client.post("/v1/users/me/ai-fallback/consent", headers=h(tok), json=body)
    assert r.status_code == 200 and r.json()["consent"] is True
    assert client.post("/v1/users/me/ai-fallback/consent", headers=h(tok), json={**body, "method": "x"}).status_code == 422
    with db(dsn) as conn:
        assert [x["method"] for x in rows(conn, "ai_fallback_consents", uid)] == ["one_tap"]


# ---------------------------------------------------------------- circles keep working
def test_circle_code_account_sees_its_circle_after_one_tap(client, dsn):
    """`friends` asks for the circle clause (2026-10-22.2); one tap records the current version, so a new account from a
    circle activation code joins its circle at once and has the Friends page."""
    assert consent.vkey(consent.version()) >= consent.vkey(FP.CONSENT_VERSION)
    with db(dsn) as conn:
        flags.set_flag(conn, "friends", "all", min_consent=FP.CONSENT_VERSION, actor="test")
        flags.set_flag(conn, FLAG, "all", actor="test")
        org = conn.execute("""INSERT INTO users (display_name, consent_at, consent_version, personal_circle_at)
                              VALUES ('Org', now(), %s, now()) RETURNING id""", (consent.version(),)).fetchone()["id"]
        circle = admin.create_circle(conn, "Example Rowing Club", org)
        codes.create(conn, circle["id"], "Example Rowing Club", 50)
        user, new = signin.find_or_create_user(conn, "email", "rower@example.com", "rower@example.com", True)
        assert new and user["status"] == "pending"
        uid, tok = user["id"], auth.create_token(conn, user["id"], "app", ["read", "write"])
    r = client.post("/v1/account/redeem-invite", headers=h(tok), json={"code": "example-rowing CLUB"})
    assert r.status_code == 200, r.text
    assert r.json()["circle"] == {"id": circle["id"], "name": "Example Rowing Club", "joined": False}
    with db(dsn) as conn:
        assert not flags.enabled(conn, uid, "friends") and flags.enabled(conn, uid, FLAG)
    prof = client.get("/v1/users/me/profile", headers=h(tok)).json()
    assert prof["consent"]["required"] is True and prof["consent"]["one_tap"] is True
    assert [p["circle_id"] for p in prof["circle_pending"]] == [circle["id"]]

    r = one_tap(client, tok)
    assert r.status_code == 200, r.text
    assert r.json()["circles_joined"] == [{"circle_id": circle["id"], "name": "Example Rowing Club"}]
    assert r.json()["circle_pending"] == []
    with db(dsn) as conn:
        assert flags.enabled(conn, uid, "friends")
        assert conn.execute("SELECT scopes FROM circle_members WHERE circle_id = %s AND user_id = %s",
                            (circle["id"], uid)).fetchone()["scopes"] == list(FP.DEFAULT_SCOPES)
        assert auth.can_view(conn, org, uid, "scores") and auth.can_view(conn, uid, org, "training")
    me = client.get("/v1/web/me", headers=h(tok)).json()
    assert "friends" in me["flags"] and FLAG in me["flags"]
    assert circle["id"] in [c["id"] for c in client.get("/v1/friends", headers=h(tok)).json()["circles"]]
