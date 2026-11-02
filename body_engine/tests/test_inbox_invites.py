"""The inbox's second half (issue #359): the notifications circles write for it (someone joined, an invite accepted, someone invited
you), system and ai_card notifications in the list, inviting someone you know into your other circle (friends/direct.py: who may
be invited, nothing leaks, limits, a refusal stays invisible) and the admin's batch invite. Synthetic people and data only.
Test database body_test_inbox_invites (BODY_TEST_DB_NAME_INBOX_INVITES), recreated on every run."""
import json
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, errors_en
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import admin as circle_admin, blocks, codes, params as FP, store as friends
from body_engine.notify import notify
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_INBOX_INVITES", "body_test_inbox_invites")
NAMES = ("Ann", "Ben", "Cat", "Dan", "Eve")


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
    """Ann, Ben, Cat, Dan agreed to the circle consent, Eve only to an older version."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, birth_date, height_cm, consent_at, consent_version,
                                                     personal_circle_at, timezone)
                                  VALUES (%s, '1990-01-01', 175, now(), %s, now(), 'Australia/Sydney') RETURNING id""",
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
def clean(dsn, world):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for t in ("circles", "shares", "circle_blocks", "circle_bans", "circle_moderation", "notifications", "admin_notices",
                  "inbox_item_state", "circle_invite_mutes"):
            conn.execute(f"DELETE FROM {t}")
    set_flag(dsn, "inbox", "all")
    yield


def h(t, lang=None):
    return {"Authorization": f"Bearer {t}", **({"X-Body-Lang": lang} if lang else {})}


def sql(dsn, query, args=()):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cur = conn.execute(query, args)
        return cur.fetchall() if cur.description else None


