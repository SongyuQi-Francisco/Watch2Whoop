"""The inbox (inbox/, issue #359 P1): the flag, own-only access, the merge of notices, what's new, addressed circle invites and
circle notifications (order, since, paging, 90 days), read / hidden state, invite cards (only what a stranger to the circle may
know), accepting and ignoring (expired, withdrawn, someone else's, blocked, no consent, no community rules), and blocks and
moderation on every read. Synthetic people and data only.
Test database body_test_inbox (BODY_TEST_DB_NAME_INBOX), recreated on every run; the pure parts run without Postgres."""
import io
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import admin as circle_admin, params as FP, store as friends
from body_engine.inbox import params as P, store as inbox
from body_engine.notices import store as notices
from body_engine.rollout import flags
from body_engine.whatsnew import entries as E, params as WP

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_INBOX", "body_test_inbox")
NAMES = ("Ann", "Ben", "Cat", "Dan", "Eve")
CJK = re.compile(r"[一-鿿]")
UTC = timezone.utc


def test_every_circle_scope_has_both_labels():
    assert set(P.SCOPE_LABELS) == set(FP.CIRCLE_SCOPES)
    assert all(zh and en and CJK.search(zh) and not CJK.search(en) for zh, en in P.SCOPE_LABELS.values())
    assert "ecg" not in P.SCOPE_LABELS


def test_keep_days_is_ninety():
    assert P.KEEP_DAYS == 90


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_accept", ""):
        pytest.exit("the test DB must not be a real DB")
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


def set_flag(dsn, key, state):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, key, state, actor="test")
    flags.invalidate()


@pytest.fixture(scope="module")
def world(dsn):
    """Ann, Ben, Cat, Dan agreed to the circle consent, Eve only to an older version; accounts two months old."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, birth_date, height_cm, consent_at, consent_version,
                                                     personal_circle_at, created_at, timezone)
                                  VALUES (%s, '1990-01-01', 175, now(), %s, now(), now() - interval '60 days', 'Australia/Sydney')
                                  RETURNING id""",
                               (n, "2026-10-22.1" if n == "Eve" else FP.CONSENT_VERSION)).fetchone()["id"] for n in NAMES}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tok["ann_ro"] = auth.create_token(conn, ids["Ann"], "ro", ["read"])
    set_flag(dsn, "friends", "all")
    set_flag(dsn, "inbox", "all")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()
    api.pool = None


@pytest.fixture(autouse=True)
def clean(request):
    """Every database test starts with an empty inbox for everyone."""
    if "dsn" not in request.fixturenames:
        yield
        return
    with psycopg.connect(request.getfixturevalue("dsn"), row_factory=dict_row) as conn:
        for t in ("circles", "shares", "circle_blocks", "circle_bans", "circle_moderation", "notifications", "admin_notices",
                  "whats_new_sends", "inbox_item_state", "circle_feed_posts"):
            conn.execute(f"DELETE FROM {t}")
    yield


def h(t, lang=None):
    return {"Authorization": f"Bearer {t}", **({"X-Body-Lang": lang} if lang else {})}


def sql(dsn, query, args=()):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cur = conn.execute(query, args)
        return cur.fetchall() if cur.description else None


def ago(**kw):
    return datetime.now(UTC) - timedelta(**kw)


def add_notice(dsn, uid, title="示例标题", body="示例正文", at=None, **kw):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        nid = notices.send(conn, uid, title, body, **kw)["id"]
        if at:
            conn.execute("UPDATE admin_notices SET created_at = %s WHERE id = %s", (at, nid))
    return nid


def add_note(dsn, uid, kind="circle.like", actor=None, post=None, body="", at=None, read=False):
    data = {k: v for k, v in (("actor_id", actor), ("post_id", post)) if v is not None}
    return sql(dsn, """INSERT INTO notifications (user_id, kind, title, body, title_en, body_en, url, data, created_at, read_at)
                       VALUES (%s, %s, '示例互动', %s, 'Example interaction', %s, '/app/?tab=friends', %s, %s, %s) RETURNING id""",
               (uid, kind, body, body, json.dumps(data), at or datetime.now(UTC), datetime.now(UTC) if read else None))[0]["id"]


def png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def add_circle(dsn, owner, name="示例圈", members=(), scopes=None, avatar=None, max_members=None):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid = circle_admin.create_circle(conn, name, owner, avatar=avatar, default_scopes=scopes, max_members=max_members)["id"]
        for m in members:
            friends.join(conn, m, list(FP.DEFAULT_SCOPES), code=friends.mint_invite(conn, owner, cid)["code"])
        conn.execute("DELETE FROM notifications WHERE kind = 'circle.joined'")   # setting up the circle is not what these tests read
    return cid


def add_invite(dsn, cid, by, to, days=30, at=None):
    return sql(dsn, """INSERT INTO circle_invites (circle_id, created_by, invitee_user_id, expires_at, created_at)
                       VALUES (%s, %s, %s, now() + make_interval(days => %s), %s) RETURNING id""",
               (cid, by, to, days, at or datetime.now(UTC)))[0]["id"]


