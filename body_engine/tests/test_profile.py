"""Test-user self-service: electronic consent (no health data is processed without it), personal profile (own only, validation, goal weight goes into the status card), score degradation when profile fields are missing.

Uses a separate test database BODY_TEST_DB_NAME (default body_test_profile), dropped and recreated on every run, never touching the production DB; skipped if it cannot connect.
"""
import io
import os
import re
import zipfile
from datetime import date
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.profile import consent

from test_history import export_xml

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_profile")
CSRF = {"X-Body-Web": "1"}
DOCS = Path(__file__).resolve().parents[2] / "docs" / "family-consent.md"
DOCS_EN = DOCS.with_name("family-consent.en.md")


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
        ids = {"owner": conn.execute("INSERT INTO users (display_name, role) VALUES ('本人', 'owner') RETURNING id").fetchone()["id"]}
        for n in ("t1", "t2", "t3"):   # The admin only gave an email: the display name is the email prefix, nothing else is filled in
            ids[n] = conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
        tok["t1_ro"] = auth.create_token(conn, ids["t1"], "ro", ["read"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


class NoSparky:
    """A user without SparkyFitness connected."""
    def __getattr__(self, name):
        raise RuntimeError("no sparky")


def H(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- consent form
def test_consent_text_matches_docs():
    if not DOCS.exists():
        pytest.skip("not inside the repo (no docs/)")
    assert consent.text() == DOCS.read_text(encoding="utf-8"), "profile/consent.md and docs/family-consent.md must be identical character for character"
    assert consent.version() == "2026-10-21.1"
    shown = consent.display_text()
    assert "一字不差" not in shown and "## 2. 在我同意之前" in shown


def test_english_consent_matches_chinese():
    """consent.en.md = docs/family-consent.en.md, same version as the Chinese, the same clauses (sections and bullets)."""
    if not DOCS_EN.exists():
        pytest.skip("not inside the repo (no docs/)")
    assert consent.text("en") == DOCS_EN.read_text(encoding="utf-8"), \
        "profile/consent.en.md and docs/family-consent.en.md must be identical character for character"
    assert consent.version("en") == consent.version(), "the English consent carries the Chinese text's version number"
    zh, en = consent.text("zh"), consent.text("en")
    for pattern in (r"^## \d+\.", r"^- ", r"^> ", r"^---$"):
        assert len(re.findall(pattern, zh, re.M)) == len(re.findall(pattern, en, re.M)), f"clause count differs: {pattern}"
    assert not re.search(r"[一-鿿]", en), "Chinese left in the English consent"
    assert len(consent.SUMMARY_EN) == len(consent.SUMMARY)
    assert not any(re.search(r"[一-鿿]", p) for p in consent.SUMMARY_EN)
    shown = consent.display_text("en")
    assert "identical" not in shown and "## 2. Before I agree" in shown


def test_consent_in_the_readers_language(client, users, dsn):
    ids, tok = users
    c = client.get("/v1/consent", headers={**H(tok["t3"]), "X-Body-Lang": "en"}).json()
    assert c["language"] == "en" and c["version"] == consent.version() and "stored, not processed" in c["text"]
    assert c["summary"] == consent.SUMMARY_EN
    assert client.get("/v1/consent", headers=H(tok["t3"])).json()["language"] == "zh"
    r = client.post("/v1/users/me/consent", json={"version": c["version"], "agree": True, "language": "en"}, headers=H(tok["t3"]))
    assert r.status_code == 200 and r.json()["consent"]["accepted_language"] == "en"
    assert client.post("/v1/users/me/consent", json={"version": c["version"], "agree": True, "language": "fr"},
                       headers=H(tok["t3"])).status_code == 422
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        assert conn.execute("SELECT language FROM user_consents WHERE user_id = %s", (ids["t3"],)).fetchone()["language"] == "en"
        conn.execute("DELETE FROM user_consents WHERE user_id = %s", (ids["t3"],))
        conn.execute("UPDATE users SET consent_at = NULL, consent_version = NULL WHERE id = %s", (ids["t3"],))


def test_consent_flow_and_gate(client, users, dsn, tmp_path, monkeypatch):
    ids, tok = users
    st = client.get("/v1/users/me/profile", headers=H(tok["t1"])).json()["consent"]
    assert st["required"] is True and st["version"] == consent.version()
    assert client.get("/v1/users/me/profile", headers=H(tok["owner"])).json()["consent"]["required"] is False
    c = client.get("/v1/consent", headers=H(tok["t1"])).json()
    assert c["version"] == consent.version() and len(c["summary"]) >= 4 and "暂存、不处理" in c["text"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert consent.gate(conn, ids["t1"]) and consent.gate(conn, ids["owner"]) is None
    # no consent: history import is rejected
    monkeypatch.setenv("BODY_IMPORT_DIR", str(tmp_path))
    monkeypatch.setenv("BODY_IMPORT_MIN_FREE_MB", "1")
    r = client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 10}, headers=H(tok["t1"]))
    assert r.status_code == 403 and "同意" in r.json()["detail"]
    # Can only consent for oneself; a read-only login cannot; stale version gives 409; agree must be true
    assert client.post(f"/v1/users/{ids['t1']}/consent", json={"version": c["version"], "agree": True},
                       headers=H(tok["t2"])).status_code == 403
    assert client.post("/v1/users/me/consent", json={"version": c["version"], "agree": True},
                       headers=H(tok["t1_ro"])).status_code == 403
    assert client.post("/v1/users/me/consent", json={"version": "2000-01-01", "agree": True},
                       headers=H(tok["t1"])).status_code == 409
    assert client.post("/v1/users/me/consent", json={"version": c["version"], "agree": False},
                       headers=H(tok["t1"])).status_code == 422
    r = client.post("/v1/users/me/consent", json={"version": c["version"], "agree": True},
                    headers={**H(tok["t1"]), "User-Agent": "Watch2Whoop test"})
    assert r.status_code == 200 and r.json()["consent"]["required"] is False
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT version, method, user_agent FROM user_consents WHERE user_id = %s", (ids["t1"],)).fetchone()
        assert row == {"version": consent.version(), "method": "app", "user_agent": "Watch2Whoop test"}
        assert consent.gate(conn, ids["t1"]) is None
        assert consent.gate(conn, ids["t2"])       # others are unaffected
    r = client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 10}, headers=H(tok["t1"]))
    assert r.status_code == 200
    client.delete(f"/v1/users/me/history-import/{r.json()['job']['id']}", headers=H(tok["t1"]))


