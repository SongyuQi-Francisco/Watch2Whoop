"""Circles and circle sharing (docs/friends-social-plan.md §3.2): create / invite / join / change scopes / leave / remove /
dissolve, shares.circle_scopes kept apart from hand-made grants, cross-user access (nothing without a share, nothing right
after leaving, never ECGs), consent gate, invite limits, the referral hook (#57) and account deletion.
Test database body_test_friends (BODY_TEST_DB_NAME_FRIENDS), recreated on every run; skipped without Postgres."""
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.account import delete, invites, signin
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import api as friends_api, params as P, store

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_FRIENDS", "body_test_friends")
NAMES = ("Ann", "Ben", "Cat", "Dan", "Eve")


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_accept", ""):
        pytest.exit("the test DB must not be a real DB")
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
def world(dsn):
    """Ann, Ben, Cat, Dan agreed to the circle consent; Eve agreed only to an older version."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, birth_date, height_cm, consent_at, consent_version)
                                  VALUES (%s, '1990-01-01', 175, now(), %s) RETURNING id""",
                               (n, "2026-10-22.1" if n == "Eve" else P.CONSENT_VERSION)).fetchone()["id"] for n in NAMES}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tok["ann_ro"] = auth.create_token(conn, ids["Ann"], "ro", ["read"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


@pytest.fixture(autouse=True)
def clean(dsn):
    """Every test starts without circles, circle grants or join-failure counts."""
    friends_api._failures._events.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM circles")
        conn.execute("DELETE FROM shares")
    yield


def h(t):
    return {"Authorization": f"Bearer {t}"}


def sees(client, tok, uid, path):
    return client.get(f"/v1/users/{uid}/{path}", headers=h(tok)).status_code


def can(dsn, viewer, owner, scope):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return auth.can_view(conn, viewer, owner, scope)


def circle(client, tok, name="家", scopes=None):
    body = {"name": name} | ({"scopes": scopes} if scopes is not None else {})
    r = client.post("/v1/friends/circles", headers=h(tok), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def join(client, tok, code, scopes=P.DEFAULT_SCOPES):
    return client.post("/v1/friends/join", headers=h(tok), json={"code": code, "scopes": list(scopes)})


def test_scopes_validation():
    assert store.clean_scopes(["training", "scores"]) == ["scores", "training"]
    with pytest.raises(store.FriendsError, match="心电图"):
        store.clean_scopes(["scores", "ecg"])
    with pytest.raises(store.FriendsError, match="不认识"):
        store.clean_scopes(["profile"])
    assert "ecg" not in P.CIRCLE_SCOPES and set(P.DEFAULT_SCOPES) <= set(P.CIRCLE_SCOPES)
    assert set(P.DEFAULT_SCOPES) == {"scores", "sleep", "training", "nutrition_summary"}


def test_create_invite_join_shares_both_ways(client, world, dsn):
    ids, tok = world
    a, b, c = ids["Ann"], ids["Ben"], ids["Cat"]
    # before any circle: nothing
    assert sees(client, tok["Ben"], a, "scores") == 403
    res = circle(client, tok["Ann"])
    code = res["invite"]["code"]
    assert res["circle"]["is_owner"] and res["circle"]["my_scopes"] == list(P.DEFAULT_SCOPES)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:   # only the hash is stored
        assert conn.execute("SELECT count(*) AS n FROM circle_invites WHERE code_sha256 LIKE %s",
                            (code.replace("-", "")[:4] + "%",)).fetchone()["n"] == 0
    pv = client.get("/v1/friends/invite", params={"code": code}, headers=h(tok["Ben"])).json()
    assert pv["name"] == "家" and pv["owner_name"] == "Ann" and pv["members"] == 1
    r = join(client, tok["Ben"], code.lower(), ["scores", "weight"])
    assert r.status_code == 200 and [m["display_name"] for m in r.json()["members"]] == ["Ann", "Ben"]
    # Ann shares her defaults with Ben, Ben shares scores + weight with Ann
    assert sees(client, tok["Ben"], a, "scores") == 200 and sees(client, tok["Ben"], a, "training/calendar") == 200
    assert not can(dsn, b, a, "weight")
    assert sees(client, tok["Ann"], b, "scores") == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert auth.can_view(conn, a, b, "weight") and not auth.can_view(conn, a, b, "training")
        assert not auth.can_view(conn, c, a, "scores")                       # not in the circle
        row = conn.execute("SELECT scopes, circle_scopes FROM shares WHERE owner_id = %s AND viewer_id = %s",
                           (a, b)).fetchone()
        assert row["scopes"] == [] and row["circle_scopes"] == list(P.DEFAULT_SCOPES)
    # the switcher on the Today page lists circle grants
    me = client.get("/v1/web/me", headers=h(tok["Ben"])).json()
    assert any(v["id"] == a and "scores" in v["scopes"] for v in me["viewable"])
    # joining again changes nothing
    again = join(client, tok["Ben"], code, ["journal"])
    assert again.json()["already"] and again.json()["my_scopes"] == ["scores", "weight"]


def test_ecg_never_through_circles(client, world, dsn):
    ids, tok = world
    code = circle(client, tok["Ann"], scopes=list(P.CIRCLE_SCOPES))["invite"]["code"]
    assert join(client, tok["Ben"], code, ["scores", "ecg"]).status_code == 400
    assert join(client, tok["Ben"], code, list(P.CIRCLE_SCOPES)).status_code == 200
    assert sees(client, tok["Ben"], ids["Ann"], "ecg") == 403 and sees(client, tok["Ann"], ids["Ben"], "ecg") == 403
    cid = client.get("/v1/friends", headers=h(tok["Ben"])).json()["circles"][0]["id"]
    assert client.patch(f"/v1/friends/circles/{cid}/me", headers=h(tok["Ben"]), json={"scopes": ["ecg"]}).status_code == 400
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):   # and the database refuses it too
            conn.execute("UPDATE circle_members SET scopes = '{ecg}' WHERE circle_id = %s", (cid,))


def test_change_scopes_and_leave_take_effect_at_once(client, world):
    ids, tok = world
    a = ids["Ann"]
    code = circle(client, tok["Ann"])["invite"]["code"]
    join(client, tok["Ben"], code)
    cid = client.get("/v1/friends", headers=h(tok["Ann"])).json()["circles"][0]["id"]
    assert sees(client, tok["Ben"], a, "training/calendar") == 200
    r = client.patch(f"/v1/friends/circles/{cid}/me", headers=h(tok["Ann"]), json={"scopes": ["scores"]})
    assert r.status_code == 200 and r.json()["my_scopes"] == ["scores"]
    assert sees(client, tok["Ben"], a, "training/calendar") == 403 and sees(client, tok["Ben"], a, "scores") == 200
    # Ben leaves: both directions stop right away
    assert client.delete(f"/v1/friends/circles/{cid}/me", headers=h(tok["Ben"])).status_code == 204
    assert sees(client, tok["Ben"], a, "scores") == 403 and sees(client, tok["Ann"], ids["Ben"], "scores") == 403
    assert client.get("/v1/friends", headers=h(tok["Ben"])).json()["circles"] == []
    assert all(v["self"] for v in client.get("/v1/web/me", headers=h(tok["Ben"])).json()["viewable"])


def test_union_over_circles_and_hand_made_grants_survive(client, world, dsn):
    ids, tok = world
    a, b = ids["Ann"], ids["Ben"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:   # an admin-made grant (weight, ecg) that already expired
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, '{weight,ecg}')", (a, b))
    c1 = circle(client, tok["Ann"], "家", ["scores"])
    c2 = circle(client, tok["Ann"], "球队", ["training"])
    join(client, tok["Ben"], c1["invite"]["code"], [])
    join(client, tok["Ben"], c2["invite"]["code"], [])
    assert sees(client, tok["Ben"], a, "scores") == 200 and sees(client, tok["Ben"], a, "training/calendar") == 200
    assert sees(client, tok["Ann"], b, "scores") == 403                     # Ben shared nothing back
    client.delete(f"/v1/friends/circles/{c2['circle']['id']}/me", headers=h(tok["Ben"]))
    assert sees(client, tok["Ben"], a, "scores") == 200 and sees(client, tok["Ben"], a, "training/calendar") == 403
    client.delete(f"/v1/friends/circles/{c1['circle']['id']}/me", headers=h(tok["Ben"]))
    # the hand-made grant is untouched by circle changes, ECG only through it
    assert can(dsn, b, a, "weight") and sees(client, tok["Ben"], a, "ecg") == 200
    assert sees(client, tok["Ben"], a, "scores") == 403
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE shares SET expires_at = now() - interval '1 day' WHERE owner_id = %s AND viewer_id = %s", (a, b))
    c3 = circle(client, tok["Ann"], "三", ["scores"])
    join(client, tok["Ben"], c3["invite"]["code"], [])
    # an expired hand-made grant no longer counts, the circle part still does
    assert not can(dsn, b, a, "weight") and sees(client, tok["Ben"], a, "ecg") == 403
    assert sees(client, tok["Ben"], a, "scores") == 200


def test_nutrition_summary_is_narrower_than_nutrition(dsn, world):
    ids, _ = world
    a, b = ids["Ann"], ids["Ben"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes, circle_scopes) VALUES (%s, %s, '{}', '{nutrition_summary}')",
                     (a, b))
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, '{nutrition}')", (a, ids["Cat"]))
        assert auth.can_view(conn, b, a, "nutrition_summary") and not auth.can_view(conn, b, a, "nutrition")
        assert auth.can_view(conn, ids["Cat"], a, "nutrition_summary")      # full food implies the summary


