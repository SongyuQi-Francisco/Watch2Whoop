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


# ---------------------------------------------------------------- the offline batch tool (Claude requests, no call)
def test_claude_request_shape():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("translate_exercises", Path(__file__).resolve().parents[2] / "tools" / "translate_exercises.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    row = {"slug": "og_1", "name_en": "Dumbbell Romanian Deadlift", "name_zh": "Dumbbell Romanian Deadlift", "description_en": "A hinge.",
           "instructions_en": ["Stand.", "Hinge."], "instructions_zh": []}
    p = tool.claude_params([row], tool.CLAUDE_MODEL)
    assert p["model"] == "claude-sonnet-5-5" and p["thinking"] == {"type": "between_tools"}
    assert "temperature" not in p and p["output_config"]["format"]["type"] == "json_schema"
    assert p["output_config"]["format"]["schema"]["additionalProperties"] is False
    (sysblock,) = p["system"]
    assert "romanian deadlift = 罗马尼亚硬拉" in sysblock["text"] and json.loads(p["messages"][0]["content"])["items"][0]["id"] == "og_1"
    assert sysblock["cache_control"] == {"type": "ephemeral"}
    other = tool.claude_params([{**row, "slug": "og_2", "name_en": "Cable Fly"}], tool.CLAUDE_MODEL, ttl="1h")
    assert other["system"][0]["text"] == sysblock["text"] and other["system"][0]["cache_control"]["ttl"] == "1h"   # cacheable prefix
    est = tool.estimate([row], 20, "claude")
    assert est["requests"] == 1 and est["cached_prefix_tokens"] > 1000
    assert est["cost_usd_batch"] <= est["cost_usd_batch_if_no_cache_hits"] and est["cost_usd_batch"] <= est["cost_usd_sync"]