def test_paper_consent_and_reconfirm(dsn, users):
    ids, _ = users
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        st = consent.accept(conn, ids["t2"], "paper", "paper", at=date(2026, 10, 20))
        assert st["required"] is False and st["reconfirm"] is False
        conn.execute("UPDATE users SET consent_version = '2000-01-01' WHERE id = %s", (ids["t2"],))
        st = consent.status(conn, ids["t2"])
        assert st["required"] is False and st["reconfirm"] is True   # Re-confirmation is requested after a revision, but data processing is not blocked
        assert consent.gate(conn, ids["t2"]) is None
        conn.execute("UPDATE users SET consent_at = NULL, consent_version = NULL WHERE id = %s", (ids["t2"],))


# ---------------------------------------------------------------- personal profile
def test_profile_self_only_and_validation(client, users):
    ids, tok = users
    p = client.get("/v1/users/me/profile", headers=H(tok["t2"])).json()
    assert p["display_name"] == "t2" and set(p["missing"]) == {"birth_date", "sex", "height_cm"}
    assert client.get(f"/v1/users/{ids['t2']}/profile", headers=H(tok["t1"])).status_code == 403
    assert client.patch("/v1/users/me/profile", json={"height_cm": 170}, headers=H(tok["t1_ro"])).status_code == 403
    for bad in ({"height_cm": 20}, {"birth_date": "1800-01-01"}, {"birth_date": "2024-01-01"}, {"sex": "x"},
                {"timezone": "Mars/Base"}, {"goal_weight_kg": 5}, {"display_name": ""}, {"display_name": None},
                {"role": "owner"}, {"timezone": None}):
        r = client.patch("/v1/users/me/profile", json=bad, headers=H(tok["t2"]))
        assert r.status_code == 422, (bad, r.text)
    r = client.patch("/v1/users/me/profile", json={"display_name": " 小王 ", "birth_date": "1995-05-06", "sex": "female",
                                                    "height_cm": 165.04, "timezone": "Asia/Shanghai",
                                                    "goal_weight_kg": 55}, headers=H(tok["t2"]))
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["display_name"], p["birth_date"], p["sex"], p["height_cm"], p["timezone"], p["goal_weight_kg"], p["missing"]) == \
        ("小王", "1995-05-06", "female", 165.0, "Asia/Shanghai", 55.0, [])
    # Goal weight goes into the status card ("distance to goal" uses it); clearing it deletes it
    st = client.get("/v1/users/me/memory", headers=H(tok["t2"])).json()["status"]
    assert any(i["key"] == "goal_weight" and i["value"] == "55" and i["source"] == "user" for i in st)
    p = client.patch("/v1/users/me/profile", json={"goal_weight_kg": None, "sex": None}, headers=H(tok["t2"])).json()
    assert p["goal_weight_kg"] is None and p["missing"] == ["sex"]
    st = client.get("/v1/users/me/memory", headers=H(tok["t2"])).json()["status"]
    assert not any(i["key"] == "goal_weight" for i in st)
    # others' are unchanged
    assert client.get("/v1/users/me/profile", headers=H(tok["t1"])).json()["display_name"] == "t1"


