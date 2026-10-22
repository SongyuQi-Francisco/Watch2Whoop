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
    ("hypoglycemia_example", {
        "occurred_at": "2026-10-08T12:00", "time_precision": "day", "category": "symptom", "severity": 2,
        "title": "练腿时轻微低血糖感（示例）", "resolved_on": date(2026, 10, 8),
        "note": "示例：空腹训练时出现，很快缓解。练前 1.5–2 小时吃正餐。",
    }),
]

# ---------- Exercises (4.3 protected areas and contraindications, 4.4 V3.0 exercise table, 4.7) ----------
# exercise_ref uses a stable English slug first; the SparkyFitness exercise ID is filled into app_exercise_id later.
_SHOULDER = "肩前侧不适（示例）：推举 / 卧推时肩前卡压"
_PRESS_CUE = "手肘与身体约 45°，不要外展到 90°；优先哑铃和中立握；出现肩前疼痛立刻换动作（只有弹响不疼可以继续）"
_TRAP = "每组留 1–2 次余力；先沉肩再拉；肩膀往耳朵跑就停组"
_KNEE = "膝盖和脚尖同方向；下蹲控制 2 秒"
EXERCISES: list[tuple[str, dict]] = [
    # Never do (4.3)
    ("upright_row", {"name_zh": "杠铃提拉 / 直立划船", "name_en": "Upright Row", "status": "banned",
                     "aliases": ["直立划船", "杠铃提拉", "哑铃提拉", "绳索提拉", "Barbell Upright Row",
                                 "Dumbbell Upright Row", "Cable Upright Row"],
                     "reason": "永久不做：容易练大上斜方；肩内旋位上举（示例）",
                     "cues": "肩中束用哑铃侧平举代替"}),
    ("behind_neck_press", {"name_zh": "颈后推举", "name_en": "Behind the Neck Press", "status": "banned",
                           "aliases": ["颈后推举", "颈后杠铃推举", "Behind-the-Neck Press"],
                           "reason": "永久不做：" + _SHOULDER, "cues": "推举用 landmine press 或轻重量哑铃推举代替"}),
    ("behind_neck_pulldown", {"name_zh": "颈后下拉", "name_en": "Behind the Neck Lat Pulldown", "status": "banned",
                              "aliases": ["颈后下拉", "颈后高位下拉", "Behind-the-Neck Pulldown"],
                              "reason": "永久不做：肩内旋位受压（示例）", "cues": "高位下拉拉到身前锁骨"}),
    ("dips", {"name_zh": "双杠臂屈伸", "name_en": "Dips", "status": "banned",
              "aliases": ["双杠臂屈伸", "臂屈伸", "Chest Dip", "Triceps Dip", "Parallel Bar Dips"],
              "reason": "永久不做：" + _SHOULDER, "cues": "三头用绳索下压，胸用哑铃卧推 / 龙门架夹胸代替"}),
    # Use caution
    ("cable_face_pull", {"name_zh": "绳索面拉 + 外旋", "name_en": "Cable Face Pull", "status": "caution",
                         "aliases": ["面拉", "绳索面拉", "Face Pull"],
                         "reason": "大重量绳索面拉容易变成练上斜方（示例）",
                         "cues": "轻重量，3 × 15；拉到额头高度，结尾「投降」姿势停 1 秒；" + _TRAP}),
    ("dumbbell_shoulder_press", {"name_zh": "哑铃推举", "name_en": "Dumbbell Shoulder Press", "status": "caution",
                                 "aliases": ["哑铃推肩", "推肩", "Seated Dumbbell Press"],
                                 "reason": _SHOULDER, "cues": "只做轻重量，或用 landmine press 代替；" + _PRESS_CUE}),
    ("freestyle_swim", {"name_zh": "自由泳", "name_en": "Freestyle Swimming", "status": "caution",
                        "aliases": ["游泳", "Swimming"], "reason": "自由泳要求肩内旋，肩可能卡压",
                        "cues": "肩有卡压感时放慢、缩短距离，或换蛙泳；有疼痛就停"}),
    # V3.0 Upper A
    ("lat_pulldown", {"name_zh": "高位下拉（正手，略宽于肩）", "name_en": "Lat Pulldown",
                      "cues": "先沉肩，拉到锁骨，手肘往口袋方向；" + _TRAP}),
    ("seated_cable_row_close", {"name_zh": "坐姿划船（窄握 / V 把）", "name_en": "Seated Cable Row (Close Grip)",
                                "cues": "挺胸，肩胛先后收再拉，不耸肩；" + _TRAP}),
    ("dumbbell_bench_press", {"name_zh": "哑铃平板卧推", "name_en": "Dumbbell Bench Press", "status": "caution",
                              "reason": _SHOULDER, "cues": "下放到胸侧，肩胛夹紧贴凳；" + _PRESS_CUE}),
    ("one_arm_dumbbell_row", {"name_zh": "单臂哑铃划船", "name_en": "One-Arm Dumbbell Row", "per_side": True,
                              "cues": "拉向髋部而不是胸口；" + _TRAP}),
    ("dumbbell_lateral_raise", {"name_zh": "哑铃侧平举", "name_en": "Dumbbell Lateral Raise",
                                "cues": "严格不借力，手肘微弯，举到肩高即可；不耸肩"}),
    ("dumbbell_hammer_curl", {"name_zh": "哑铃锤式弯举", "name_en": "Dumbbell Hammer Curl", "cues": "中立握，对肘部友好"}),
    # V3.0 Lower A
    ("goblet_squat", {"name_zh": "高脚杯深蹲", "name_en": "Goblet Squat",
                      "cues": "哑铃贴胸，蹲到大腿平行或略低，膝盖朝脚尖"}),
    ("leg_press", {"name_zh": "腿举", "name_en": "Leg Press",
                   "cues": "下放到膝盖约 90°，腰不离开靠背；高脚位（脚放踏板上方）更多练臀和腘绳肌"}),
    ("dumbbell_romanian_deadlift", {"name_zh": "哑铃罗马尼亚硬拉", "name_en": "Dumbbell Romanian Deadlift",
                                    "status": "caution", "reason": "下背：新手做硬拉类动作容易弓背",
                                    "cues": "先用哑铃、轻重量；屁股往后推，哑铃贴腿，感觉腘绳肌拉伸；全程背部中立"}),
    ("dumbbell_walking_lunge", {"name_zh": "哑铃行走弓步", "name_en": "Dumbbell Walking Lunge", "per_side": True,
                                "status": "caution", "reason": "膝盖", "cues": "前腿发力，躯干直立；" + _KNEE}),
    ("seated_leg_curl", {"name_zh": "坐姿腿弯举", "name_en": "Seated Leg Curl", "cues": "顶峰停 1 秒"}),
    ("standing_calf_raise", {"name_zh": "站姿提踵", "name_en": "Standing Calf Raise", "cues": "最低点停 1 秒"}),
    ("dead_bug", {"name_zh": "Dead bug", "name_en": "Dead Bug", "per_side": True, "cues": "下背始终贴地"}),
    # V3.0 Upper B
    ("incline_dumbbell_press", {"name_zh": "上斜哑铃卧推（30°）", "name_en": "Incline Dumbbell Press",
                                "status": "caution", "aliases": ["上斜卧推"], "reason": _SHOULDER,
                                "cues": "中立握或手肘 45°，对肩最友好的推胸；" + _PRESS_CUE}),
    ("close_grip_underhand_pulldown", {"name_zh": "反手窄距高位下拉", "name_en": "Close-Grip Underhand Pulldown",
                                       "aliases": ["反手窄距下拉"], "cues": "拉到下巴，感觉背阔下部；" + _TRAP}),
    ("seated_cable_row_wide", {"name_zh": "器械 / 绳索坐姿划船（宽握）", "name_en": "Seated Cable Row (Wide Grip)",
                               "cues": "拉到下胸，练中下斜方和菱形肌，对抗圆肩；" + _TRAP}),
    ("landmine_press", {"name_zh": "Landmine press（单臂）", "name_en": "Single-Arm Landmine Press", "per_side": True,
                        "cues": "斜向上推，替代哑铃推举，肩部压力小"}),
    ("cable_fly_mid", {"name_zh": "龙门架夹胸（中位）", "name_en": "Cable Fly (Mid)", "aliases": ["龙门架中胸"],
                       "cues": "手肘微弯固定，胸前合拢停 1 秒"}),
    ("cable_triceps_pushdown", {"name_zh": "绳索下压（三头）", "name_en": "Cable Triceps Pushdown",
                                "cues": "大臂贴身体不动"}),
    ("band_external_rotation", {"name_zh": "弹力带外旋", "name_en": "Band External Rotation", "per_side": True,
                                "cues": "轻弹力带；肩袖保护"}),
    # V3.0 Lower B
    ("barbell_hip_thrust", {"name_zh": "杠铃臀推", "name_en": "Barbell Hip Thrust",
                            "cues": "下巴微收，顶峰夹臀 1 秒，肋骨不外翻"}),
    ("bulgarian_split_squat", {"name_zh": "保加利亚分腿蹲", "name_en": "Bulgarian Split Squat", "per_side": True,
                               "status": "caution",
                               "reason": "膝盖（示例）",
                               "cues": "前脚离凳一大步，身体略前倾练臀；" + _KNEE}),
    ("seated_leg_extension", {"name_zh": "坐姿腿屈伸", "name_en": "Seated Leg Extension", "cues": "顶峰停 1 秒，下放控制"}),
    ("back_extension_45", {"name_zh": "俯身 / 45° 背部伸展", "name_en": "45° Back Extension",
                           "cues": "背部中立，用臀发力起身"}),
    ("plank", {"name_zh": "平板支撑", "name_en": "Plank", "cues": "臀部不塌不翘"}),
]


# ---------- Journal backfill ----------
JOURNAL_BACKFILL: list[tuple[str, str, float, str]] = [
    ("2026-10-13", "training_upper", 1, "训练日志：上肢（示例）"),
    ("2026-10-08", "training_legs", 1, "训练日志：腿（示例）"),
    ("2026-10-08", "low_blood_sugar", 1, "练腿时轻微低血糖感（示例）"),
]
