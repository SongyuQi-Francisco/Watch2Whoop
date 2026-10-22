"""Avatars people upload themselves (#200, avatars/): who may fetch a picture, type / size checks, replace / delete, the
`avatars` flag (gates uploading only), owner-only circle logos, the upload rate limit, URLs in the Friends pages.
Test database body_test_avatars (BODY_TEST_DB_NAME_AVATARS), recreated on every run; skipped without Postgres. All names are made up."""
import io
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.avatars import api as avatars_api, params as AP
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import admin, cards, leaderboard, params as P
from body_engine.images import params as IP
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_AVATARS", "body_test_avatars")


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
        flags.set_flag(conn, "avatars", "all", actor="test")
    return dsn


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


@pytest.fixture(autouse=True)
def clean(dsn):
    avatars_api._uploads._events.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, "avatars", "all", actor="test")
        conn.execute("DELETE FROM circles")
        conn.execute("DELETE FROM shares")
        conn.execute("UPDATE users SET avatar_image_id = NULL")
        conn.execute("DELETE FROM images")
        conn.execute("DELETE FROM users")
    flags.invalidate()
    yield


def h(t, **extra):
    return {"Authorization": f"Bearer {t}", **extra}


def member(conn, name):
    uid = conn.execute("""INSERT INTO users (display_name, consent_at, consent_version, personal_circle_at)
                          VALUES (%s, now(), %s, now()) RETURNING id""", (name, P.CONSENT_VERSION)).fetchone()["id"]
    return uid, auth.create_token(conn, uid, "t", ["read", "write"])


def png(size=(300, 200), fmt="PNG", **save):
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(buf, fmt, **save)
    return buf.getvalue()


def circle_of(conn, owner, *others, name="Example Club"):
    c = admin.create_circle(conn, name, owner)
    for u in others:
        conn.execute("INSERT INTO circle_members (circle_id, user_id, scopes) VALUES (%s, %s, %s)", (c["id"], u, ["training"]))
    return c["id"]


def put(client, tok, path, data, ctype="image/png"):
    return client.put(path, content=data, headers=h(tok, **{"Content-Type": ctype}))


