"""Private nicknames for circle members (#212, friends/remarks.py, flag member_remarks): set / clear / length, only with a
common circle (404 otherwise, the same as for nobody), hidden after leaving and back after rejoining, never seen by the
person it is about or by anyone else in the circle, every place a co-member is shown (cards, leaderboard, circle member
list, /v1/web/me viewable, feed comments, cheers received), the AI's `nickname`, the flag, account deletion.
Test database body_test_member_remarks (BODY_TEST_DB_NAME_MEMBER_REMARKS), recreated on every run; skipped without Postgres.
All names are made up."""
import os
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import admin, cards, feed, leaderboard, params as P, remarks, store
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_MEMBER_REMARKS", "body_test_member_remarks")


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_accept", ""):
        pytest.exit("the test DB must not be a real DB")
    try:
        admin_conn = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin_conn:
        admin_conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin_conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
        flags.set_flag(conn, "friends", "all", actor="test")
    return dsn


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


@pytest.fixture(autouse=True)
def clean(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, remarks.FLAG, "all", actor="test")
        conn.execute("DELETE FROM circles")
        conn.execute("DELETE FROM shares")
        conn.execute("DELETE FROM users")
    flags.invalidate()
    yield


def h(t, **extra):
    return {"Authorization": f"Bearer {t}", **extra}


def member(conn, name, scopes=("read", "write")):
    uid = conn.execute("""INSERT INTO users (display_name, consent_at, consent_version, personal_circle_at)
                          VALUES (%s, now(), %s, now()) RETURNING id""", (name, P.CONSENT_VERSION)).fetchone()["id"]
    return uid, auth.create_token(conn, uid, "t", list(scopes))


def circle_of(conn, owner, *others, name="Example Club"):
    c = admin.create_circle(conn, name, owner)
    for u in others:
        conn.execute("INSERT INTO circle_members (circle_id, user_id, scopes) VALUES (%s, %s, %s)",
                     (c["id"], u, list(P.DEFAULT_SCOPES)))
        store.sync_user(conn, u)
    return c["id"]


def put(client, tok, target, text, **hdr):
    return client.put(f"/v1/friends/remarks/{target}", json={"remark": text}, headers=h(tok, **hdr))


@pytest.fixture
def abc(dsn):
    """Ann, Bo and Cy in one circle; Eve outside."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        a, at = member(conn, "Ann Example")
        b, bt = member(conn, "Bo Sample")
        c, ct = member(conn, "Cy Demo")
        e, et = member(conn, "Eve Outside")
        cid = circle_of(conn, a, b, c)
    return {"a": a, "at": at, "b": b, "bt": bt, "c": c, "ct": ct, "e": e, "et": et, "cid": cid}


def _everything(client, dsn, viewer, tok) -> str:
    """Every response a viewer's pages read, as one string (to check a remark never leaks into it)."""
    parts = [client.get(p, headers=h(tok)).text for p in
             ("/v1/friends", "/v1/friends/overview", "/v1/web/me", *(f"/v1/friends/leaderboard?board={b}" for b in P.BOARDS))]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        parts.append(repr(leaderboard.ai_overview(conn, viewer)))
    return "\n".join(parts)


def test_set_shown_everywhere_for_me_only(client, dsn, abc):
    a, b, c = abc["a"], abc["b"], abc["c"]
    r = put(client, abc["at"], b, "  Bobby  ")
    assert r.status_code == 200 and r.json() == {"target_id": b, "remark": "Bobby"}        # trimmed
    # Ann sees it next to the original name: cards, leaderboard, circle list, viewable, AI
    ov = client.get("/v1/friends/overview", headers=h(abc["at"])).json()
    by = {m["id"]: m for m in ov["members"]}
    assert by[b]["remark"] == "Bobby" and by[b]["display_name"] == "Bo Sample" and by[c]["remark"] is None
    assert ov["remark_max"] == P.REMARK_MAX and "remark" not in ov["me"]
    rows = {r["id"]: r for r in client.get("/v1/friends/leaderboard?board=body", headers=h(abc["at"])).json()["rows"]}
    assert rows[b]["remark"] == "Bobby" and rows[b]["display_name"] == "Bo Sample" and rows[a]["remark"] is None
    circle = client.get("/v1/friends", headers=h(abc["at"])).json()["circles"][0]
    assert {m["id"]: m["remark"] for m in circle["members"]} == {a: None, b: "Bobby", c: None}
    viewable = {v["id"]: v for v in client.get("/v1/web/me", headers=h(abc["at"])).json()["viewable"]}
    assert viewable[b]["remark"] == "Bobby" and viewable[b]["display_name"] == "Bo Sample" and "remark" not in viewable[c]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ai = {m["name"]: m for m in leaderboard.ai_overview(conn, a)["members"]}
    assert ai["Bo Sample"]["nickname"] == "Bobby" and "nickname" not in ai["Cy Demo"]
    # Bo (the person it is about) and Cy (same circle) never see it anywhere
    assert "Bobby" not in _everything(client, dsn, b, abc["bt"])
    assert "Bobby" not in _everything(client, dsn, c, abc["ct"])


