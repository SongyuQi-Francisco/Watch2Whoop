"""Circle activation codes and circle avatars (#199, friends/codes.py, friends/admin.py, images/): matching rules, the use cap
(also under concurrent sign-ups), expiry / disable, the new-account path (nothing reaches the circle before the consent,
then the circle's default scopes) and the existing-account path (Friends page), leaving, the member cap, the CLI helpers,
and avatars (type / size checks, metadata removed, members only).
Test database body_test_circle_codes (BODY_TEST_DB_NAME_CIRCLE_CODES), recreated on every run; skipped without Postgres.
All names and codes are made up."""
import io
import os
import threading
from datetime import datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, cli
from body_engine.account import api as account_api, signin
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import admin, api as friends_api, codes, params as P, store
from body_engine.images import params as IP, store as images
from body_engine.profile import consent
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CIRCLE_CODES", "body_test_circle_codes")
CODE = "Example Lifting Club"


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
def clean(dsn, monkeypatch):
    monkeypatch.setattr(account_api, "start_provisioning", lambda uid: None)   # no OW / SparkyFitness here
    friends_api._failures._events.clear()
    from body_engine.web import login_limit
    for lim in vars(login_limit).values():
        if isinstance(lim, login_limit.Limiter):
            lim._events.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM circles")
        conn.execute("DELETE FROM shares")
        conn.execute("DELETE FROM images")
        conn.execute("DELETE FROM users")
    yield


def h(t, **extra):
    return {"Authorization": f"Bearer {t}", **extra}


def member(conn, name):
    """An active account that agreed to the circle consent."""
    uid = conn.execute("""INSERT INTO users (display_name, consent_at, consent_version, personal_circle_at)
                          VALUES (%s, now(), %s, now()) RETURNING id""", (name, P.CONSENT_VERSION)).fetchone()["id"]
    return uid, auth.create_token(conn, uid, "t", ["read", "write"])


def newcomer(conn, n):
    """A signed-in `pending` account (self-service sign-up, before the invite step)."""
    user, new = signin.find_or_create_user(conn, "email", f"new{n}@example.com", f"new{n}@example.com", True)
    assert new and user["status"] == "pending"
    return user["id"], auth.create_token(conn, user["id"], "app", ["read", "write"])


def club(conn, max_uses=50, max_members=None, **kw):
    owner, tok = member(conn, "Org")
    c = admin.create_circle(conn, "Example Lifting Club", owner, max_members=max_members)
    code = codes.create(conn, c["id"], kw.pop("code", CODE), max_uses, **kw)
    return c["id"], code, owner, tok


def png(size=(300, 200), mode="RGB", fmt="PNG", **save):
    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128)).save(buf, fmt, **save)
    return buf.getvalue()


# ---------------------------------------------------------------- matching and admin
def test_normalize_ignores_case_spaces_and_dashes():
    n = codes.normalize("Example Lifting Club")
    assert n == "examplelifting club".replace(" ", "")
    for variant in ("example-lifting-club", "  EXAMPLE LIFTING   CLUB ", "Example\tLifting–Club", "ＥｘａｍｐｌｅＬｉｆｔｉｎｇＣｌｕｂ"):
        assert codes.normalize(variant) == n
    assert codes.normalize("Example Lifting Clubs") != n


