"""Machine Chinese for the English-only exercises (issue #248): glossary, the committed data file, applying it in the importers
(never over human Chinese, idempotent), the exercise_zh_machine flag, and the quality grade.
Own database body_test_exercise_zh (dropped and recreated on every run); skipped if Postgres is unreachable."""
import json
import os
import re

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.training import catalog, quality as Q, translations as T
from body_engine.training.opengym import COMMIT, importer

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_EXERCISE_ZH", "body_test_exercise_zh")
CJK = re.compile(r"[一-鿿]")
ASCII_OK = re.compile(r"^(TRX|V 把|L 字支撑|T 杆划船|V 字两头起|PVC 管|Pallof 抗旋转推)$")


# ---------------------------------------------------------------- glossary
def test_glossary_has_one_chinese_rendering_per_term():
    g = T.glossary()
    assert len(g) > 400 and g["bench press"][0] == "卧推" and g["romanian deadlift"][0] == "罗马尼亚硬拉"
    for en, zh in g.items():
        assert en == en.lower().strip() and zh, en
        assert all(CJK.search(x) or ASCII_OK.match(x) for x in zh), (en, zh)


def test_terms_and_misses_use_the_longest_term():
    assert T.terms_in("Dumbbell Romanian Deadlift") == ["dumbbell", "romanian deadlift"]
    assert T.terms_in("Crossover Reverse Lunge") == ["crossover", "reverse lunge"]
    assert T.glossary_misses("Dumbbell Romanian Deadlift", "哑铃罗马尼亚硬拉") == []
    assert T.glossary_misses("Dumbbell Romanian Deadlift", "哑铃直腿硬拉") == ["romanian deadlift"]


# ---------------------------------------------------------------- data file
def test_committed_lines_are_well_formed():
    lines = T.load()
    assert len(lines) >= 50
    for slug, e in lines.items():
        assert e["slug"] == slug and re.fullmatch(r"[0-9a-f]{16}", e["src_sha"]) and e["zh_source"] == "machine"
        assert CJK.search(e["name_zh"]) and isinstance(e["instructions_zh"], list) and e["model"]
        assert e["glossary_misses"] == T.glossary_misses(e["name_en"], e["name_zh"])
        blob = json.dumps(e, ensure_ascii=False).lower()
        assert not re.search(r"gymvisual|gym visual|catalogue/media|\.(gif|png|jpe?g|webp|mp4)\b", blob), slug
    assert sum(1 for e in lines.values() if e["glossary_misses"]) <= len(lines) * 0.05   # a person looks at the rest


def test_apply_only_when_the_english_is_unchanged_and_never_over_human():
    row = {"slug": "x1", "name_zh": "Goblet Squat", "name_en": "Goblet Squat", "description_en": "A squat.", "instructions_en": ["Stand.", "Squat."],
           "instructions_zh": [], "cues_zh": None, "zh_source": None}
    table = {"x1": {"slug": "x1", "src_sha": T.src_sha(row), "name_zh": "高脚杯深蹲", "description_zh": "一种深蹲。",
                    "instructions_zh": ["站立。", "下蹲。"], "zh_source": "machine"}}
    r = dict(row)
    assert T.apply(r, table) == "applied"
    assert (r["name_zh"], r["cues_zh"], r["instructions_zh"], r["zh_source"]) == ("高脚杯深蹲", "一种深蹲。", ["站立。", "下蹲。"], "machine")
    assert T.english_view(r)["name_zh"] == "Goblet Squat" and T.english_view(r)["instructions_zh"] == []
    changed = {**row, "instructions_en": ["Stand tall.", "Squat."]}
    assert T.apply(changed, table) == "stale" and changed["name_zh"] == "Goblet Squat"
    human = {**row, "name_zh": "高脚杯蹲", "zh_source": "human"}
    assert T.apply(human, table) is None and human["name_zh"] == "高脚杯蹲"


# ---------------------------------------------------------------- quality grade
def test_grade_rules():
    full = {"slug": "a", "name_zh": "哑铃卧推", "name_en": "Dumbbell Bench Press", "instructions_zh": ["躺下。", "推起。"],
            "instructions_en": ["Lie down.", "Press."], "primary_muscles": ["chest"], "secondary_muscles": ["triceps"],
            "equipment": ["dumbbell"], "category": "strength", "source": "free-exercise-db"}
    g = Q.grade(full, media_ok=True)
    assert g["score"] == 100 and g["passed"] and g["missing"] == []
    no_media = Q.grade(full, media_ok=False)
    assert not no_media["passed"] and "media" in no_media["missing"] and no_media["score"] == 75
    assert not Q.grade({**full, "name_zh": full["name_en"]}, media_ok=True)["passed"]
    assert not Q.grade(full, media_ok=True, duplicate_of="b")["passed"]
    v = Q.grade({**full, "name_en": "Dumbbell Bench Press V. 2"}, media_ok=True)
    assert v["variant"] and v["score"] == 90 and v["passed"]
    cue_only = Q.grade({**full, "instructions_zh": ["一句要点"], "instructions_en": [], "cues_zh": "一句要点", "description_en": "A cue."}, media_ok=True)
    assert cue_only["passed"] and cue_only["score"] == 90 and "steps" in cue_only["missing"]


