"""Managing many circles (#269 P2, flag circle_prefs, migration 765, friends/prefs.py): pin (max 3, first in the switcher),
mute (no ❤️ / comment notifications or red dot through it, its posts out of 「全部」), archive (hidden from the switcher and
「全部」, muted, still sharing), most-recent order, the invite inbox and 「全部忽略」.
Test database body_test_circle_prefs (BODY_TEST_DB_NAME_CIRCLE_PREFS), recreated on every run; skipped without Postgres."""
import json
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import api as friends_api, leaderboard, params as P, store
from body_engine.rollout import flags
from body_engine.training import loads
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CIRCLE_PREFS", "body_test_circle_prefs")
NAMES = ("Ann", "Ben", "Cat", "Dan")
FEED = "/v1/friends/feed"


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
        for f in ("friends", P.FEED_FLAG, P.BY_CIRCLE_FLAG, "notifications"):
            flags.set_flag(conn, f, "all", actor="test")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(t):
    return {"Authorization": f"Bearer {t}"}


def circle(client, tok, name):
    r = client.post("/v1/friends/circles", headers=h(tok), json={"name": name, "scopes": ["scores", "training"]})
    assert r.status_code == 201, r.text
    return r.json()


def join(client, tok, code):
    r = client.post("/v1/friends/join", headers=h(tok), json={"code": code, "scopes": ["scores", "training"]})
    assert r.status_code == 200, r.text


def put(client, tok, cid, **body):
    return client.put(f"/v1/friends/circles/{cid}/prefs", headers=h(tok), json=body)


@pytest.fixture(autouse=True)
def setup(dsn, world, client):
    """Ann's 「家」 with Ben and Ann's 「跑团」 with Cat (scores + training everywhere); Dan is in neither."""
    ids, tok = world
    friends_api._failures._events.clear()
    leaderboard.clear_cache()
    loads.clear_history_cache()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for t in ("circles", "shares", "circle_feed_posts", "circle_feed_seen", "training_session_logs", "notifications",
                  "circle_invites"):
            conn.execute(f"DELETE FROM {t}")
        flags.set_flag(conn, "circle_prefs", "all", actor="test")
    home = circle(client, tok["Ann"], "家")
    join(client, tok["Ben"], home["invite"]["code"])
    club = circle(client, tok["Ann"], "跑团")
    join(client, tok["Cat"], club["invite"]["code"])
    yield ids, tok, home["circle"]["id"], club["circle"]["id"]
    loads.clear_history_cache()


def switcher(client, tok):
    return [(c["name"], c["pinned"], c["muted"], c["archived"]) for c in client.get("/v1/friends/overview", headers=h(tok)).json()["circles"]]


def test_pin_mute_archive_rules(client, dsn, world, setup):
    ids, tok, home, club = setup
    assert put(client, tok["Ann"], home, pinned=True).json() == {"pinned": True, "muted": False, "archived": False}
    assert put(client, tok["Dan"], home, pinned=True).status_code == 404                    # not a member
    # archiving unpins; an archived circle can't be pinned
    assert put(client, tok["Ann"], home, archived=True).json() == {"pinned": False, "muted": False, "archived": True}
    r = put(client, tok["Ann"], home, pinned=True)
    assert r.status_code == 409 and "归档" in r.json()["detail"]
    assert "archived" in put(client, tok["Ann"], home, pinned=True, archived=False).text      # both at once is fine
    # at most MAX_PINNED
    extra = [circle(client, tok["Ann"], f"圈{i}")["circle"]["id"] for i in range(P.MAX_PINNED)]
    for cid in extra[:P.MAX_PINNED - 1]:
        assert put(client, tok["Ann"], cid, pinned=True).status_code == 200
    r = put(client, tok["Ann"], extra[-1], pinned=True)
    assert r.status_code == 409 and str(P.MAX_PINNED) in r.json()["detail"]
    en = client.put(f"/v1/friends/circles/{extra[-1]}/prefs", headers=h(tok["Ann"]) | {"X-Body-Lang": "en"}, json={"pinned": True})
    assert en.json()["detail"] == f"You can pin at most {P.MAX_PINNED} circles"
    # clearing everything leaves no row; leaving a circle drops the row
    put(client, tok["Ann"], home, pinned=False)
    put(client, tok["Ben"], home, muted=True)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM circle_member_prefs WHERE user_id = %s AND circle_id = %s",
                            (ids["Ann"], home)).fetchone()["n"] == 0
    client.delete(f"/v1/friends/circles/{home}/me", headers=h(tok["Ben"]))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM circle_member_prefs WHERE user_id = %s", (ids["Ben"],)).fetchone()["n"] == 0
    # flag off: 404, and no prefs in the answers
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, "circle_prefs", "off", actor="test")
    assert put(client, tok["Ann"], club, muted=True).status_code == 404
    assert client.delete("/v1/friends/invites", headers=h(tok["Ann"])).status_code == 404
    c = client.get("/v1/friends/overview", headers=h(tok["Ann"])).json()["circles"][0]
    assert "pinned" not in c and "prefs" not in client.get("/v1/friends", headers=h(tok["Ann"])).json()["circles"][0]


