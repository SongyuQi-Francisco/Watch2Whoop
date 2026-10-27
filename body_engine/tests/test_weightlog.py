"""Manual weigh-ins with their measuring conditions (weightlog/, migration 950): tags + note stored next to the weight through
POST /v1/users/{user}/weight (no offline_mode flag needed), idempotent replays, the 409 conflict leaves the conditions alone,
overwrite replaces them, GET /weight/recent merges them, the AI reads them in get_weight and writes them with log_weight, undo
removes them, and the Today page's chips agree with weightlog/params.py. SparkyFitness is a fake.
Separate test database BODY_TEST_DB_NAME_WEIGHTLOG (default body_test_weightlog); skipped without Postgres."""
import os
import re
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from body_engine import api, auth, sparky_client
from body_engine.assistant import sparky_tools, sparky_write
from body_engine.assistant.butler_tools import TOOL_SPECS as BUTLER_TOOLS
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.weightlog import params as P, service, store
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_WEIGHTLOG", "body_test_weightlog")
STATIC = Path(__file__).resolve().parent.parent / "src" / "body_engine" / "web" / "static"


# ---------------------------------------------------------------- pure
def test_clean_tags_and_note():
    assert store.clean_tags(["unclothed", "fasting", "fasting"]) == ["fasting", "unclothed"]   # chip order, no duplicates
    assert store.clean_tags([]) == [] and store.clean_tags(None) == []
    with pytest.raises(ValueError, match="未知"):
        store.clean_tags(["fasting", "drunk"])
    with pytest.raises(ValueError, match="只能选一个"):
        store.clean_tags(["clothed", "unclothed"])
    assert store.clean_note("  饭后两小时 ") == "饭后两小时" and store.clean_note("  ") is None
    with pytest.raises(ValueError):
        store.clean_note("x" * (P.MAX_NOTE + 1))


def test_page_chips_agree_with_params():
    js = (STATIC / "weight.js").read_text(encoding="utf-8")
    chips = re.findall(r'\["(\w+)", \(\) => tr\("([^"]+)"\)\]', js)
    assert [k for k, _ in chips] == list(P.CONTEXT_TAGS)          # same keys, same order
    assert dict(chips) == P.CONTEXT_TAGS                          # same Chinese texts the AI reads
    en = (STATIC / "i18n-en.js").read_text(encoding="utf-8")
    for _, zh in chips:
        assert f'"{zh}":' in en, zh
    assert [list(g) for g in P.EXCLUSIVE] == [["clothed", "unclothed"]] and 'EXCLUSIVE = [["clothed", "unclothed"]]' in js
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'src="weight.js"' in index and '"weight.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "me-weight" in js and "weightOn" not in js              # not behind the offline_mode flag


def test_tool_spec_is_short():
    spec = next(t for t in BUTLER_TOOLS if t["name"] == "log_weight")
    assert set(spec["input_schema"]["properties"]) >= {"context", "note"}
    assert len(str(spec["input_schema"]["properties"]["context"])) < 160


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
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


