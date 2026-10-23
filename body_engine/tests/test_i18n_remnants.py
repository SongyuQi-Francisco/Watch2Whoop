"""What an English user could still meet in Chinese (issue #64, the part the literal scanners of test_i18n*.py cannot see):
texts that come from the server's data tables and are shown as they are, and the vocabulary the page translates at render
time. No database: the tables, the pure builders and fakes are enough.

- the curated exercise library (training/catalog_data.py): every cue / step list has English (training/catalog_en.py), every
  label of the catalog's vocabulary and every built-in plan template text has an entry in the page's i18n-en.js (the page
  shows them through tr(), because the stored plan keeps the Chinese names the AI and the training log use);
- the training readiness text and the prescription text follow the request's language;
- the back end's name (ai/naming.py) is replaced by the English name in an English request.
"""
from datetime import date

import pytest

from body_engine.ai import naming
from body_engine.i18n import use_lang
from body_engine.training import catalog, catalog_data as CD, catalog_en as CE, params as TP, plans

from test_i18n import CJK, EN_TEXT


# ---------------------------------------------------------------- the exercise library
def test_every_curated_exercise_has_english_cues():
    slugs = [e["slug"] for e in CD.CURATED_EXERCISES]
    assert set(CE.CUES_EN) == set(slugs), {"no English": sorted(set(slugs) - set(CE.CUES_EN)), "no exercise": sorted(set(CE.CUES_EN) - set(slugs))}
    stepped = {e["slug"] for e in CD.CURATED_EXERCISES if e["instructions_zh"] != [e["cues_zh"]]}
    assert set(CE.INSTRUCTIONS_EN) == stepped, "an exercise with its own steps needs them in English (and only those)"
    for e in CD.CURATED_EXERCISES:
        if e["slug"] in stepped:
            assert len(CE.INSTRUCTIONS_EN[e["slug"]]) == len(e["instructions_zh"]), e["slug"]
    texts = [*CE.CUES_EN.values(), *(s for steps in CE.INSTRUCTIONS_EN.values() for s in steps)]
    assert all(t.strip() and not CJK.search(t) for t in texts)


def test_exercise_view_follows_the_language():
    rows = catalog._curated_rows()
    plain, stepped = rows["barbell_bench_press"], rows["dumbbell_bench_press"]
    zh = catalog.format_exercise(plain, None, detail=True)
    assert CJK.search(zh["cues"]) and zh["instructions_zh"] == [plain["cues_zh"]]
    with use_lang("en"):
        en = catalog.format_exercise(plain, None, detail=True)
        assert en["cues"] == CE.CUES_EN["barbell_bench_press"] and en["instructions_zh"] == [en["cues"]]
        assert catalog.format_exercise(stepped, None, detail=True)["instructions_zh"] == CE.INSTRUCTIONS_EN["dumbbell_bench_press"]
        assert catalog.format_exercise(plain, {"status": "active", "cues": "我自己的提示"}, detail=True)["cues"] == "我自己的提示"   # the user's own cue stays
        assert catalog.format_exercise(plain, None)["name_en"] == "Barbell Bench Press"
    assert catalog.format_exercise(plain, None, detail=True)["cues"] == zh["cues"]   # back to zh outside the context


@pytest.mark.parametrize("table", ["MUSCLE_ZH", "GROUP_ZH", "EQUIPMENT_ZH", "PATTERN_ZH", "JOINT_STRESS_ZH"])
def test_catalog_vocabulary_has_english(table):
    """The page shows the catalog's labels through tr(): every one needs an entry in i18n-en.js."""
    missing = sorted({v for v in getattr(CD, table).values() if v not in EN_TEXT})
    assert not missing, f"add to i18n-en.js: {missing}"


def test_plan_templates_have_english():
    """A saved plan keeps the template's Chinese names; the page shows them through tr() (sname())."""
    wanted = {"自定义周训练计划"}
    for tpl in TP.SPLIT_TEMPLATES.values():
        wanted |= {tpl["name"], tpl["summary"], *(name for _dow, name, _ex in tpl["days"])}
    missing = sorted(w for w in wanted if w not in EN_TEXT)
    assert not missing, f"add to i18n-en.js: {missing}"


# ---------------------------------------------------------------- texts built per request
class _Conn:
    """Just enough of a connection for plans.readiness_advice: every query answers with `row`."""
    def __init__(self, row):
        self.row = row

    def execute(self, *_a, **_k):
        return self

    def fetchone(self):
        return self.row


@pytest.mark.parametrize("recovery,tier", [(30, "red"), (55, "yellow"), (88, "green")])
def test_readiness_follows_the_language(recovery, tier):
    day = date(2026, 10, 20)
    conn = _Conn({"date": day, "recovery": recovery, "sleep_debt_min": 0})
    zh = plans.readiness_advice(conn, 1, day)
    with use_lang("en"):
        en = plans.readiness_advice(conn, 1, day)
    assert zh["tier"] == en["tier"] == tier and CJK.search(zh["label"]) and CJK.search(zh["advice"])
    assert not CJK.search(en["label"]) and not CJK.search(en["advice"]) and str(recovery) in en["advice"]
    with use_lang("en"):
        none = plans.readiness_advice(_Conn(None), 1, day)
    assert none["tier"] == "unknown" and not CJK.search(none["label"] + none["advice"])


def test_prescription_text_follows_the_language():
    assert plans.format_sets_text(3, None, None, 30) == "3 组 × 30 秒"
    assert plans.format_sets_text(4, 8, 12, None, 20, "每侧") == "4 × 8–12 · 20 kg（每侧）"
    with use_lang("en"):
        assert plans.format_sets_text(3, None, None, 30) == "3 sets × 30 s"
        assert plans.format_sets_text(4, 8, 12, None, 20, "每侧") == "4 × 8–12 · 20 kg (per side)"
        assert plans.format_sets_text(None, None, None) == "1 set" and plans.format_sets_text(4, None, None) == "4 sets"


def test_sign_in_mail_subject_follows_the_language():
    from body_engine.account import mailer
    subject, body = mailer.code_message("654321", 10, "en")
    assert subject == "Watch2Whoop sign-in code: 654321" and body.index("sign-in code") < body.index("验证码")   # English first, Chinese below
    assert "登录验证码" in mailer.code_message("654321", 10)[0]


def test_back_end_name_in_an_english_request():
    text = "SparkyFitness: User 1 is not connected to SparkyFitness"
    assert naming.scrub(text, output=True) == "底层数据库: User 1 is not connected to our database"   # unchanged for Chinese requests
    with use_lang("en"):
        assert naming.scrub(text, output=True) == "Our database: User 1 is not connected to our database"
        assert naming.scrub("Logged in SparkyFitness", output=True) == "Logged in our database"
        assert naming.scrub("在 SparkyFitness 里记了", output=True) == "在 底层数据库 里记了"   # next to Chinese: the Chinese name
