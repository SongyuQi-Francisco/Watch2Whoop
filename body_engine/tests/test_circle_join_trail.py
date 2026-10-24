"""Circle join trail (#269, migration 745, friends/trail.py): every way into a circle records how (created / invite code /
referral invite / circle activation code) and who invited; only the circle's owner sees it, and only with the
circle_join_trail flag on; the backfill of migration 745 rebuilds the trail of members who joined before it.
Test database body_test_circle_trail (BODY_TEST_DB_NAME_CIRCLE_TRAIL), recreated on every run; skipped without Postgres."""
import os
from datetime import timedelta
from importlib import resources

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import codes, params as P, store
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CIRCLE_TRAIL", "body_test_circle_trail")
NAMES = ("Ann", "Ben", "Cat", "Dan")


@pytest.fixture(scope="module")
def dsn():
    if not TEST_DB.startswith("body_test_"):
        pytest.exit("the test DB must be a body_test_* DB")
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
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, birth_date, height_cm, consent_at, consent_version,
                                                     personal_circle_at)
                                  VALUES (%s, '1990-01-01', 175, now(), %s, now()) RETURNING id""",
                               (n, P.CONSENT_VERSION)).fetchone()["id"] for n in NAMES}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        flags.set_flag(conn, "friends", "all", actor="test")
        flags.set_flag(conn, "circle_join_trail", "all", actor="test")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


@pytest.fixture(autouse=True)
def clean(dsn, world):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM circles")
        conn.execute("DELETE FROM shares")
        flags.set_flag(conn, "circle_join_trail", "all", actor="test")
    yield


def h(t):
    return {"Authorization": f"Bearer {t}"}


def circle(client, tok, name):
    r = client.post("/v1/friends/circles", json={"name": name}, headers=h(tok))
    assert r.status_code == 201, r.text
    return r.json()


def members(client, tok, cid):
    c = next(c for c in client.get("/v1/friends", headers=h(tok)).json()["circles"] if c["id"] == cid)
    return {m["display_name"]: m for m in c["members"]}


def test_owner_sees_how_each_member_came_in(client, dsn, world):
    ids, tok = world
    made = circle(client, tok["Ann"], "家")
    cid = made["circle"]["id"]
    assert client.post("/v1/friends/join", json={"code": made["invite"]["code"], "scopes": ["scores"]},
                       headers=h(tok["Ben"])).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:   # Cat signed up with Ann's referral code
        conn.execute("UPDATE circles SET personal = true WHERE id = %s", (cid,))   # referrals invite to a personal circle only
        assert store.referral_invites(conn, ids["Ann"], ids["Cat"]) == cid
        inv = conn.execute("SELECT id FROM circle_invites WHERE invitee_user_id = %s", (ids["Cat"],)).fetchone()["id"]
        k = codes.create(conn, cid, "Family Club 1", 5)
    assert client.post("/v1/friends/join", json={"invite_id": inv, "scopes": ["scores"]}, headers=h(tok["Cat"])).status_code == 200
    assert client.post("/v1/friends/join", json={"code": "Family Club 1", "scopes": ["scores"]},
                       headers=h(tok["Dan"])).status_code == 200

    m = members(client, tok["Ann"], cid)
    assert m["Ann"]["join"] == {"via": "created", "by": None, "code": None, "inferred": False}
    assert m["Ben"]["join"]["via"] == "invite_code" and m["Ben"]["join"]["by"]["id"] == ids["Ann"] and m["Ben"]["join"]["by"]["me"]
    assert m["Cat"]["join"]["via"] == "referral" and m["Cat"]["join"]["by"]["display_name"] == "Ann"
    assert m["Dan"]["join"] == {"via": "circle_code", "by": None, "code": k["code"], "inferred": False}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT invite_id, circle_code_id FROM circle_members WHERE circle_id = %s AND user_id = %s",
                           (cid, ids["Dan"])).fetchone()
        assert row["circle_code_id"] == k["id"] and row["invite_id"] is None
        assert conn.execute("SELECT invite_id FROM circle_members WHERE circle_id = %s AND user_id = %s",
                            (cid, ids["Cat"])).fetchone()["invite_id"] == inv


def test_members_never_see_it_and_the_flag_hides_it(client, dsn, world):
    ids, tok = world
    made = circle(client, tok["Ann"], "家")
    cid = made["circle"]["id"]
    client.post("/v1/friends/join", json={"code": made["invite"]["code"], "scopes": ["scores"]}, headers=h(tok["Ben"]))
    assert all("join" not in m for m in members(client, tok["Ben"], cid).values())       # a member: never
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, "circle_join_trail", "off", actor="test")
    assert all("join" not in m for m in members(client, tok["Ann"], cid).values())       # the owner, flag off
    with psycopg.connect(dsn, row_factory=dict_row) as conn:                              # still recorded while off
        assert conn.execute("SELECT join_via FROM circle_members WHERE circle_id = %s AND user_id = %s",
                            (cid, ids["Ben"])).fetchone()["join_via"] == "invite_code"


def test_owner_who_took_over_sees_the_trail(client, dsn, world):
    ids, tok = world
    made = circle(client, tok["Ann"], "家")
    cid = made["circle"]["id"]
    for n in ("Ben", "Cat"):
        client.post("/v1/friends/join", json={"code": made["invite"]["code"], "scopes": ["scores"]}, headers=h(tok[n]))
    assert client.delete(f"/v1/friends/circles/{cid}/me", headers=h(tok["Ann"])).status_code == 204
    m = members(client, tok["Ben"], cid)          # Ben is the owner now: Cat was invited by Ann, who left
    assert m["Cat"]["join"]["via"] == "invite_code" and m["Cat"]["join"]["by"]["display_name"] == "Ann"
    assert not m["Cat"]["join"]["by"]["me"]
    assert "join" not in members(client, tok["Cat"], cid)["Ben"]


def _rerun_745(conn):
    sql = resources.files("body_engine.migrations").joinpath("745_circle_join_trail.sql").read_text()
    conn.execute(sql)


def test_backfill_rebuilds_the_trail(dsn, world):
    """Members who joined before migration 745 (no trail): each join wrote its rows in one transaction (same now())."""
    ids, _ = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid = conn.execute("INSERT INTO circles (name, owner_id, created_at) VALUES ('旧圈', %s, now() - interval '9 days') "
                           "RETURNING id, created_at", (ids["Ann"],)).fetchone()
        cid, t0 = cid["id"], cid["created_at"]

        def add(u, at):
            conn.execute("INSERT INTO circle_members (circle_id, user_id, joined_at) VALUES (%s, %s, %s)", (cid, ids[u], at))

        add("Ann", t0)
        # two codes live when Ben joined → which one is a guess; one code live when Cat joined → exact
        c1 = conn.execute("""INSERT INTO circle_invites (circle_id, created_by, code_sha256, expires_at, used_count, created_at,
                                                         revoked_at)
                             VALUES (%s, %s, 'sha-1', NULL, 1, %s + interval '1 day', %s + interval '5 days') RETURNING id""",
                          (cid, ids["Ann"], t0, t0)).fetchone()["id"]
        c2 = conn.execute("""INSERT INTO circle_invites (circle_id, created_by, code_sha256, expires_at, used_count, created_at)
                             VALUES (%s, %s, 'sha-2', NULL, 2, %s + interval '2 days') RETURNING id""",
                          (cid, ids["Ann"], t0)).fetchone()["id"]
        add("Ben", t0 + timedelta(days=3))
        add("Cat", t0 + timedelta(days=6))
        # Dan: an accepted referral invite, used up in the joining transaction
        t_dan = t0 + timedelta(days=7)
        ref = conn.execute("""INSERT INTO circle_invites (circle_id, created_by, invitee_user_id, expires_at, used_count,
                                                          revoked_at, created_at)
                              VALUES (%s, %s, %s, %s + interval '30 days', 1, %s, %s) RETURNING id""",
                           (cid, ids["Ann"], ids["Dan"], t0, t_dan, t0)).fetchone()["id"]
        add("Dan", t_dan)
        _rerun_745(conn)
        got = {r["user_id"]: r for r in conn.execute("SELECT * FROM circle_members WHERE circle_id = %s", (cid,))}
        assert got[ids["Ann"]]["join_via"] == "created"
        assert (got[ids["Ben"]]["join_via"], got[ids["Ben"]]["invite_id"], got[ids["Ben"]]["join_inferred"]) == ("invite_code", c2, True)
        assert got[ids["Ben"]]["invited_by"] == ids["Ann"]
        assert (got[ids["Cat"]]["invite_id"], got[ids["Cat"]]["join_inferred"]) == (c2, False)   # c1 was revoked by then
        assert (got[ids["Dan"]]["join_via"], got[ids["Dan"]]["invite_id"], got[ids["Dan"]]["invited_by"]) == ("referral", ref, ids["Ann"])
        assert c1 != c2
        # running it again changes nothing; a member nothing explains stays unrecorded
        conn.execute("UPDATE circle_members SET join_via = NULL, invite_id = NULL, invited_by = NULL WHERE circle_id = %s "
                     "AND user_id = %s", (cid, ids["Cat"]))
        conn.execute("DELETE FROM circle_invites WHERE id = %s", (c2,))
        _rerun_745(conn)
        cat = conn.execute("SELECT join_via FROM circle_members WHERE circle_id = %s AND user_id = %s", (cid, ids["Cat"])).fetchone()
        assert cat["join_via"] is None
        assert conn.execute("SELECT join_via FROM circle_members WHERE circle_id = %s AND user_id = %s",
                            (cid, ids["Dan"])).fetchone()["join_via"] == "referral"


def test_backfill_circle_code(dsn, world):
    ids, _ = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        c = conn.execute("INSERT INTO circles (name, owner_id) VALUES ('俱乐部', %s) RETURNING id, created_at", (ids["Ann"],)).fetchone()
        conn.execute("INSERT INTO circle_members (circle_id, user_id, joined_at) VALUES (%s, %s, %s)", (c["id"], ids["Ann"], c["created_at"]))
        k = codes.create(conn, c["id"], "Old Club Code", 5)
        conn.execute("""INSERT INTO circle_code_uses (code_id, user_id, new_account, joined_at)
                        VALUES (%s, %s, false, now() - interval '1 hour')""", (k["id"], ids["Ben"]))
        conn.execute("INSERT INTO circle_members (circle_id, user_id, joined_at) VALUES (%s, %s, now() - interval '1 hour')",
                     (c["id"], ids["Ben"]))
        _rerun_745(conn)
        ben = conn.execute("SELECT * FROM circle_members WHERE circle_id = %s AND user_id = %s", (c["id"], ids["Ben"])).fetchone()
        assert (ben["join_via"], ben["circle_code_id"], ben["invited_by"]) == ("circle_code", k["id"], None)
