"""Circle training feed (flag circle_feed, friends/feed.py, migration 635): auto posts only for people who ticked the training
scope, one post per day that follows the day's sets, nothing outside the circle or after leaving, ❤️ and comments with the right
permissions, no way to delete a post, comments deletable by their writer and reportable, red dot, notification hook.
Test database body_test_circle_feed (BODY_TEST_DB_NAME_CIRCLE_FEED), recreated on every run; skipped without Postgres."""
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
from body_engine.friends import api as friends_api, feed, params as P
from body_engine.rollout import flags
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CIRCLE_FEED", "body_test_circle_feed")
NAMES = ("Ann", "Ben", "Cat", "Dan")
FEED = "/v1/friends/feed"


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
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, birth_date, height_cm, consent_at, consent_version, personal_circle_at)
                                  VALUES (%s, '1990-01-01', 175, now(), %s, now()) RETURNING id""",
                               (n, P.CONSENT_VERSION)).fetchone()["id"] for n in NAMES}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tok["Ben_ro"] = auth.create_token(conn, ids["Ben"], "ro", ["read"])
        flags.set_flag(conn, "friends", "all", actor="test")
        flags.set_flag(conn, P.FEED_FLAG, "all", actor="test")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


@pytest.fixture(autouse=True)
def clean(dsn):
    friends_api._failures._events.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for t in ("circles", "shares", "circle_feed_posts", "circle_feed_seen", "training_session_logs", "workouts"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM exercise_catalog WHERE slug = 'bench-press' AND source_ref IS NULL AND name_en = 'Bench press'")   # a test's own row
        flags.set_flag(conn, P.FEED_FLAG, "all", actor="test")
    yield


def h(t):
    return {"Authorization": f"Bearer {t}"}


def circle(client, tok, name="家", scopes=P.DEFAULT_SCOPES):
    r = client.post("/v1/friends/circles", headers=h(tok), json={"name": name, "scopes": list(scopes)})
    assert r.status_code == 201, r.text
    return r.json()


def join(client, tok, code, scopes=P.DEFAULT_SCOPES):
    r = client.post("/v1/friends/join", headers=h(tok), json={"code": code, "scopes": list(scopes)})
    assert r.status_code == 200, r.text
    return r.json()


def today(dsn, uid):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return views.local_today(conn, uid)


def log_sets(dsn, uid, exercises, day=None):
    """Write a training day: exercises = [(slug, [(reps, kg, done)])]"""
    day = day or today(dsn, uid)
    ex = [{"slug": s, "sets": [{"reps": r, "weight_kg": w, "done": d} for r, w, d in sets]} for s, sets in exercises]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises) VALUES (%s, %s, 'page', %s::jsonb)
                        ON CONFLICT (user_id, date, source) DO UPDATE SET exercises = EXCLUDED.exercises""", (uid, day, json.dumps(ex)))


def get_feed(client, tok, **params):
    r = client.get(FEED, headers=h(tok), params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def pair(client, world):
    """Ann owns a circle with Ben; both tick the default scopes (which include training)."""
    ids, tok = world
    code = circle(client, tok["Ann"])["invite"]["code"]
    join(client, tok["Ben"], code)
    return ids, tok, code


def test_flag_off_is_404(client, world, dsn):
    ids, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, P.FEED_FLAG, "off", actor="test")
    assert client.get(FEED, headers=h(tok["Ann"])).status_code == 404
    assert client.get(f"{FEED}/unread", headers=h(tok["Ann"])).status_code == 404


def test_auto_post_only_with_training_scope(client, world, dsn):
    ids, tok = world
    code = circle(client, tok["Ann"])["invite"]["code"]
    join(client, tok["Ben"], code, ["scores", "sleep"])            # Ben did NOT tick training
    join(client, tok["Cat"], code, ["training"])                   # Cat did
    log_sets(dsn, ids["Ben"], [("bench-press", [(8, 60, True)])])
    log_sets(dsn, ids["Cat"], [("squat", [(5, 100, True)])])
    posts = get_feed(client, tok["Ann"])["posts"]
    assert [p["user"]["name"] for p in posts] == ["Cat"]            # no post for Ben, he is not in the feed at all
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM circle_feed_posts WHERE user_id = %s", (ids["Ben"],)).fetchone()["n"] == 0
    # Ben sees nothing of Cat either? He is in the circle but Ann/Cat's training is visible to him: Cat ticked it, so yes
    assert [p["user"]["name"] for p in get_feed(client, tok["Ben"])["posts"]] == ["Cat"]
    # ticking training later starts the feed (no prompt)
    assert client.patch(f"/v1/friends/circles/{_cid(dsn, 'Ann')}/me", headers=h(tok["Ben"]), json={"scopes": ["scores", "training"]}).status_code == 200
    assert {p["user"]["name"] for p in get_feed(client, tok["Ann"])["posts"]} == {"Ben", "Cat"}


