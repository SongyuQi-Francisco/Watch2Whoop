"""Circles and circle sharing (docs/friends-social-plan.md §3.2): create / invite / join / change scopes / leave / remove /
dissolve, shares.circle_scopes kept apart from hand-made grants, cross-user access (nothing without a share, nothing right
after leaving, never ECGs), consent gate, invite limits, the referral hook (#57) and account deletion.
Test database body_test_friends (BODY_TEST_DB_NAME_FRIENDS), recreated on every run; skipped without Postgres."""
import os
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.account import delete, invites, signin
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.assistant.tools import run_tool
from body_engine.friends import api as friends_api, leaderboard, params as P, store
from body_engine.friends import api as friends_api, params as P, store
from body_engine.rollout import flags

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
    """Ann, Ben, Cat, Dan agreed to the circle consent; Eve agreed only to an older version. Their personal circles are
    settled (personal_circle_at set, none made), so these tests see only the circles they make; personal circles have
    their own users below."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, birth_date, height_cm, consent_at, consent_version,
                                                     personal_circle_at)
                                  VALUES (%s, '1990-01-01', 175, now(), %s, now()) RETURNING id""",
                               (n, "2026-10-22.1" if n == "Eve" else P.CONSENT_VERSION)).fetchone()["id"] for n in NAMES}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tok["ann_ro"] = auth.create_token(conn, ids["Ann"], "ro", ["read"])
        flags.set_flag(conn, "friends", "all", actor="test")   # the Friends page is behind this flag (docs/rollout.md)
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
    leaderboard.clear_cache()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM circles")
        conn.execute("DELETE FROM shares")
        for t in ("daily_scores", "workouts", "healthspan_weekly", "daily_metrics", "assistant_status"):
            conn.execute(f"DELETE FROM {t}")
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
    assert join(client, tok["Ben"], new).status_code == 404                # the 3 days are over
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


def _mint(client, tok, cid, **body):
    return client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok), json=body)


def _expiry_days(dsn, cid, code_id):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        r = conn.execute("""SELECT expires_at, extract(epoch FROM expires_at - created_at) / 86400 AS days
                            FROM circle_invites WHERE id = %s""", (code_id,)).fetchone()
    return None if r["expires_at"] is None else round(float(r["days"]), 2)


def test_invite_expiry_choices(client, world, dsn):
    """#156: the owner picks 1 / 3 / 7 / 30 days or never; no choice = 3 days; the creation invite is 3 days."""
    ids, tok = world
    res = circle(client, tok["Ann"])
    cid = res["circle"]["id"]
    assert _expiry_days(dsn, cid, res["invite"]["id"]) == P.INVITE_DEFAULT_DAYS == 3
    assert tuple(client.get("/v1/friends", headers=h(tok["Ann"])).json()["limits"]["invite_days"]) == P.INVITE_DAYS_CHOICES
    for days in P.INVITE_DAYS_CHOICES:
        r = _mint(client, tok["Ann"], cid, days=days)
        assert r.status_code == 200, r.text
        assert _expiry_days(dsn, cid, r.json()["id"]) == days
        assert join(client, tok["Ben"], r.json()["code"]).status_code == 200      # a fresh code works
        client.delete(f"/v1/friends/circles/{cid}/me", headers=h(tok["Ben"]))
    r = _mint(client, tok["Ann"], cid)                                             # no body at all → the default
    assert _expiry_days(dsn, cid, r.json()["id"]) == 3
    r = client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok["Ann"]))
    assert _expiry_days(dsn, cid, r.json()["id"]) == 3


def test_invite_expiry_invalid_values_are_400(client, world):
    ids, tok = world
    cid = circle(client, tok["Ann"])["circle"]["id"]
    for bad in (0, 2, 5, 31, 365, -1):
        r = _mint(client, tok["Ann"], cid, days=bad)
        assert r.status_code == 400 and "有效期" in r.json()["detail"], (bad, r.text)
    r = _mint(client, tok["Ann"], cid, days=7, permanent=True)
    assert r.status_code == 400
    en = client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok["Ann"]) | {"X-Body-Lang": "en"}, json={"days": 2})
    assert en.status_code == 400 and "1 / 3 / 7 / 30" in en.json()["detail"] and "有效" not in en.json()["detail"]
    # nothing was minted by the bad requests: only the creation invite is live
    assert client.get("/v1/friends", headers=h(tok["Ann"])).json()["circles"][0]["invites"]["live"] == 1