# ---------------------------------------------------------------- licensed media + quality gate (database)
def _png(tag: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag.encode()


def test_media_fetch_and_licensed_view(conn, tmp_path, monkeypatch):
    from body_engine.training import media_licensed as ML
    monkeypatch.setenv("BODY_MEDIA_DIR", str(tmp_path))
    matches = tmp_path / "m.json"
    matches.write_text(json.dumps({"everkinetic_commit": "a" * 40, "matches": [
        {"slug": "goblet_squat", "source": "wger", "source_id": 7, "confidence": "high", "images_usable": 1},
        {"slug": "goblet_squat", "source": "everkinetic", "source_id": "0042", "confidence": "high", "images_usable": 2},
        {"slug": "leg_press", "source": "wger", "source_id": 8, "confidence": "medium", "images_usable": 1}]}))
    info = {"images": [
        {"id": 1, "uuid": "u1", "image": "https://wger.de/media/u1.png", "is_main": True, "license": 2, "license_author": "Ann", "is_ai_generated": False},
        {"id": 2, "uuid": "u2", "image": "https://wger.de/media/u2.png", "license": 2, "license_author": "", "author_history": [], "is_ai_generated": False},
        {"id": 3, "uuid": "u3", "image": "https://wger.de/media/u3.png", "license": 2, "license_author": "Bot", "is_ai_generated": True},
        {"id": 4, "uuid": "u4", "image": "https://evil.example/u4.png", "license": 2, "license_author": "X", "is_ai_generated": False}]}
    calls = []

    def fake_get(url, timeout=60):
        calls.append(url)
        return json.dumps(info).encode() if "/api/v2/" in url else _png(url)
    rep = ML.run(conn, matches=matches, media=tmp_path, get=fake_get)
    assert rep["updated"] == 1 and rep["failed"] == []
    media = conn.execute("SELECT media FROM exercise_catalog WHERE slug = 'goblet_squat'").fetchone()["media"]
    assert [m["file"] for m in media] == ["licensed/wger/u1.png", "licensed/everkinetic/0042-relaxation.png", "licensed/everkinetic/0042-tension.png"]
    assert media[0]["author"] == "Ann" and media[0]["license"] == "CC-BY-SA-4.0" and media[1]["author"].startswith("Everkinetic")
    assert not any("u2" in c or "u3" in c or "evil" in c for c in calls)        # no author / AI-generated / other host: not fetched
    n = len(calls)
    again = ML.run(conn, matches=matches, media=tmp_path, get=fake_get)
    assert again["updated"] == 0 and again["unchanged"] == 1 and len(calls) == n + 1   # only the wger metadata again, no picture
    flags.set_flag(conn, "exercise_media_licensed", "off", actor="test")
    flags.invalidate()
    row = next(r for r in catalog.rows_for(conn, None) if r["slug"] == "goblet_squat")
    assert not row.get("_licensed") and "media_credits" not in catalog.get_exercise(conn, None, "goblet_squat")
    flags.set_flag(conn, "exercise_media_licensed", "all", actor="test")
    flags.invalidate()
    d = catalog.get_exercise(conn, None, "goblet_squat")
    assert [u.rsplit("/exercises/", 1)[1] for u in d["image_urls"]] == [m["file"] for m in media]
    assert d["media_credits"][0]["author"] == "Ann" and d["no_demo"] is False
    lp = next(r for r in catalog.catalog_rows(conn) if r["slug"] == "leg_press")   # medium match only: its free-exercise-db pictures
    for img in lp["images"]:
        (tmp_path / "exercises" / img).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "exercises" / img).write_bytes(b"\xff\xd8\xff")
    other = catalog.get_exercise(conn, None, "leg_press")    # kept while testing (owner's decision B), credited as such
    assert other["image_urls"] and other["no_demo"] is False
    assert {c["source"] for c in other["media_credits"]} == {"free-exercise-db"} and "出处不明" in other["media_credits"][0]["license"]
    flags.set_flag(conn, "exercise_media_free_db_off", "all", actor="test")   # the one-step switch before commercial use
    flags.invalidate()
    other = catalog.get_exercise(conn, None, "leg_press")
    assert other["image_urls"] == [] and other["no_demo"] is True and catalog.shown_images(conn, lp) == []
    assert catalog.get_exercise(conn, None, "goblet_squat")["image_urls"]          # licensed pictures stay
    flags.set_flag(conn, "exercise_media_licensed", "off", actor="test")         # the kill switch works without the other flag too
    flags.invalidate()
    assert catalog.get_exercise(conn, None, "leg_press")["no_demo"] is True and catalog.shown_images(conn, lp) == []
    flags.set_flag(conn, "exercise_media_free_db_off", "off", actor="test")
    flags.invalidate()
    assert catalog.shown_images(conn, lp) == lp["images"] and "media_credits" not in catalog.get_exercise(conn, None, "leg_press")