def add_week(dsn, uid, week_start, through, outcome="sent", at=None):
    """One row of whats_new_sends; a sent week also has its notification, like whatsnew/service.run writes them."""
    nid = None
    if outcome == "sent":
        nid = sql(dsn, """INSERT INTO notifications (user_id, kind, title, body, url) VALUES (%s, %s, '本周更新', '示例', %s)
                          RETURNING id""", (uid, WP.KIND, WP.URL))[0]["id"]
    sql(dsn, """INSERT INTO whats_new_sends (user_id, week_start, through, items, outcome, notification_id, created_at)
                VALUES (%s, %s, %s, 1, %s, %s, %s)""", (uid, week_start, through, outcome, nid, at or datetime.now(UTC)))
    return nid


def listing(client, tok, **params):
    r = client.get("/v1/users/me/inbox", headers=h(tok), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def item_ids(client, tok, **params):
    return [i["id"] for i in listing(client, tok, **params)["items"]]


def code(response):
    return response.json()["detail"]["code"]


# ---------------------------------------------------------------- the flag, and who may ask
def test_flag_off_answers_404_and_the_old_paths_stay_as_they_were(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    inv = add_invite(dsn, add_circle(dsn, ben), ben, ann)
    nid = add_notice(dsn, ann)
    note = add_note(dsn, ann, actor=ben)
    set_flag(dsn, "inbox", "off")
    try:
        H = h(tok["Ann"])
        assert client.get("/v1/users/me/inbox", headers=H).status_code == 404
        assert client.post("/v1/users/me/inbox/read", headers=H, json={"all": True}).status_code == 404
        assert client.delete(f"/v1/users/me/inbox/items/notice:{nid}", headers=H).status_code == 404
        assert client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=H).status_code == 404
        assert client.post(f"/v1/users/me/inbox/invites/{inv}/ignore", headers=H).status_code == 404
        assert client.get(f"/v1/users/me/inbox/invites/{inv}/avatar", headers=H).status_code == 404
        assert "inbox" not in client.get("/v1/users/me/flags", headers=H).json()["flags"]
        # nothing moved: the sheet still has its notice, the Friends page its invite, the notification is unread
        assert [n["id"] for n in client.get("/v1/users/me/notices", headers=H).json()["items"]] == [nid]
        assert [i["id"] for i in client.get("/v1/friends", headers=H).json()["invites"]] == [inv]
        assert sql(dsn, "SELECT read_at FROM notifications WHERE id = %s", (note,))[0]["read_at"] is None
        assert sql(dsn, "SELECT count(*) AS n FROM inbox_item_state")[0]["n"] == 0
    finally:
        set_flag(dsn, "inbox", "all")
    assert "inbox" in client.get("/v1/users/me/flags", headers=h(tok["Ann"])).json()["flags"]


def test_only_ones_own_inbox(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    nid = add_notice(dsn, ben, title="只给 Ben")
    inv = add_invite(dsn, add_circle(dsn, ann), ann, ben)
    H = h(tok["Ann"])
    assert client.get(f"/v1/users/{ben}/inbox", headers=H).status_code == 403
    assert client.post(f"/v1/users/{ben}/inbox/read", headers=H, json={"all": True}).status_code == 403
    assert client.delete(f"/v1/users/{ben}/inbox/items/notice:{nid}", headers=H).status_code == 403
    assert client.post(f"/v1/users/{ben}/inbox/invites/{inv}/accept", headers=H).status_code == 403
    assert client.post(f"/v1/users/{ben}/inbox/invites/{inv}/ignore", headers=H).status_code == 403
    assert client.get(f"/v1/users/{ben}/inbox/invites/{inv}/avatar", headers=H).status_code == 403
    assert client.get("/v1/users/me/inbox").status_code == 401
    # Ann's own list has none of it, by `me` or her own id, and Ben's is untouched
    assert item_ids(client, tok["Ann"]) == []
    assert client.get(f"/v1/users/{ann}/inbox", headers=H).json()["items"] == []
    assert set(item_ids(client, tok["Ben"])) == {f"notice:{nid}", f"invite:{inv}"}


def test_read_only_token_reads_but_never_writes(client, dsn, world):
    ids, tok = world
    nid = add_notice(dsn, ids["Ann"])
    inv = add_invite(dsn, add_circle(dsn, ids["Ben"]), ids["Ben"], ids["Ann"])
    H = h(tok["ann_ro"])
    assert client.get("/v1/users/me/inbox", headers=H).status_code == 200
    assert client.post("/v1/users/me/inbox/read", headers=H, json={"all": True}).status_code == 403
    assert client.delete(f"/v1/users/me/inbox/items/notice:{nid}", headers=H).status_code == 403
    assert client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=H).status_code == 403
    assert client.post(f"/v1/users/me/inbox/invites/{inv}/ignore", headers=H).status_code == 403


# ---------------------------------------------------------------- the merge
@pytest.fixture
def one_entry(monkeypatch):
    monkeypatch.setattr(E, "ENTRIES", (E.Entry(date.today() - timedelta(days=2), "示例：新功能一句话。", "Example: one sentence."),))


def test_merges_the_four_sources_newest_first(client, dsn, world, one_entry):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    nid = add_notice(dsn, ann, at=ago(hours=4), link="/app/?tab=me", link_label="打开")
    add_week(dsn, ann, date.today() - timedelta(days=1), date.today(), at=ago(hours=3))
    inv = add_invite(dsn, add_circle(dsn, ben), ben, ann, at=ago(hours=2))
    note = add_note(dsn, ann, actor=ben, at=ago(hours=1))
    add_notice(dsn, ben, title="别人的")   # never in Ann's list
    out = listing(client, tok["Ann"])
    week = (date.today() - timedelta(days=1)).isoformat()
    assert [i["id"] for i in out["items"]] == [f"notification:{note}", f"invite:{inv}", f"whatsnew:{week}", f"notice:{nid}"]
    assert [i["type"] for i in out["items"]] == ["circle", "circle_invite", "whatsnew", "notice"]
    assert out["unread"] == 4 and out["more"] is False and out["next"] is None and out["keep_days"] == P.KEEP_DAYS
    assert out["now"].endswith("Z")
    assert all(i["read"] is False and i["title"] and "at" in i for i in out["items"])
    assert not any(k.startswith("_") for i in out["items"] for k in i)
    by_type = {i["type"]: i for i in out["items"]}
    assert by_type["notice"]["url"] == "/app/?tab=me" and by_type["notice"]["link_label"] == "打开"
    assert by_type["circle"]["kind"] == "circle.like" and by_type["circle"]["url"] == "/app/?tab=friends"
    assert "data" not in by_type["circle"]   # actor ids stay on the server


def test_since_only_returns_newer_items_and_unread_counts_everything(client, dsn, world):
    ids, tok = world
    ann = ids["Ann"]
    old = add_notice(dsn, ann, at=ago(hours=5))
    new = add_notice(dsn, ann, at=ago(hours=1))
    assert item_ids(client, tok["Ann"], since=ago(hours=3).isoformat()) == [f"notice:{new}"]
    out = listing(client, tok["Ann"], since=ago(hours=3).isoformat())
    assert out["unread"] == 2
    assert item_ids(client, tok["Ann"]) == [f"notice:{new}", f"notice:{old}"]
    # the answer's `now` is the next `since`, written with Z so it survives an unescaped query string
    first = listing(client, tok["Ann"])
    assert client.get(f"/v1/users/me/inbox?since={first['now']}", headers=h(tok["Ann"])).json()["items"] == []
    later = add_notice(dsn, ann)
    assert client.get(f"/v1/users/me/inbox?since={first['now']}", headers=h(tok["Ann"])).json()["items"][0]["id"] == f"notice:{later}"
    # a time without a zone is refused, not guessed
    assert client.get("/v1/users/me/inbox", headers=h(tok["Ann"]), params={"since": "2026-01-01T00:00:00"}).status_code == 422


def test_paging_gives_every_item_once_also_with_equal_times(client, dsn, world):
    ids, tok = world
    ann = ids["Ann"]
    at = ago(hours=1)
    expected = {f"notice:{add_notice(dsn, ann, at=at)}" for _ in range(3)} | {f"notice:{add_notice(dsn, ann, at=ago(hours=2))}"}
    seen, before = [], None
    for _ in range(5):
        out = listing(client, tok["Ann"], limit=2, **({"before": before} if before else {}))
        seen += [i["id"] for i in out["items"]]
        before = out["next"]
        assert out["unread"] == 4
        if not out["more"]:
            break
    assert before is None and sorted(seen) == sorted(expected)
    bad = client.get("/v1/users/me/inbox", headers=h(tok["Ann"]), params={"before": "nonsense"})
    assert bad.status_code == 422 and code(bad) == "bad_cursor"
    assert client.get("/v1/users/me/inbox", headers=h(tok["Ann"]), params={"limit": P.PAGE_LIMIT_MAX + 1}).status_code == 422


def test_ninety_days_and_old_state_goes_away(client, dsn, world, one_entry):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    keep = add_notice(dsn, ann, at=ago(days=P.KEEP_DAYS - 1))
    add_notice(dsn, ann, at=ago(days=P.KEEP_DAYS + 1))
    add_note(dsn, ann, actor=ben, at=ago(days=P.KEEP_DAYS + 1))
    add_invite(dsn, add_circle(dsn, ben), ben, ann, days=200, at=ago(days=P.KEEP_DAYS + 1))
    add_week(dsn, ann, date.today() - timedelta(days=100), date.today(), at=ago(days=P.KEEP_DAYS + 1))
    assert item_ids(client, tok["Ann"]) == [f"notice:{keep}"]
    sql(dsn, """INSERT INTO inbox_item_state (user_id, item_id, hidden_at, updated_at)
                VALUES (%s, 'notice:999999', now(), now() - make_interval(days => %s))""", (ann, P.KEEP_DAYS + 1))
    assert client.delete(f"/v1/users/me/inbox/items/notice:{keep}", headers=h(tok["Ann"])).status_code == 204
    assert [r["item_id"] for r in sql(dsn, "SELECT item_id FROM inbox_item_state WHERE user_id = %s", (ann,))] == [f"notice:{keep}"]


def test_withdrawn_and_expired_notices_are_not_listed_and_english_is_used(client, dsn, world):
    ids, tok = world
    ann = ids["Ann"]
    gone = add_notice(dsn, ann)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert notices.revoke(conn, gone)
    expired = add_notice(dsn, ann)
    sql(dsn, "UPDATE admin_notices SET expires_at = now() - interval '1 minute' WHERE id = %s", (expired,))
    kept = add_notice(dsn, ann, title_en="An example title", body_en="An example body", link="/app/", link_label="打开", link_label_en="Open")
    r = client.get("/v1/users/me/inbox", headers=h(tok["Ann"], "en")).json()
    assert [i["id"] for i in r["items"]] == [f"notice:{kept}"]
    assert (r["items"][0]["title"], r["items"][0]["body"], r["items"][0]["link_label"]) == ("An example title", "An example body", "Open")
    assert item_ids(client, tok["Ann"]) == [f"notice:{kept}"]
    assert listing(client, tok["Ann"])["items"][0]["title"] == "示例标题"


def test_whats_new_weeks_are_listed_once_also_when_the_push_is_off(client, dsn, world, monkeypatch):
    ids, tok = world
    ann = ids["Ann"]
    today = date.today()
    monkeypatch.setattr(E, "ENTRIES", (E.Entry(today - timedelta(days=1), "示例：这周的。", "Example: this week."),
                                       E.Entry(today - timedelta(days=9), "示例：上周的。", "Example: last week."),
                                       E.Entry(today - timedelta(days=30), "示例：太早的。", "Example: too old.")))
    sent = add_week(dsn, ann, today - timedelta(days=7), today - timedelta(days=7), at=ago(days=7))
    add_week(dsn, ann, today, today, outcome="off", at=ago(minutes=5))
    out = listing(client, tok["Ann"])
    assert [i["id"] for i in out["items"]] == [f"whatsnew:{today.isoformat()}", f"whatsnew:{(today - timedelta(days=7)).isoformat()}"]
    assert not any(i["id"].startswith("notification:") for i in out["items"])   # the sent week's notification is not listed twice
    newest, older = out["items"]
    assert [e["text"] for e in newest["entries"]] == ["示例：这周的。"]   # only what is new since the week before
    assert [e["text"] for e in older["entries"]] == ["示例：上周的。"]    # at most LOOKBACK_DAYS back
    assert newest["url"] == WP.URL and newest["body"] == "示例：这周的。"
    en = client.get("/v1/users/me/inbox", headers=h(tok["Ann"], "en")).json()["items"]
    assert en[0]["entries"][0]["text"] == "Example: this week." and not CJK.search(en[0]["title"] + en[0]["body"])
    # reading: the sent week through its notification (the app's badge counts those), the other in the inbox's own state
    assert client.post("/v1/users/me/inbox/read", headers=h(tok["Ann"]), json={"all": True}).json() == {"marked": 2, "unread": 0}
    assert sql(dsn, "SELECT read_at FROM notifications WHERE id = %s", (sent,))[0]["read_at"] is not None
    assert [r["item_id"] for r in sql(dsn, "SELECT item_id FROM inbox_item_state")] == [f"whatsnew:{today.isoformat()}"]
    assert all(i["read"] for i in listing(client, tok["Ann"])["items"])


# ---------------------------------------------------------------- read and hidden
def test_read_by_ids_and_all_write_where_each_item_keeps_it(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    nid = add_notice(dsn, ann)
    note = add_note(dsn, ann, actor=ben)
    inv = add_invite(dsn, add_circle(dsn, ben), ben, ann)
    H = h(tok["Ann"])
    r = client.post("/v1/users/me/inbox/read", headers=H, json={"ids": [f"notice:{nid}", f"notification:{note}"]})
    assert r.status_code == 200 and r.json() == {"marked": 2, "unread": 1}
    assert sql(dsn, "SELECT seen_at FROM admin_notices WHERE id = %s", (nid,))[0]["seen_at"] is not None
    assert sql(dsn, "SELECT read_at FROM notifications WHERE id = %s", (note,))[0]["read_at"] is not None
    assert client.get("/v1/users/me/notices", headers=H).json()["items"] == []   # the sheet does not come back for it
    assert sql(dsn, "SELECT count(*) AS n FROM inbox_item_state")[0]["n"] == 0
    assert {i["id"]: i["read"] for i in listing(client, tok["Ann"])["items"]} == \
        {f"notice:{nid}": True, f"notification:{note}": True, f"invite:{inv}": False}
    assert client.post("/v1/users/me/inbox/read", headers=H, json={"all": True}).json() == {"marked": 1, "unread": 0}
    assert all(i["read"] for i in listing(client, tok["Ann"])["items"])
    assert sql(dsn, "SELECT revoked_at FROM circle_invites WHERE id = %s", (inv,))[0]["revoked_at"] is None   # reading is not ignoring
    assert client.post("/v1/users/me/inbox/read", headers=H, json={"all": True}).json() == {"marked": 0, "unread": 0}


def test_something_read_on_the_old_paths_is_read_here(client, dsn, world):
    ids, tok = world
    nid = add_notice(dsn, ids["Ann"])
    note = add_note(dsn, ids["Ann"], actor=ids["Ben"])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert notices.mark_seen(conn, ids["Ann"], nid)
    sql(dsn, "UPDATE notifications SET read_at = now() WHERE id = %s", (note,))
    out = listing(client, tok["Ann"])
    assert out["unread"] == 0 and sorted(i["id"] for i in out["items"]) == sorted([f"notice:{nid}", f"notification:{note}"])


def test_read_needs_ids_or_all_and_leaves_other_peoples_items_alone(client, dsn, world):
    ids, tok = world
    ben = ids["Ben"]
    nid = add_notice(dsn, ben)
    note = add_note(dsn, ben, actor=ids["Ann"])
    inv = add_invite(dsn, add_circle(dsn, ids["Ann"]), ids["Ann"], ben)
    H = h(tok["Ann"])
    for body in ({}, {"all": False}, {"ids": ["x"], "all": True}):
        r = client.post("/v1/users/me/inbox/read", headers=H, json=body)
        assert r.status_code == 422 and code(r) == "bad_request"
    r = client.post("/v1/users/me/inbox/read", headers=H, json={"ids": [f"notice:{nid}", f"notification:{note}", f"invite:{inv}", "notice:x"]})
    assert r.json() == {"marked": 0, "unread": 0}
    assert sql(dsn, "SELECT seen_at FROM admin_notices WHERE id = %s", (nid,))[0]["seen_at"] is None
    assert sql(dsn, "SELECT read_at FROM notifications WHERE id = %s", (note,))[0]["read_at"] is None
    assert sql(dsn, "SELECT count(*) AS n FROM inbox_item_state")[0]["n"] == 0
    assert client.delete(f"/v1/users/me/inbox/items/notice:{nid}", headers=H).status_code == 404
    assert listing(client, tok["Ben"])["unread"] == 3


def test_hiding_one_item(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    nid = add_notice(dsn, ann)
    note = add_note(dsn, ann, actor=ben)
    inv = add_invite(dsn, add_circle(dsn, ben), ben, ann)
    H = h(tok["Ann"])
    assert client.delete(f"/v1/users/me/inbox/items/notification:{note}", headers=H).status_code == 204
    out = listing(client, tok["Ann"])
    assert {i["id"] for i in out["items"]} == {f"notice:{nid}", f"invite:{inv}"} and out["unread"] == 2
    assert sql(dsn, "SELECT read_at FROM notifications WHERE id = %s", (note,))[0]["read_at"] is not None   # the badge goes down too
    gone = client.delete(f"/v1/users/me/inbox/items/notification:{note}", headers=H)
    assert gone.status_code == 404 and code(gone) == "not_in_inbox"
    # hiding an invite only changes the view: it still stands, and the Friends page still lists it
    assert client.delete(f"/v1/users/me/inbox/items/invite:{inv}", headers=H).status_code == 204
    assert sql(dsn, "SELECT revoked_at FROM circle_invites WHERE id = %s", (inv,))[0]["revoked_at"] is None
    assert [i["id"] for i in client.get("/v1/friends", headers=H).json()["invites"]] == [inv]
    assert item_ids(client, tok["Ann"]) == [f"notice:{nid}"]


# ---------------------------------------------------------------- invite cards
def test_card_gives_public_circle_facts_only(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    cid = add_circle(dsn, ben, name="示例跑团", members=[ids["Cat"], ids["Dan"]], avatar=png())
    inv = add_invite(dsn, cid, ben, ann)
    # someone else's invite to the same circle, and a declined one: nothing about them on Ann's card
    add_invite(dsn, cid, ben, ids["Eve"])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        friends.decline(conn, ids["Eve"], sql(dsn, "SELECT id FROM circle_invites WHERE invitee_user_id = %s", (ids["Eve"],))[0]["id"])
    item = listing(client, tok["Ann"])["items"][0]
    assert item["id"] == f"invite:{inv}" and item["type"] == "circle_invite" and item["title"] == "Ben 邀请你加入「示例跑团」"
    card = item["invite"]
    assert set(card) == {"invite_id", "circle_name", "avatar", "owner_name", "members", "expires_at", "default_scopes", "shares",
                         "consent_ok", "rules_ok", "can_mute"}
    assert card["invite_id"] == inv and card["circle_name"] == "示例跑团" and card["owner_name"] == "Ben" and card["members"] == 3
    assert card["default_scopes"] == list(FP.DEFAULT_SCOPES)
    assert [s["label"] for s in card["shares"]] == ["评分", "睡眠", "训练", "饮食达标率"]
    assert all(s["label"] in item["body"] for s in card["shares"])
    assert card["consent_ok"] is True and card["rules_ok"] is True
    assert card["avatar"].startswith(f"/v1/users/me/inbox/invites/{inv}/avatar")
    text = json.dumps(item, ensure_ascii=False)
    for leak in ("Cat", "Dan", "Eve", "owner_id", "created_by", "invitee", "circle_id", "declined"):
        assert leak not in text


def test_card_lists_a_circles_own_default_scopes_in_both_languages(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    add_invite(dsn, add_circle(dsn, ben, scopes=["weight", "scores"]), ben, ann)
    zh = listing(client, tok["Ann"])["items"][0]
    assert zh["invite"]["default_scopes"] == ["scores", "weight"] and "评分、体重" in zh["body"]
    en = client.get("/v1/users/me/inbox", headers=h(tok["Ann"], "en")).json()["items"][0]
    assert [s["label"] for s in en["invite"]["shares"]] == ["Scores", "Weight"]
    assert en["body"] == "Members will see your: scores, weight. You can change this or leave any time."
    assert en["title"] == "Ben invited you to join “示例圈”"   # the circle's name is the owner's own words


def test_invites_to_circles_i_am_in_are_not_listed(client, dsn, world):
    ids, tok = world
    cid = add_circle(dsn, ids["Ben"], members=[ids["Ann"]])
    add_invite(dsn, cid, ids["Ben"], ids["Ann"])
    assert item_ids(client, tok["Ann"]) == []


def test_avatar_only_for_the_person_the_open_invite_is_for(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    cid = add_circle(dsn, ben, avatar=png())
    inv = add_invite(dsn, cid, ben, ann)
    url = listing(client, tok["Ann"])["items"][0]["invite"]["avatar"]
    r = client.get(url, headers=h(tok["Ann"]))
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp" and r.content
    assert client.get(url, headers={**h(tok["Ann"]), "If-None-Match": r.headers["etag"]}).status_code == 304
    assert client.get(url, headers=h(tok["Cat"])).status_code == 404
    assert client.get(f"/v1/friends/circles/{cid}/avatar", headers=h(tok["Ann"])).status_code == 404   # the members-only one stays so
    sql(dsn, "UPDATE circle_invites SET revoked_at = now() WHERE id = %s", (inv,))
    assert client.get(url, headers=h(tok["Ann"])).status_code == 404


# ---------------------------------------------------------------- accept and ignore
def test_accept_joins_with_the_default_scopes_the_card_listed(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    cid = add_circle(dsn, ben, scopes=["scores", "training"])
    inv = add_invite(dsn, cid, ben, ann)
    listed = listing(client, tok["Ann"])["items"][0]["invite"]["default_scopes"]
    r = client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == cid and r.json()["my_scopes"] == listed == ["scores", "training"]
    m = sql(dsn, "SELECT scopes, join_via, invite_id, invited_by FROM circle_members WHERE circle_id = %s AND user_id = %s", (cid, ann))[0]
    assert m == {"scopes": ["scores", "training"], "join_via": "referral", "invite_id": inv, "invited_by": ben}
    i = sql(dsn, "SELECT revoked_at, used_count FROM circle_invites WHERE id = %s", (inv,))[0]
    assert i["revoked_at"] is not None and i["used_count"] == 1
    # the sharing is what the card said, and no more
    assert sql(dsn, "SELECT circle_scopes FROM shares WHERE owner_id = %s AND viewer_id = %s", (ann, ben))[0]["circle_scopes"] == ["scores", "training"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert auth.can_view(conn, ben, ann, "scores") and not auth.can_view(conn, ben, ann, "weight")
    assert item_ids(client, tok["Ann"]) == []
    # the answer got lost and the app sends it again: same circle, nothing new
    again = client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))
    assert again.status_code == 200 and again.json()["already"] is True and again.json()["id"] == cid
    assert sql(dsn, "SELECT used_count FROM circle_invites WHERE id = %s", (inv,))[0]["used_count"] == 1


@pytest.mark.parametrize("how", ["expired", "withdrawn", "someone_else", "missing", "code"])
def test_accept_refuses_what_is_not_open_for_me(client, dsn, world, how):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    cid = add_circle(dsn, ben)
    if how == "expired":
        inv = add_invite(dsn, cid, ben, ann, days=-1)
    elif how == "withdrawn":
        inv = add_invite(dsn, cid, ben, ann)
        sql(dsn, "UPDATE circle_invites SET revoked_at = now() WHERE id = %s", (inv,))
    elif how == "someone_else":
        inv = add_invite(dsn, cid, ben, ids["Cat"])
    elif how == "missing":
        inv = 987654
    else:   # an invite code is not addressed to anyone: it stays on the Friends page's join flow, with its own choice of scopes
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            inv = friends.mint_invite(conn, ben, cid)["id"]
    r = client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))
    assert r.status_code == 404 and r.json()["detail"] == {"code": "invite_gone", "message": "这个邀请已失效"}
    assert sql(dsn, "SELECT count(*) AS n FROM circle_members WHERE circle_id = %s AND user_id = %s", (cid, ann))[0]["n"] == 0
    assert f"invite:{inv}" not in item_ids(client, tok["Ann"])
    if how == "someone_else":
        assert sql(dsn, "SELECT revoked_at FROM circle_invites WHERE id = %s", (inv,))[0]["revoked_at"] is None
        assert client.post(f"/v1/users/me/inbox/invites/{inv}/ignore", headers=h(tok["Ann"])).status_code == 404
        assert sql(dsn, "SELECT revoked_at FROM circle_invites WHERE id = %s", (inv,))[0]["revoked_at"] is None
        assert f"invite:{inv}" in item_ids(client, tok["Cat"])


def test_no_circle_consent_gets_its_own_code_then_works(client, dsn, world):
    ids, tok = world
    eve, ben = ids["Eve"], ids["Ben"]
    cid = add_circle(dsn, ben)
    inv = add_invite(dsn, cid, ben, eve)
    card = listing(client, tok["Eve"])["items"][0]["invite"]
    assert card["consent_ok"] is False   # the card still shows, so the app can ask before the tap
    r = client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Eve"]))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "consent_required" and r.json()["detail"]["required_version"] == FP.CONSENT_VERSION
    en = client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Eve"], "en"))
    assert not CJK.search(en.json()["detail"]["message"])
    assert sql(dsn, "SELECT count(*) AS n FROM circle_members WHERE circle_id = %s AND user_id = %s", (cid, eve))[0]["n"] == 0
    assert sql(dsn, "SELECT revoked_at FROM circle_invites WHERE id = %s", (inv,))[0]["revoked_at"] is None
    sql(dsn, "UPDATE users SET consent_version = %s WHERE id = %s", (FP.CONSENT_VERSION, eve))
    try:
        assert client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Eve"])).status_code == 200
    finally:
        sql(dsn, "UPDATE users SET consent_version = '2026-10-22.1' WHERE id = %s", (eve,))


def test_consent_code_also_when_the_friends_flag_asks_for_that_consent(client, dsn, world):
    """The flag can carry min_consent_version: then it is "off" for Eve until she agrees, and she must still get the code."""
    ids, tok = world
    inv = add_invite(dsn, add_circle(dsn, ids["Ben"]), ids["Ben"], ids["Eve"])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, "friends", "all", min_consent=FP.CONSENT_VERSION, actor="test")
    flags.invalidate()
    try:
        assert item_ids(client, tok["Eve"]) == [f"invite:{inv}"]
        assert code(client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Eve"]))) == "consent_required"
    finally:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            flags.set_flag(conn, "friends", "all", clear_min_consent=True, actor="test")
        flags.invalidate()


def test_expired_invite_is_gone_before_any_consent_question(client, dsn, world):
    ids, tok = world
    inv = add_invite(dsn, add_circle(dsn, ids["Ben"]), ids["Ben"], ids["Eve"], days=-1)
    assert code(client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Eve"]))) == "invite_gone"


def test_community_rules_get_their_own_code_while_the_flag_is_on(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    inv = add_invite(dsn, add_circle(dsn, ben), ben, ann)
    set_flag(dsn, "circle_rules", "all")
    try:
        assert listing(client, tok["Ann"])["items"][0]["invite"]["rules_ok"] is False
        r = client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))
        assert r.status_code == 428
        assert r.json()["detail"]["code"] == "rules_required" and r.json()["detail"]["required_version"] == FP.RULES_VERSION
        sql(dsn, "INSERT INTO circle_rules_acceptances (user_id, version) VALUES (%s, %s)", (ann, FP.RULES_VERSION))
        assert client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"])).status_code == 200
    finally:
        set_flag(dsn, "circle_rules", "off")
        sql(dsn, "DELETE FROM circle_rules_acceptances WHERE user_id = %s", (ann,))


def test_a_full_circle_is_a_clear_refusal(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    inv = add_invite(dsn, add_circle(dsn, ben, members=[ids["Cat"]], max_members=2), ben, ann)
    r = client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"], "en"))
    assert r.status_code == 409 and code(r) == "join_refused" and not CJK.search(r.json()["detail"]["message"])
    assert sql(dsn, "SELECT revoked_at FROM circle_invites WHERE id = %s", (inv,))[0]["revoked_at"] is None


def test_ignore_ends_the_invite_and_the_owner_sees_no_trace(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    cid = add_circle(dsn, ben)
    inv = add_invite(dsn, cid, ben, ann)

    def owner_view():
        view = client.get("/v1/friends", headers=h(tok["Ben"])).json()
        view.pop("now")
        return view
    before = owner_view()
    r = client.post(f"/v1/users/me/inbox/invites/{inv}/ignore", headers=h(tok["Ann"]))
    assert r.status_code == 200 and r.json() == {"ignored": True}
    assert item_ids(client, tok["Ann"]) == []
    assert code(client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))) == "invite_gone"
    assert code(client.post(f"/v1/users/me/inbox/invites/{inv}/ignore", headers=h(tok["Ann"]))) == "invite_gone"
    assert owner_view() == before   # declined looks exactly like still waiting
    assert "Ann" not in json.dumps(owner_view())
    assert sql(dsn, "SELECT count(*) AS n FROM circle_members WHERE circle_id = %s AND user_id = %s", (cid, ann))[0]["n"] == 0


def test_accept_and_ignore_are_gone_without_the_friends_flag(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    inv = add_invite(dsn, add_circle(dsn, ben), ben, ann)
    note = add_note(dsn, ann, actor=ben)
    nid = add_notice(dsn, ann)
    set_flag(dsn, "friends", "off")
    try:
        assert item_ids(client, tok["Ann"]) == [f"notice:{nid}"]
        assert code(client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))) == "invite_gone"
        assert code(client.post(f"/v1/users/me/inbox/invites/{inv}/ignore", headers=h(tok["Ann"]))) == "invite_gone"
    finally:
        set_flag(dsn, "friends", "all")
    assert set(item_ids(client, tok["Ann"])) == {f"notice:{nid}", f"invite:{inv}", f"notification:{note}"}


# ---------------------------------------------------------------- blocks and moderation
def add_comment(dsn, author, writer, text="示例评论"):
    post = sql(dsn, "INSERT INTO circle_feed_posts (user_id, date) VALUES (%s, %s) ON CONFLICT (user_id, date) DO UPDATE SET date = EXCLUDED.date RETURNING id",
               (author, date.today()))[0]["id"]
    comment = sql(dsn, "INSERT INTO circle_feed_comments (post_id, user_id, body) VALUES (%s, %s, %s) RETURNING id", (post, writer, text))[0]["id"]
    note = add_note(dsn, author, kind="circle.comment", actor=writer, post=post, body=text[:120].strip())
    return post, comment, note


@pytest.mark.parametrize("who", ["i_blocked_them", "they_blocked_me"])
def test_a_block_either_way_hides_their_invites_and_interactions(client, dsn, world, who):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    add_circle(dsn, ann, members=[ben])   # blocking needs a shared circle
    inv = add_invite(dsn, add_circle(dsn, ben, name="Ben 的圈"), ben, ann)
    like = add_note(dsn, ann, actor=ben)
    _, _, comment = add_comment(dsn, ann, ben)
    other = add_note(dsn, ann, actor=ids["Cat"])
    nid = add_notice(dsn, ann)
    shown = {f"invite:{inv}", f"notification:{like}", f"notification:{comment}", f"notification:{other}", f"notice:{nid}"}
    assert set(item_ids(client, tok["Ann"])) == shown
    blocker, target = ("Ann", ben) if who == "i_blocked_them" else ("Ben", ann)
    assert client.put(f"/v1/friends/blocks/{target}", headers=h(tok[blocker])).status_code == 200
    assert set(item_ids(client, tok["Ann"])) == {f"notification:{other}", f"notice:{nid}"}
    assert listing(client, tok["Ann"])["unread"] == 2
    # not acceptable, and the answer is the one for any dead invite: it does not tell who blocked whom
    r = client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))
    assert r.status_code == 404 and code(r) == "invite_gone"
    assert client.get(f"/v1/users/me/inbox/invites/{inv}/avatar", headers=h(tok["Ann"])).status_code == 404
    assert client.delete(f"/v1/friends/blocks/{target}", headers=h(tok[blocker])).status_code == 200
    assert set(item_ids(client, tok["Ann"])) == shown


@pytest.mark.parametrize("blocked", ["Ben", "Cat"])
def test_blocking_the_circle_owner_or_the_invite_maker_hides_the_invite(client, dsn, world, blocked):
    ids, tok = world
    ann, ben, cat = ids["Ann"], ids["Ben"], ids["Cat"]
    add_circle(dsn, ann, members=[ben, cat])
    inv = add_invite(dsn, add_circle(dsn, ben, members=[cat]), cat, ann)   # Ben owns the circle, Cat made the invite
    assert item_ids(client, tok["Ann"]) == [f"invite:{inv}"]
    assert client.put(f"/v1/friends/blocks/{ids[blocked]}", headers=h(tok["Ann"])).status_code == 200
    assert item_ids(client, tok["Ann"]) == []
    assert code(client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))) == "invite_gone"