def _cid(dsn, name):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute("SELECT circle_id FROM circle_members m JOIN users u ON u.id = m.user_id WHERE u.display_name = %s LIMIT 1", (name,)).fetchone()["circle_id"]


def test_one_post_per_day_and_content_follows_sets(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(10, 50, True), (8, 60, True), (8, 70, False)])])
    p = get_feed(client, tok["Ben"])["posts"]
    assert len(p) == 1
    post = p[0]
    assert post["set_count"] == 2 and post["exercise_count"] == 1
    assert post["volume_kg"] == 10 * 50 + 8 * 60                    # only the sets ticked done: reps × kg
    assert [s["weight_kg"] for s in post["exercises"][0]["sets"]] == [50, 60]
    pid = post["id"]
    # more sets and a new exercise the same day: the same post, updated
    log_sets(dsn, ids["Ann"], [("bench-press", [(10, 50, True), (8, 60, True), (8, 70, True)]), ("squat", [(5, 100, True)])])
    p = get_feed(client, tok["Ben"])["posts"]
    assert len(p) == 1 and p[0]["id"] == pid
    assert p[0]["exercise_count"] == 2 and p[0]["set_count"] == 4 and p[0]["volume_kg"] == 500 + 480 + 560 + 500
    # the author sees their own post too
    assert [x["user"]["self"] for x in get_feed(client, tok["Ann"])["posts"]] == [True]
    # a day without ticked sets has no post
    log_sets(dsn, ids["Ben"], [("squat", [(5, 100, False)])])
    assert [x["user"]["name"] for x in get_feed(client, tok["Ann"])["posts"]] == ["Ann"]