def test_quality_gate_hides_rows_below_the_bar(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("BODY_MEDIA_DIR", str(tmp_path))
    (tmp_path / "exercises" / "licensed" / "x").mkdir(parents=True)
    (tmp_path / "exercises" / "licensed" / "x" / "a.png").write_bytes(_png("a"))
    conn.execute("""UPDATE exercise_catalog SET media = '[{"file": "licensed/x/a.png", "source": "x"}]'::jsonb, updated_at = now() WHERE slug = 'goblet_squat'""")
    flags.set_flag(conn, "exercise_media_licensed", "all", actor="test")
    flags.set_flag(conn, "exercise_quality_gate", "off", actor="test")
    flags.invalidate()
    before = {x["slug"] for x in catalog.search(conn, None, "squat", with_meta=False, limit=150)}
    assert "goblet_squat" in before and len(before) > 1
    flags.set_flag(conn, "exercise_quality_gate", "all", actor="test")
    flags.invalidate()
    after = {x["slug"] for x in catalog.search(conn, None, "squat", with_meta=False, limit=150)}
    assert after == {"goblet_squat"}                                   # the only one with a licensed picture
    exact = catalog.search(conn, None, "Hack Squat", with_meta=False)   # below the bar, but its exact name still finds it
    assert exact and exact[0]["slug"] == "hack_squat"
    assert {x["slug"] for x in catalog.search(conn, None, None, with_meta=False, limit=150)} == {"goblet_squat"}
    assert all(a["slug"] == "goblet_squat" for a in catalog.recommend_alternatives(conn, "leg_press", None, with_meta=False))
    assert catalog.get_exercise(conn, None, "hack_squat")["slug"] == "hack_squat"   # still in the library
    for f in ("exercise_quality_gate", "exercise_media_licensed"):
        flags.set_flag(conn, f, "off", actor="test")
    flags.invalidate()


def test_equipment_labels_have_chinese_and_english_behind_the_flag():
    import re as _re
    from pathlib import Path
    from body_engine.training import catalog_data as CD
    from body_engine.training.opengym import mapping as M
    en = (Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static" / "i18n-en.js").read_text(encoding="utf-8")
    keys = set(_re.findall(r'^\s*"((?:[^"\\]|\\.)*)"\s*:', en, _re.M))
    raw = set(M.EQUIPMENT) | {"weighted", "suspension trainer", "weight plate", "rope", "assisted", "landmine", "bosu ball", "sandbag",
                              "stationary bike", "elliptical machine", "wheel roller", "macebell", "tire", "hammer", "upper body ergometer",
                              "skierg machine", "stepmill machine"}   # every equipment name the pinned snapshot carries
    labels = {**CD.EQUIPMENT_ZH_EXTRA, **CD.EQUIPMENT_ZH}
    for e in raw:
        key = M.EQUIPMENT.get(e, e)
        assert key in labels, e
        assert labels[key] in keys, labels[key]                       # an English label for tr()
    row = {"slug": "x", "name_zh": "x", "name_en": "x", "category": "strength", "modality": "weight_reps", "equipment": ["sandbag"],
           "primary_muscles": [], "secondary_muscles": []}
    assert catalog.format_exercise(row)["equipment_zh"] == ["沙袋"]
    assert catalog.format_exercise(T.english_view(row))["equipment_zh"] == ["sandbag"]   # flag off: as before


def test_curated_steps_file_and_view():
    from body_engine.training import catalog_data as CD
    st = T.curated_steps()
    curated = {x["slug"]: x for x in CD.CURATED_EXERCISES}
    assert len(st) >= 90 and set(st) <= set(curated)
    for slug, v in st.items():
        assert 3 <= len(v["zh"]) == len(v["en"]) <= 6, slug
        assert all(CJK.search(s) for s in v["zh"]) and not any(CJK.search(s) for s in v["en"]), slug
    for slug in ("upright_row", "behind_neck_press", "behind_neck_pulldown", "dips"):   # restricted: a swap hint in both languages
        assert any("肩部不适" in s for s in st[slug]["zh"]) and any("shoulder" in s.lower() for s in st[slug]["en"]), slug
    row = {"slug": "upright_row", "source": "curated", "instructions_zh": [curated["upright_row"]["cues_zh"]], "instructions_en": []}
    v = T.with_curated_steps(row)
    assert v["instructions_zh"] == st["upright_row"]["zh"] and v["steps_source"] == "ai" and row["instructions_zh"] != v["instructions_zh"]
    assert T.with_curated_steps({**row, "source": "free-exercise-db"}) is not None


def test_curated_steps_flag(conn):
    flags.set_flag(conn, T.STEPS_FLAG, "off", actor="test")
    flags.invalidate()
    slug = next(iter(T.curated_steps()))
    off = catalog.get_exercise(conn, None, slug)
    assert len(off["instructions_zh"]) <= 1 and off.get("steps_source") is None
    flags.set_flag(conn, T.STEPS_FLAG, "all", actor="test")
    flags.invalidate()
    on = catalog.get_exercise(conn, None, slug)
    assert on["instructions_zh"] == T.curated_steps()[slug]["zh"] and on["steps_source"] == "ai"
    from body_engine.i18n import use_lang
    with use_lang("en"):
        assert catalog.get_exercise(conn, None, slug)["instructions_zh"] == T.curated_steps()[slug]["en"]   # the English page
    flags.set_flag(conn, T.STEPS_FLAG, "off", actor="test")
    flags.invalidate()
