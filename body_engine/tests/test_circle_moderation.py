"""Circle moderation (App Store guideline 1.2, friends/moderation.py, blocks.py, rules.py, migration 1100): blocking hides
both ways on the server (feed, comments, ❤️, cards, leaderboard, cheers) and pauses circle sharing; reports hide for the
reporter at once and for everyone after params.REPORT_HIDE_AFTER distinct reporters; the admin queue (HTTP + CLI) with
keep / remove / ban-user and its audit log; the overdue count; the keyword filter; the community rules gate.
Synthetic people and data only. Test database body_test_circle_moderation (BODY_TEST_DB_NAME_CIRCLE_MODERATION), recreated
on every run; the pure parts run without Postgres."""
import argparse
import json
import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, wordfilter
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends import api as friends_api, moderation, moderation_cli, params as P, rules
from body_engine.rollout import flags
from body_engine.training import loads
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CIRCLE_MODERATION", "body_test_circle_moderation")
NAMES = ("Ann", "Ben", "Cat", "Dan", "Eve", "Fay")
FEED = "/v1/friends/feed"
DOCS = Path(__file__).resolve().parents[2] / "docs" / "legal"


# ---------------------------------------------------------------- without a database
def test_rules_text_matches_docs_and_version():
    for lang, name in (("zh", "community-rules.md"), ("en", "community-rules.en.md")):
        assert rules.text(lang) == (DOCS / name).read_text(encoding="utf-8"), \
            f"friends/{rules.FILES[lang]} and docs/legal/{name} must be identical character for character"
    assert f"版本：{P.RULES_VERSION}" in rules.text("zh")
    assert f"Version: {P.RULES_VERSION}" in rules.text("en")


def test_wordfilter_matching(tmp_path, monkeypatch):
    ws = wordfilter.parse(["# a comment line", "", "badword   # trailing comment", "示例词", "BadWord"])
    assert ws == ("badword", "示例词")
    assert wordfilter.find("this has a BADWORD in it", ws) == "badword"
    assert wordfilter.find("b a d w o r d", ws) == "badword"          # spaces / punctuation do not get around it
    assert wordfilter.find("b.a.d-w_o.r.d", ws) == "badword"
    assert wordfilter.find("ｂａｄｗｏｒｄ", ws) == "badword"           # full-width letters (NFKC)
    assert wordfilter.find("这里有示例词", ws) == "示例词"
    assert wordfilter.find("all good here", ws) is None
    # the file: missing = empty list; read again when it changes
    monkeypatch.setenv(wordfilter.ENV, str(tmp_path / "none.txt"))
    assert wordfilter.words() == ()
    wordfilter.check("anything")                                      # nothing listed, nothing refused
    f = tmp_path / "words.txt"
    f.write_text("示例词\n", encoding="utf-8")
    monkeypatch.setenv(wordfilter.ENV, str(f))
    assert wordfilter.words() == ("示例词",)
    with pytest.raises(wordfilter.Blocked):
        wordfilter.check("一个示例词")
    f.write_text("other\n", encoding="utf-8")
    os.utime(f, (1, 1))   # make sure the mtime differs
    assert wordfilter.words() == ("other",)


# ---------------------------------------------------------------- with a database
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
        tok["Ann_ro"] = auth.create_token(conn, ids["Ann"], "ro", ["read"])
        for f in ("friends", P.FEED_FLAG, "avatars"):
            flags.set_flag(conn, f, "all", actor="test")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