def test_a_comment_hidden_from_me_leaves_the_inbox(client, dsn, world):
    ids, tok = world
    ann, ben, cat = ids["Ann"], ids["Ben"], ids["Cat"]
    _, deleted, n1 = add_comment(dsn, ann, ben, "示例：作者删掉的")
    _, reported, n2 = add_comment(dsn, ann, ben, "示例：我举报的")
    _, removed, n3 = add_comment(dsn, ann, cat, "示例：审核删除的")
    _, kept, n4 = add_comment(dsn, ann, cat, "示例：还在的")
    assert len(item_ids(client, tok["Ann"])) == 4
    sql(dsn, "UPDATE circle_feed_comments SET deleted_at = now() WHERE id = %s", (deleted,))
    item = sql(dsn, "INSERT INTO circle_moderation (kind, target_id, user_id) VALUES ('comment', %s, %s) RETURNING id", (reported, ben))[0]["id"]
    sql(dsn, "INSERT INTO circle_reports (item_id, user_id, reason) VALUES (%s, %s, 'spam')", (item, ann))
    sql(dsn, "INSERT INTO circle_moderation (kind, target_id, user_id, status) VALUES ('comment', %s, %s, 'removed')", (removed, cat))
    out = listing(client, tok["Ann"])
    assert [i["id"] for i in out["items"]] == [f"notification:{n4}"] and out["items"][0]["body"] == "示例：还在的"


