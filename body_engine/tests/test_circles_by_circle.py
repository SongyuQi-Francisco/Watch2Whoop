"""Friends tab one circle at a time (#269 P1, flag circles_by_circle, docs/circles-redesign.md §2): member cards, the
training feed (with its ❤️ and comments) and leaderboards narrowed to one circle, each person shown only with what they
picked in that circle; the switcher list; get_friends_overview's `circle`; flag off = the old merged views.
Test database body_test_circles_by (BODY_TEST_DB_NAME_CIRCLES_BY), recreated on every run; skipped without Postgres."""
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
from body_engine.friends import api as friends_api, leaderboard, params as P
from body_engine.rollout import flags
from body_engine.training import loads
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CIRCLES_BY", "body_test_circles_by")
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
        flags.set_flag(conn, "friends", "all", actor="test")
        flags.set_flag(conn, P.FEED_FLAG, "all", actor="test")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(t):
    return {"Authorization": f"Bearer {t}"}


def circle(client, tok, name, scopes):
    r = client.post("/v1/friends/circles", headers=h(tok), json={"name": name, "scopes": scopes})
    assert r.status_code == 201, r.text
    return r.json()


def join(client, tok, code, scopes):
    r = client.post("/v1/friends/join", headers=h(tok), json={"code": code, "scopes": scopes})
    assert r.status_code == 200, r.text