def test_personal_record_and_watch_workout(client, world, dsn, pair, monkeypatch):
    ids, tok, _ = pair
    monkeypatch.setenv("BODY_EXERCISE_IMAGE_BASE_URL", "https://img.example/ex")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("""INSERT INTO exercise_catalog (slug, name_zh, name_en, primary_muscles, images) VALUES ('bench-press', '卧推', 'Bench press', '{chest}', '{bench/0.jpg}')
                        ON CONFLICT (slug) DO UPDATE SET primary_muscles = EXCLUDED.primary_muscles, images = EXCLUDED.images, updated_at = now()""")
    from datetime import timedelta
    d = today(dsn, ids["Ann"])
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])], day=d - timedelta(days=1))
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 65, True)]), ("squat", [(5, 100, True)])])   # squat: first time = no record
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
                        VALUES (%s, 'ow', 'w1', 'strength_training', '力量训练', %s, now() - interval '1 hour', now(), 62)""", (ids["Ann"], d))
    posts = get_feed(client, tok["Ben"])["posts"]
    assert [p["date"] for p in posts] == [d.isoformat(), (d - timedelta(days=1)).isoformat()]   # newest first
    newest = posts[0]
    assert newest["duration_min"] == 62 and newest["pr_count"] == 1
    assert {e["slug"]: e["pr"] for e in newest["exercises"]} == {"bench-press": True, "squat": False}
    assert newest["started_at"] is not None and posts[1]["started_at"] is None          # the header's "1 h ago" needs a watch time
    for e in newest["exercises"]:                                                       # what the post's exercise row draws
        assert set(e) >= {"thumb", "muscles"} and isinstance(e["muscles"], list)
    bench = next(e for e in newest["exercises"] if e["slug"] == "bench-press")
    assert bench["muscles"] == ["chest"] and bench["thumb"] == "https://img.example/ex/bench/0.jpg"


def log_imported(dsn, uid, exercises, day):
    """An imported (Hevy, #161) day: exercises = [(slug or None, name, [(reps, kg, type)])]; imported sets carry no done tick."""
    ex = [{"workout": "2026-01-01T00:00:00Z", "slug": s, "name": n, **({"unmatched": True} if s is None else {}),
           "sets": [{"set": i + 1, "type": t, "reps": r, "weight_kg": w} for i, (r, w, t) in enumerate(sets)]} for s, n, sets in exercises]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises, status) VALUES (%s, %s, 'hevy', %s::jsonb, 'imported')
                        ON CONFLICT (user_id, date, source) DO UPDATE SET exercises = EXCLUDED.exercises""", (uid, day, json.dumps(ex)))


def test_page_and_imported_rows_make_one_post(client, world, dsn, pair):
    """A day with the page's own row and a Hevy-imported row (#161): one post, each set counted once; imported sets count
    without a done tick but not their warm-ups; records compare against imported history too; unmatched names stay apart."""
    ids, tok, _ = pair
    from datetime import timedelta
    d = today(dsn, ids["Ann"])
    log_imported(dsn, ids["Ann"], [("bench-press", "Bench Press (Barbell)", [(8, 60, "normal")])], d - timedelta(days=1))
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 65, True), (8, 70, False)])], day=d)
    log_imported(dsn, ids["Ann"], [("squat", "Squat (Barbell)", [(10, 40, "warmup"), (5, 100, "normal"), (5, 100, "failure")]),
                                   (None, "Gizmo Press", [(12, 30, "normal")])], d)
    posts = get_feed(client, tok["Ben"])["posts"]
    assert [p["date"] for p in posts] == [d.isoformat(), (d - timedelta(days=1)).isoformat()]   # one post per day
    today_post = posts[0]
    assert today_post["exercise_count"] == 3 and today_post["set_count"] == 1 + 2 + 1   # page done + imported minus warm-up
    assert today_post["volume_kg"] == 8 * 65 + 5 * 100 * 2 + 12 * 30
    assert {e["name"]: e["pr"] for e in today_post["exercises"]} == {"bench-press": True, "Squat (Barbell)": False, "Gizmo Press": False}
    assert posts[1]["set_count"] == 1 and posts[1]["volume_kg"] == 480                   # the imported-only day posts too
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM circle_feed_posts WHERE user_id = %s AND date = %s",
                            (ids["Ann"], d)).fetchone()["n"] == 1


def test_outside_the_circle_nothing(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    pid = get_feed(client, tok["Ben"])["posts"][0]["id"]
    assert get_feed(client, tok["Cat"]) == {"posts": [], "next": None, "has_circles": False}   # Cat is in no circle
    for call in (lambda: client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Cat"])),
                 lambda: client.get(f"{FEED}/posts/{pid}/comments", headers=h(tok["Cat"])),
                 lambda: client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Cat"]), json={"body": "hi"})):
        assert call().status_code == 404
    # Cat in another circle with Dan only: still nothing of Ann
    circle(client, tok["Dan"], "other")
    assert get_feed(client, tok["Cat"])["posts"] == []


def test_leaving_the_circle_hides_posts_both_ways(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    log_sets(dsn, ids["Ben"], [("squat", [(5, 100, True)])])
    assert {p["user"]["name"] for p in get_feed(client, tok["Ben"])["posts"]} == {"Ann", "Ben"}
    pid = next(p["id"] for p in get_feed(client, tok["Ben"])["posts"] if p["user"]["name"] == "Ann")
    assert client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ben"]), json={"body": "nice"}).status_code == 201
    assert client.delete(f"/v1/friends/circles/{_cid(dsn, 'Ben')}/me", headers=h(tok["Ben"])).status_code == 204
    assert get_feed(client, tok["Ben"])["posts"] == []                          # Ben sees nothing, not even a stale post
    assert client.get(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ben"])).status_code == 404
    assert [p["user"]["name"] for p in get_feed(client, tok["Ann"])["posts"]] == ["Ann"]   # Ben's post is gone for Ann at once
    # Ben's earlier comment on Ann's post is not shown any more either
    assert client.get(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ann"])).json()["comments"] == []
    assert get_feed(client, tok["Ann"])["posts"][0]["comment_count"] == 0


def test_removed_member_and_revoked_training_scope(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ben"], [("squat", [(5, 100, True)])])
    assert len(get_feed(client, tok["Ann"])["posts"]) == 1
    # Ben un-ticks training: his post disappears for Ann (no longer shared with the circle)
    assert client.patch(f"/v1/friends/circles/{_cid(dsn, 'Ann')}/me", headers=h(tok["Ben"]), json={"scopes": ["scores"]}).status_code == 200
    assert get_feed(client, tok["Ann"])["posts"] == []


def test_like_permissions_and_toggle(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    pid = get_feed(client, tok["Ben"])["posts"][0]["id"]
    assert client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben_ro"])).status_code == 403          # read-only token
    r = client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben"]))
    assert r.status_code == 200 and r.json() == {"count": 1, "mine": True}
    assert client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben"])).json()["count"] == 1          # idempotent
    assert get_feed(client, tok["Ann"])["posts"][0]["likes"] == {"count": 1, "mine": False}
    assert client.delete(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben"])).json() == {"count": 0, "mine": False}
    # ❤️ is apart from the cheers 👏
    assert client.post("/v1/friends/nudges", headers=h(tok["Ben"]), json={"to": ids["Ann"], "kind": "clap"}).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM circle_nudges").fetchone()["n"] == 1


def test_post_cannot_be_deleted(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    pid = get_feed(client, tok["Ann"])["posts"][0]["id"]
    for who in ("Ann", "Ben"):
        for method in ("delete", "put", "patch"):
            r = getattr(client, method)(f"{FEED}/posts/{pid}", headers=h(tok[who]))
            assert r.status_code in (404, 405)
    assert not any(getattr(r, "methods", None) and "DELETE" in r.methods and r.path.endswith("/posts/{post_id}") for r in api.app.routes)
    assert len(get_feed(client, tok["Ann"])["posts"]) == 1


def test_comments_text_only_limit_delete_report(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    pid = get_feed(client, tok["Ben"])["posts"][0]["id"]
    url = f"{FEED}/posts/{pid}/comments"
    assert client.post(url, headers=h(tok["Ben"]), json={"body": "   "}).status_code == 400
    assert client.post(url, headers=h(tok["Ben"]), json={"body": "x" * (P.COMMENT_MAX + 1)}).status_code == 400
    assert client.post(url, headers=h(tok["Ben"]), json={"body": "hi", "image": "x"}).status_code == 422   # text only
    assert client.post(url, headers=h(tok["Ben_ro"]), json={"body": "hi"}).status_code == 403
    c = client.post(url, headers=h(tok["Ben"]), json={"body": "加油\n💪"})
    assert c.status_code == 201 and c.json()["body"] == "加油 💪"
    cid = c.json()["id"]
    assert [x["body"] for x in client.get(url, headers=h(tok["Ann"])).json()["comments"]] == ["加油 💪"]
    assert get_feed(client, tok["Ann"])["posts"][0]["comment_count"] == 1
    # the post's author can't delete someone else's comment, only report it; the writer can't report their own
    assert client.delete(f"{FEED}/comments/{cid}", headers=h(tok["Ann"])).status_code == 404
    assert client.post(f"{FEED}/comments/{cid}/report", headers=h(tok["Ben"])).status_code == 400
    assert client.post(f"{FEED}/comments/{cid}/report", headers=h(tok["Cat"])).status_code == 404       # outside the circle
    assert client.post(f"{FEED}/comments/{cid}/report", headers=h(tok["Ann"]), json={"reason": "spam"}).json() == {"reported": True}
    assert client.post(f"{FEED}/comments/{cid}/report", headers=h(tok["Ann"])).status_code == 200        # twice = once
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM circle_feed_reports").fetchone()["n"] == 1
    assert client.delete(f"{FEED}/comments/{cid}", headers=h(tok["Ben"])).status_code == 204
    assert client.get(url, headers=h(tok["Ann"])).json()["comments"] == []
    assert client.delete(f"{FEED}/comments/{cid}", headers=h(tok["Ben"])).status_code == 404


def test_newest_comments_preview_under_the_post(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    feed0 = get_feed(client, tok["Ben"])
    assert feed0["comment_preview"] == P.COMMENT_PREVIEW and feed0["posts"][0]["preview_comments"] == []
    pid = feed0["posts"][0]["id"]
    url = f"{FEED}/posts/{pid}/comments"
    made = [client.post(url, headers=h(tok[w]), json={"body": f"c{i}"}).json()["id"] for i, w in enumerate(("Ben", "Ann", "Ben"))]
    post = get_feed(client, tok["Ann"])["posts"][0]
    assert post["comment_count"] == 3                                                    # the total, the preview is only the newest
    assert [(c["id"], c["user"]["name"], c["body"]) for c in post["preview_comments"]] == [(made[1], "Ann", "c1"), (made[2], "Ben", "c2")]
    assert set(post["preview_comments"][0]) == {"id", "user", "body"} and set(post["preview_comments"][0]["user"]) == {"id", "name"}
    client.delete(f"{FEED}/comments/{made[2]}", headers=h(tok["Ben"]))
    assert [c["body"] for c in get_feed(client, tok["Ann"])["posts"][0]["preview_comments"]] == ["c0", "c1"]   # a deleted one drops out
    # nothing from outside the circle reaches the preview: Cat is not in the circle, and a member who left is gone
    assert get_feed(client, tok["Cat"])["posts"] == []


def test_comment_rate_limit(client, world, dsn, pair, monkeypatch):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    pid = get_feed(client, tok["Ben"])["posts"][0]["id"]
    monkeypatch.setattr(P, "COMMENTS_PER_HOUR", 2)
    codes = [client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ben"]), json={"body": f"c{i}"}).status_code for i in range(3)]
    assert codes == [201, 201, 429]


def test_pagination_newest_first(client, world, dsn, pair):
    ids, tok, _ = pair
    from datetime import timedelta
    d = today(dsn, ids["Ann"])
    for i in range(5):
        log_sets(dsn, ids["Ann"], [("squat", [(5, 100 + i, True)])], day=d - timedelta(days=i))
    p1 = get_feed(client, tok["Ben"], limit=2)
    assert [p["date"] for p in p1["posts"]] == [d.isoformat(), (d - timedelta(days=1)).isoformat()] and p1["next"]
    p2 = get_feed(client, tok["Ben"], limit=2, before=p1["next"])
    assert [p["date"] for p in p2["posts"]] == [(d - timedelta(days=2)).isoformat(), (d - timedelta(days=3)).isoformat()]
    p3 = get_feed(client, tok["Ben"], limit=2, before=p2["next"])
    assert len(p3["posts"]) == 1 and p3["next"] is None
    assert client.get(FEED, headers=h(tok["Ben"]), params={"before": "junk"}).status_code == 400


def test_red_dot_and_seen(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    assert client.get(f"{FEED}/unread", headers=h(tok["Ann"])).json() == {"count": 0}          # first call starts the clock
    pid = get_feed(client, tok["Ben"])["posts"][0]["id"]
    client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben"]))
    client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ben"]), json={"body": "hi"})
    client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ann"]))                               # my own ❤️ doesn't count
    assert client.get(f"{FEED}/unread", headers=h(tok["Ann"])).json() == {"count": 2}
    assert client.get(f"{FEED}/unread", headers=h(tok["Ben"])).json() == {"count": 0}
    assert client.post(f"{FEED}/seen", headers=h(tok["Ann"])).status_code == 204
    assert client.get(f"{FEED}/unread", headers=h(tok["Ann"])).json() == {"count": 0}


def inbox(dsn, uid):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute("SELECT kind, title, title_en, body, url, data FROM notifications WHERE user_id = %s ORDER BY id",
                            (uid,)).fetchall()


def test_like_and_comment_notify_the_author(client, world, dsn, pair):
    ids, tok, _ = pair
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM notifications")
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    pid = get_feed(client, tok["Ben"])["posts"][0]["id"]
    # my own ❤️ and comment on my own post: no notification
    client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ann"]))
    client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ann"]), json={"body": "me"})
    assert inbox(dsn, ids["Ann"]) == []
    # Ben's ❤️: one in-app notification for Ann, linking to the post
    assert client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben"])).status_code == 200
    got = inbox(dsn, ids["Ann"])
    assert len(got) == 1 and got[0]["kind"] == feed.LIKE == "circle.like"
    assert "Ben" in got[0]["title"] and "Ben" in got[0]["title_en"]
    assert got[0]["url"] == f"/app/?tab=friends&feed={pid}" and got[0]["data"] == {"actor_id": ids["Ben"], "post_id": pid}
    # unlike + like again: not told twice
    client.delete(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben"]))
    client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben"]))
    assert len(inbox(dsn, ids["Ann"])) == 1
    # Ben's comment: one more, with the text
    client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ben"]), json={"body": "strong!"})
    got = inbox(dsn, ids["Ann"])
    assert [g["kind"] for g in got] == ["circle.like", "circle.comment"] and got[1]["body"] == "strong!"
    assert got[1]["url"] == f"/app/?tab=friends&feed={pid}"
    assert inbox(dsn, ids["Ben"]) == []                                  # the one who liked / commented gets nothing


def test_privacy_nothing_but_training_in_a_post(client, world, dsn, pair):
    ids, tok, _ = pair
    log_sets(dsn, ids["Ann"], [("bench-press", [(8, 60, True)])])
    text = json.dumps(get_feed(client, tok["Ben"]), ensure_ascii=False, default=str)
    for word in ("ecg", "lab", "weight_kg_body", "body_weight"):
        assert word not in text
    assert set(get_feed(client, tok["Ben"])["posts"][0]) == {"id", "date", "user", "circles", "title", "duration_min", "exercise_count",
                                                            "set_count", "volume_kg", "pr_count", "exercises", "likes", "comment_count",
                                                            "started_at", "preview_comments"}