def test_create_rules(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid, code, owner, _ = club(conn)
        assert code["code"] == CODE and code["code_norm"] == "exampleliftingclub" and code["uses"] == 0
        with pytest.raises(ValueError, match="同样的"):
            codes.create(conn, cid, "example-lifting-club", 5)
        for bad in ("ab", "x" * 41):
            with pytest.raises(ValueError, match="个字符"):
                codes.create(conn, cid, bad, 5)
        with pytest.raises(ValueError, match="次数"):
            codes.create(conn, cid, "Another Club", 0)
        with pytest.raises(ValueError, match="过去"):
            codes.create(conn, cid, "Another Club", 5, datetime.now().astimezone() - timedelta(hours=1))
        assert codes.disable(conn, code["id"]) and not codes.disable(conn, code["id"])
        again = codes.create(conn, cid, "EXAMPLE LIFTING CLUB", 5)            # a disabled code frees its text
        assert codes.find(conn, CODE)["id"] == again["id"]
        # an owner who has not agreed to the circle consent cannot own a club circle
        eve = conn.execute("INSERT INTO users (display_name) VALUES ('Eve') RETURNING id").fetchone()["id"]
        with pytest.raises(store.FriendsError):
            admin.create_circle(conn, "Other", eve)
        assert [c["name"] for c in admin.list_circles(conn)] == ["Example Lifting Club"]
        assert admin.find_circle(conn, "Example Lifting Club") == cid and admin.find_circle(conn, str(cid)) == cid


def test_cli_expiry_end_of_day_in_owner_zone(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid, _, owner, _ = club(conn)
        conn.execute("UPDATE users SET timezone = 'Australia/Sydney' WHERE id = %s", (owner,))
        at = cli._expiry(conn, cid, "2030-03-01", None)
        assert at.isoformat() == "2030-03-02T00:00:00+11:00"
        assert cli._expiry(conn, cid, None, None) is None
        assert cli._expiry(conn, cid, None, 7) > datetime.now().astimezone() + timedelta(days=6)


# ---------------------------------------------------------------- new account
def test_new_account_joins_only_after_consent_then_can_leave(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid, code, owner, otok = club(conn)
        uid, tok = newcomer(conn, 1)
    r = client.post("/v1/account/redeem-invite", headers=h(tok), json={"code": "example-lifting CLUB"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active" and body["circle"] == {"id": cid, "name": "Example Lifting Club", "joined": False}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT 1 FROM user_provisioning WHERE user_id = %s", (uid,)).fetchone()
        # before the consent: not a member, nothing shared either way, not listed to the owner
        assert not conn.execute("SELECT 1 FROM circle_members WHERE user_id = %s", (uid,)).fetchone()
        assert not auth.can_view(conn, owner, uid, "scores") and not auth.can_view(conn, uid, owner, "scores")
        assert conn.execute("SELECT count(*) AS n FROM shares").fetchone()["n"] == 0
        [use] = codes.uses(conn, code["id"])
        assert use["user_id"] == uid and use["new_account"] and use["joined_at"] is None and not use["member"]
        assert codes.list_codes(conn)[0]["waiting_consent"] == 1
    view = client.get("/v1/friends", headers=h(otok)).json()
    assert [m["id"] for m in view["circles"][0]["members"]] == [owner]
    # the Friends page before the consent does not put them in either
    assert client.get("/v1/friends", headers=h(tok)).json()["circles"] == []
    assert client.get(f"/v1/users/{owner}/scores", headers=h(tok)).status_code == 403
    # the consent sheet lists the circle and what will be shared there
    prof = client.get("/v1/users/me/profile", headers=h(tok)).json()
    assert prof["circle_pending"] == [{"circle_id": cid, "name": "Example Lifting Club", "scopes": list(P.DEFAULT_SCOPES)}]
    r = client.post("/v1/users/me/consent", headers=h(tok), json={"version": consent.version(), "agree": True})
    assert r.status_code == 200, r.text
    assert r.json()["circles_joined"] == [{"circle_id": cid, "name": "Example Lifting Club"}]
    assert r.json()["circle_pending"] == []
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT scopes FROM circle_members WHERE circle_id = %s AND user_id = %s",
                            (cid, uid)).fetchone()["scopes"] == list(P.DEFAULT_SCOPES)
        assert auth.can_view(conn, owner, uid, "scores") and auth.can_view(conn, uid, owner, "training")
        assert not auth.can_view(conn, owner, uid, "weight") and not auth.can_view(conn, owner, uid, "ecg")
        assert codes.uses(conn, code["id"])[0]["joined_at"] is not None
    # they change what they share, then leave: the owner loses access at once
    r = client.patch(f"/v1/friends/circles/{cid}/me", headers=h(tok), json={"scopes": ["sleep"]})
    assert r.status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert not auth.can_view(conn, owner, uid, "scores") and auth.can_view(conn, owner, uid, "sleep")
    assert client.delete(f"/v1/friends/circles/{cid}/me", headers=h(tok)).status_code == 204
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert not auth.can_view(conn, owner, uid, "sleep") and not auth.can_view(conn, uid, owner, "scores")
        assert codes.list_codes(conn)[0]["uses"] == 1     # leaving does not give the use back
    # leaving is final: the Friends page does not put them back (only their own personal circle is there)
    assert cid not in [c["id"] for c in client.get("/v1/friends", headers=h(tok)).json()["circles"]]


def test_circle_default_scopes_and_flag_off_waits(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        owner, _ = member(conn, "Org")
        c = admin.create_circle(conn, "Quiet Club", owner, default_scopes=["sleep"])
        codes.create(conn, c["id"], "Quiet Club 1", 5)
        uid, tok = newcomer(conn, 2)
    assert client.post("/v1/account/redeem-invite", headers=h(tok), json={"code": "quiet club 1"}).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, "friends", "off", actor="test")
    try:
        client.post("/v1/users/me/consent", headers=h(tok), json={"version": consent.version(), "agree": True})
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert not conn.execute("SELECT 1 FROM circle_members WHERE user_id = %s", (uid,)).fetchone()   # waits
    finally:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            flags.set_flag(conn, "friends", "all", actor="test")
    client.get("/v1/friends", headers=h(tok))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT scopes FROM circle_members WHERE user_id = %s AND circle_id = %s",
                            (uid, c["id"])).fetchone()["scopes"] == ["sleep"]


def test_unknown_code_falls_back_to_invite_codes(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        club(conn)
        _, tok = newcomer(conn, 3)
    r = client.post("/v1/account/redeem-invite", headers=h(tok), json={"code": "Example Lifting Klub"})
    assert r.status_code == 401


# ---------------------------------------------------------------- existing account
def test_existing_account_joins_on_friends_page(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid, code, owner, _ = club(conn)
        ann, tok = member(conn, "Ann")
    pv = client.get("/v1/friends/invite", params={"code": "EXAMPLE-LIFTING-CLUB"}, headers=h(tok)).json()
    assert pv["circle_code"] and pv["name"] == "Example Lifting Club" and pv["members"] == 1
    assert pv["default_scopes"] == list(P.DEFAULT_SCOPES)
    r = client.post("/v1/friends/join", headers=h(tok), json={"code": "example lifting club", "scopes": ["scores"]})
    assert r.status_code == 200, r.text
    assert r.json()["my_scopes"] == ["scores"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert auth.can_view(conn, owner, ann, "scores") and not auth.can_view(conn, owner, ann, "sleep")
        [use] = codes.uses(conn, code["id"])
        assert not use["new_account"] and use["joined_at"] is not None and use["member"]
    # again: already in, nothing consumed
    r = client.post("/v1/friends/join", headers=h(tok), json={"code": CODE, "scopes": ["scores"]})
    assert r.status_code == 200 and r.json()["already"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert codes.find(conn, CODE)["uses"] == 1


def test_existing_account_without_circle_consent_is_asked_first(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        club(conn)
        old = conn.execute("""INSERT INTO users (display_name, consent_at, consent_version) VALUES ('Old', now(), '2026-10-22.1')
                              RETURNING id""").fetchone()["id"]
        tok = auth.create_token(conn, old, "t", ["read", "write"])
    r = client.post("/v1/friends/join", headers=h(tok), json={"code": CODE, "scopes": ["scores"]})
    assert r.status_code == 409
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert codes.find(conn, CODE)["uses"] == 0


# ---------------------------------------------------------------- limits
def test_used_up_disabled_expired_give_clear_messages(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        _, code, _, _ = club(conn, max_uses=1)
        (_, t1), (u2, t2) = newcomer(conn, 10), newcomer(conn, 11)
        _, ann = member(conn, "Ann")
    assert client.post("/v1/account/redeem-invite", headers=h(t1), json={"code": CODE}).status_code == 200
    r = client.post("/v1/account/redeem-invite", headers=h(t2), json={"code": CODE})
    assert r.status_code == 410 and r.json()["detail"] == "这个圈子激活码已经用完了"
    r = client.post("/v1/account/redeem-invite", headers=h(t2, **{"X-Body-Lang": "en"}), json={"code": CODE})
    assert r.json()["detail"] == "This circle code has been used up"
    assert client.get("/v1/friends/invite", params={"code": CODE}, headers=h(ann)).status_code == 410
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE circle_codes SET max_uses = 5, expires_at = now() - interval '1 second' WHERE id = %s", (code["id"],))
    r = client.post("/v1/account/redeem-invite", headers=h(t2), json={"code": CODE})
    assert r.status_code == 410 and r.json()["detail"] == "这个圈子激活码已过期"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE circle_codes SET expires_at = NULL WHERE id = %s", (code["id"],))
        codes.disable(conn, code["id"])
    r = client.post("/v1/account/redeem-invite", headers=h(t2), json={"code": CODE})
    assert r.status_code == 410 and r.json()["detail"] == "这个圈子激活码已停用"
    r = client.post("/v1/friends/join", headers=h(ann), json={"code": CODE, "scopes": []})
    assert r.status_code == 410
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT status FROM users WHERE id = %s", (u2,)).fetchone()["status"] == "pending"
        listed = codes.list_codes(conn)[0]
        assert listed["state"] == "disabled" and listed["uses"] == 1 and listed["new_accounts"] == 1


def test_concurrent_sign_ups_never_exceed_max_uses(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        _, code, _, _ = club(conn, max_uses=3)
        racers = [newcomer(conn, 100 + i)[0] for i in range(8)]
        conn.commit()
    barrier, results = threading.Barrier(len(racers)), {}

    def go(uid):
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            barrier.wait()
            try:
                results[uid] = codes.redeem(c, uid, CODE) is not None
                c.commit()
            except store.FriendsError as e:
                results[uid] = e.status
                c.rollback()

    threads = [threading.Thread(target=go, args=(u,)) for u in racers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values(), key=str) == [410] * 5 + [True] * 3
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = codes.find(conn, CODE)
        assert (row["uses"], row["max_uses"]) == (3, 3)
        assert len(codes.uses(conn, code["id"])) == 3
        assert conn.execute("SELECT count(*) AS n FROM users WHERE id = ANY(%s) AND status = 'active'", (racers,)).fetchone()["n"] == 3


def test_member_cap_counts_people_waiting_for_consent(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid, _, _, _ = club(conn, max_uses=10, max_members=2)
        (_, t1), (_, t2) = newcomer(conn, 20), newcomer(conn, 21)
        _, ann = member(conn, "Ann")
        inv = store.mint_invite(conn, conn.execute("SELECT owner_id FROM circles WHERE id = %s", (cid,)).fetchone()["owner_id"], cid)
    assert client.post("/v1/account/redeem-invite", headers=h(t1), json={"code": CODE}).status_code == 200
    # owner + one waiting = full: neither the next sign-up nor an ordinary invite gets in
    r = client.post("/v1/account/redeem-invite", headers=h(t2), json={"code": CODE})
    assert r.status_code == 409 and "已满" in r.json()["detail"]
    r = client.post("/v1/friends/join", headers=h(ann), json={"code": inv["code"], "scopes": []})
    assert r.status_code == 409


def test_redeeming_twice_while_waiting_uses_one_slot(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        club(conn, max_uses=5)
        uid, tok = newcomer(conn, 30)
    for _ in range(2):
        assert client.post("/v1/account/redeem-invite", headers=h(tok), json={"code": CODE}).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert codes.find(conn, CODE)["uses"] == 1


# ---------------------------------------------------------------- avatars
def test_image_processing_checks_and_strips_metadata():
    exif = Image.Exif()
    exif[0x010F] = "Some Camera"          # Make
    exif[0x0112] = 6                      # Orientation
    src = png((400, 300), fmt="JPEG", exif=exif.tobytes())
    assert Image.open(io.BytesIO(src)).getexif()
    out = images.process(src)
    im = Image.open(io.BytesIO(out["data"]))
    assert im.format == "WEBP" and im.size == (IP.SIZE, IP.SIZE) and out["mime"] == "image/webp"
    assert not im.getexif() and "exif" not in im.info and "icc_profile" not in im.info
    for good in (png(), png(fmt="WEBP"), png(mode="RGBA")):
        assert images.process(good)["width"] == IP.SIZE
    for bad, msg in ((b"", "空"), (b"not an image at all", "读不出"), (png(fmt="GIF"), "只支持"), (png((20, 20)), "太小"),
                     (b"\x89PNG" + b"0" * (IP.MAX_UPLOAD_BYTES + 1), "太大")):
        with pytest.raises(images.ImageError, match=msg):
            images.process(bad)


def test_avatar_only_for_members(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid, _, owner, otok = club(conn)
        _, stranger = member(conn, "Stranger")
        img = admin.set_avatar(conn, cid, png())
    view = client.get("/v1/friends", headers=h(otok)).json()["circles"][0]
    assert view["avatar"] == f"/v1/friends/circles/{cid}/avatar?v={img}" and view["max_members"] == P.MAX_MEMBERS
    r = client.get(view["avatar"], headers=h(otok))
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp" and "private" in r.headers["cache-control"]
    assert client.get(view["avatar"], headers=h(otok, **{"If-None-Match": r.headers["etag"]})).status_code == 304
    assert client.get(view["avatar"], headers=h(stranger)).status_code == 404
    # replacing deletes the old image; clearing removes the URL
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        new = admin.set_avatar(conn, cid, png(fmt="WEBP"))
        assert images.get(conn, img) is None and images.get(conn, new) is not None
        admin.set_avatar(conn, cid, None)
        assert conn.execute("SELECT count(*) AS n FROM images").fetchone()["n"] == 0
    assert client.get("/v1/friends", headers=h(otok)).json()["circles"][0]["avatar"] is None
    assert client.get(f"/v1/friends/circles/{cid}/avatar", headers=h(otok)).status_code == 404


def test_create_circle_with_avatar_and_feed_refs(dsn):
    from body_engine.friends import feed
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        owner, _ = member(conn, "Org")
        c = admin.create_circle(conn, "Pic Club", owner, avatar=png(), max_members=60)
        assert c["avatar_image_id"] and c["max_members"] == 60
        ann, _ = member(conn, "Ann")
        codes.create(conn, c["id"], "Pic Club Code", 5)
        codes.redeem(conn, ann, "pic club code", ["training"])
        refs = feed._circles_between(conn, owner, ann)
        assert refs == [{"id": c["id"], "name": "Pic Club", "avatar": f"/v1/friends/circles/{c['id']}/avatar?v={c['avatar_image_id']}"}]
        assert feed._circle_fields(refs)["circles"] == ["Pic Club"]