def test_permanent_invite_never_expires(client, world, dsn):
    ids, tok = world
    cid = circle(client, tok["Ann"])["circle"]["id"]
    r = _mint(client, tok["Ann"], cid, permanent=True)
    assert r.status_code == 200 and r.json()["expires_at"] is None
    with psycopg.connect(dsn, row_factory=dict_row) as conn:      # years later it still works; the 3-day one is long over
        conn.execute("UPDATE circle_invites SET created_at = now() - interval '5 years' WHERE id = %s", (r.json()["id"],))
        conn.execute("UPDATE circle_invites SET expires_at = now() - interval '1 minute' WHERE circle_id = %s AND expires_at IS NOT NULL", (cid,))
    inv = client.get("/v1/friends", headers=h(tok["Ann"])).json()["circles"][0]["invites"]
    assert inv["live"] == 1 and inv["until"] is None and inv["codes"][0]["expires_at"] is None
    assert client.get("/v1/friends/invite", params={"code": r.json()["code"]}, headers=h(tok["Ben"])).status_code == 200
    assert join(client, tok["Ben"], r.json()["code"]).status_code == 200
    # an addressed invite (referral) can't be made permanent
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO circle_invites (circle_id, created_by, invitee_user_id) VALUES (%s, %s, %s)",
                         (cid, ids["Ann"], ids["Dan"]))


def test_revoke_one_invite_and_remaining_time(client, world, dsn):
    ids, tok = world
    res = circle(client, tok["Ann"])
    cid, first = res["circle"]["id"], res["invite"]
    seven = _mint(client, tok["Ann"], cid, days=7).json()
    forever = _mint(client, tok["Ann"], cid, permanent=True).json()
    shown = client.get("/v1/friends", headers=h(tok["Ann"])).json()["circles"][0]["invites"]
    assert shown["live"] == 3 and [c["id"] for c in shown["codes"]] == [forever["id"], seven["id"], first["id"]]
    assert shown["until"] is None and all("code" not in c for c in shown["codes"])    # no plaintext, one is permanent
    # only the owner; an outsider doesn't even see the circle
    url = lambda i: f"/v1/friends/circles/{cid}/invites/{i}"
    assert client.delete(url(seven["id"]), headers=h(tok["Cat"])).status_code == 404
    join(client, tok["Ben"], first["code"])
    assert client.delete(url(seven["id"]), headers=h(tok["Ben"])).status_code == 403
    # revoke the 7-day and the permanent one: they stop working, the other keeps working
    assert client.delete(url(seven["id"]), headers=h(tok["Ann"])).status_code == 204
    assert client.delete(url(forever["id"]), headers=h(tok["Ann"])).status_code == 204
    assert join(client, tok["Cat"], seven["code"]).status_code == 404
    assert join(client, tok["Dan"], forever["code"]).status_code == 404
    assert client.get("/v1/friends/invite", params={"code": forever["code"]}, headers=h(tok["Dan"])).status_code == 404
    assert join(client, tok["Cat"], first["code"]).status_code == 200
    # twice, an unknown id and another circle's id are all 404
    assert client.delete(url(seven["id"]), headers=h(tok["Ann"])).status_code == 404
    assert client.delete(url(999999), headers=h(tok["Ann"])).status_code == 404
    other = circle(client, tok["Ben"], name="别的")
    assert client.delete(url(other["invite"]["id"]), headers=h(tok["Ann"])).status_code == 404
    assert join(client, tok["Dan"], other["invite"]["code"]).status_code == 200            # untouched
    # the members who joined with the revoked code's sibling stay
    assert len(client.get("/v1/friends", headers=h(tok["Ann"])).json()["circles"][0]["members"]) == 3
    # wrong codes still count towards the limit
    friends_api._failures._events.clear()
    for _ in range(P.JOIN_MAX_FAILURES):
        assert join(client, tok["Eve"], forever["code"]).status_code in (404, 409)


def test_referral_invite_stays_30_days(client, world, dsn):
    ids, tok = world
    cid = circle(client, tok["Ann"])["circle"]["id"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        store.referral_invites(conn, ids["Ann"], ids["Dan"])
        days = conn.execute("""SELECT extract(epoch FROM expires_at - created_at) / 86400 AS d FROM circle_invites
                               WHERE invitee_user_id = %s""", (ids["Dan"],)).fetchone()["d"]
    assert round(float(days)) == P.REFERRAL_INVITE_DAYS == 30


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
    assert [(c["name"], c["personal"]) for c in over["circles"]] == [("friend 的圈子", True)]
    assert [i["circle_id"] for i in over["invites"]] == [cid]
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


# ---------------------------------------------------------------- Friends tab: member cards and cheers (PR B)
def _scores(dsn, uid, days=8, recovery=70, strain=10.0, workout_today=False):
    from body_engine.web import views
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        today = views.local_today(conn, uid)
        for i in range(days):
            conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, strain, sleep_score, sleep_actual_min, algo_version)
                            VALUES (%s, %s, %s, %s, 80, 420, 't') ON CONFLICT (user_id, date) DO NOTHING""",
                         (uid, today - timedelta(days=i), recovery, strain))
        if workout_today:
            conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
                            VALUES (%s, 'shortcut', %s, 'running', '跑步', %s, now(), now(), 30)""", (uid, f"t{uid}", today))
        return today