def test_owner_powers_and_non_members(client, world):
    ids, tok = world
    res = circle(client, tok["Ann"])
    cid, code = res["circle"]["id"], res["invite"]["code"]
    join(client, tok["Ben"], code)
    join(client, tok["Cat"], code)
    # a member who is not the owner cannot invite, rename, remove or dissolve; an outsider sees nothing
    assert client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok["Ben"])).status_code == 403
    assert client.patch(f"/v1/friends/circles/{cid}", headers=h(tok["Ben"]), json={"name": "x"}).status_code == 403
    assert client.delete(f"/v1/friends/circles/{cid}/members/{ids['Cat']}", headers=h(tok["Ben"])).status_code == 403
    assert client.delete(f"/v1/friends/circles/{cid}", headers=h(tok["Ben"])).status_code == 403
    assert client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok["Dan"])).status_code == 404
    assert client.patch(f"/v1/friends/circles/{cid}/me", headers=h(tok["Dan"]), json={"scopes": []}).status_code == 404
    assert "invites" not in client.get("/v1/friends", headers=h(tok["Ben"])).json()["circles"][0]
    # read-only token cannot write
    assert client.post("/v1/friends/circles", headers=h(tok["ann_ro"]), json={"name": "x"}).status_code == 403
    # the owner removes Cat: Cat and Ben stop seeing each other at once
    assert sees(client, tok["Cat"], ids["Ben"], "scores") == 200
    assert client.delete(f"/v1/friends/circles/{cid}/members/{ids['Cat']}", headers=h(tok["Ann"])).status_code == 204
    assert sees(client, tok["Cat"], ids["Ben"], "scores") == 403 and sees(client, tok["Ben"], ids["Cat"], "scores") == 403
    assert client.delete(f"/v1/friends/circles/{cid}/members/{ids['Ann']}", headers=h(tok["Ann"])).status_code == 400
    # the owner leaves: Ben (longest-standing) takes over
    client.delete(f"/v1/friends/circles/{cid}/me", headers=h(tok["Ann"]))
    c = client.get("/v1/friends", headers=h(tok["Ben"])).json()["circles"][0]
    assert c["is_owner"] and [m["display_name"] for m in c["members"]] == ["Ben"]
    # dissolving removes everything
    join(client, tok["Dan"], client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok["Ben"])).json()["code"])
    assert sees(client, tok["Dan"], ids["Ben"], "scores") == 200
    assert client.delete(f"/v1/friends/circles/{cid}", headers=h(tok["Ben"])).status_code == 204
    assert sees(client, tok["Dan"], ids["Ben"], "scores") == 403
    assert client.get("/v1/friends", headers=h(tok["Dan"])).json()["circles"] == []