@pytest.fixture(scope="module")
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name, role="member"):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, %s, 'Australia/Sydney', now(), '2026-10-22.2') RETURNING id""",
                                (name, role)).fetchone()["id"]
        ids = {"a": add("a", "owner"), "b": add("b")}
        conn.commit()
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


class FakeSparky:
    """The check-ins of one user, like SparkyFitness /measurements/check-in (+ the range read and delete)."""
    def __init__(self):
        self.days = {}

    def get(self, path, **_):
        return self.days.get(path.rsplit("/", 1)[1], {})

    def request(self, method, path, body=None):
        if method == "DELETE":
            self.days = {d: c for d, c in self.days.items() if c["id"] != path.rsplit("/", 1)[1]}
        else:
            cur = self.days.get(body["entry_date"], {"id": f"c{len(self.days)}"})
            self.days[body["entry_date"]] = {**cur, **body}

    def rows(self):
        return [{"entry_date": d, "weight": c.get("weight")} for d, c in self.days.items()]


@pytest.fixture()
def fake(monkeypatch):
    f = FakeSparky()
    monkeypatch.setattr(sparky_client, "write_client_for_user", lambda conn, uid: f)
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: f)
    monkeypatch.setattr(sparky_client, "weight", lambda c, s, e: sorted(
        [{"date": r["entry_date"], "weight_kg": r["weight"]} for r in f.rows() if r["weight"] is not None], key=lambda r: r["date"]))
    return f


def H(t):
    return {"Authorization": f"Bearer {t}"}


def days(dsn, uid):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        today = sparky_write.user_today(conn, uid)
    return today.isoformat(), (today - timedelta(days=2)).isoformat()


def ctx_row(dsn, uid, day):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute("SELECT tags, note, measured_at FROM weight_context WHERE user_id = %s AND date = %s", (uid, day)).fetchone()


def test_post_stores_conditions_without_the_flag(client, users, dsn, fake):
    ids, tok = users
    today, past = days(dsn, ids["a"])
    body = {"date": today, "weight_kg": 88.4, "context_tags": ["unclothed", "fasting"], "note": " 早饭前 ", "measured_at": "2026-10-28T07:30:00+11:00"}
    r = client.post("/v1/users/me/weight", json=body, headers=H(tok["a"]))
    assert r.status_code == 200 and r.json()["written"] is True
    row = ctx_row(dsn, ids["a"], today)
    assert row["tags"] == ["fasting", "unclothed"] and row["note"] == "早饭前" and row["measured_at"] is not None
    # a replay (the offline queue) writes nothing new and keeps the same conditions
    r = client.post("/v1/users/me/weight", json=body, headers=H(tok["a"]))
    assert r.status_code == 200 and r.json()["unchanged"] is True and ctx_row(dsn, ids["a"], today)["tags"] == ["fasting", "unclothed"]
    # same weight, other conditions: the conditions follow; a body without them leaves them alone
    client.post("/v1/users/me/weight", json={"date": today, "weight_kg": 88.4, "context_tags": ["after_workout"]}, headers=H(tok["a"]))
    assert ctx_row(dsn, ids["a"], today)["tags"] == ["after_workout"] and ctx_row(dsn, ids["a"], today)["note"] == "早饭前"
    client.post("/v1/users/me/weight", json={"date": today, "weight_kg": 88.4}, headers=H(tok["a"]))
    assert ctx_row(dsn, ids["a"], today)["tags"] == ["after_workout"]
    # a conflict writes neither the weight nor the new conditions; overwrite replaces both
    r = client.post("/v1/users/me/weight", json={"date": today, "weight_kg": 87.9, "context_tags": ["before_bed"]}, headers=H(tok["a"]))
    assert r.status_code == 409 and r.json()["detail"]["existing_kg"] == 88.4
    assert ctx_row(dsn, ids["a"], today)["tags"] == ["after_workout"] and fake.days[today]["weight"] == 88.4
    r = client.post("/v1/users/me/weight", json={"date": today, "weight_kg": 87.9, "context_tags": ["before_bed"], "note": "", "overwrite": True}, headers=H(tok["a"]))
    assert r.status_code == 200 and fake.days[today]["weight"] == 87.9
    assert ctx_row(dsn, ids["a"], today)["tags"] == ["before_bed"] and ctx_row(dsn, ids["a"], today)["note"] is None
    # a past day with nothing but a weight stores no row; clearing the last condition removes it
    client.post("/v1/users/me/weight", json={"date": past, "weight_kg": 89.0}, headers=H(tok["a"]))
    assert ctx_row(dsn, ids["a"], past) is None
    client.post("/v1/users/me/weight", json={"date": today, "weight_kg": 87.9, "context_tags": []}, headers=H(tok["a"]))
    assert ctx_row(dsn, ids["a"], today) is None


def test_post_rejects_bad_conditions_and_other_users(client, users, dsn, fake):
    ids, tok = users
    today, _ = days(dsn, ids["a"])
    ok = {"date": today, "weight_kg": 80.0}
    assert client.post("/v1/users/me/weight", json={**ok, "context_tags": ["drunk"]}, headers=H(tok["a"])).status_code == 422
    assert client.post("/v1/users/me/weight", json={**ok, "context_tags": ["clothed", "unclothed"]}, headers=H(tok["a"])).status_code == 422
    assert client.post("/v1/users/me/weight", json={**ok, "note": "x" * 201}, headers=H(tok["a"])).status_code == 422
    assert not fake.days                                                 # nothing reached the training log
    assert client.post(f"/v1/users/{ids['a']}/weight", json=ok, headers=H(tok["b"])).status_code in (403, 404)
    assert client.get(f"/v1/users/{ids['a']}/weight/recent", headers=H(tok["b"])).status_code in (403, 404)


def test_recent_lists_weights_with_conditions(client, users, dsn, fake):
    ids, tok = users
    today, past = days(dsn, ids["a"])
    client.post("/v1/users/me/weight", json={"date": past, "weight_kg": 89.0}, headers=H(tok["a"]))
    client.post("/v1/users/me/weight", json={"date": today, "weight_kg": 88.0, "context_tags": ["fasting"], "note": "睡醒", "measured_at": "2026-10-28T07:30:00+11:00"},
                headers=H(tok["a"]))
    items = client.get("/v1/users/me/weight/recent?limit=5", headers=H(tok["a"])).json()["items"]
    assert [i["date"] for i in items] == [today, past]                   # newest first
    assert items[0]["weight_kg"] == 88.0 and items[0]["tags"] == ["fasting"] and items[0]["note"] == "睡醒" and items[0]["at"]
    assert items[1]["tags"] == [] and items[1]["note"] is None and items[1]["at"] is None
    assert client.get("/v1/users/me/weight/recent?limit=1", headers=H(tok["a"])).json()["items"][0]["date"] == today
    # the training log not reachable: the own table answers
    def down(conn, uid):
        raise sparky_client.SparkyError("down")
    import pytest as _p
    with _p.MonkeyPatch.context() as m:
        m.setattr(sparky_client, "client_for_user", down)
        assert client.get("/v1/users/me/weight/recent", headers=H(tok["a"])).status_code == 200


def test_ai_reads_and_writes_conditions_and_undo_removes_them(users, dsn, fake):
    ids, _ = users
    uid = ids["a"]
    today, _ = days(dsn, uid)
    spec = next(t for t in BUTLER_TOOLS if t["name"] == "log_weight")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM weight_context WHERE user_id = %s", (uid,))   # the earlier tests share this database
        a = dict.fromkeys(spec["input_schema"]["properties"])
        a.update(date=today, weight_kg=86.5, overwrite=False, context=["after_meal"], note="外卖后")
        res = spec["handler"](conn, uid, a)
        conn.commit()
        assert res["written"]["weight"] == 86.5
        out = sparky_tools._weight(conn, uid, {"start": today, "end": today})
        e = out["entries"][0]
        assert e["weight_kg"] == 86.5 and e["ctx"] == "饭后" and e["note"] == "外卖后" and "at" not in e
        # a weight without conditions has no extra fields
        service.log_weight(conn, uid, {"date": today, "weight_kg": 86.5, "overwrite": False}, context_tags=[], note="")
        assert set(sparky_tools._weight(conn, uid, {"start": today, "end": today})["entries"][0]) == {"date", "weight_kg"}
        service.log_weight(conn, uid, {"date": today, "weight_kg": 86.5, "overwrite": False}, context_tags=["fasting"])
        sparky_write.undo_entry(conn, uid, "weight", today, None)
        conn.commit()
    assert ctx_row(dsn, uid, today) is None and today not in fake.days