def add_circle(dsn, owner, name="示例圈", members=()):
    """A circle with members, and no notifications left over from setting it up."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cid = circle_admin.create_circle(conn, name, owner)["id"]
        for m in members:
            friends.join(conn, m, list(FP.DEFAULT_SCOPES), code=friends.mint_invite(conn, owner, cid)["code"])
        conn.execute("DELETE FROM notifications WHERE data->>'circle_id' = %s", (str(cid),))
    return cid


def items(client, tok, lang=None):
    r = client.get("/v1/users/me/inbox", headers=h(tok, lang))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def kinds(client, tok):
    return [i.get("kind") or i["type"] for i in items(client, tok)]


def send(client, tok, target, circle):
    return client.post(f"/v1/friends/people/{target}/invites", headers=h(tok), json={"circle_id": circle})


def options(client, tok, target):
    return client.get(f"/v1/friends/people/{target}/invite-options", headers=h(tok))


def card(client, tok):
    return next(i for i in items(client, tok) if i["type"] == "circle_invite")


def standing(client, tok, target, circle):
    r = options(client, tok, target)
    assert r.status_code == 200, r.text
    return next(c["status"] for c in r.json()["circles"] if c["id"] == circle)


@pytest.fixture
def two(dsn, world):
    """Ann owns a circle Ben is in (so they know each other) and another one Ben is not in."""
    ids, tok = world
    shared = add_circle(dsn, ids["Ann"], "共同圈", [ids["Ben"]])
    other = add_circle(dsn, ids["Ann"], "另一个圈")
    return ids, tok, shared, other


# ---------------------------------------------------------------- who is told what
def test_joining_with_a_code_tells_the_owner_only(client, dsn, world):
    ids, tok = world
    cid = add_circle(dsn, ids["Ann"], "示例圈", [ids["Ben"]])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        friends.join(conn, ids["Cat"], list(FP.DEFAULT_SCOPES), code=friends.mint_invite(conn, ids["Ann"], cid)["code"])
    got = items(client, tok["Ann"])
    assert [i["kind"] for i in got] == ["circle.joined"]
    assert got[0]["circle_id"] == cid and got[0]["read"] is False and "Cat" in got[0]["title"] and "示例圈" in got[0]["title"]
    assert got[0]["url"] == f"/app/?tab=friends&circle_id={cid}"
    assert items(client, tok["Ben"]) == [] and items(client, tok["Cat"]) == []
    en = items(client, tok["Ann"], "en")[0]["title"]
    assert en == "Cat joined your circle “示例圈”"


def test_an_activation_code_tells_the_owner_too(client, dsn, world):
    ids, tok = world
    cid = add_circle(dsn, ids["Ann"])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        code = codes.create(conn, cid, "Example Club 2026", 5, None)["code"]
        codes.redeem(conn, ids["Dan"], code)
    assert kinds(client, tok["Ann"]) == ["circle.joined"]


def test_an_accepted_invite_is_one_message_to_the_person_who_sent_it(client, dsn, two):
    ids, tok, _, other = two
    assert send(client, tok["Ann"], ids["Ben"], other).status_code == 200
    invite = card(client, tok["Ben"])["invite"]["invite_id"]
    r = client.post(f"/v1/users/me/inbox/invites/{invite}/accept", headers=h(tok["Ben"]))
    assert r.status_code == 200, r.text
    got = items(client, tok["Ann"])
    assert [i["kind"] for i in got] == ["circle.invite_accepted"]
    assert "Ben" in got[0]["title"] and "另一个圈" in got[0]["title"] and got[0]["circle_id"] == other
    m = sql(dsn, "SELECT join_via, invited_by FROM circle_members WHERE circle_id = %s AND user_id = %s", (other, ids["Ben"]))[0]
    assert m == {"join_via": "member_invite", "invited_by": ids["Ann"]}
    assert standing(client, tok["Ann"], ids["Ben"], other) == "accepted"


def test_nothing_is_written_while_the_flag_is_off(client, dsn, world):
    ids, tok = world
    cid = add_circle(dsn, ids["Ann"])
    set_flag(dsn, "inbox", "off")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        friends.join(conn, ids["Cat"], list(FP.DEFAULT_SCOPES), code=friends.mint_invite(conn, ids["Ann"], cid)["code"])
    assert sql(dsn, "SELECT count(*) AS n FROM notifications")[0]["n"] == 0
    assert client.get("/v1/users/me/inbox", headers=h(tok["Ann"])).status_code == 404


def test_nothing_about_someone_blocked_or_with_a_hidden_profile(client, dsn, world):
    ids, tok = world
    for how in ("block", "hidden"):
        cid = add_circle(dsn, ids["Ann"])
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            code = friends.mint_invite(conn, ids["Ann"], cid)["code"]
            if how == "block":
                conn.execute("INSERT INTO circle_blocks (user_id, blocked_user_id) VALUES (%s, %s)", (ids["Ann"], ids["Cat"]))
            else:
                conn.execute("""INSERT INTO circle_moderation (kind, target_id, user_id, status)
                                VALUES ('profile', %s, %s, 'hidden')""", (ids["Dan"], ids["Dan"]))
            friends.join(conn, ids["Cat"] if how == "block" else ids["Dan"], list(FP.DEFAULT_SCOPES), code=code)
        assert sql(dsn, "SELECT count(*) AS n FROM notifications")[0]["n"] == 0, how


def test_nothing_about_a_circle_the_owner_muted(client, dsn, world):
    ids, tok = world
    set_flag(dsn, "circle_prefs", "all")
    cid = add_circle(dsn, ids["Ann"])
    r = client.put(f"/v1/friends/circles/{cid}/prefs", headers=h(tok["Ann"]), json={"muted": True})
    assert r.status_code == 200, r.text
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        friends.join(conn, ids["Cat"], list(FP.DEFAULT_SCOPES), code=friends.mint_invite(conn, ids["Ann"], cid)["code"])
    assert kinds(client, tok["Ann"]) == []
    set_flag(dsn, "circle_prefs", "off")


# ---------------------------------------------------------------- system and ai_card in the list
def test_system_notifications_are_listed_but_not_upgrades_or_test_pushes(client, dsn, world):
    ids, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for kind in ("system", "system.maintenance", "system.upgrade", "system.test"):
            notify(conn, ids["Ann"], kind, {"zh": "示例", "en": "Example"}, "")
    got = items(client, tok["Ann"])
    assert sorted(i["kind"] for i in got) == ["system", "system.maintenance"]
    assert {i["type"] for i in got} == {"system"} and got[0]["read"] is False
    r = client.post("/v1/users/me/inbox/read", headers=h(tok["Ann"]), json={"all": True})
    assert r.json() == {"marked": 2, "unread": 0}
    assert sql(dsn, "SELECT count(*) AS n FROM notifications WHERE read_at IS NULL")[0]["n"] == 2   # the two left out stay as they were


def test_ai_card_notifications_only_while_their_flag_is_on(client, dsn, world):
    ids, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        notify(conn, ids["Ann"], "ai_card", {"zh": "示例额度卡", "en": "Example card"}, "", url="/app/?tab=me&ai_cards=1")
    assert items(client, tok["Ann"]) == []
    set_flag(dsn, "ai_cards", "all")
    try:
        got = items(client, tok["Ann"], "en")
        assert [(i["type"], i["title"]) for i in got] == [("ai_card", "Example card")]
    finally:
        set_flag(dsn, "ai_cards", "off")


# ---------------------------------------------------------------- inviting someone I know
def test_only_someone_i_share_a_circle_with_and_every_other_answer_is_the_same(client, dsn, two):
    ids, tok, shared, other = two
    assert options(client, tok["Ann"], ids["Ben"]).status_code == 200
    answers = []
    for target in (ids["Cat"], 999_999, ids["Ann"]):   # a stranger who is here, nobody, myself
        answers.append((options(client, tok["Ann"], target).status_code, options(client, tok["Ann"], target).json()))
        answers.append((send(client, tok["Ann"], target, other).status_code, send(client, tok["Ann"], target, other).json()))
    assert {a[0] for a in answers} == {404}
    assert len({json.dumps(a[1]) for a in answers}) == 1
    sql(dsn, "INSERT INTO circle_blocks (user_id, blocked_user_id) VALUES (%s, %s)", (ids["Ben"], ids["Ann"]))
    r = send(client, tok["Ann"], ids["Ben"], other)
    assert r.status_code == 404 and json.dumps(r.json()) == json.dumps(answers[0][1])
    assert sql(dsn, "SELECT count(*) AS n FROM circle_invites WHERE invitee_user_id IS NOT NULL")[0]["n"] == 0


def test_options_list_my_own_circles_with_where_the_person_stands(client, dsn, two):
    ids, tok, shared, other = two
    theirs = add_circle(dsn, ids["Ben"], "别人的圈", [ids["Ann"]])
    r = options(client, tok["Ann"], ids["Ben"]).json()
    assert {c["id"]: c["status"] for c in r["circles"]} == {shared: "member", other: "open"}
    assert theirs not in {c["id"] for c in r["circles"]}
    assert r["per_day"] == FP.DIRECT_INVITES_PER_DAY and r["left_today"] == FP.DIRECT_INVITES_PER_DAY
    assert set(r["circles"][0]) == {"id", "name", "avatar", "members", "status"}   # nothing about the other members


def test_only_the_owner_can_invite_into_a_circle(client, dsn, world):
    ids, tok = world
    cid = add_circle(dsn, ids["Ann"], "示例圈", [ids["Ben"], ids["Cat"]])
    other = add_circle(dsn, ids["Ann"], "别的圈", [ids["Dan"], ids["Ben"]])
    assert send(client, tok["Ben"], ids["Dan"], cid).status_code == 403   # Ben is a member of it, Ann owns it
    assert send(client, tok["Ben"], ids["Cat"], other).status_code == 403
    assert sql(dsn, "SELECT count(*) AS n FROM circle_invites WHERE invitee_user_id IS NOT NULL")[0]["n"] == 0


def test_the_card_says_what_joining_shares_and_nothing_about_who_else(client, dsn, two):
    ids, tok, _, other = two
    assert send(client, tok["Ann"], ids["Ben"], other).json() == {"status": "pending"}
    got = card(client, tok["Ben"])
    inv = got["invite"]
    assert got["id"] == f"invite:{inv['invite_id']}" and inv["circle_name"] == "另一个圈" and inv["owner_name"] == "Ann"
    assert inv["default_scopes"] == list(FP.DEFAULT_SCOPES) and inv["can_mute"] is True and inv["consent_ok"] is True
    assert set(inv) == {"invite_id", "circle_name", "avatar", "owner_name", "members", "expires_at", "default_scopes", "shares",
                        "consent_ok", "rules_ok", "can_mute"}   # no id of the inviter or the owner, no member names
    # the push that came with it is not a second row, and reading the card reads the push
    assert [i["type"] for i in items(client, tok["Ben"])] == ["circle_invite"]
    note = sql(dsn, "SELECT id, url, read_at FROM notifications WHERE user_id = %s AND kind = 'circle.invite'", (ids["Ben"],))[0]
    assert note["url"] == f"/app/?tab=me&inbox=invite:{inv['invite_id']}" and note["read_at"] is None
    r = client.post("/v1/users/me/inbox/read", headers=h(tok["Ben"]), json={"ids": [got["id"]]})
    assert r.json() == {"marked": 1, "unread": 0}
    assert card(client, tok["Ben"])["read"] is True
    assert sql(dsn, "SELECT read_at FROM notifications WHERE id = %s", (note["id"],))[0]["read_at"] is not None


def test_answering_closes_the_push(client, dsn, two):
    ids, tok, _, other = two
    send(client, tok["Ann"], ids["Ben"], other)
    invite = card(client, tok["Ben"])["invite"]["invite_id"]
    assert client.post(f"/v1/users/me/inbox/invites/{invite}/ignore", headers=h(tok["Ben"])).status_code == 200
    assert sql(dsn, "SELECT read_at FROM notifications WHERE user_id = %s AND kind = 'circle.invite'", (ids["Ben"],))[0]["read_at"]


def test_a_refusal_stays_invisible_and_no_second_invite_until_it_expires(client, dsn, two):
    ids, tok, _, other = two
    send(client, tok["Ann"], ids["Ben"], other)
    invite = card(client, tok["Ben"])["invite"]["invite_id"]
    assert standing(client, tok["Ann"], ids["Ben"], other) == "pending"
    client.post(f"/v1/users/me/inbox/invites/{invite}/ignore", headers=h(tok["Ben"]))
    assert standing(client, tok["Ann"], ids["Ben"], other) == "pending"   # the same answer: no way to tell
    assert send(client, tok["Ann"], ids["Ben"], other).json() == {"status": "pending", "already": True}
    assert [i for i in items(client, tok["Ben"]) if i["type"] == "circle_invite"] == []
    assert sql(dsn, "SELECT count(*) AS n FROM circle_invites WHERE invitee_user_id = %s", (ids["Ben"],))[0]["n"] == 1
    sql(dsn, "UPDATE circle_invites SET expires_at = now() - interval '1 minute' WHERE id = %s", (invite,))
    assert standing(client, tok["Ann"], ids["Ben"], other) == "open"
    assert send(client, tok["Ann"], ids["Ben"], other).json() == {"status": "pending"}
    assert card(client, tok["Ben"])["invite"]["invite_id"] != invite


def test_one_pending_invite_per_person_and_circle(client, dsn, two):
    ids, tok, _, other = two
    assert "already" not in send(client, tok["Ann"], ids["Ben"], other).json()
    assert send(client, tok["Ann"], ids["Ben"], other).json()["already"] is True
    assert sql(dsn, "SELECT count(*) AS n FROM circle_invites WHERE invitee_user_id = %s", (ids["Ben"],))[0]["n"] == 1
    assert options(client, tok["Ann"], ids["Ben"]).json()["left_today"] == FP.DIRECT_INVITES_PER_DAY - 1


def test_a_daily_limit_on_invites_sent(client, dsn, two, monkeypatch):
    ids, tok, _, other = two
    third = add_circle(dsn, ids["Ann"], "第三个圈")
    monkeypatch.setattr(FP, "DIRECT_INVITES_PER_DAY", 1)
    assert send(client, tok["Ann"], ids["Ben"], other).status_code == 200
    assert options(client, tok["Ann"], ids["Ben"]).json()["left_today"] == 0
    assert send(client, tok["Ann"], ids["Ben"], other).status_code == 200   # sending again costs nothing
    r = send(client, tok["Ann"], ids["Ben"], third)
    assert r.status_code == 429
    r = client.post(f"/v1/friends/people/{ids['Ben']}/invites", headers=h(tok["Ann"], "en"), json={"circle_id": third})
    assert "limit of 1 invites" in r.json()["detail"]
    # a day later it is open again
    sql(dsn, "UPDATE circle_invites SET created_at = now() - interval '25 hours' WHERE created_by = %s", (ids["Ann"],))
    assert send(client, tok["Ann"], ids["Ben"], third).status_code == 200


def test_a_full_circle_says_so(client, dsn, two):
    ids, tok, _, other = two
    sql(dsn, "INSERT INTO circle_members (circle_id, user_id, scopes, join_via) VALUES (%s, %s, '{}', 'invite_code')", (other, ids["Dan"]))
    sql(dsn, "UPDATE circles SET max_members = 2 WHERE id = %s", (other,))
    assert standing(client, tok["Ann"], ids["Ben"], other) == "full"
    r = send(client, tok["Ann"], ids["Ben"], other)
    assert r.status_code == 409 and "已满" in r.json()["detail"]


def test_a_read_only_token_cannot_send_and_the_flag_hides_everything(client, dsn, two):
    ids, tok, _, other = two
    assert send(client, tok["ann_ro"], ids["Ben"], other).status_code == 403
    set_flag(dsn, "inbox", "off")
    assert options(client, tok["Ann"], ids["Ben"]).status_code == 404
    assert send(client, tok["Ann"], ids["Ben"], other).status_code == 404
    assert client.get("/v1/friends/invite-mutes", headers=h(tok["Ann"])).status_code == 404


def test_someone_without_the_circle_consent_is_asked_first(client, dsn, world):
    ids, tok = world
    cid = add_circle(dsn, ids["Ann"], "示例圈")
    other = add_circle(dsn, ids["Ann"], "别的圈")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:   # Eve came in before the consent changed
        conn.execute("""INSERT INTO circle_members (circle_id, user_id, scopes, join_via) VALUES (%s, %s, %s, 'invite_code')""",
                     (cid, ids["Eve"], list(FP.DEFAULT_SCOPES)))
    assert send(client, tok["Ann"], ids["Eve"], other).status_code == 200
    got = card(client, tok["Eve"])
    assert got["invite"]["consent_ok"] is False
    r = client.post(f"/v1/users/me/inbox/invites/{got['invite']['invite_id']}/accept", headers=h(tok["Eve"]))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "consent_required"
    assert standing(client, tok["Ann"], ids["Eve"], other) == "pending"


# ---------------------------------------------------------------- 「不再接收此人的邀请」
def test_muting_closes_their_invites_and_they_cannot_tell(client, dsn, two):
    ids, tok, _, other = two
    third = add_circle(dsn, ids["Ann"], "第三个圈")
    send(client, tok["Ann"], ids["Ben"], other)
    send(client, tok["Ann"], ids["Ben"], third)
    invite = card(client, tok["Ben"])["invite"]["invite_id"]
    r = client.post(f"/v1/users/me/inbox/invites/{invite}/mute", headers=h(tok["Ben"]))
    assert r.status_code == 200 and r.json() == {"muted": True}
    assert [i for i in items(client, tok["Ben"]) if i["type"] == "circle_invite"] == []
    assert standing(client, tok["Ann"], ids["Ben"], other) == "pending" and standing(client, tok["Ann"], ids["Ben"], third) == "pending"
    # a new one looks the same to the sender and never reaches the person
    fourth = add_circle(dsn, ids["Ann"], "第四个圈")
    assert send(client, tok["Ann"], ids["Ben"], fourth).json() == {"status": "pending"}
    assert [i for i in items(client, tok["Ben"]) if i["type"] == "circle_invite"] == []
    assert sql(dsn, "SELECT count(*) AS n FROM notifications WHERE user_id = %s AND kind = 'circle.invite' AND read_at IS NULL",
               (ids["Ben"],))[0]["n"] == 0
    # the list and undoing it
    people = client.get("/v1/friends/invite-mutes", headers=h(tok["Ben"])).json()["people"]
    assert [(p["id"], p["display_name"]) for p in people] == [(ids["Ann"], "Ann")]
    assert client.delete(f"/v1/friends/invite-mutes/{ids['Ann']}", headers=h(tok["Ben"])).status_code == 204
    assert client.delete(f"/v1/friends/invite-mutes/{ids['Ann']}", headers=h(tok["Ben"])).status_code == 404
    assert client.get("/v1/friends/invite-mutes", headers=h(tok["Ben"])).json()["people"] == []
    fifth = add_circle(dsn, ids["Ann"], "第五个圈")
    send(client, tok["Ann"], ids["Ben"], fifth)
    assert card(client, tok["Ben"])["invite"]["circle_name"] == "第五个圈"


def test_mute_is_only_for_an_invite_someone_sent_by_hand(client, dsn, two):
    ids, tok, _, other = two
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert circle_admin.invite_users(conn, other, [ids["Ben"]])[0]["status"] == "invited"
    got = card(client, tok["Ben"])
    assert got["invite"]["can_mute"] is False
    r = client.post(f"/v1/users/me/inbox/invites/{got['invite']['invite_id']}/mute", headers=h(tok["Ben"]))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "invite_gone"
    assert client.post("/v1/users/me/inbox/invites/999999/mute", headers=h(tok["Ben"])).status_code == 404


# ---------------------------------------------------------------- the admin's batch
def test_admin_batch_sends_cards_only_to_who_may_get_one_and_is_safe_to_rerun(client, dsn, world):
    ids, tok = world
    cid = add_circle(dsn, ids["Ann"], "示例俱乐部", [ids["Dan"]])
    sql(dsn, "INSERT INTO users (display_name, status) VALUES ('Pending', 'pending') RETURNING id")
    pending = sql(dsn, "SELECT id FROM users WHERE display_name = 'Pending'")[0]["id"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        first = circle_admin.invite_users(conn, cid, [ids["Ben"], ids["Dan"], ids["Eve"], pending, 999_999, ids["Ben"]])
    assert {r["user_id"]: r["status"] for r in first} == {
        ids["Ben"]: "invited", ids["Dan"]: "member", ids["Eve"]: "no_consent", pending: "not_active", 999_999: "no_account"}
    assert len(first) == 5
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        again = circle_admin.invite_users(conn, cid, [ids["Ben"]])
    assert again == [{"user_id": ids["Ben"], "status": "already_invited"}]
    got = card(client, tok["Ben"])
    assert got["invite"]["owner_name"] == "Ann" and got["invite"]["circle_name"] == "示例俱乐部"
    row = sql(dsn, "SELECT code_sha256, origin, created_by FROM circle_invites WHERE id = %s", (got["invite"]["invite_id"],))[0]
    assert row == {"code_sha256": None, "origin": "admin", "created_by": ids["Ann"]}
    r = client.post(f"/v1/users/me/inbox/invites/{got['invite']['invite_id']}/accept", headers=h(tok["Ben"]))
    assert r.status_code == 200, r.text
    assert sql(dsn, "SELECT join_via FROM circle_members WHERE circle_id = %s AND user_id = %s", (cid, ids["Ben"]))[0]["join_via"] == "admin_invite"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert circle_admin.invite_users(conn, cid, [ids["Ben"]])[0]["status"] == "member"
        with pytest.raises(ValueError):
            circle_admin.invite_users(conn, 999_999, [ids["Ben"]])


def test_admin_batch_leaves_out_who_blocked_the_owner_or_muted_them(client, dsn, world):
    ids, tok = world
    cid = add_circle(dsn, ids["Ann"])
    sql(dsn, "INSERT INTO circle_blocks (user_id, blocked_user_id) VALUES (%s, %s)", (ids["Ben"], ids["Ann"]))
    sql(dsn, "INSERT INTO circle_invite_mutes (user_id, muted_user_id) VALUES (%s, %s)", (ids["Cat"], ids["Ann"]))
    sql(dsn, "INSERT INTO circle_bans (user_id) VALUES (%s)", (ids["Dan"],))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        out = circle_admin.invite_users(conn, cid, [ids["Ben"], ids["Cat"], ids["Dan"]])
    assert [r["status"] for r in out] == ["blocked", "muted", "banned"]


def test_new_refusals_have_english():
    assert errors_en.translate("这个人不在你不再接收邀请的名单里") == "This person is not on your list of muted invites"
    assert errors_en.translate("今天发出的邀请到上限了（10 个），明天再试") == "You reached today's limit of 10 invites; try again tomorrow"


def test_hidden_names_are_masked_in_the_mute_list(client, dsn, two):
    ids, tok, _, other = two
    sql(dsn, "INSERT INTO circle_invite_mutes (user_id, muted_user_id) VALUES (%s, %s)", (ids["Ben"], ids["Dan"]))
    sql(dsn, """INSERT INTO circle_moderation (kind, target_id, user_id, status) VALUES ('profile', %s, %s, 'removed')""",
        (ids["Dan"], ids["Dan"]))
    assert client.get("/v1/friends/invite-mutes", headers=h(tok["Ben"])).json()["people"][0]["display_name"] == blocks.hidden_name()