def test_invite_codes_expire_reset_and_rate_limit(client, world, dsn):
    ids, tok = world
    res = circle(client, tok["Ann"])
    cid, old = res["circle"]["id"], res["invite"]["code"]
    new = client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok["Ann"]), json={"reset": True}).json()["code"]
    assert join(client, tok["Ben"], old).status_code == 404                # reset: the old link is dead
    assert client.get("/v1/friends", headers=h(tok["Ann"])).json()["circles"][0]["invites"]["live"] == 1
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE circle_invites SET expires_at = now() - interval '1 minute' WHERE circle_id = %s", (cid,))
    assert join(client, tok["Ben"], new).status_code == 404                # 72 hours are over
    # live codes are capped: the oldest goes
    codes = [client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok["Ann"])).json()["code"]
             for _ in range(P.MAX_LIVE_CODES + 1)]
    assert join(client, tok["Ben"], codes[0]).status_code == 404 and join(client, tok["Ben"], codes[-1]).status_code == 200
    # wrong codes are counted per user / IP
    friends_api._failures._events.clear()
    for _ in range(P.JOIN_MAX_FAILURES):
        assert join(client, tok["Cat"], "AAAAA-BBBBB").status_code == 404
    assert join(client, tok["Cat"], codes[-1]).status_code == 429
    assert client.get("/v1/friends/invite", params={"code": codes[-1]}, headers=h(tok["Cat"])).status_code == 429