def test_feed_comments_and_cheers_use_my_remark(client, dsn, abc):
    a, b = abc["a"], abc["b"]
    assert put(client, abc["at"], b, "Bobby").status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        post = conn.execute("INSERT INTO circle_feed_posts (user_id, date) VALUES (%s, %s) RETURNING id",
                            (a, date.today())).fetchone()["id"]
        conn.execute("INSERT INTO circle_feed_comments (post_id, user_id, body) VALUES (%s, %s, 'nice one (example)')", (post, b))
        assert [c["user"]["remark"] for c in feed.comments(conn, a, post)] == ["Bobby"]
        assert [c["user"]["remark"] for c in feed._previews(conn, a, [post])[post]] == ["Bobby"]
        assert [c["user"].get("remark") for c in feed.comments(conn, abc["c"], post)] == [None]   # Cy: plain name
    assert client.post("/v1/friends/nudges", json={"to": a, "kind": "clap"}, headers=h(abc["bt"])).status_code == 200
    assert client.get("/v1/friends/overview", headers=h(abc["at"])).json()["me"]["received"]["clap"] == ["Bobby"]


def test_only_with_a_common_circle(client, dsn, abc):
    no_circle = put(client, abc["at"], abc["e"], "Evie")
    nobody = put(client, abc["at"], 999999, "Ghost")
    assert no_circle.status_code == nobody.status_code == 404 and no_circle.json() == nobody.json()   # same answer
    assert put(client, abc["at"], abc["a"], "Me").status_code == 400                                  # not yourself
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM circle_member_remarks").fetchone()["n"] == 0


def test_leaving_hides_rejoining_brings_back(client, dsn, abc):
    a, b = abc["a"], abc["b"]
    assert put(client, abc["at"], b, "Bobby").status_code == 200
    assert client.delete(f"/v1/friends/circles/{abc['cid']}/me", headers=h(abc["bt"])).status_code == 204
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert remarks.for_viewer(conn, a, [b]) == {}                                  # not shown any more
        assert conn.execute("SELECT remark FROM circle_member_remarks").fetchone()["remark"] == "Bobby"   # kept
    assert put(client, abc["at"], b, "Changed").status_code == 404                    # and can't be changed meanwhile
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO circle_members (circle_id, user_id, scopes) VALUES (%s, %s, %s)",
                     (abc["cid"], b, list(P.DEFAULT_SCOPES)))
        store.sync_user(conn, b)
    by = {m["id"]: m for m in client.get("/v1/friends/overview", headers=h(abc["at"])).json()["members"]}
    assert by[b]["remark"] == "Bobby"


def test_cross_user_b_cannot_read_or_change_a_remark(client, dsn, abc):
    a, b = abc["a"], abc["b"]
    assert put(client, abc["at"], b, "Bobby").status_code == 200
    # Bo writing "about Bo" is refused; Bo writing about Ann only makes Bo's own row
    assert put(client, abc["bt"], b, "Hacked").status_code == 400
    assert put(client, abc["bt"], a, "Annie").status_code == 200
    assert client.delete(f"/v1/friends/remarks/{a}", headers=h(abc["bt"])).status_code == 204   # clears Bo's only
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT viewer_id, target_id, remark FROM circle_member_remarks").fetchall()
    assert rows == [{"viewer_id": a, "target_id": b, "remark": "Bobby"}]
    assert "Bobby" not in _everything(client, dsn, b, abc["bt"])
    # no endpoint takes someone else's id as the viewer; a read-only token can't write at all
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ro = auth.create_token(conn, b, "ro", ["read"])
    assert put(client, ro, a, "Annie").status_code == 403


def test_length_clear_and_english_errors(client, dsn, abc):
    b = abc["b"]
    assert put(client, abc["at"], b, "x" * P.REMARK_MAX).status_code == 200
    long = put(client, abc["at"], b, "x" * (P.REMARK_MAX + 1))
    assert long.status_code == 400 and str(P.REMARK_MAX) in long.json()["detail"]
    en = put(client, abc["at"], b, "x" * (P.REMARK_MAX + 1), **{"X-Body-Lang": "en"})
    assert en.json()["detail"] == f"A nickname is at most {P.REMARK_MAX} characters"
    assert put(client, abc["at"], abc["a"], "Me", **{"X-Body-Lang": "en"}).json()["detail"] == "You can't set a nickname for yourself"
    assert put(client, abc["at"], b, "   ").json() == {"target_id": b, "remark": None}             # blank = clear
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM circle_member_remarks").fetchone()["n"] == 0
    assert put(client, abc["at"], b, "第一行\n第二行").json()["remark"] == "第一行 第二行"                # one line


def test_flag_off_hides_and_refuses(client, dsn, abc):
    b = abc["b"]
    assert put(client, abc["at"], b, "Bobby").status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, remarks.FLAG, "off", actor="test")
    flags.invalidate()
    assert put(client, abc["at"], b, "Other").status_code == 404
    assert client.delete(f"/v1/friends/remarks/{b}", headers=h(abc["at"])).status_code == 404
    assert "Bobby" not in _everything(client, dsn, abc["a"], abc["at"])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, remarks.FLAG, "all", actor="test")
    flags.invalidate()
    assert "Bobby" in client.get("/v1/friends/overview", headers=h(abc["at"])).text


def test_deleting_an_account_deletes_both_directions(client, dsn, abc):
    from body_engine.account import delete
    a, b, c = abc["a"], abc["b"], abc["c"]
    assert put(client, abc["at"], b, "Bobby").status_code == 200
    assert put(client, abc["bt"], a, "Annie").status_code == 200
    assert put(client, abc["ct"], b, "B").status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        delete._delete_rows(conn, b)
        assert conn.execute("SELECT count(*) AS n FROM circle_member_remarks").fetchone()["n"] == 0