def test_profile_language(client, users, dsn):
    """Language (issue #13): empty = follow the device; zh / en only; /v1/web/me and the AI briefing / system prompt carry it."""
    from body_engine.ai import prompt
    from body_engine.assistant import briefing
    _, tok = users
    ids = users[0]
    assert client.get("/v1/users/me/profile", headers=H(tok["t1"])).json()["language"] is None
    for bad in ({"language": "fr"}, {"language": "EN"}, {"language": 1}):
        assert client.patch("/v1/users/me/profile", json=bad, headers=H(tok["t1"])).status_code == 422, bad
    assert client.patch("/v1/users/me/profile", json={"language": "en"}, headers=H(tok["t1"])).json()["language"] == "en"
    assert client.get("/v1/web/me", headers=H(tok["t1"])).json()["me"]["language"] == "en"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert briefing._me(conn, ids["t1"])["language"] == "en"
        assert "语言：en" in prompt.build_system_prompt(conn, ids["t1"])
    # other users unaffected; null goes back to "follow the device"
    assert client.get("/v1/users/me/profile", headers=H(tok["t2"])).json()["language"] is None
    assert client.patch("/v1/users/me/profile", json={"language": None}, headers=H(tok["t1"])).json()["language"] is None
    assert client.get("/v1/web/me", headers=H(tok["t1"])).json()["me"]["language"] is None


def test_profile_cookie_needs_csrf(client, users):
    _, tok = users
    client.cookies.clear()
    assert client.post("/v1/web/login", json={"token": tok["t1"]}).status_code == 200
    assert client.patch("/v1/users/me/profile", json={"height_cm": 180}).status_code == 403
    assert client.patch("/v1/users/me/profile", json={"height_cm": 180}, headers=CSRF).json()["height_cm"] == 180
    client.cookies.clear()


# ---------------------------------------------------------------- degradation when profile is missing
def test_scores_without_profile(dsn, users, tmp_path):
    """No birthday / sex / height: scores are still computed (HRmax uses observed values only), body age and VO₂max norms give the reason they are missing, no error."""
    from body_engine.importers import apple_zip
    from body_engine.insights import fitness, healthspan_store
    from body_engine.scores import engine as scores_engine
    ids, _ = users
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("apple_health_export/export.xml", export_xml(6))
    p = tmp_path / "export.zip"
    p.write_bytes(buf.getvalue())
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        uid = conn.execute("""INSERT INTO users (display_name, consent_at) VALUES ('noprofile', now())
                              RETURNING id""").fetchone()["id"]
        apple_zip.run(conn, uid, p)
        with conn.transaction():
            res = scores_engine.run(conn, None, uid)
        assert res["rows"] > 0 and res["with_strain"] > 0
        assert healthspan_store.compute(conn, uid, sparky=NoSparky()) == []   # no birthday: body age is not computed, no error
        assert fitness.vo2max(conn, uid, date(2026, 9, 6))["norm_median"] is None