def test_consent_gate(client, world):
    ids, tok = world
    r = client.post("/v1/friends/circles", headers=h(tok["Eve"]), json={"name": "x"})
    assert r.status_code == 409 and "同意书" in r.json()["detail"]
    r = client.post("/v1/friends/circles", headers=h(tok["Eve"]) | {"X-Body-Lang": "en"}, json={"name": "x"})
    assert "consent" in r.json()["detail"]
    code = circle(client, tok["Ann"])["invite"]["code"]
    assert join(client, tok["Eve"], code).status_code == 409
    assert client.get("/v1/friends", headers=h(tok["Eve"])).json()["consent_ok"] is False
    assert client.post("/v1/friends/circles", headers=h(tok["Ann"]), json={"name": " "}).status_code == 400


def test_referral_signup_gets_invite_but_shares_nothing_until_accepting(client, world, dsn, monkeypatch):
    ids, tok = world
    monkeypatch.setenv("BODY_REFERRALS_ENABLED", "1")
    cid = circle(client, tok["Ann"])["circle"]["id"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        code = invites.referral_code(conn, ids["Ann"])["code"]
        new, _ = signin.find_or_create_user(conn, "email", "friend@example.com", "friend@example.com", True)
        assert invites.redeem(conn, new["id"], code)
        assert conn.execute("SELECT circle_id FROM invite_redemptions WHERE user_id = %s", (new["id"],)).fetchone()["circle_id"] == cid
        conn.execute("UPDATE users SET consent_at = now(), consent_version = %s WHERE id = %s", (P.CONSENT_VERSION, new["id"]))
        t = auth.create_token(conn, new["id"], "t", ["read", "write"])
    over = client.get("/v1/friends", headers=h(t)).json()
    assert over["circles"] == [] and [i["circle_id"] for i in over["invites"]] == [cid]
    assert over["invites"][0]["from_name"] == "Ann"
    assert sees(client, t, ids["Ann"], "scores") == 403 and sees(client, tok["Ann"], new["id"], "scores") == 403
    inv = over["invites"][0]["id"]
    # nobody else can use an invite addressed to the new user
    r = client.post("/v1/friends/join", headers=h(tok["Dan"]), json={"invite_id": inv, "scopes": []})
    assert r.status_code == 404
    r = client.post("/v1/friends/join", headers=h(t), json={"invite_id": inv, "scopes": ["sleep"]})
    assert r.status_code == 200 and r.json()["my_scopes"] == ["sleep"]
    assert sees(client, tok["Ann"], new["id"], "sleep/nights") == 200 and sees(client, tok["Ann"], new["id"], "scores") == 403
    assert client.get("/v1/friends", headers=h(t)).json()["invites"] == []
    assert client.post("/v1/friends/join", headers=h(t), json={"invite_id": inv, "scopes": []}).status_code == 404


def test_decline_invite(client, world, dsn):
    ids, tok = world
    cid = circle(client, tok["Ann"])["circle"]["id"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        store.referral_invites(conn, ids["Ann"], ids["Dan"])
    inv = client.get("/v1/friends", headers=h(tok["Dan"])).json()["invites"][0]
    assert inv["circle_id"] == cid
    assert client.delete(f"/v1/friends/invites/{inv['id']}", headers=h(tok["Ben"])).status_code == 404
    assert client.delete(f"/v1/friends/invites/{inv['id']}", headers=h(tok["Dan"])).status_code == 204
    assert client.get("/v1/friends", headers=h(tok["Dan"])).json()["invites"] == []


def test_account_deletion_passes_circles_on(client, world, dsn):
    ids, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        gone = conn.execute("""INSERT INTO users (display_name, consent_at, consent_version) VALUES ('Gus', now(), %s)
                               RETURNING id""", (P.CONSENT_VERSION,)).fetchone()["id"]
        res = store.create(conn, gone, "Gus 的圈")
        cid = res["circle"]["id"]
        store.join(conn, ids["Ben"], P.DEFAULT_SCOPES, code=res["invite"]["code"])
        store.join(conn, ids["Cat"], P.DEFAULT_SCOPES, code=res["invite"]["code"])
        assert auth.can_view(conn, ids["Ben"], gone, "scores")
        conn.commit()
        delete.delete_account(conn, gone)
        c = conn.execute("SELECT owner_id FROM circles WHERE id = %s", (cid,)).fetchone()
        assert c["owner_id"] == ids["Ben"]
        assert auth.can_view(conn, ids["Cat"], ids["Ben"], "scores")
        assert conn.execute("SELECT count(*) AS n FROM shares WHERE owner_id = %s OR viewer_id = %s",
                            (gone, gone)).fetchone()["n"] == 0
