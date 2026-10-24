"""openGym exercise text import (issue #248): snapshot is text only and pinned, mapping, merge into the existing library, licences,
idempotency, the emergency flag, and the library still being fast and sane with ~5,500 more exercises.
Own database body_test_opengym (dropped and recreated on every run); skipped if Postgres is unreachable."""
import os
import re
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.training import catalog, catalog_data as CD, muscle_load, params as P
from body_engine.training.opengym import COMMIT, importer, mapping as M

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_OPENGYM", "body_test_opengym")
CJK = re.compile(r"[一-鿿]")


# ---------------------------------------------------------------- pure
def test_snapshot_is_pinned_text_only():
    meta, entries = importer.load_snapshot()
    assert meta["commit"] == COMMIT and meta["entries"] == len(entries) == 5632
    allowed = {"id", "name", "bodyPart", "equipment", "target", "secondaryMuscles", "category", "description", "instructions", "textSource"}
    for e in entries:
        assert set(e) <= allowed, e["id"]
        blob = " ".join([e["name"], e["description"], *e["instructions"]]).lower()
        assert not re.search(r"\.(gif|png|jpe?g|webp|mp4)\b|catalogue/media|gymvisual", blob), e["id"]
        assert e["textSource"] in M.LICENSES
    assert meta["by_text_source"] == {"exercisedb": 1324, "opengym": 4308}


def test_importer_refuses_a_snapshot_from_another_commit(tmp_path):
    import gzip
    import json
    bad = tmp_path / "s.json.gz"
    with gzip.open(bad, "wt") as f:
        json.dump({"meta": {"commit": "0" * 40}, "exercises": []}, f)
    with pytest.raises(ValueError, match="pinned commit"):
        importer.load_snapshot(bad)


def test_title_and_convert_mapping():
    assert importer.title("push-up (on stability ball)") == "Push-up (on Stability Ball)"
    assert importer.title("cable lat pulldown to the chest") == "Cable Lat Pulldown to the Chest"
    from collections import Counter
    um = Counter()
    row = importer.convert({"id": "0001", "name": "barbell upright row", "bodyPart": "shoulders", "equipment": "barbell", "target": "delts",
                            "secondaryMuscles": ["biceps", "hip flexors", "trapezius"], "category": "strength", "description": "d",
                            "instructions": ["a", " b "], "textSource": "exercisedb"}, um)
    assert row["slug"] == "og_0001" and row["name_zh"] == row["name_en"] == "Barbell Upright Row"
    assert row["primary_muscles"] == ["shoulders"] and row["secondary_muscles"] == ["biceps", "traps"]
    assert row["equipment"] == ["barbell"] and row["modality"] == "weight_reps" and row["movement_pattern"] == "vertical_push"
    assert (row["source"], row["license"]) == ("exercisedb", "MIT") and row["instructions_en"] == ["a", "b"]
    assert row["family_slug"] == "upright_row" and row["joint_stress"] == next(x["joint_stress"] for x in CD.CURATED_EXERCISES if x["slug"] == "upright_row")
    assert row["images"] == [] and row["text_sources"][0]["commit"] == COMMIT
    assert um == {("muscle (secondary)", "hip flexors"): 1}
    cardio = importer.convert({"id": "2", "name": "high knees", "bodyPart": "cardio", "equipment": "body weight", "target": "cardiovascular system",
                               "secondaryMuscles": [], "category": "plyometrics", "description": "", "instructions": [], "textSource": "opengym"}, um)
    assert (cardio["category"], cardio["modality"], cardio["movement_pattern"], cardio["primary_muscles"]) == ("cardio", "duration", "cardio", [])
    assert cardio["license"] == "AGPL-3.0" and cardio["description_en"] is None
    odd = importer.convert({"id": "3", "name": "sled push", "bodyPart": "upper legs", "equipment": "sled thing", "target": "levator scapulae",
                            "secondaryMuscles": [], "category": "strength", "description": "", "instructions": [], "textSource": "opengym"}, um)
    assert odd["equipment"] == ["sled thing"] and odd["primary_muscles"] == [] and odd["movement_pattern"] is None
    assert ("equipment", "sled thing") in um and ("muscle (target)", "levator scapulae") in um