# ---------------------------------------------------------------- revoking tokens (App Review demo account: revoke when review is over)
def test_revoke_tokens_by_name_and_all(client, users, dsn):
    from body_engine.web import login_codes
    ids, _ = users
    uid = ids["t2"]
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        review = auth.create_token(conn, uid, "app-review", ["read", "write"])
        other = auth.create_token(conn, uid, "other", ["read"])
        code = login_codes.create(conn, uid, "reviewer", 2)
    client.cookies.clear()
    assert client.post("/v1/web/login", json={"token": review}).status_code == 200
    assert client.get("/v1/web/me").status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        assert auth.revoke_tokens(conn, uid, "app-review") == {"tokens": 1, "login_codes": 0}
        names = {r["name"]: r["revoked_at"] is not None for r in auth.list_tokens(conn, uid)}
        assert names["app-review"] is True and names["other"] is False
    assert client.get("/v1/web/me").status_code == 401          # the session made from it ends at once
    assert client.get("/v1/users/me/profile", headers=H(other)).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        res = auth.revoke_tokens(conn, uid, everything=True)
        assert res["tokens"] >= 1 and res["login_codes"] == 1
        assert login_codes.redeem(conn, code) is None             # unused one-time code no longer works
        with pytest.raises(ValueError):
            auth.revoke_tokens(conn, uid)
    assert client.get("/v1/users/me/profile", headers=H(other)).status_code == 401
    client.cookies.clear()


# ---------------------------------------------------------------- first-run intro (issue #60)
def test_intro_flag_follows_the_user(client, users):
    ids, tok = users
    client.cookies.clear()   # t2's tokens were all revoked by the test above: owner and t1 here
    me = lambda t: client.get("/v1/web/me", headers=H(tok[t])).json()["me"]
    assert me("owner")["intro_seen"] is False                                  # new user: the Today page shows the intro once
    r = client.put("/v1/users/me/intro", headers=H(tok["owner"]), json={"seen": True})
    assert r.status_code == 200 and r.json()["intro_seen"] is True
    first = r.json()["intro_seen_at"]
    assert client.put("/v1/users/me/intro", headers=H(tok["owner"]), json={"seen": True}).json()["intro_seen_at"] == first   # kept
    assert me("owner")["intro_seen"] is True and me("t1")["intro_seen"] is False   # per user, on the server (any device)
    assert client.put("/v1/users/me/intro", headers=H(tok["owner"]), json={"seen": False}).json()["intro_seen"] is False
    assert me("owner")["intro_seen"] is False
    # Only your own, with write; cookie writes need the CSRF header
    assert client.put(f"/v1/users/{ids['t1']}/intro", headers=H(tok["owner"]), json={"seen": True}).status_code == 403
    assert client.put("/v1/users/me/intro", headers=H(tok["t1_ro"]), json={"seen": True}).status_code == 403
    assert client.put("/v1/users/me/intro", headers=H(tok["t1"]), json={"seen": "yes"}).status_code == 422
    assert client.put("/v1/users/me/intro", json={"seen": True}).status_code == 401
    web = TestClient(api.app, base_url="https://testserver")
    assert web.post("/v1/web/login", json={"token": tok["t1"]}).status_code == 200
    assert web.put("/v1/users/me/intro", json={"seen": True}).status_code == 403          # no X-Body-Web
    assert web.put("/v1/users/me/intro", headers=CSRF, json={"seen": True}).status_code == 200
    assert web.get("/v1/web/me").json()["me"]["intro_seen"] is True


def test_intro_page_wiring(client):
    """intro.js is loaded and cached, re-openable from the Me page next to 「使用手册」, shown once via the server flag."""
    html = client.get("/app/").text
    assert html.index('src="intro.js"') < html.index('src="app.js"')
    js = client.get("/app/intro.js").text
    assert "/v1/users/me/intro" in js and "intro_seen" in js and "window.BodyIntro" in js and "/app/handbook/" not in js.split("*/", 1)[1]
    app_js = client.get("/app/app.js").text
    assert 'id="me-handbook"' in app_js and app_js.index('id="me-handbook"') < app_js.index('id="me-intro"')
    assert "openHandbook, openAiSettings" in app_js
    assert '"intro.js"' in client.get("/app/sw.js").text
    assert "家人" not in js
