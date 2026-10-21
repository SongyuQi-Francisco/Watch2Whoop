"""Default journal items every user has (system design section 7). Notes are generic; a user's own limits are their diet
constraints (nutrition/constraints.py, issue #76), not text here.

Date = the day the behavior happened; analysis looks at the next day's outcome. Supplement journal items (supp_*) follow their supplements, see md_seed.py / store.add_supplement.
(key, Chinese name, kind, unit, category, description)
"""
from ..i18n import is_en

DEFAULT_JOURNAL_ITEMS: list[tuple[str, str, str, str | None, str, str]] = [
    # Diet
    ("caffeine", "喝了咖啡 / 含咖啡因饮料", "bool", None, "diet", "咖啡、茶、能量饮料、预训练补剂都算"),
    ("caffeine_after_noon", "中午 12 点后摄入咖啡因", "bool", None, "diet", "规则：12 点后不喝（6.2）"),
    ("alcohol", "喝酒", "bool", None, "diet", "啤酒、葡萄酒、烈酒都算"),
    ("late_dinner", "晚饭太晚（睡前 3 小时内吃正餐）", "bool", None, "diet", "睡前饿了喝牛奶 / 酸奶不算"),
    ("red_meat_g", "红肉（生重）", "number", "g", "diet", "牛、羊、猪肉都算，培根、火腿等加工肉也算"),
    ("water_l", "喝水量", "number", "L", "diet", "一天喝的水（含饮料）"),
    ("underfed_training", "练前没吃够（离上一餐 > 4 小时且没加餐）", "bool", None, "diet", "练前 1.5–2 小时吃一顿有主食的正餐"),
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
    ("low_blood_sugar", "低血糖感（头晕、手抖、出冷汗、心慌）", "bool", None, "symptom", "先吃点快糖；反复出现要告诉医生"),
    ("excessive_thirst", "极度口渴", "bool", None, "symptom", "吃够喝够后仍反复出现要告诉 GP"),
    ("sick", "生病 / 不舒服", "bool", None, "symptom", "感冒、发烧、肠胃不适等"),
    ("shoulder_pain", "肩疼（不只是弹响）", "bool", None, "symptom", "弹响伴疼痛要看 physio（2.3）"),
]

# English names of the default items for the Today page (issue #42): shown only while the stored Chinese name is still the default
# one (a renamed item is the user's own text). The AI keeps reading label_zh.
DEFAULT_LABELS_EN: dict[str, str] = {
    "caffeine": "Coffee / caffeinated drink",
    "caffeine_after_noon": "Caffeine after 12:00",
    "alcohol": "Alcohol",
    "late_dinner": "Late dinner (main meal within 3 h of bed)",
    "red_meat_g": "Red meat (raw weight)",
    "water_l": "Water",
    "underfed_training": "Trained underfed (> 4 h since a meal, no snack)",
    "training_upper": "Upper body (strength)",
    "training_legs": "Legs (strength)",
    "incline_walk": "Incline walk",
    "basketball": "Basketball",
    "swimming": "Swimming",
    "late_training": "Workout ended within 3 h of bed",
    "shoulder_routine": "Shoulder routine",
    "sauna": "Sauna",
    "morning_sunlight": "Sunlight within 30 min of waking",
    "nap": "Nap",
    "screen_before_bed": "Phone in the last hour before bed",
    "stress": "Stressed",
    "low_blood_sugar": "Low blood sugar (dizzy, shaky, cold sweat, racing heart)",
    "excessive_thirst": "Extreme thirst",
    "sick": "Sick / unwell",
    "shoulder_pain": "Shoulder pain (not just clicking)",
}
_DEFAULT_ZH = {k: label for k, label, *_ in DEFAULT_JOURNAL_ITEMS}


def with_label(rows: list[dict]) -> list[dict]:
    """Add `label` (the name to show in the request's language) next to label_zh: the English default name while the item still
    has its default Chinese name, otherwise the stored name. For the Today page endpoints only."""
    en = is_en()
    out = []
    for r in rows:
        k, zh = r.get("key"), r.get("label_zh")
        out.append({**r, "label": DEFAULT_LABELS_EN[k] if en and zh is not None and _DEFAULT_ZH.get(k) == zh and k in DEFAULT_LABELS_EN else zh})
    return out


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
