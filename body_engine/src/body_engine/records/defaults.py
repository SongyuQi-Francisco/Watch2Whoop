"""Default journal items every user has (system design section 7 + rules from body-management.md).

Date = the day the behavior happened; analysis looks at the next day's outcome. Supplement journal items (supp_*) follow their supplements, see md_seed.py / store.add_supplement.
(key, Chinese name, kind, unit, category, description)
"""
DEFAULT_JOURNAL_ITEMS: list[tuple[str, str, str, str | None, str, str]] = [
    # Diet
    ("caffeine", "喝了咖啡 / 含咖啡因饮料", "bool", None, "diet", "咖啡、茶、能量饮料、预训练补剂都算"),
    ("caffeine_after_noon", "中午 12 点后摄入咖啡因", "bool", None, "diet", "规则：12 点后不喝（6.2）"),
    ("alcohol", "喝酒", "bool", None, "diet", "尿酸、脂肪肝都要避免"),
    ("late_dinner", "晚饭太晚（睡前 3 小时内吃正餐）", "bool", None, "diet", "睡前饿了喝牛奶 / 酸奶不算"),
    ("red_meat_g", "红肉（生重）", "number", "g", "diet", "牛羊猪瘦肉都算；每周 ≤ 250g，牛肋条 ≤ 1 次 / 周"),
    ("water_l", "喝水量", "number", "L", "diet", "吃肌酸期间目标 3–3.5L"),
    ("underfed_training", "练前没吃够（离上一餐 > 4 小时且没加餐）", "bool", None, "diet", "规则见 4.2.1"),
    # Training
    ("training_upper", "练上肢（力量）", "bool", None, "training", "胸、背、肩、手臂"),
    ("training_legs", "练腿（力量）", "bool", None, "training", "下肢 A / B，或任何下肢力量动作"),
    ("incline_walk", "爬坡", "bool", None, "training", "跑步机爬坡有氧"),
    ("basketball", "打篮球", "bool", None, "training", "算中高强度训练，第二天不练下肢"),
    ("swimming", "游泳", "bool", None, "training", "肩卡压时换蛙泳"),
    ("late_training", "训练在睡前 3 小时内结束", "bool", None, "training", "规则：训练尽量在睡前 3 小时以前结束"),
    ("shoulder_routine", "做了肩部 routine", "bool", None, "training", "第 3 节的 5 个动作"),
    # Lifestyle
    ("sauna", "蒸桑拿", "bool", None, "lifestyle", "每次 10–15 分钟，前后各喝 300–500ml 水"),
    ("morning_sunlight", "起床 30 分钟内晒太阳", "bool", None, "lifestyle", "调生物钟"),
    ("nap", "午睡", "bool", None, "lifestyle", "规则：最多 20 分钟，下午 3 点后不睡"),
    ("screen_before_bed", "睡前 1 小时还在看手机", "bool", None, "lifestyle", ""),
    ("stress", "压力大", "bool", None, "lifestyle", "主观感觉"),
    # Symptoms
    ("low_blood_sugar", "低血糖感（头晕、手抖、出冷汗、心慌）", "bool", None, "symptom", "处理办法见 4.2.1"),
    ("excessive_thirst", "极度口渴", "bool", None, "symptom", "吃够喝够后仍反复出现要告诉 GP"),
    ("sick", "生病 / 不舒服", "bool", None, "symptom", "感冒、发烧、肠胃不适等"),
    ("shoulder_pain", "肩疼（不只是弹响）", "bool", None, "symptom", "弹响伴疼痛要看 physio（2.3）"),
]

SORT_BASE = {"supplement": 10, "diet": 20, "training": 30, "lifestyle": 40, "symptom": 50}


def init_journal_items(conn, user_id: int) -> dict:
    """Create the default journal items. Existing ones are left alone (the user may have renamed or disabled them)."""
    from .store import upsert_journal_item
    created = 0
    for i, (key, label, kind, unit, cat, desc) in enumerate(DEFAULT_JOURNAL_ITEMS):
        row = upsert_journal_item(conn, user_id, key, {
            "label_zh": label, "kind": kind, "unit": unit, "category": cat, "description": desc or None,
            "sort": SORT_BASE[cat] + i}, overwrite=False)
        created += row is not None
    return {"journal_items_expected": len(DEFAULT_JOURNAL_ITEMS), "journal_items_created": created}