def test_duplicates_keep_the_best_row():
    rows = [{"slug": "og_1", "name_en": "Lever Shoulder Press", "equipment": ["machine"], "source": "exercisedb"},
            {"slug": "machine_shoulder_press", "name_en": "Machine Shoulder Press", "equipment": ["machine"], "source": "curated", "zh_source": "human"},
            {"slug": "og_2", "name_en": "Side to Side Hop V. 2", "equipment": ["body only"], "source": "opengym"},
            {"slug": "og_3", "name_en": "Side to Side Hop", "equipment": ["body only"], "source": "opengym"},
            {"slug": "og_4", "name_en": "Dumbbell Shoulder Press", "equipment": ["dumbbell"], "source": "opengym"}]
    assert Q.duplicates(rows) == {"og_1": "machine_shoulder_press", "og_2": "og_3"}


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def conn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    with psycopg.connect(Settings(db_name=TEST_DB).dsn, row_factory=dict_row, autocommit=True) as c:
        migrate(c)
        catalog.ensure_seeded(c)
        yield c


ENTRY = {"id": "9001", "name": "dumbbell goblet hold", "bodyPart": "upper legs", "equipment": "dumbbell", "target": "quads",
         "secondaryMuscles": ["glutes"], "category": "strength", "description": "Hold a dumbbell at the chest.",
         "instructions": ["Stand tall.", "Hold the dumbbell."], "textSource": "opengym"}


def _table(conn):
    src = {"name_en": "Dumbbell Goblet Hold", "description_en": ENTRY["description"], "instructions_en": ENTRY["instructions"]}
    return {"og_9001": {"slug": "og_9001", "src_sha": T.src_sha(src), "name_zh": "哑铃高脚杯保持", "description_zh": "胸前握住哑铃。",
                        "instructions_zh": ["站直。", "握住哑铃。"], "zh_source": "machine"}}


def _flag(conn, state):
    flags.set_flag(conn, T.FLAG, state, actor="test")
    flags.invalidate()


def test_import_applies_translations_behind_the_flag(conn, monkeypatch):
    monkeypatch.setattr(T, "entries", lambda: _table(conn))
    snap = ({"commit": COMMIT}, [ENTRY])
    rep = importer.run(conn, snap)
    assert rep["machine_chinese"] == 1
    r = conn.execute("SELECT * FROM exercise_catalog WHERE slug = 'og_9001'").fetchone()
    assert (r["name_zh"], r["zh_source"], r["instructions_zh"], r["cues_zh"]) == ("哑铃高脚杯保持", "machine", ["站直。", "握住哑铃。"], "胸前握住哑铃。")
    assert r["name_en"] == "Dumbbell Goblet Hold" and r["instructions_en"] == ["Stand tall.", "Hold the dumbbell."]
    assert conn.execute("SELECT count(*) AS n FROM exercise_catalog WHERE zh_source = 'human'").fetchone()["n"] >= 100   # curated
    rev = r["rev"]
    importer.run(conn, snap)                                   # idempotent: same data, same rev
    assert conn.execute("SELECT rev FROM exercise_catalog WHERE slug = 'og_9001'").fetchone()["rev"] == rev
    flags.set_flag(conn, "exercise_opengym", "all", actor="test")
    _flag(conn, "off")
    off = next(x for x in catalog.rows_for(conn, None) if x["slug"] == "og_9001")
    assert off["name_zh"] == "Dumbbell Goblet Hold" and not catalog.search(conn, None, "高脚杯保持", with_meta=False)
    assert catalog.get_exercise(conn, None, "og_9001")["instructions_zh"] == ["Stand tall.", "Hold the dumbbell."]
    _flag(conn, "all")
    on = catalog.search(conn, None, "高脚杯保持", with_meta=False)
    assert on and on[0]["slug"] == "og_9001" and on[0]["name_zh"] == "哑铃高脚杯保持"
    d = catalog.get_exercise(conn, None, "og_9001")
    assert d["instructions_zh"] == ["站直。", "握住哑铃。"] and d["zh_source"] == "machine"
    _flag(conn, "off")


def test_changed_english_drops_the_old_translation(conn, monkeypatch):
    monkeypatch.setattr(T, "entries", lambda: _table(conn))
    changed = {**ENTRY, "instructions": ["Stand tall.", "Hold the dumbbell close."]}
    rep = importer.run(conn, ({"commit": COMMIT}, [changed]))
    assert rep["machine_chinese"] == 0
    r = conn.execute("SELECT name_zh, zh_source, instructions_zh FROM exercise_catalog WHERE slug = 'og_9001'").fetchone()
    assert (r["name_zh"], r["zh_source"], r["instructions_zh"]) == ("Dumbbell Goblet Hold", None, [])


def test_human_chinese_is_never_overwritten(conn, monkeypatch):
    conn.execute("UPDATE exercise_catalog SET name_zh = '人工名', zh_source = 'human' WHERE slug = 'og_9001'")
    monkeypatch.setattr(T, "entries", lambda: _table(conn))
    importer.run(conn, ({"commit": COMMIT}, [ENTRY]))
    r = conn.execute("SELECT name_zh, zh_source FROM exercise_catalog WHERE slug = 'og_9001'").fetchone()
    assert (r["name_zh"], r["zh_source"]) == ("人工名", "human")


def test_free_db_rows_get_english_steps_and_translation():
    raw = {"id": "Incline_Push-Up", "name": "Incline Push-Up", "category": "strength", "equipment": "body only",
           "primaryMuscles": ["chest"], "instructions": ["Stand facing a bench.", "Lower your chest."], "images": []}
    row = catalog.convert_free_exercise_db_entry(raw)
    assert row["instructions_en"] == ["Stand facing a bench.", "Lower your chest."] and row["zh_source"] in (None, "machine")
    if row["zh_source"] is None:   # no committed line for this exact text: stays English
        assert row["name_zh"] == "Incline Push-Up" and row["instructions_zh"] == row["instructions_en"]