def test_upload_replace_delete_and_who_can_see(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ann, atok = member(conn, "Ann")
        ben, btok = member(conn, "Ben")
        eve, etok = member(conn, "Eve")
        circle_of(conn, ann, ben)
    assert client.get(f"/v1/avatars/users/{ann}", headers=h(btok)).status_code == 404      # no picture yet
    r = put(client, atok, "/v1/avatars/me", png((400, 300), fmt="JPEG"), "image/jpeg")
    assert r.status_code == 200
    url = r.json()["avatar"]
    assert url.startswith(f"/v1/avatars/users/{ann}?v=")
    own = client.get(url, headers=h(atok))
    assert own.status_code == 200 and own.headers["content-type"] == "image/webp" and "private" in own.headers["cache-control"]
    assert Image.open(io.BytesIO(own.content)).size == (IP.SIZE, IP.SIZE)
    assert client.get(url, headers=h(atok, **{"If-None-Match": own.headers["etag"]})).status_code == 304
    assert client.get(url, headers=h(btok)).status_code == 200                                # same circle
    assert client.get(url, headers=h(etok)).status_code == 404                                # not in a circle with her
    assert client.get(url).status_code == 401
    # it shows up where the pages look: Friends cards (Ben sees Ann's, Eve sees nothing), /web/me
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert cards.overview(conn, ben)["members"][0]["avatar"] == url
        rows = {r["id"]: r for r in leaderboard.board(conn, ben, "body")["rows"]}
        assert rows[ann]["avatar"] == url and rows[ben]["avatar"] is None
    assert client.get("/v1/web/me", headers=h(atok)).json()["me"]["avatar"] == url
    assert client.get("/v1/web/me", headers=h(btok)).json()["me"]["avatar"] is None
    # replacing deletes the old image row; deleting goes back to no picture
    r2 = put(client, atok, "/v1/avatars/me", png(fmt="WEBP"), "image/webp")
    assert r2.json()["avatar"] != url
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM images").fetchone()["n"] == 1
    assert client.delete("/v1/avatars/me", headers=h(atok)).status_code == 204
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM images").fetchone()["n"] == 0
    assert client.get(r2.json()["avatar"], headers=h(atok)).status_code == 404
    assert client.get("/v1/web/me", headers=h(atok)).json()["me"]["avatar"] is None


def test_type_and_size_checks(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        _, tok = member(conn, "Ann")
    assert put(client, tok, "/v1/avatars/me", b"").status_code == 400
    assert put(client, tok, "/v1/avatars/me", b"not an image").status_code == 400
    assert put(client, tok, "/v1/avatars/me", png(fmt="GIF"), "image/gif").status_code == 400
    assert put(client, tok, "/v1/avatars/me", png((20, 20))).status_code == 400                # too small
    big = b"\x89PNG" + b"0" * IP.MAX_UPLOAD_BYTES
    r = put(client, tok, "/v1/avatars/me", big)
    assert r.status_code == 413
    # the declared type is not trusted: a PNG sent as text/plain is still a fine PNG
    assert put(client, tok, "/v1/avatars/me", png(), "text/plain").status_code == 200
    # English texts follow X-Body-Lang
    r = client.put("/v1/avatars/me", content=b"x", headers=h(tok, **{"X-Body-Lang": "en"}))
    assert r.status_code == 400 and "Can't read" in r.json()["detail"]


def test_flag_gates_uploading_only(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ann, atok = member(conn, "Ann")
        ben, btok = member(conn, "Ben")
        cid = circle_of(conn, ann, ben)
        admin.set_avatar(conn, cid, png())                                                  # set by the admin (CLI path)
        flags.set_flag(conn, "avatars", "off", actor="test")
    flags.invalidate()
    assert put(client, atok, "/v1/avatars/me", png()).status_code == 404
    assert client.delete("/v1/avatars/me", headers=h(atok)).status_code == 404
    assert put(client, atok, f"/v1/avatars/circles/{cid}", png()).status_code == 404
    assert client.delete(f"/v1/avatars/circles/{cid}", headers=h(atok)).status_code == 404
    circles = client.get("/v1/friends", headers=h(btok)).json()["circles"]
    shown = next(c for c in circles if c["id"] == cid)["avatar"]
    assert shown and client.get(shown, headers=h(btok)).status_code == 200                  # still shown with the flag off


def test_circle_logo_owner_only_members_only(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        owner, otok = member(conn, "Org")
        ben, btok = member(conn, "Ben")
        eve, etok = member(conn, "Eve")
        cid = circle_of(conn, owner, ben)
        personal = conn.execute("SELECT id FROM circles WHERE owner_id = %s AND personal", (owner,)).fetchone()
    assert put(client, btok, f"/v1/avatars/circles/{cid}", png()).status_code == 403        # a member, not the owner
    assert put(client, etok, f"/v1/avatars/circles/{cid}", png()).status_code == 404        # not in the circle
    assert client.delete(f"/v1/avatars/circles/{cid}", headers=h(btok)).status_code == 403
    r = put(client, otok, f"/v1/avatars/circles/{cid}", png())
    assert r.status_code == 200 and r.json()["avatar"].startswith(f"/v1/friends/circles/{cid}/avatar?v=")
    assert client.get(r.json()["avatar"], headers=h(btok)).status_code == 200
    assert client.get(r.json()["avatar"], headers=h(etok)).status_code == 404               # members only
    assert put(client, otok, f"/v1/avatars/circles/{cid}", b"junk").status_code == 400
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM images").fetchone()["n"] == 1        # the failed upload left nothing
    if personal:                                                                            # everyone's own circle has no logo
        assert put(client, otok, f"/v1/avatars/circles/{personal['id']}", png()).status_code == 400
    assert client.delete(f"/v1/avatars/circles/{cid}", headers=h(otok)).status_code == 204
    assert client.get("/v1/friends", headers=h(btok)).json()["circles"][0]["avatar"] is None
    # dissolving a circle takes its logo along
    put(client, otok, f"/v1/avatars/circles/{cid}", png())
    assert client.delete(f"/v1/friends/circles/{cid}", headers=h(otok)).status_code == 204
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM images").fetchone()["n"] == 0


def test_read_only_token_cannot_upload_and_rate_limit(client, dsn, monkeypatch):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ann, atok = member(conn, "Ann")
        ro = auth.create_token(conn, ann, "ro", ["read"])
    assert put(client, ro, "/v1/avatars/me", png()).status_code == 403
    for _ in range(AP.UPLOADS_PER_WINDOW):
        assert put(client, atok, "/v1/avatars/me", b"junk").status_code == 400             # refused uploads count too
    r = put(client, atok, "/v1/avatars/me", png())
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
