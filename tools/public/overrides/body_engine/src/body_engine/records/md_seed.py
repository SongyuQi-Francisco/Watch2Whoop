"""Example historical records for `body-engine import-records` (fictional, for the public repository).

The original project transcribed its owner's notes (body-management.md) into this module: supplements, a health check,
body events, exercise rules and a journal backfill. Replace the lists below with your own records, or leave them as an
example of the format. Every entry has a stable import_key, so re-importing creates no duplicates and does not
overwrite later edits made in the database.
"""
from datetime import date

SOURCE = "body-management.md"

# ---------- Supplements ----------
SUPPLEMENTS: list[tuple[str, dict]] = [
    ("creatine", {
        "name": "肌酸", "dose": "5g", "timing": "每天，随餐", "ingredients": "一水肌酸",
        "status": "active", "started_on": date(2026, 9, 1), "journal_key": "supp_creatine",
        "rationale": "增肌（示例）",
        "advice": "每天 3–5g 即可，不需要冲击期；多喝水。抽血前告诉医生在吃肌酸（血肌酐会偏高）。",
    }),
    ("fish_oil", {
        "name": "鱼油（示例）", "dose": "1 粒", "timing": "每天，随餐", "ingredients": "EPA + DHA",
        "status": "active", "journal_key": "supp_fish_oil", "rationale": "示例",
        "advice": "每周吃 2 顿鱼的话 1 粒就够。",
    }),
    ("liver_detox", {
        "name": "护肝片（示例）", "dose": "1 片", "timing": "每天，随餐", "status": "paused",
        "rationale": "示例：复查前先停，避免干扰肝功能结果",
        "advice": "复查前先停；要不要继续问 GP。",
    }),
]

# ---------- Lab results / health check ----------
EXAM_2026_08 = date(2026, 8, 30)
LAB_SOURCE = "示例体检报告"
LAB_DATE_NOTE = "示例数据。"
LABS: list[tuple[str, dict]] = [
    ("uric_acid", {"test_name": "尿酸", "category": "blood", "value": 400, "unit": "μmol/L", "ref_high": 420,
                   "flag": "normal", "note": "示例数据"}),
    ("triglycerides", {"test_name": "甘油三酯", "category": "blood", "value": 1.4, "unit": "mmol/L", "ref_high": 1.7,
                       "flag": "normal", "note": "示例数据"}),
    ("alt", {"test_name": "ALT（谷丙转氨酶）", "category": "blood", "value": 30, "unit": "U/L", "ref_high": 40,
             "flag": "normal", "note": "示例数据"}),
]

# ---------- Body events ----------
EVENTS: list[tuple[str, dict]] = [
    ("exam_2026_08", {
        "occurred_at": "2026-08-30T12:00", "time_precision": "approx", "category": "medical",
        "title": "体检（示例）", "note": "示例数据。",
    }),
    ("hypoglycemia_2026_10_15", {
        "occurred_at": "2026-10-15T12:00", "time_precision": "day", "category": "symptom", "severity": 2,
        "title": "练腿时轻微低血糖感（示例）", "resolved_on": date(2026, 10, 15),
        "note": "示例：空腹训练时出现，很快缓解。练前 1.5–2 小时吃正餐。",
    }),
]

# @@EXERCISES@@

# ---------- Journal backfill ----------
JOURNAL_BACKFILL: list[tuple[str, str, float, str]] = [
    ("2026-10-13", "training_upper", 1, "训练日志：上肢（示例）"),
    ("2026-10-15", "training_legs", 1, "训练日志：腿（示例）"),
    ("2026-10-15", "low_blood_sugar", 1, "练腿时轻微低血糖感（示例）"),
]