def test_a_long_comment_still_matches_its_notification(client, dsn, world):
    ids, tok = world
    _, _, note = add_comment(dsn, ids["Ann"], ids["Ben"], "示例" * 100)
    assert item_ids(client, tok["Ann"]) == [f"notification:{note}"]


def test_interactions_from_hidden_banned_or_deleted_people_are_left_out(client, dsn, world):
    ids, tok = world
    ann, ben, cat = ids["Ann"], ids["Ben"], ids["Cat"]
    add_note(dsn, ann, actor=ben)   # profile hidden by moderation
    add_note(dsn, ann, actor=cat)   # banned from circles
    add_note(dsn, ann, actor=987654)   # the account is gone
    keep = add_note(dsn, ann, actor=ids["Dan"])
    sql(dsn, "INSERT INTO circle_moderation (kind, target_id, user_id, status) VALUES ('profile', %s, %s, 'hidden')", (ben, ben))
    sql(dsn, "INSERT INTO circle_bans (user_id) VALUES (%s)", (cat,))
    assert item_ids(client, tok["Ann"]) == [f"notification:{keep}"]


def test_card_hides_the_name_of_an_owner_moderation_hid(client, dsn, world):
    ids, tok = world
    ben = ids["Ben"]
    add_invite(dsn, add_circle(dsn, ben), ben, ids["Ann"])
    sql(dsn, "INSERT INTO circle_moderation (kind, target_id, user_id, status) VALUES ('profile', %s, %s, 'removed')", (ben, ben))
    item = listing(client, tok["Ann"])["items"][0]
    assert item["invite"]["owner_name"] == FP.HIDDEN_NAME[0] and "Ben" not in json.dumps(item)


def test_someone_banned_from_circles_gets_no_cards(client, dsn, world):
    ids, tok = world
    ann, ben = ids["Ann"], ids["Ben"]
    inv = add_invite(dsn, add_circle(dsn, ben), ben, ann)
    sql(dsn, "INSERT INTO circle_bans (user_id) VALUES (%s)", (ann,))
    assert item_ids(client, tok["Ann"]) == []
    assert code(client.post(f"/v1/users/me/inbox/invites/{inv}/accept", headers=h(tok["Ann"]))) == "invite_gone"


def test_state_goes_with_the_account(dsn, world):
    ids, _ = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        gone = conn.execute("INSERT INTO users (display_name) VALUES ('Temp') RETURNING id").fetchone()["id"]
        inbox._set_state(conn, gone, ["notice:1"], hidden=True)
        conn.execute("DELETE FROM users WHERE id = %s", (gone,))
        assert conn.execute("SELECT count(*) AS n FROM inbox_item_state WHERE user_id = %s", (gone,)).fetchone()["n"] == 0