@pytest.fixture(autouse=True)
def clean(request, monkeypatch, tmp_path):
    monkeypatch.setenv(wordfilter.ENV, str(tmp_path / "no-words.txt"))
    if "dsn" not in request.fixturenames:
        yield
        return
    dsn = request.getfixturevalue("dsn")
    friends_api._failures._events.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for t in ("circles", "shares", "circle_feed_posts", "circle_feed_seen", "circle_nudges", "training_session_logs",
                  "circle_blocks", "circle_moderation", "circle_moderation_log", "circle_bans", "circle_rules_acceptances",
                  "notifications"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("UPDATE users SET display_name = CASE id " + " ".join(
            f"WHEN {i} THEN '{n}'" for n, i in request.getfixturevalue("world")[0].items()) + " ELSE display_name END")
        flags.set_flag(conn, P.RULES_FLAG, "off", actor="test")
    loads.clear_history_cache()
    yield


def h(t):
    return {"Authorization": f"Bearer {t}"}


def circle(client, tok, name="Gym"):
    r = client.post("/v1/friends/circles", headers=h(tok), json={"name": name, "scopes": list(P.DEFAULT_SCOPES)})
    assert r.status_code == 201, r.text
    return r.json()


def join(client, tok, code):
    r = client.post("/v1/friends/join", headers=h(tok), json={"code": code, "scopes": list(P.DEFAULT_SCOPES)})
    assert r.status_code == 200, r.text
    return r.json()


def log_sets(dsn, uid):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        day = views.local_today(conn, uid)
        ex = [{"slug": "bench-press", "sets": [{"reps": 8, "weight_kg": 50, "done": True}]}]
        conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises) VALUES (%s, %s, 'page', %s::jsonb)
                        ON CONFLICT (user_id, date, source) DO UPDATE SET exercises = EXCLUDED.exercises""", (uid, day, json.dumps(ex)))


def feed(client, tok):
    r = client.get(FEED, headers=h(tok))
    assert r.status_code == 200, r.text
    return r.json()["posts"]


def authors(client, tok):
    return {p["user"]["id"] for p in feed(client, tok)}


@pytest.fixture
def group(client, world, dsn):
    """Ann owns a circle; everyone else joins (default scopes include training). Ann has a post with a comment by Ben."""
    ids, tok = world
    code = circle(client, tok["Ann"])["invite"]["code"]
    for n in NAMES[1:]:
        join(client, tok[n], code)
    log_sets(dsn, ids["Ann"])
    pid = feed(client, tok["Ben"])[0]["id"]
    r = client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ben"]), json={"body": "nice"})
    assert r.status_code == 201, r.text
    return ids, tok, pid, r.json()["id"]


def comments(client, tok, pid):
    return [c["body"] for c in client.get(f"{FEED}/posts/{pid}/comments", headers=h(tok)).json()["comments"]]


def member_ids(client, tok):
    return {m["id"] for m in client.get("/v1/friends/overview", headers=h(tok)).json()["members"]}


def board_ids(client, tok):
    return {r["id"] for r in client.get("/v1/friends/leaderboard", headers=h(tok), params={"board": "body"}).json()["rows"]}


def share(dsn, owner, viewer):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        r = conn.execute("SELECT circle_scopes FROM shares WHERE owner_id = %s AND viewer_id = %s", (owner, viewer)).fetchone()
        return r and r["circle_scopes"]


def test_block_hides_both_ways_and_pauses_sharing(client, world, dsn, group):
    ids, tok, pid, cid = group
    client.put(f"{FEED}/posts/{pid}/like", headers=h(tok["Ben"]))
    assert share(dsn, ids["Ann"], ids["Ben"])
    assert client.put(f"/v1/friends/blocks/{ids['Ben']}", headers=h(tok["Ann_ro"])).status_code == 403   # needs write
    assert client.put(f"/v1/friends/blocks/{ids['Ann']}", headers=h(tok["Ann"])).status_code == 400     # not yourself
    r = client.put(f"/v1/friends/blocks/{ids['Ben']}", headers=h(tok["Ann"]))
    assert r.status_code == 200 and r.json() == {"user_id": ids["Ben"], "blocked": True}
    # Ann no longer sees Ben's comment / ❤️ / card / board row; Ben no longer sees Ann's post at all
    assert comments(client, tok["Ann"], pid) == []
    assert feed(client, tok["Ann"])[0]["likes"]["count"] == 0
    assert ids["Ben"] not in member_ids(client, tok["Ann"]) and ids["Ann"] not in member_ids(client, tok["Ben"])
    assert ids["Ben"] not in board_ids(client, tok["Ann"]) and ids["Ann"] not in board_ids(client, tok["Ben"])
    assert ids["Ann"] not in authors(client, tok["Ben"])
    assert client.get(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ben"])).status_code == 404
    assert client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Ben"]), json={"body": "x"}).status_code == 404
    # others are unaffected
    assert comments(client, tok["Cat"], pid) == ["nice"] and ids["Ben"] in member_ids(client, tok["Cat"])
    # no cheers between the two; sharing paused both ways (circle grants only)
    assert client.post("/v1/friends/nudges", headers=h(tok["Ben"]), json={"to": ids["Ann"], "kind": "clap"}).status_code == 404
    assert not share(dsn, ids["Ann"], ids["Ben"]) and not share(dsn, ids["Ben"], ids["Ann"])
    # still listed as a member (marked) so the owner can remove them; the 「已屏蔽」 list
    view = client.get("/v1/friends", headers=h(tok["Ann"])).json()
    ben = next(m for c in view["circles"] for m in c["members"] if m["id"] == ids["Ben"])
    assert ben.get("blocked") is True and view["blocked_count"] == 1
    assert [b["id"] for b in client.get("/v1/friends/blocks", headers=h(tok["Ann"])).json()["blocks"]] == [ids["Ben"]]
    # unblock: everything back, sharing restored
    assert client.delete(f"/v1/friends/blocks/{ids['Ben']}", headers=h(tok["Ann"])).status_code == 200
    assert client.delete(f"/v1/friends/blocks/{ids['Ben']}", headers=h(tok["Ann"])).status_code == 404
    assert comments(client, tok["Ann"], pid) == ["nice"] and share(dsn, ids["Ann"], ids["Ben"])
    assert ids["Ann"] in authors(client, tok["Ben"])


def test_block_only_people_in_my_circles(client, world, dsn):
    ids, tok = world
    circle(client, tok["Ann"])
    assert client.put(f"/v1/friends/blocks/{ids['Ben']}", headers=h(tok["Ann"])).status_code == 404


def report(client, tok, kind, target, reason="spam", note=None):
    return client.post("/v1/friends/reports", headers=h(tok), json={"kind": kind, "id": target, "reason": reason,
                                                                       **({"note": note} if note else {})})


def test_report_post_hides_for_reporter_then_for_everyone(client, world, dsn, group, monkeypatch):
    ids, tok, pid, cid = group
    assert report(client, tok["Ben"], "post", pid, reason="bogus").status_code == 422    # reasons are an enum
    assert report(client, tok["Ann"], "post", pid).status_code == 400                   # own post
    r = report(client, tok["Ben"], "post", pid, note="  example  note ")
    assert r.status_code == 200 and r.json() == {"reported": True, "hidden_for_everyone": False}
    assert pid not in {p["id"] for p in feed(client, tok["Ben"])}                      # gone for the reporter
    assert pid in {p["id"] for p in feed(client, tok["Cat"])}                          # still there for others
    assert report(client, tok["Ben"], "post", pid).status_code == 404                  # can't see it any more
    assert report(client, tok["Cat"], "post", pid).json()["hidden_for_everyone"] is False
    assert report(client, tok["Dan"], "post", pid).json()["hidden_for_everyone"] is True   # REPORT_HIDE_AFTER = 3
    assert pid not in {p["id"] for p in feed(client, tok["Eve"])}
    assert pid in {p["id"] for p in feed(client, tok["Ann"])}                          # the author still sees it
    # the admin queue
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        q = moderation.queue(conn)
        assert len(q) == 1 and q[0]["kind"] == "post" and q[0]["status"] == "hidden" and q[0]["reports"] == 3
        assert q[0]["reasons"] == {"spam": 3} and q[0]["notes"] == ["example note"]
        item = q[0]["id"]
        moderation.resolve(conn, item, "keep", note="fine")
    assert pid in {p["id"] for p in feed(client, tok["Eve"])}                          # shown again
    assert pid not in {p["id"] for p in feed(client, tok["Ben"])}                      # the reporter's own hide stays
    # one new report after "keep" puts it back in the queue but does not hide it
    assert report(client, tok["Eve"], "post", pid).json()["hidden_for_everyone"] is False
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert [r["status"] for r in moderation.queue(conn)] == ["open"]
        moderation.resolve(conn, item, "remove")
        assert [r["action"] for r in moderation.log(conn, item)] == ["remove", "keep", "auto_hide"]
    assert pid not in {p["id"] for p in feed(client, tok["Fay"])}
    assert pid in {p["id"] for p in feed(client, tok["Ann"])}


def test_report_comment_and_remove(client, world, dsn, group):
    ids, tok, pid, cid = group
    assert report(client, tok["Ann"], "comment", cid, reason="harassment").status_code == 200
    assert comments(client, tok["Ann"], pid) == [] and comments(client, tok["Cat"], pid) == ["nice"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        item = moderation.queue(conn)[0]
        assert item["content"]["text"] == "nice" and item["user_id"] == ids["Ben"]
        moderation.resolve(conn, item["id"], "remove")
    assert comments(client, tok["Cat"], pid) == [] and comments(client, tok["Ben"], pid) == []   # deleted for everyone


def test_report_profile_blocks_and_masks(client, world, dsn, group):
    ids, tok, pid, cid = group
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO images (kind, user_id, mime, sha256, data, width, height) VALUES "
                     "('user_avatar', %s, 'image/webp', 'x', '\\x00', 1, 1)", (ids["Ben"],))
        img = conn.execute("SELECT max(id) AS i FROM images").fetchone()["i"]
        conn.execute("UPDATE users SET avatar_image_id = %s WHERE id = %s", (img, ids["Ben"]))
    r = report(client, tok["Ann"], "profile", ids["Ben"], reason="hate")
    assert r.status_code == 200
    assert [b["id"] for b in client.get("/v1/friends/blocks", headers=h(tok["Ann"])).json()["blocks"]] == [ids["Ben"]]
    report(client, tok["Cat"], "profile", ids["Ben"])
    assert report(client, tok["Dan"], "profile", ids["Ben"]).json()["hidden_for_everyone"] is True
    eve = client.get("/v1/friends/overview", headers=h(tok["Eve"])).json()
    ben = next(m for m in eve["members"] if m["id"] == ids["Ben"])
    assert ben["display_name"] == P.HIDDEN_NAME[0] and ben["avatar"] is None
    assert next(c for c in feed(client, tok["Eve"])[0]["preview_comments"])["user"]["name"] == P.HIDDEN_NAME[0]
    assert client.get(f"/v1/avatars/users/{ids['Ben']}", headers=h(tok["Eve"])).status_code == 404
    assert client.get("/v1/friends/overview", headers=h(tok["Ben"])).json()["me"]["display_name"] == "Ben"   # not to himself
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        moderation.resolve(conn, moderation.queue(conn)[0]["id"], "remove")
        assert conn.execute("SELECT avatar_image_id FROM users WHERE id = %s", (ids["Ben"],)).fetchone()["avatar_image_id"] is None


def test_ban_user_takes_them_out_of_circles(client, world, dsn, group):
    ids, tok, pid, cid = group
    report(client, tok["Ann"], "comment", cid)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        moderation.resolve(conn, moderation.queue(conn)[0]["id"], "ban-user", note="example")
    assert ids["Ben"] not in member_ids(client, tok["Ann"])
    ov = client.get("/v1/friends", headers=h(tok["Ben"])).json()
    assert ov["banned"] is True and ov["circles"] == []
    r = client.post("/v1/friends/circles", headers=h(tok["Ben"]), json={"name": "New", "scopes": list(P.DEFAULT_SCOPES)})
    assert r.status_code == 403
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert moderation.unban(conn, ids["Ben"]) is True
        assert [r["action"] for r in moderation.log(conn)][:2] == ["unban", "ban-user"]
    assert client.post("/v1/friends/circles", headers=h(tok["Ben"]), json={"name": "New", "scopes": []}).status_code == 201


def test_admin_endpoints_and_cli(client, world, dsn, group, monkeypatch, capsys):
    ids, tok, pid, cid = group
    report(client, tok["Ann"], "comment", cid, note="example")
    assert client.get("/v1/admin/circles/reports", headers=h(tok["Cat"])).status_code == 403
    monkeypatch.setenv("BODY_ADMIN_USER_IDS", str(ids["Fay"]))
    assert client.get("/v1/admin/circles/reports", headers=h(tok["Cat"])).status_code == 403
    q = client.get("/v1/admin/circles/reports", headers=h(tok["Fay"])).json()
    assert q["pending"] == 1 and q["overdue"] == 0 and q["items"][0]["content"]["text"] == "nice"
    item = q["items"][0]["id"]
    assert client.post(f"/v1/admin/circles/reports/{item}/resolve", headers=h(tok["Fay"]), json={"action": "nuke"}).status_code == 422
    assert client.post("/v1/admin/circles/reports/99999/resolve", headers=h(tok["Fay"]), json={"action": "keep"}).status_code == 404
    r = client.post(f"/v1/admin/circles/reports/{item}/resolve", headers=h(tok["Fay"]), json={"action": "keep", "note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "kept" and r.json()["resolved_by"] == ids["Fay"]
    # the overdue count (for the private doctor) and the CLI
    report(client, tok["Cat"], "post", pid)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE circle_moderation SET first_reported_at = now() - interval '30 hours' WHERE kind = 'post'")
        o = moderation.overdue(conn)
        assert o["pending"] == 1 and o["overdue"] == 1 and o["oldest_hours"] >= 29.9
        moderation_cli.run(conn, argparse.Namespace(reports_cmd="list", status="pending", limit=10, json=False))
        out = capsys.readouterr().out
        assert "post" in out and "spam×1" in out
        post_item = moderation.queue(conn)[0]["id"]
        moderation_cli.run(conn, argparse.Namespace(reports_cmd="resolve", id=post_item, action="remove", note=None))
        assert "removed" in capsys.readouterr().out
        assert conn.execute("SELECT actor FROM circle_moderation_log WHERE item_id = %s AND action = 'remove'",
                            (post_item,)).fetchone()["actor"] == "cli"
        assert moderation.overdue(conn)["pending"] == 0


def test_keyword_filter_on_write(client, world, dsn, group, tmp_path, monkeypatch):
    ids, tok, pid, cid = group
    f = tmp_path / "words.txt"
    f.write_text("示例词\nexampleword\n", encoding="utf-8")
    monkeypatch.setenv(wordfilter.ENV, str(f))
    r = client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Cat"]), json={"body": "这是 示 例 词"})
    assert r.status_code == 422 and r.json()["detail"] == wordfilter.MESSAGE_ZH
    r = client.post(f"{FEED}/posts/{pid}/comments", headers={**h(tok["Cat"]), "X-Body-Lang": "en"}, json={"body": "ExampleWord!"})
    assert r.status_code == 422 and r.json()["detail"] == wordfilter.MESSAGE_EN
    assert client.post("/v1/friends/circles", headers=h(tok["Cat"]), json={"name": "exampleword club", "scopes": []}).status_code == 422
    cid_ = client.get("/v1/friends", headers=h(tok["Ann"])).json()["circles"][0]["id"]
    assert client.patch(f"/v1/friends/circles/{cid_}", headers=h(tok["Ann"]), json={"name": "示例词圈"}).status_code == 422
    assert client.patch("/v1/users/me/profile", headers=h(tok["Cat"]), json={"display_name": "exampleword"}).status_code == 422
    assert client.patch("/v1/users/me/profile", headers=h(tok["Cat"]), json={"display_name": "Cat"}).status_code == 200


def test_rules_gate(client, world, dsn, group):
    ids, tok, pid, cid = group
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, P.RULES_FLAG, "all", actor="test")
    flags.invalidate()
    g = client.get("/v1/friends/rules", headers=h(tok["Cat"]), params={"lang": "en"}).json()
    assert g["required"] is True and g["accepted"] is False and g["version"] == P.RULES_VERSION
    assert g["markdown"].startswith("# Circle community rules") and "harassment" in g["report_reasons"]
    assert client.get("/v1/friends", headers=h(tok["Cat"])).json()["rules"]["accepted"] is False
    assert client.post("/v1/friends/circles", headers=h(tok["Cat"]), json={"name": "Cat's", "scopes": []}).status_code == 428
    assert client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Cat"]), json={"body": "hi"}).status_code == 428
    assert client.post("/v1/friends/rules/accept", headers=h(tok["Cat"]), json={"version": "old"}).status_code == 409
    r = client.post("/v1/friends/rules/accept", headers=h(tok["Cat"]), json={"version": P.RULES_VERSION})
    assert r.status_code == 200 and r.json()["accepted"] is True
    assert client.post("/v1/friends/circles", headers=h(tok["Cat"]), json={"name": "Cat's", "scopes": []}).status_code == 201
    assert client.post(f"{FEED}/posts/{pid}/comments", headers=h(tok["Cat"]), json={"body": "hi"}).status_code == 201
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT version FROM circle_rules_acceptances WHERE user_id = %s", (ids["Cat"],)).fetchone()["version"] \
            == P.RULES_VERSION
        flags.set_flag(conn, P.RULES_FLAG, "off", actor="test")
    flags.invalidate()
