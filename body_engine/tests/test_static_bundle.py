"""Static data bundle (issue #248, item 2): revisions by trigger, full / delta documents, code modules, privacy, the endpoints
(flag, auth, gzip, ETag), the pack command and the size of the shipped package.
Own database body_test_static_bundle (dropped and recreated on every run); skipped if Postgres is unreachable."""
import gzip
import hashlib
import json
import os
import re

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.static_bundle import FORMAT, build, pack
from body_engine.training import catalog, custom
from body_engine.training.opengym import importer

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_STATIC_BUNDLE", "body_test_static_bundle")
H = lambda t: {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
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
        catalog.ensure_seeded(conn)
    return dsn


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        uid = c.execute("INSERT INTO users (display_name, role) VALUES ('Zed', 'member') RETURNING id").fetchone()["id"]
        tok = auth.create_token(c, uid, "web", ["read", "write"])
        # personal exercise data that must never reach the bundle
        c.execute("INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, cues, reason) VALUES (%s, 'upright_row', 'ZED-PRIVATE', "
                  "'banned', 'ZED-PRIVATE-CUE', 'ZED-PRIVATE-REASON')", (uid,))
        custom.add(c, uid, {"name_zh": "ZED-CUSTOM-MOVE", "primary_muscles": ["chest"], "equipment": ["dumbbell"]})
    return uid, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def _flag(conn, key, state):
    flags.set_flag(conn, key, state, actor="test")
    flags.invalidate()


# ---------------------------------------------------------------- pure
def test_exercise_row_is_compact_and_has_provenance():
    row = {"slug": "x", "name_zh": "叉", "name_en": "X", "aliases": [], "category": "strength", "force": None, "per_side": False,
           "cues_zh": None, "description_en": "A cue.", "instructions_en": ["a"], "images": [], "source": "opengym", "license": "AGPL-3.0",
           "text_sources": [{"source": "opengym", "license": "AGPL-3.0", "ref": "1", "commit": "c", "body_part": "waist"}],
           "rev": 7, "updated_at": 1, "primary_muscles": ["chest"]}
    out = build.exercise_row(row)
    assert out == {"slug": "x", "name_zh": "叉", "name_en": "X", "category": "strength", "instructions_en": ["a"], "source": "opengym",
                   "license": "AGPL-3.0", "primary_muscles": ["chest"], "cues_en": "A cue.",
                   "text_sources": [{"source": "opengym", "license": "AGPL-3.0", "ref": "1"}]}


def test_english_text_in_the_chinese_columns_moves_to_the_english_fields():
    row = {"slug": "y", "name_zh": "Bench Dip", "name_en": "Bench Dip", "cues_zh": "Sit on a bench.", "instructions_zh": ["Sit.", "Dip."],
           "source": "free-exercise-db"}
    out = build.exercise_row(row)
    assert out == {"slug": "y", "name_en": "Bench Dip", "cues_en": "Sit on a bench.", "instructions_en": ["Sit.", "Dip."], "source": "free-exercise-db"}
    zh = build.exercise_row({"slug": "z", "name_zh": "卧推", "name_en": "Bench Press", "cues_zh": "沉肩。", "instructions_zh": ["躺下"],
                             "instructions_en": ["Lie down"]})
    assert zh["name_zh"] == "卧推" and zh["cues_zh"] == "沉肩。" and zh["instructions_zh"] == ["躺下"] and zh["instructions_en"] == ["Lie down"]


def test_code_modules_are_json_and_stable():
    for name in build.CODE_MODULES:
        a = build.code_module(name)
        assert json.loads(json.dumps(a, ensure_ascii=False)) == a
        assert build.code_hash(name) == build.code_hash(name)
    tax = build.code_module("taxonomy")
    assert "chest" in tax["muscles"] and tax["muscles"]["chest"]["en"] == "Chest" and "barbell" in tax["equipment"]
    assert "dips" in tax["default_caution_slugs"] and set(tax["imported_sources"]) == {"opengym", "exercisedb"}
    assert build.code_module("split_templates")["3_day_ppl"]["days"][0][0] == 1
    g = build.code_module("glossary")
    assert "recovery" in g["zh"]["metrics"] and "recovery" in g["en"]["metrics"]


# ---------------------------------------------------------------- revisions
def test_every_row_has_a_distinct_rev_and_identical_reseed_changes_nothing(conn):
    revs = [r["rev"] for r in conn.execute("SELECT rev FROM exercise_catalog")]
    assert len(revs) == len(set(revs)) >= 100 and min(revs) > 0
    v = build.current_version(conn)
    catalog.seed_catalog(conn)                       # the same data again: updated_at moves, content does not
    assert build.current_version(conn) == v
    assert [r["rev"] for r in conn.execute("SELECT rev FROM exercise_catalog ORDER BY slug")] == \
        [r["rev"] for r in sorted(conn.execute("SELECT slug, rev FROM exercise_catalog"), key=lambda r: r["slug"])]


def test_change_delete_and_return_move_the_version(conn):
    v0 = build.current_version(conn)
    conn.execute("UPDATE exercise_catalog SET cues_zh = 'new cue' WHERE slug = 'plank'")
    v1 = build.current_version(conn)
    assert v1 > v0 and conn.execute("SELECT rev FROM exercise_catalog WHERE slug = 'plank'").fetchone()["rev"] == v1
    row = conn.execute("SELECT * FROM exercise_catalog WHERE slug = 'plank'").fetchone()
    conn.execute("DELETE FROM exercise_catalog WHERE slug = 'plank'")
    v2 = build.current_version(conn)
    assert v2 > v1 and conn.execute("SELECT rev FROM static_tombstones WHERE key = 'plank'").fetchone()["rev"] == v2
    up, gone = build.exercises_since(conn, v1)
    assert gone == ["plank"] and up == []
    conn.execute("""INSERT INTO exercise_catalog (slug, name_zh, name_en, category, modality, equipment, primary_muscles, source)
                    VALUES ('plank', %s, %s, 'strength', 'duration', %s, %s, 'curated')""",
                 (row["name_zh"], row["name_en"], row["equipment"], row["primary_muscles"]))
    assert conn.execute("SELECT count(*) AS n FROM static_tombstones WHERE key = 'plank'").fetchone()["n"] == 0   # it is back: no tombstone
    up, gone = build.exercises_since(conn, v1)
    assert [r["slug"] for r in up] == ["plank"] and gone == []


def test_a_code_module_that_changed_gets_a_new_rev(conn):
    build.refresh_code_modules(conn, force=True)
    v = build.current_version(conn)
    conn.execute("UPDATE static_modules SET hash = 'old' WHERE name = 'glossary'")      # what a deploy with new glossary texts looks like
    build.refresh_code_modules(conn, force=True)
    v2 = build.current_version(conn)
    assert v2 > v
    doc = build.document(conn, v)
    assert set(doc["modules"]) == {"glossary"} and doc["full"] is False
    build.refresh_code_modules(conn, force=True)
    assert build.current_version(conn) == v2                                            # unchanged now: no further bump


# ---------------------------------------------------------------- documents
def test_full_and_delta_documents(conn, world):
    full = build.document(conn)
    n = conn.execute("SELECT count(*) AS n FROM exercise_catalog").fetchone()["n"]
    assert full["full"] and full["since"] is None and full["format"] == FORMAT and full["version"] == build.current_version(conn)
    assert len(full["modules"]["exercises"]["upsert"]) == n and set(full["modules"]) == set(build.MODULES)
    assert {a["license"] for a in full["attribution"]} == {"Unlicense", "AGPL-3.0", "MIT"} and full["notices"] == "THIRD_PARTY_NOTICES.md"
    blob = json.dumps(full, ensure_ascii=False)
    assert "ZED" not in blob                                                             # no custom exercise, no exercise_meta
    assert not any(r["slug"].startswith("my_") for r in full["modules"]["exercises"]["upsert"])
    v = full["version"]
    assert build.document(conn, v)["modules"] == {} and build.document(conn, v)["full"] is False
    ahead = build.document(conn, v + 1000)
    assert ahead["full"] is True and ahead["since"] is None                              # a client from a rolled-back server
    conn.execute("UPDATE exercise_catalog SET cues_zh = 'again' WHERE slug = 'dead_bug'")
    d = build.document(conn, v)
    assert [r["slug"] for r in d["modules"]["exercises"]["upsert"]] == ["dead_bug"] and d["modules"]["exercises"]["delete"] == []
    assert "taxonomy" not in d["modules"] and d["version"] > v


def test_bundle_with_the_openGym_import_keeps_licences_and_stays_small(conn):
    importer.run(conn)
    doc = build.document(conn)
    rows = doc["modules"]["exercises"]["upsert"]
    assert len(rows) > 5000
    og = next(r for r in rows if r["slug"] == "og_0001")
    assert og["license"] == "MIT" and og["source"] == "exercisedb" and og["text_sources"] == [{"source": "exercisedb", "license": "MIT", "ref": "0001"}]
    ours = [r for r in rows if r["slug"].startswith("og_")]
    assert og["instructions_en"] and not any("images" in r or re.search(r"\.(gif|mp4|png|jpe?g)\b|catalogue/media|gymvisual", json.dumps(r)) for r in ours)
    size = len(build.encode(doc))
    assert size < 3 * 1024 * 1024, size                                                  # the budget of the package inside the app
    assert build.encode(doc) == build.encode(build.document(conn))                      # same data, same bytes


# ---------------------------------------------------------------- endpoints
def test_endpoints_are_gated_by_the_flag_and_need_a_login(conn, client, world):
    uid, tok = world
    _flag(conn, "static_bundle", "off")
    assert client.get("/v1/static-bundle/manifest", headers=H(tok)).status_code == 404
    assert client.get("/v1/static-bundle", headers=H(tok)).status_code == 404
    _flag(conn, "static_bundle", "all")
    assert client.get("/v1/static-bundle/manifest").status_code == 401
    assert client.get("/v1/static-bundle").status_code == 401
    assert client.get("/v1/static-bundle/manifest", headers=H(tok)).status_code == 200


def test_manifest_and_bundle_over_http(conn, client, world):
    uid, tok = world
    _flag(conn, "static_bundle", "all")
    _flag(conn, "exercise_opengym", "off")
    man = client.get("/v1/static-bundle/manifest", headers=H(tok)).json()
    assert man["version"] == build.current_version(conn) and man["format"] == FORMAT
    assert man["flags"] == {"exercise_opengym": False} and set(man["modules"]) == set(build.MODULES)
    assert man["modules"]["exercises"]["count"] == conn.execute("SELECT count(*) AS n FROM exercise_catalog").fetchone()["n"]

    r = client.get("/v1/static-bundle", headers={**H(tok), "Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip" and r.headers["x-static-bundle-version"] == str(man["version"])
    doc = r.json()                                       # the test client decodes gzip itself
    assert doc["full"] and doc["flags"] == {"exercise_opengym": False} and len(doc["modules"]["exercises"]["upsert"]) == man["modules"]["exercises"]["count"]
    plain = client.get("/v1/static-bundle", headers={**H(tok), "Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.json() == doc

    etag = r.headers["etag"]
    again = client.get("/v1/static-bundle", headers={**H(tok), "If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""

    conn.execute("UPDATE exercise_catalog SET cues_zh = 'http delta' WHERE slug = 'bird_dog'")
    delta = client.get(f"/v1/static-bundle?since={man['version']}", headers=H(tok)).json()
    assert delta["full"] is False and delta["since"] == man["version"] and delta["version"] > man["version"]
    assert [x["slug"] for x in delta["modules"]["exercises"]["upsert"]] == ["bird_dog"]
    assert client.get(f"/v1/static-bundle?since={man['version']}", headers={**H(tok), "If-None-Match": etag}).status_code == 200   # other etag
    assert client.get("/v1/static-bundle?since=-1", headers=H(tok)).status_code == 422

    _flag(conn, "exercise_opengym", "all")
    assert client.get("/v1/static-bundle/manifest", headers=H(tok)).json()["flags"] == {"exercise_opengym": True}
    _flag(conn, "exercise_opengym", "off")


# ---------------------------------------------------------------- pack
def test_pack_writes_a_verifiable_package(conn, tmp_path):
    v0 = build.current_version(conn)
    conn.execute("UPDATE exercise_catalog SET cues_zh = 'pack' WHERE slug = 'chin_up'")
    man = pack.pack(conn, tmp_path / "out", since=v0)
    files = {f["file"]: f for f in man["files"]}
    version = man["version"]
    assert set(files) == {f"bundle-v{version}.json.gz", f"delta-v{v0}-to-v{version}.json.gz"}
    for name, f in files.items():
        data = (tmp_path / "out" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"] and len(data) == f["bytes"] == os.path.getsize(tmp_path / "out" / name)
        assert json.loads(gzip.decompress(data))["version"] == version
    on_disk = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert on_disk["version"] == version and on_disk["flags"] == {"exercise_opengym": True} and on_disk["attribution"]
    again = pack.pack(conn, tmp_path / "out2", since=v0)
    assert [f["sha256"] for f in again["files"]] == [f["sha256"] for f in man["files"]]       # reproducible
    with pytest.raises(ValueError):
        pack.pack(conn, tmp_path / "out3", since=version)