def test_mapping_targets_exist_in_the_catalog_taxonomy():
    assert set(M.TARGET.values()) <= set(CD.MUSCLE_ZH) and set(M.SECONDARY.values()) <= set(CD.MUSCLE_ZH)
    assert set(M.EQUIPMENT.values()) <= set(CD.EQUIPMENT_ZH) | {"medicine ball", "exercise ball"}
    assert set(M.CATEGORY.values()) == {"strength", "stretching", "cardio", "mobility"}


# ---------------------------------------------------------------- database
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


@pytest.fixture(scope="module")
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def report(conn):
    before = {r["slug"]: r for r in conn.execute("SELECT * FROM exercise_catalog").fetchall()}
    dry = importer.run(conn, dry_run=True)
    assert conn.execute("SELECT count(*) AS n FROM exercise_catalog").fetchone()["n"] == len(before)   # a dry run writes nothing
    rep = importer.run(conn)
    return before, dry, rep


@pytest.fixture(scope="module")
def uid(conn, report):
    return conn.execute("INSERT INTO users (display_name, role) VALUES ('T', 'member') RETURNING id").fetchone()["id"]


def _flag(conn, state):
    flags.set_flag(conn, "exercise_opengym", state, actor="test")
    flags.invalidate()


def test_import_counts_and_dry_run_matches(conn, report):
    before, dry, rep = report
    total = conn.execute("SELECT count(*) AS n FROM exercise_catalog").fetchone()["n"]
    assert dry["new_rows"] == rep["new_rows"] and dry["merged_into_existing"] == rep["merged_into_existing"]
    assert dry["catalog_total"] == total == len(before) + rep["new_rows"]
    assert rep["entries"] == 5632 and rep["variant_stubs_skipped"] == 922 and rep["internal_duplicates_merged"] >= 6
    assert rep["new_rows"] + rep["merged_into_existing"] + rep["internal_duplicates_merged"] == rep["entries"]
    assert rep["new_by_source"].keys() <= {"exercisedb", "opengym"} and rep["merged_into_existing"] > 20
    kinds = {u["kind"] for u in rep["unmapped"]}
    assert {"equipment", "muscle (secondary)", "muscle (target)"} <= kinds and "cardiovascular system" not in {u["name"] for u in rep["unmapped"]}
    assert "weighted" in {u["name"] for u in rep["unmapped"] if u["kind"] == "equipment"}      # listed, not guessed
    assert "hip flexors" in {u["name"] for u in rep["unmapped"] if u["kind"] == "muscle (secondary)"}


def test_import_is_idempotent(conn, report):
    stamp = conn.execute("SELECT count(*) AS n, max(updated_at) AS t FROM exercise_catalog").fetchone()
    again = importer.run(conn)
    assert again["existing_rows_updated"] == 0
    assert conn.execute("SELECT count(*) AS n, max(updated_at) AS t FROM exercise_catalog").fetchone() == stamp


def test_licence_source_and_text_only_columns(conn, report):
    rows = conn.execute("SELECT * FROM exercise_catalog WHERE slug LIKE 'og\\_%'").fetchall()
    assert len(rows) == report[2]["new_rows"]
    for r in rows:
        assert (r["source"], r["license"]) in (("exercisedb", "MIT"), ("opengym", "AGPL-3.0"))
        assert r["images"] == [] and not CJK.search(r["name_en"])
        assert r["name_zh"] == r["name_en"] or r["zh_source"] == "machine"   # Chinese only from the committed translations
        assert r["text_sources"][0]["commit"] == COMMIT and r["text_sources"][0]["source"] == r["source"]
    assert conn.execute("SELECT count(*) AS n FROM exercise_catalog WHERE license IS NULL").fetchone()["n"] == 0   # curated rows too


def test_merge_keeps_what_the_library_already_had(conn, report):
    before = report[0]
    merged = conn.execute("SELECT * FROM exercise_catalog WHERE source = 'curated' AND jsonb_array_length(text_sources) > 0").fetchall()
    assert merged
    for r in merged:
        old = before[r["slug"]]
        assert r["name_zh"] == old["name_zh"] and r["joint_stress"] == old["joint_stress"] and r["images"] == old["images"]
        assert r["cues_zh"] == old["cues_zh"] and r["instructions_zh"] == old["instructions_zh"] and r["source"] == "curated"
        assert r["description_en"] or r["instructions_en"]
        assert r["slug"] not in {"og_" + s["ref"] for s in r["text_sources"]}      # merged, not duplicated as a new row
    assert not any(r["slug"].startswith("og_") for r in merged)