def log_sets(dsn, uid):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        day = views.local_today(conn, uid)
        ex = [{"slug": "bench-press", "sets": [{"reps": 8, "weight_kg": 60, "done": True}]}]
        conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises) VALUES (%s, %s, 'page', %s::jsonb)
                        ON CONFLICT (user_id, date, source) DO UPDATE SET exercises = EXCLUDED.exercises""", (uid, day, json.dumps(ex)))


@pytest.fixture(autouse=True)
def setup(dsn, world, client):
    """Ann's 「家」 with Ben (Ben shares only scores there) and Ann's 「跑团」 with Ben and Cat (everyone shares scores +
    training). Dan is in no circle with them. The flag is on unless a test turns it off."""
    ids, tok = world
    friends_api._failures._events.clear()
    leaderboard.clear_cache()
    loads.clear_history_cache()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for t in ("circles", "shares", "circle_feed_posts", "circle_feed_seen", "training_session_logs", "daily_scores"):
            conn.execute(f"DELETE FROM {t}")
        flags.set_flag(conn, P.BY_CIRCLE_FLAG, "all", actor="test")
    home = circle(client, tok["Ann"], "家", ["scores", "training"])
    join(client, tok["Ben"], home["invite"]["code"], ["scores"])
    club = circle(client, tok["Ann"], "跑团", ["scores", "training"])
    join(client, tok["Ben"], club["invite"]["code"], ["scores", "training"])
    join(client, tok["Cat"], club["invite"]["code"], ["scores", "training"])
    yield ids, tok, home["circle"]["id"], club["circle"]["id"]
    loads.clear_history_cache()


def cards(client, tok, cid=None):
    r = client.get("/v1/friends/overview", headers=h(tok), params={"circle": cid} if cid else {})
    assert r.status_code == 200, r.text
    return r.json()


def test_member_cards_per_circle_with_what_was_picked_there(client, setup):
    ids, tok, home, club = setup
    every = cards(client, tok["Ann"])
    assert every["circle"] is None and [c["name"] for c in every["circles"]] == ["家", "跑团"]
    assert [c["members"] for c in every["circles"]] == [2, 3]
    assert sorted(m["display_name"] for m in every["members"]) == ["Ben", "Cat"]
    ben_all = next(m for m in every["members"] if m["display_name"] == "Ben")
    assert "training" in ben_all["shared"]                       # all circles: the union (Ben ticked training in 跑团)

    at_home = cards(client, tok["Ann"], home)
    assert at_home["circle"] == home and [m["display_name"] for m in at_home["members"]] == ["Ben"]
    assert at_home["members"][0]["shared"] == ["scores"] and at_home["members"][0]["trained"] is None
    assert "training" in at_home["members"][0]["hidden"]
    in_club = cards(client, tok["Ann"], club)
    assert sorted(m["display_name"] for m in in_club["members"]) == ["Ben", "Cat"]
    assert "training" in next(m for m in in_club["members"] if m["display_name"] == "Ben")["shared"]
    # what Ben's friends see of him at home: only scores
    assert cards(client, tok["Ben"], home)["me"]["seen"]["scopes"] == ["scores"]


def test_someone_elses_circle_is_404_and_flag_off_is_the_old_view(client, dsn, setup):
    ids, tok, home, club = setup
    assert client.get("/v1/friends/overview", headers=h(tok["Cat"]), params={"circle": home}).status_code == 404
    assert client.get("/v1/friends/leaderboard", headers=h(tok["Dan"]), params={"circle": club}).status_code == 404
    assert client.get(FEED, headers=h(tok["Cat"]), params={"circle": home}).status_code == 404
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, P.BY_CIRCLE_FLAG, "off", actor="test")
    old = cards(client, tok["Ann"], home)                        # the param is ignored, nothing new in the answer
    assert "circles" not in old and "circle" not in old
    assert sorted(m["display_name"] for m in old["members"]) == ["Ben", "Cat"]
    assert client.get("/v1/friends/overview", headers=h(tok["Cat"]), params={"circle": home}).status_code == 200


def test_leaderboard_per_circle(client, setup):
    ids, tok, home, club = setup
    def rows(cid):
        r = client.get("/v1/friends/leaderboard", headers=h(tok["Ann"]), params={"board": "activity", "circle": cid})
        assert r.status_code == 200, r.text
        assert r.json()["circle"] == cid
        return {x["display_name"]: x["status"] for x in r.json()["rows"]}
    at_home = rows(home)
    assert set(at_home) == {"Ann", "Ben"} and at_home["Ben"] == "not_shared"      # activity needs training: not picked at home
    in_club = rows(club)
    assert set(in_club) == {"Ann", "Ben", "Cat"} and in_club["Ben"] != "not_shared"


def test_feed_per_circle_and_its_comments(client, dsn, setup):
    ids, tok, home, club = setup
    for n in ("Ann", "Ben"):
        log_sets(dsn, ids[n])
    authors = lambda who, cid=None: sorted(p["user"]["name"] for p in client.get(FEED, headers=h(tok[who]),   # noqa: E731
                                                                                  params={"circle": cid} if cid else {}).json()["posts"])
    assert authors("Ann") == ["Ann", "Ben"]
    assert authors("Ann", home) == ["Ann"]                       # Ben did not tick training at home
    assert authors("Ann", club) == ["Ann", "Ben"]
    ann_post = next(p for p in client.get(FEED, headers=h(tok["Ben"])).json()["posts"] if p["user"]["name"] == "Ann")["id"]
    url = f"{FEED}/posts/{ann_post}/comments"
    client.post(url, headers=h(tok["Cat"]), json={"body": "from Cat"})
    client.put(f"{FEED}/posts/{ann_post}/like", headers=h(tok["Cat"]))

    def comments(cid=None):
        return [c["body"] for c in client.get(url, headers=h(tok["Ben"]), params={"circle": cid} if cid else {}).json()["comments"]]

    assert comments() == ["from Cat"] and comments(club) == ["from Cat"]
    assert comments(home) == []                                  # at home, only home's people: Cat is not there
    post = lambda cid: next(p for p in client.get(FEED, headers=h(tok["Ben"]), params={"circle": cid}).json()["posts"]   # noqa: E731
                            if p["id"] == ann_post)
    assert (post(home)["comment_count"], post(home)["likes"]["count"], post(home)["preview_comments"]) == (0, 0, [])
    assert (post(club)["comment_count"], post(club)["likes"]["count"]) == (1, 1)
    liked = client.put(f"{FEED}/posts/{ann_post}/like", headers=h(tok["Ben"]), params={"circle": home}).json()
    assert liked == {"count": 1, "mine": True}                   # Ben's own ❤️ only; Cat's is not counted at home


def test_ai_overview_takes_a_circle(dsn, setup):
    ids, tok, home, club = setup
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        every = leaderboard.ai_overview(conn, ids["Ann"])
        assert [c["name"] for c in every["circles"]] == ["家", "跑团"] and every["circle"] is None
        assert sorted(m["name"] for m in every["members"]) == ["Ann", "Ben", "Cat"]
        assert leaderboard.ai_overview(conn, ids["Ann"], "家")["circle"] == home
        assert sorted(m["name"] for m in leaderboard.ai_overview(conn, ids["Ann"], "家")["members"]) == ["Ann", "Ben"]
        assert leaderboard.ai_overview(conn, ids["Ann"], str(club))["circle"] == club
        with pytest.raises(ValueError, match="跑团"):
            leaderboard.ai_overview(conn, ids["Ann"], "篮球")
        flags.set_flag(conn, P.BY_CIRCLE_FLAG, "off", actor="test")
        off = leaderboard.ai_overview(conn, ids["Ann"], "家")
        assert "circles" not in off and sorted(m["name"] for m in off["members"]) == ["Ann", "Ben", "Cat"]