def test_switcher_order_pinned_then_most_recent(client, dsn, setup):
    ids, tok, home, club = setup
    assert [c[0] for c in switcher(client, tok["Ann"])] == ["跑团", "家"]   # Cat joined 跑团 last
    put(client, tok["Ann"], home, pinned=True)
    assert switcher(client, tok["Ann"])[0] == ("家", True, False, False)
    put(client, tok["Ann"], club, archived=True)
    assert switcher(client, tok["Ann"])[1] == ("跑团", False, False, True)   # still listed (the page hides it), flagged archived
    mine = client.get("/v1/friends", headers=h(tok["Ann"])).json()
    assert mine["prefs_on"] and {c["name"]: c["prefs"]["archived"] for c in mine["circles"]} == {"家": False, "跑团": True}
    assert all(c["recent_at"] for c in mine["circles"])


def test_archive_hides_from_all_but_keeps_sharing(client, dsn, setup):
    ids, tok, home, club = setup
    put(client, tok["Ann"], club, archived=True)
    names = [m["display_name"] for m in client.get("/v1/friends/overview", headers=h(tok["Ann"])).json()["members"]]
    assert names == ["Ben"]                                                     # Cat only shares the archived circle
    assert [m["display_name"] for m in client.get("/v1/friends/overview", headers=h(tok["Ann"]),
                                                  params={"circle": club}).json()["members"]] == ["Cat"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert auth.can_view(conn, ids["Cat"], ids["Ann"], "scores")           # Cat still sees Ann: nothing changed for her
    assert "Ann" in [m["display_name"] for m in client.get("/v1/friends/overview", headers=h(tok["Cat"])).json()["members"]]
    # and Ann's "what friends see" still counts Cat
    assert client.get("/v1/friends/overview", headers=h(tok["Ann"])).json()["me"]["seen"]["count"] == 2


def _train(dsn, uid):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        day = views.local_today(conn, uid)
        ex = [{"slug": "bench-press", "sets": [{"reps": 8, "weight_kg": 60, "done": True}]}]
        conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises) VALUES (%s, %s, 'page', %s::jsonb)
                        ON CONFLICT (user_id, date, source) DO UPDATE SET exercises = EXCLUDED.exercises""", (uid, day, json.dumps(ex)))


def test_mute_quiets_notifications_red_dot_and_all_feed(client, dsn, setup):
    ids, tok, home, club = setup
    for n in ("Ann", "Ben", "Cat"):
        _train(dsn, ids[n])

    def authors(cid=None):
        return sorted(p["user"]["name"] for p in client.get(FEED, headers=h(tok["Ann"]), params={"circle": cid} if cid else {}).json()["posts"])

    assert authors() == ["Ann", "Ben", "Cat"]
    client.get(f"{FEED}/unread", headers=h(tok["Ann"]))                        # starts Ann's red-dot clock
    put(client, tok["Ann"], club, muted=True)
    assert authors() == ["Ann", "Ben"] and authors(club) == ["Ann", "Cat"]     # muted: out of 「全部」, still in its own view
    post = next(p for p in client.get(FEED, headers=h(tok["Cat"])).json()["posts"] if p["user"]["name"] == "Ann")["id"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE circle_feed_seen SET seen_at = now() - interval '1 hour' WHERE user_id = %s", (ids["Ann"],))
    client.put(f"{FEED}/posts/{post}/like", headers=h(tok["Cat"]))             # through the muted circle only
    client.post(f"{FEED}/posts/{post}/comments", headers=h(tok["Cat"]), json={"body": "hi"})
    client.put(f"{FEED}/posts/{post}/like", headers=h(tok["Ben"]))             # through 家 (not muted)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        got = [r["data"]["actor_id"] for r in conn.execute("SELECT data FROM notifications WHERE user_id = %s", (ids["Ann"],))]
    assert got == [ids["Ben"]]
    assert client.get(f"{FEED}/unread", headers=h(tok["Ann"])).json()["count"] == 1
    # the others see no difference: Cat still sees Ann's post and her own comment
    assert [c["body"] for c in client.get(f"{FEED}/posts/{post}/comments", headers=h(tok["Cat"])).json()["comments"]] == ["hi"]


def test_invite_inbox_and_ignore_all(client, dsn, world, setup):
    ids, tok, home, club = setup
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for cid in (home, club):
            conn.execute("UPDATE circles SET personal = (id = %s) WHERE id = %s", (cid, cid))
        store.referral_invites(conn, ids["Ann"], ids["Dan"])                    # to Ann's personal circle (家)
        conn.execute("""INSERT INTO circle_invites (circle_id, created_by, invitee_user_id, expires_at)
                        VALUES (%s, %s, %s, now() + interval '30 days')""", (club, ids["Ann"], ids["Dan"]))
    inbox = client.get("/v1/friends", headers=h(tok["Dan"])).json()["invites"]
    assert sorted((i["name"], i["members"]) for i in inbox) == [("家", 2), ("跑团", 2)]
    assert all(i["default_scopes"] == list(P.DEFAULT_SCOPES) for i in inbox)
    assert client.delete("/v1/friends/invites", headers=h(tok["Dan"])).json() == {"declined": 2}
    assert client.get("/v1/friends", headers=h(tok["Dan"])).json()["invites"] == []