def test_restricted_exercise_variants_keep_their_family_and_tags(conn, report):
    fam = conn.execute("SELECT slug, name_en, joint_stress, family_slug FROM exercise_catalog WHERE slug LIKE 'og\\_%' AND family_slug IS NOT NULL").fetchall()
    assert any(r["family_slug"] == "dips" for r in fam) and any(r["family_slug"] == "upright_row" for r in fam)
    for r in fam:
        want = next((x["joint_stress"] for x in CD.CURATED_EXERCISES if x["slug"] == r["family_slug"]), [])
        assert r["joint_stress"] == want
    row = next(r for r in catalog.catalog_rows(conn) if r["slug"] == fam[0]["slug"])
    assert catalog.format_exercise(row)["status"] == "caution"


def test_flag_is_the_emergency_switch(conn, uid, report):
    _flag(conn, "off")
    assert not any(r["source"] in M.IMPORTED_SOURCES for r in catalog.rows_for(conn, uid))
    assert not any(r["source"] in M.IMPORTED_SOURCES for r in catalog.rows_for(conn, None))
    assert catalog.search(conn, uid, "3/4 sit-up", with_meta=False) == []
    assert catalog.resolve(conn, "og_0001", uid) is None
    _flag(conn, "all")
    assert any(r["source"] in M.IMPORTED_SOURCES for r in catalog.rows_for(conn, uid))
    assert any(r["source"] in M.IMPORTED_SOURCES for r in catalog.rows_for(conn, None))
    hit = catalog.search(conn, uid, "3/4 sit-up", with_meta=False)
    assert hit and hit[0]["slug"] == "og_0001" and hit[0]["name_en"] == "3/4 Sit-up"
    _flag(conn, "off")
    assert catalog.imported_visible(conn, uid) is False
    _flag(conn, "all")


def test_detail_shows_english_text_and_licence(conn, uid, report):
    _flag(conn, "all")
    d = catalog.get_exercise(conn, uid, "og_0001")
    assert d["source"] == "exercisedb" and d["license"] == "MIT" and d["instructions_zh"][0].startswith("Lie flat")
    assert d["cues"] and d["images"] == [] and d["image_urls"] == []
    assert d["equipment"] == ["body only"] and d["primary_muscles"] == ["abdominals"]


def test_search_and_lookup_stay_fast_with_thousands_of_exercises(conn, uid, report):
    _flag(conn, "all")
    assert conn.execute("SELECT count(*) AS n FROM exercise_catalog").fetchone()["n"] > 5000
    catalog.search(conn, uid, "x", with_meta=False)
    for kw in ({"query": "bench press"}, {"query": "卧推"}, {"query": "squat", "limit": 150}, {"muscle": "chest"}, {"equipment": "cable"},
               {"query": "lat pulldown", "muscle": "lats", "equipment": "cable"}):
        t = time.perf_counter()
        out = catalog.search(conn, uid, with_meta=False, **kw)
        assert out and len(out) <= 150
        assert time.perf_counter() - t < 0.5, kw
    t = time.perf_counter()
    assert catalog.resolve_with_candidates(conn, uid, "zzzz no such", with_meta=False)[0] is None
    assert time.perf_counter() - t < 0.5
    assert len(catalog.search(conn, uid, "curl", limit=10_000, with_meta=False)) == 150     # the result size is capped


def test_curated_rows_still_rank_first_and_alternatives_stay_curated(conn, uid, report):
    _flag(conn, "all")
    top = catalog.search(conn, uid, "bench press", with_meta=False)[:3]
    assert top[0]["source"] == "curated"
    alts = catalog.recommend_alternatives(conn, "incline_dumbbell_press", uid)
    assert alts and all(a["source"] == "curated" for a in alts)
    for tpl in P.SPLIT_TEMPLATES.values():          # the weekly templates resolve to the curated exercises, never to an import
        for _dow, _name, rows in tpl["days"]:
            for slug, *_ in rows:
                assert catalog.resolve(conn, slug, uid)["slug"] == slug


def test_muscle_load_recommendations_with_the_big_library(conn, uid, report):
    _flag(conn, "all")
    d = muscle_load.detail(conn, uid, "chest")
    assert 1 <= len(d["recommended"]) <= P.RECOMMEND_LIMIT
    assert d["recommended"][0]["slug"] in {x["slug"] for x in CD.CURATED_EXERCISES}
    _flag(conn, "off")