def test_cards_show_only_shared_blocks(client, world, dsn):
    ids, tok = world
    a, b = ids["Ann"], ids["Ben"]
    _scores(dsn, a, recovery=80, workout_today=True)
    _scores(dsn, b, recovery=30)
    code = circle(client, tok["Ann"])["invite"]["code"]
    join(client, tok["Ben"], code, ["sleep"])
    ov = client.get("/v1/friends/overview", headers=h(tok["Ben"])).json()
    assert ov["has_circles"] and ov["me"]["self"] and ov["me"]["scores"]["recovery"] == 30
    ann = ov["members"][0]
    assert ann["display_name"] == "Ann" and ann["circles"] == ["家"]
    assert ann["scores"]["recovery"] == 80 and ann["scores"]["zone"] == "green" and ann["scores"]["synced_today"]
    assert len(ann["scores"]["week"]) == 7 and ann["trained"] is True and ann["hidden"] == []
    # Ben shares only sleep: Ann sees his name and nothing else
    ben = client.get("/v1/friends/overview", headers=h(tok["Ann"])).json()["members"][0]
    assert ben["scores"] is None and ben["trained"] is None and set(ben["hidden"]) == {"scores", "training"}
    assert ben["shared"] == ["sleep"]
    # English labels for the page
    en = client.get("/v1/friends/overview", headers=h(tok["Ben"]) | {"X-Body-Lang": "en"}).json()
    assert en["members"][0]["scores"]["label"] == "Ready to push"
    # an ECG grant never shows up as shared, even a hand-made one
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE shares SET scopes = '{ecg}' WHERE owner_id = %s AND viewer_id = %s", (a, b))
    assert "ecg" not in client.get("/v1/friends/overview", headers=h(tok["Ben"])).json()["members"][0]["shared"]
    # after leaving: no card at all
    cid = client.get("/v1/friends", headers=h(tok["Ben"])).json()["circles"][0]["id"]
    client.delete(f"/v1/friends/circles/{cid}/me", headers=h(tok["Ben"]))
    ov = client.get("/v1/friends/overview", headers=h(tok["Ben"])).json()
    assert ov["members"] == [] and not ov["has_circles"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM daily_scores"); conn.execute("DELETE FROM workouts")


def test_cards_last_sync_when_today_is_empty(client, world, dsn):
    from body_engine.web import views
    ids, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        today = views.local_today(conn, ids["Ann"])
        conn.execute("INSERT INTO daily_scores (user_id, date, recovery, algo_version) VALUES (%s, %s, 50, 't')",
                     (ids["Ann"], today - timedelta(days=3)))
    join(client, tok["Ben"], circle(client, tok["Ann"])["invite"]["code"])
    s = client.get("/v1/friends/overview", headers=h(tok["Ben"])).json()["members"][0]["scores"]
    assert s["recovery"] is None and not s["synced_today"] and s["last_date"] == str(today - timedelta(days=3))
    assert [d["zone"] for d in s["week"]].count("yellow") == 1
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM daily_scores")


def test_nudges_once_a_day_and_only_inside_circles(client, world, dsn):
    ids, tok = world
    a = ids["Ann"]
    join(client, tok["Ben"], circle(client, tok["Ann"])["invite"]["code"])
    send = lambda t, to, kind: client.post("/v1/friends/nudges", headers=h(tok[t]), json={"to": to, "kind": kind})
    assert send("Ben", a, "clap").json() == {"sent": ["clap"]}
    assert send("Ben", a, "clap").status_code == 409
    assert send("Ben", a, "sleep").json() == {"sent": ["clap", "sleep"]}
    assert send("Ben", a, "hug").status_code == 400
    assert send("Cat", a, "clap").status_code == 404            # not in a circle with Ann
    assert send("Ann", a, "clap").status_code == 404            # not to oneself
    assert client.post("/v1/friends/nudges", headers=h(tok["ann_ro"]), json={"to": ids["Ben"], "kind": "clap"}).status_code == 403
    assert client.get("/v1/friends/overview", headers=h(tok["Ann"])).json()["me"]["received"] == \
        {"clap": ["Ben"], "sleep": ["Ben"], "train": []}
    assert client.get("/v1/friends/overview", headers=h(tok["Ben"])).json()["members"][0]["sent"] == ["clap", "sleep"]


# ---------------------------------------------------------------- leaderboards (PR C)
def test_board_scoring_pieces():
    assert leaderboard.acwr_score(1.0) == 100 and leaderboard.acwr_score(1.5) == 100
    assert leaderboard.acwr_score(0.4) == 50 and leaderboard.acwr_score(1.75) == 50 and leaderboard.acwr_score(2.5) == 0
    assert leaderboard.band_score(0.75, P.LOSS_BAND) == 100 and leaderboard.band_score(0.25, P.LOSS_BAND) == 50
    assert leaderboard.band_score(-0.3, P.LOSS_BAND) == 0 and leaderboard.band_score(1.5, P.LOSS_BAND) == 50
    assert leaderboard.maintain_score(0.3) == 100 and leaderboard.maintain_score(2.0) == 0
    assert leaderboard.pace_score(0.5) == 100 and leaderboard.pace_score(1.0) == 50 and leaderboard.pace_score(1.5) == 0
    assert set(P.BOARDS) == set(P.BOARD_SCOPES) and not any("ecg" in v for v in P.BOARD_SCOPES.values())


def _rows(client, tok, board):
    r = client.get("/v1/friends/leaderboard", params={"board": board}, headers=h(tok))
    assert r.status_code == 200, r.text
    return {x["display_name"]: x for x in r.json()["rows"]}, r.json()


def test_body_board_ranks_only_people_who_share(client, world, dsn):
    ids, tok = world
    _scores(dsn, ids["Ann"], recovery=80)
    _scores(dsn, ids["Ben"], recovery=40)
    _scores(dsn, ids["Cat"], days=2, recovery=90)            # too few days
    code = circle(client, tok["Ann"])["invite"]["code"]
    join(client, tok["Ben"], code, ["sleep"])                  # shares no scores
    join(client, tok["Cat"], code)
    rows, body = _rows(client, tok["Ann"], "body")
    assert body["window_days"] == 7 and body["scopes"] == ["scores"]
    assert rows["Ann"]["status"] == "ok" and rows["Ann"]["rank"] == 1 and rows["Ann"]["sub"]["green_days"] == 7
    assert rows["Ben"]["status"] == "not_shared" and rows["Ben"]["rank"] is None and rows["Ben"]["score"] is None
    assert rows["Cat"]["status"] == "insufficient" and rows["Cat"]["rank"] is None
    assert list(body["rows"])[0]["display_name"] == "Ann"     # ranked first, not-ranked rows after
    # Ben sees his own row (always) and Ann's (she shares scores); Ann ranks above him
    rows, _ = _rows(client, tok["Ben"], "body")
    assert rows["Ann"]["rank"] == 1 and rows["Ben"]["rank"] == 2 and rows["Ben"]["self"]
    # leaving: gone from the board at once
    cid = client.get("/v1/friends", headers=h(tok["Ben"])).json()["circles"][0]["id"]
    client.delete(f"/v1/friends/circles/{cid}/me", headers=h(tok["Ben"]))
    rows, _ = _rows(client, tok["Ann"], "body")
    assert "Ben" not in rows
    assert client.get("/v1/friends/leaderboard", params={"board": "x"}, headers=h(tok["Ann"])).status_code == 400


def test_activity_board_needs_training_and_a_baseline(client, world, dsn):
    ids, tok = world
    _scores(dsn, ids["Ann"], days=35, strain=10.0, workout_today=True)
    _scores(dsn, ids["Ben"], days=35, strain=10.0)
    join(client, tok["Ben"], circle(client, tok["Ann"])["invite"]["code"], ["scores"])   # no training
    rows, _ = _rows(client, tok["Ann"], "activity")
    assert rows["Ben"]["status"] == "not_shared"
    a = rows["Ann"]
    assert a["status"] == "ok" and a["sub"] == {"acwr": 1.0, "training_days": 1}
    assert a["score"] == round(0.7 * 100 + 0.3 * 25)


def test_lifestyle_and_recomp_use_own_goals(client, world, dsn, monkeypatch):
    from body_engine.web import views
    ids, tok = world
    a = ids["Ann"]
    days = [{"date": None, "logged": True, "calories_ok": i < 5, "protein_ok": i < 6} for i in range(7)]
    monkeypatch.setattr(leaderboard, "_nutrition_days", lambda conn, uid, s, e: days)
    monkeypatch.setattr(leaderboard, "_bedtime_hits", lambda conn, uid: 6)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        today = views.local_today(conn, a)
        for i in range(14):   # 80 kg a week ago, 79.4 now: −0.75 %/week toward a 75 kg goal
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                            VALUES (%s, %s, 'body_mass', %s, 'kg', 'avg', 'shortcut')""", (a, today - timedelta(days=i), 79.4 if i < 7 else 80.0))
    assert client.patch("/v1/users/me/profile", headers=h(tok["Ann"]), json={"goal_weight_kg": 75}).status_code == 200
    monkeypatch.setattr(leaderboard, "_weights", lambda conn, uid, s, e: {
        r["date"]: r["value"] for r in conn.execute("SELECT date, value FROM daily_metrics WHERE user_id = %s", (uid,))})
    monkeypatch.setattr(leaderboard, "_strength_volume", lambda conn, uid, s, e: {e - timedelta(days=i): 1000.0 for i in range(35)})
    rows, _ = _rows(client, tok["Ann"], "lifestyle")
    assert rows["Ann"]["sub"] == {"on_time": 6, "food_met": 5, "days": 7}
    assert rows["Ann"]["score"] == round(100 * (0.5 * 6 / 7 + 0.5 * 5 / 7))
    rows, _ = _rows(client, tok["Ann"], "recomp")
    sub = rows["Ann"]["sub"]
    assert sub["mode"] == "lose" and sub["rate_pct_week"] == -0.75 and sub["volume_ratio"] == 1.0 and sub["protein_met"] == 6
    assert rows["Ann"]["score"] == round(0.4 * 100 + 0.4 * 100 + 0.2 * 100 * 6 / 7)
    # Ben shares training + nutrition adherence but not weight: not on the recomp board for Ann
    join(client, tok["Ben"], circle(client, tok["Ann"], "x")["invite"]["code"], ["training", "nutrition_summary", "sleep"])
    rows, _ = _rows(client, tok["Ann"], "recomp")
    assert rows["Ben"]["status"] == "not_shared"
    rows, _ = _rows(client, tok["Ann"], "lifestyle")
    assert rows["Ben"]["status"] == "ok"                      # sleep + nutrition_summary are enough here


def test_longevity_board_uses_four_weeks(client, world, dsn):
    from body_engine.web import views
    ids, tok = world
    a = ids["Ann"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        today = views.local_today(conn, a)
        for weeks, delta, pace in ((0, -2.0, 0.8), (4, -1.5, 0.9)):
            d = today - timedelta(days=7 * weeks)
            conn.execute("""INSERT INTO healthspan_weekly (user_id, week_start, as_of, chrono_age, body_age, delta_years,
                            pace_of_aging, algo_version) VALUES (%s, %s, %s, 40, %s, %s, %s, 't')""",
                         (a, d - timedelta(days=6), d, 40 + delta, delta, pace))
    rows, body = _rows(client, tok["Ann"], "longevity")
    assert body["window_days"] == 28
    assert rows["Ann"]["sub"] == {"gap_change_years": -0.5, "pace": 0.8}
    assert rows["Ann"]["score"] == round(0.6 * 75 + 0.4 * leaderboard.pace_score(0.8))


def test_ai_overview_only_shows_what_is_shared(client, world, dsn):
    ids, tok = world
    _scores(dsn, ids["Ann"], recovery=80)
    _scores(dsn, ids["Cat"], recovery=60)
    code = circle(client, tok["Ann"])["invite"]["code"]
    join(client, tok["Ben"], code)
    join(client, tok["Cat"], code, ["sleep"])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        out = run_tool(conn, ids["Ben"], "mcp:test", "get_friends_overview", {})
    by = {m["name"]: m for m in out["members"]}
    assert by["Ann"]["recovery"] == 80 and by["Ann"]["boards"]["body"].startswith("#1")
    assert "recovery" not in by["Cat"] and by["Cat"]["boards"]["body"] == "not_shared" and "scores" in by["Cat"]["not_shared"]
    assert by["Ben"]["self"] and "Dan" not in by
# ---------------------------------------------------------------- feature flag `friends` (docs/rollout.md)
def _flag(dsn, state, **kw):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, "friends", state, actor="test", **kw)


def test_flag_off_closes_everything_but_hand_made_shares(client, world, dsn):
    ids, tok = world
    a, b = ids["Ann"], ids["Ben"]
    join(client, tok["Ben"], circle(client, tok["Ann"])["invite"]["code"])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE shares SET scopes = '{weight}' WHERE owner_id = %s AND viewer_id = %s", (a, b))
    assert sees(client, tok["Ben"], a, "scores") == 200
    try:
        _flag(dsn, "off")
        # every circle endpoint is gone, circle grants stop at once, the hand-made grant stays
        assert client.get("/v1/friends", headers=h(tok["Ben"])).status_code == 404
        assert client.post("/v1/friends/circles", headers=h(tok["Ann"]), json={"name": "x"}).status_code == 404
        assert client.post("/v1/friends/join", headers=h(tok["Cat"]), json={"code": "AAAAA-BBBBB", "scopes": []}).status_code == 404
        assert sees(client, tok["Ben"], a, "scores") == 403 and sees(client, tok["Ann"], b, "scores") == 403
        assert can(dsn, b, a, "weight") and not can(dsn, b, a, "scores")
        me = client.get("/v1/web/me", headers=h(tok["Ben"])).json()
        assert "friends" not in me["flags"] and [v["scopes"] for v in me["viewable"] if v["id"] == a] == [["weight"]]
        # only Ben in the beta cohort: Ann's circle grant still needs the flag on for Ann too
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            flags.cohort_add(conn, b, "beta", actor="test")
        _flag(dsn, "cohort", cohort="beta")
        assert client.get("/v1/friends", headers=h(tok["Ben"])).status_code == 200
        assert client.get("/v1/friends", headers=h(tok["Ann"])).status_code == 404
        assert sees(client, tok["Ben"], a, "scores") == 403
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            flags.cohort_add(conn, a, "beta", actor="test")
        assert sees(client, tok["Ben"], a, "scores") == 200 and "friends" in client.get("/v1/web/me", headers=h(tok["Ben"])).json()["flags"]
    finally:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            for u in (a, b):
                flags.cohort_remove(conn, u, "beta", actor="test")
        _flag(dsn, "all")


def test_flag_min_consent(client, world, dsn):
    """The flag asks for consent 2026-10-22.2: Eve (older consent) has no Friends page until she confirms it."""
    ids, tok = world
    try:
        _flag(dsn, "all", min_consent=P.CONSENT_VERSION)
        assert client.get("/v1/friends", headers=h(tok["Eve"])).status_code == 404
        assert client.get("/v1/friends", headers=h(tok["Ann"])).status_code == 200
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert P.CONSENT_VERSION in flags.consent_versions_needed(conn, ids["Eve"])
            conn.execute("UPDATE users SET consent_version = %s WHERE id = %s", (P.CONSENT_VERSION, ids["Eve"]))
        flags.invalidate()
        assert client.get("/v1/friends", headers=h(tok["Eve"])).status_code == 200
    finally:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("UPDATE users SET consent_version = '2026-10-22.1' WHERE id = %s", (ids["Eve"],))
            flags.set_flag(conn, "friends", "all", clear_min_consent=True, actor="test")


def test_today_page_shell_has_both_tab_bars(client):
    """The page ships the old 饮食 tab (shown) and the 朋友 tab (hidden); app.js swaps them when the flag is on."""
    html = client.get("/app/").text
    assert '<button data-tab="food" class="tab">' in html and '<button data-tab="friends" class="tab" hidden>' in html
    js = client.get("/app/app.js").text
    assert 'friendsOn()' in js and "applyTabs();" in js


def test_flag_off_closes_leaderboard_and_ai_tool(client, world, dsn):
    ids, tok = world
    join(client, tok["Ben"], circle(client, tok["Ann"])["invite"]["code"])
    try:
        _flag(dsn, "off")
        assert client.get("/v1/friends/leaderboard", params={"board": "body"}, headers=h(tok["Ben"])).status_code == 404
        assert client.get("/v1/friends/overview", headers=h(tok["Ben"])).status_code == 404
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with pytest.raises(flags.FlagOff):
                run_tool(conn, ids["Ben"], "mcp:test", "get_friends_overview", {})
    finally:
        _flag(dsn, "all")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert run_tool(conn, ids["Ben"], "mcp:test", "get_friends_overview", {})["members"]


# ---------------------------------------------------------------- personal circles + referral onboarding (plan A, 2026-10-22)
def _person(dsn, name, language=None, consent_version=P.CONSENT_VERSION):
    """A fresh user whose personal circle is not settled yet; returns (id, write token)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("""INSERT INTO users (display_name, language, consent_at, consent_version) VALUES (%s, %s, now(), %s)
                              RETURNING id""", (name, language, consent_version)).fetchone()["id"]
        return uid, auth.create_token(conn, uid, "t", ["read", "write"])


def _referred(dsn, inviter, name):
    """`name` signs up with `inviter`'s referral code (#57), then confirms the consent; returns (id, write token)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        code = invites.referral_code(conn, inviter)["code"]
        new, _ = signin.find_or_create_user(conn, "email", f"{name}@example.com", f"{name}@example.com", True)
        assert invites.redeem(conn, new["id"], code)
        conn.execute("UPDATE users SET display_name = %s, consent_at = now(), consent_version = %s WHERE id = %s",
                     (name, P.CONSENT_VERSION, new["id"]))
        return new["id"], auth.create_token(conn, new["id"], "t", ["read", "write"])


def _offer(client, tok):
    r = client.get("/v1/friends/referral", headers=h(tok))
    assert r.status_code == 200, r.text
    return r.json()["offer"]


def test_personal_circle_made_once_on_first_use(client, dsn):
    pia, tok = _person(dsn, "Pia")
    over = client.get("/v1/friends", headers=h(tok)).json()
    assert [(c["name"], c["personal"], c["is_owner"], c["my_scopes"]) for c in over["circles"]] == \
        [("Pia 的圈子", True, True, list(P.DEFAULT_SCOPES))]
    assert len(client.get("/v1/friends", headers=h(tok)).json()["circles"]) == 1   # no second one
    cid = over["circles"][0]["id"]
    assert client.post(f"/v1/friends/circles/{cid}/invite", headers=h(tok)).status_code == 200   # a normal circle
    # dissolved by its owner: not made again
    assert client.delete(f"/v1/friends/circles/{cid}", headers=h(tok)).status_code == 204
    assert client.get("/v1/friends", headers=h(tok)).json()["circles"] == []
    # the owner's language
    _, tok_en = _person(dsn, "Quinn", language="en")
    assert [c["name"] for c in client.get("/v1/friends", headers=h(tok_en)).json()["circles"]] == ["Quinn's circle"]
    # no consent for circles yet: nothing made, tried again after confirming
    roy, tok_roy = _person(dsn, "Roy", consent_version="2026-10-22.1")
    assert client.get("/v1/friends", headers=h(tok_roy)).json()["circles"] == []
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE users SET consent_version = %s WHERE id = %s", (P.CONSENT_VERSION, roy))
    assert [c["name"] for c in client.get("/v1/friends", headers=h(tok_roy)).json()["circles"]] == ["Roy 的圈子"]


def test_owner_of_a_hand_made_circle_gets_no_personal_one(client, dsn):
    sam, tok = _person(dsn, "Sam")
    made = circle(client, tok, name="篮球")["circle"]
    c = client.get("/v1/friends", headers=h(tok)).json()["circles"]
    assert [(x["id"], x["name"], x["personal"]) for x in c] == [(made["id"], "篮球", False)]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT personal_circle_at FROM users WHERE id = %s", (sam,)).fetchone()["personal_circle_at"]
        assert store.home_circle(conn, sam) == made["id"]   # referrals invite to it
    # a member (not owner) of someone else's circle still gets their own
    tia, tok_tia = _person(dsn, "Tia")
    join(client, tok_tia, client.post(f"/v1/friends/circles/{made['id']}/invite", headers=h(tok)).json()["code"])
    names = [x["name"] for x in client.get("/v1/friends", headers=h(tok_tia)).json()["circles"]]
    assert sorted(names) == ["Tia 的圈子", "篮球"]


def test_referral_onboarding_join_or_not(client, dsn, monkeypatch):
    monkeypatch.setenv("BODY_REFERRALS_ENABLED", "1")
    una, tok_una = _person(dsn, "Una")
    vic, tok_vic = _referred(dsn, una, "Vic")
    offer = _offer(client, tok_vic)
    assert (offer["name"], offer["inviter_name"], offer["members"], offer["scopes"]) == \
        ("Una 的圈子", "Una", 1, list(P.DEFAULT_SCOPES))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:   # made for Una when Vic signed up
        assert conn.execute("SELECT circle_id FROM invite_redemptions WHERE user_id = %s", (vic,)).fetchone()["circle_id"] \
            == offer["circle_id"]
    assert not can(dsn, una, vic, "scores") and not can(dsn, vic, una, "scores")   # nothing before the tap
    # 「下一步」 with 「加入」 ticked: joins with the default scopes
    r = client.post("/v1/friends/join", headers=h(tok_vic), json={"invite_id": offer["invite_id"], "scopes": offer["scopes"]})
    assert r.status_code == 200 and r.json()["name"] == "Una 的圈子"
    assert can(dsn, una, vic, "scores") and can(dsn, vic, una, "training") and not can(dsn, una, vic, "weight")
    assert not can(dsn, una, vic, "ecg")
    assert _offer(client, tok_vic) is None
    # 「加入」 unticked: nothing happens, nothing shared; the invite waits on the Friends page until declined
    wes, tok_wes = _referred(dsn, una, "Wes")
    offer = _offer(client, tok_wes)
    assert offer and not can(dsn, una, wes, "scores") and not can(dsn, wes, vic, "scores")
    assert [i["id"] for i in client.get("/v1/friends", headers=h(tok_wes)).json()["invites"]] == [offer["invite_id"]]
    assert client.delete(f"/v1/friends/invites/{offer['invite_id']}", headers=h(tok_wes)).status_code == 204
    assert _offer(client, tok_wes) is None and client.get("/v1/friends", headers=h(tok_wes)).json()["invites"] == []


def test_friends_of_friends_stay_invisible(client, dsn, monkeypatch):
    """A invites B, B joins A's circle; B invites C, C joins B's circle: A and C never see each other."""
    monkeypatch.setenv("BODY_REFERRALS_ENABLED", "1")
    a, tok_a = _person(dsn, "Abe")
    b, tok_b = _referred(dsn, a, "Bea")
    ob = _offer(client, tok_b)
    assert client.post("/v1/friends/join", headers=h(tok_b), json={"invite_id": ob["invite_id"], "scopes": ob["scopes"]}).status_code == 200
    c, tok_c = _referred(dsn, b, "Cy")
    oc = _offer(client, tok_c)
    assert oc["name"] == "Bea 的圈子" and oc["circle_id"] != ob["circle_id"]   # B's own circle, not A's
    assert client.post("/v1/friends/join", headers=h(tok_c), json={"invite_id": oc["invite_id"], "scopes": list(P.CIRCLE_SCOPES)}).status_code == 200
    assert can(dsn, a, b, "scores") and can(dsn, b, c, "scores") and can(dsn, c, b, "scores")
    for scope in P.CIRCLE_SCOPES:
        assert not can(dsn, a, c, scope) and not can(dsn, c, a, scope)
    assert sees(client, tok_a, c, "scores") == 403 and sees(client, tok_c, a, "scores") == 403
    members = lambda tok, cid: [m["display_name"] for m in next(x for x in client.get("/v1/friends", headers=h(tok)).json()["circles"] if x["id"] == cid)["members"]]
    assert members(tok_a, ob["circle_id"]) == ["Abe", "Bea"] and members(tok_c, oc["circle_id"]) == ["Bea", "Cy"]
    names_a = {m["display_name"] for m in client.get("/v1/friends/overview", headers=h(tok_a)).json()["members"]}
    assert names_a == {"Bea"}


def test_flag_off_referral_changes_nothing_then_catches_up(client, dsn, monkeypatch):
    monkeypatch.setenv("BODY_REFERRALS_ENABLED", "1")
    try:
        _flag(dsn, "off")
        xia, tok_xia = _person(dsn, "Xia")
        yan, tok_yan = _referred(dsn, xia, "Yan")
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert conn.execute("SELECT circle_id FROM invite_redemptions WHERE user_id = %s", (yan,)).fetchone()["circle_id"] is None
            assert conn.execute("SELECT count(*) AS n FROM circles WHERE owner_id = ANY(%s)", ([xia, yan],)).fetchone()["n"] == 0
            assert conn.execute("SELECT count(*) AS n FROM circle_invites WHERE invitee_user_id = %s", (yan,)).fetchone()["n"] == 0
            assert conn.execute("SELECT count(*) AS n FROM users WHERE id = ANY(%s) AND personal_circle_at IS NOT NULL",
                                ([xia, yan],)).fetchone()["n"] == 0
        assert client.get("/v1/friends/referral", headers=h(tok_yan)).status_code == 404
        # on for the invitee only: no offer, nothing made for the inviter, no error
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            flags.cohort_add(conn, yan, "beta", actor="test")
        _flag(dsn, "cohort", cohort="beta")
        assert _offer(client, tok_yan) is None
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert conn.execute("SELECT count(*) AS n FROM circles WHERE owner_id = %s", (xia,)).fetchone()["n"] == 0
        # on for both: the invite is made now and onboarding / the Friends page can offer it
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            flags.cohort_add(conn, xia, "beta", actor="test")
        offer = _offer(client, tok_yan)
        assert offer["name"] == "Xia 的圈子" and offer["inviter_name"] == "Xia"
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert conn.execute("SELECT circle_id FROM invite_redemptions WHERE user_id = %s", (yan,)).fetchone()["circle_id"] \
                == offer["circle_id"]
    finally:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            for u in (xia, yan):
                flags.cohort_remove(conn, u, "beta", actor="test")
        _flag(dsn, "all")
