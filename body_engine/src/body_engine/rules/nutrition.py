"""Nutrition rules: adjust calories by weekly average weight (5.2), pre-workout eating (4.2.1), red meat / beef short rib frequency caps (5.3.1, 5.3.2).
Advice only; never changes goals in SparkyFitness (Body Engine only has a read-only key)."""
import re
from datetime import date, timedelta
from statistics import fmean

from . import params as P
from .base import Reminder, RuleResult
from .common import days_back, need

CALORIES = "calorie_adjust"
FUEL = "pre_workout_fuel"
CAPS = "frequency_caps"


# ================= Adjust calories by weekly average weight =================
def _window_avg(weights: list[dict], end: date, n_days: int = 7) -> dict:
    lo = end - timedelta(days=n_days - 1)
    vals = [w["weight_kg"] for w in weights if lo.isoformat() <= w["date"] <= end.isoformat()]
    return {"start": lo.isoformat(), "end": end.isoformat(), "n": len(vals),
            "avg_kg": round(fmean(vals), 2) if len(vals) >= P.MIN_WEIGHINS_PER_WEEK else None}


def _goal_lines(ctx, delta: int) -> tuple[str, list[dict]]:
    goals = {}
    for g in ctx.goals or []:
        if g.get("calories"):
            goals.setdefault(g.get("name") or "当前目标", g["calories"])
    rows = [{"goal": name, "calories": kcal, "suggested": kcal + delta} for name, kcal in goals.items()]
    text = "；".join(f"{r['goal'].split('（')[0]} {r['calories']:g} → {r['suggested']:g} kcal" for r in rows)
    return text, rows


def calorie_adjust(ctx) -> RuleResult:
    if "weights" in ctx.errors and not ctx.weights:
        return RuleResult(CALORIES, "error", f"读不到体重：{ctx.errors['weights']}")
    weights = ctx.weights or []
    w0 = _window_avg(weights, ctx.day)
    w1 = _window_avg(weights, ctx.day - timedelta(days=7))
    w2 = _window_avg(weights, ctx.day - timedelta(days=14))
    one_week = round(w1["avg_kg"] - w0["avg_kg"], 2) if w0["avg_kg"] and w1["avg_kg"] else None   # positive = loss
    two_week = round((w2["avg_kg"] - w0["avg_kg"]) / 2, 2) if w0["avg_kg"] and w2["avg_kg"] else None
    details = {"this_week": w0, "last_week": w1, "two_weeks_ago": w2, "loss_kg_1wk": one_week,
               "loss_kg_per_wk_2wk": two_week, "weights": weights}
    evidence = {**details, "source": "body-management.md 5.2（V2.1）、5.2.0", "weights": weights[-10:]}
    week = f"{ctx.day.isocalendar().year}-W{ctx.day.isocalendar().week:02d}"
    if one_week is not None and one_week > P.FAST_LOSS_KG_PER_WEEK:
        text, rows = _goal_lines(ctx, P.FAST_LOSS_KCAL)
        return RuleResult(CALORIES, "triggered", reminders=[Reminder(
            CALORIES, "warning", f"体重降得太快：建议每天加 {P.FAST_LOSS_KCAL} kcal",
            f"近 7 天平均 {w0['avg_kg']} kg，前 7 天 {w1['avg_kg']} kg，一周降了 {one_week} kg（超过 {P.FAST_LOSS_KG_PER_WEEK} kg）。"
            f"按 5.2 每天加约 {P.FAST_LOSS_KCAL} kcal" + (f"（{text}）" if text else "") +
            "。降太快会让尿酸短期升高、疲劳和早醒（2.1.2）。只是建议，要改请在 SparkyFitness 里改目标。",
            subject=f"fast:{week}", evidence={**evidence, "suggested_goals": rows}, sticky=True)], details=details)
    if two_week is not None and two_week < P.SLOW_LOSS_KG_PER_WEEK:
        text, rows = _goal_lines(ctx, P.SLOW_LOSS_KCAL)
        trend = f"平均每周降 {two_week} kg" if two_week >= 0 else f"平均每周反而升 {-two_week} kg"
        return RuleResult(CALORIES, "triggered", reminders=[Reminder(
            CALORIES, "info", f"体重降得慢：建议每天少 {-P.SLOW_LOSS_KCAL} kcal",
            f"近 7 天平均 {w0['avg_kg']} kg，两周前那 7 天 {w2['avg_kg']} kg，{trend}（不到 {P.SLOW_LOSS_KG_PER_WEEK} kg）。"
            f"按 5.2 每天减约 {-P.SLOW_LOSS_KCAL} kcal" + (f"（{text}）" if text else "") +
            "：先减油和零食，不先砍训练日碳水。如果最近疲劳或早醒，先别减。只是建议，要改请在 SparkyFitness 里改目标。",
            subject=f"slow:{week}", evidence={**evidence, "suggested_goals": rows}, sticky=True)], details=details)
    if one_week is None and two_week is None:
        return RuleResult(CALORIES, "insufficient_data",
                          f"周均体重算不出来：每个 7 天窗口至少要 {P.MIN_WEIGHINS_PER_WEEK} 次称重，"
                          f"近 7 天 {w0['n']} 次、前 7 天 {w1['n']} 次、两周前 {w2['n']} 次", details=details)
    lo, hi = P.TARGET_LOSS_RANGE
    return RuleResult(CALORIES, "not_triggered",
                      f"一周降 {one_week} kg、两周平均每周降 {two_week} kg，没到调整线（目标每周 {lo}–{hi} kg）",
                      details=details)


# ================= Pre-workout eating =================
def _is_training_item(ctx, exercise_id: str, category: str | None = None) -> bool:
    ex = (ctx.exercises or {}).get(exercise_id) or {}
    if ex.get("modality") == "weight_reps":
        return True
    return (ex.get("category") or category) == "cardio" and any(
        k in (ex.get("name") or "") for k in ("篮球", "游泳", "basketball", "swim"))


def pre_workout_fuel(ctx) -> RuleResult:
    if (r := need(ctx, FUEL, "plans", "presets", "exercises", "sessions")) is not None:
        return r
    planned = ctx.planned_exercises()
    logged = [e for s in ctx.sessions_on(ctx.day) for e in s["exercises"]]
    training = [p for p in planned if _is_training_item(ctx, p["exercise_id"], p.get("category"))] + \
               [e for e in logged if _is_training_item(ctx, e["exercise_id"], e.get("category"))]
    if not training:
        what = "、".join(dict.fromkeys(p["preset"] or p["name"] for p in planned)) or "休息"
        return RuleResult(FUEL, "not_triggered", f"今天计划：{what}，没有力量训练或球类 / 游泳")
    legs = sorted({x["name"] for x in training if ctx.is_leg_exercise(x["exercise_id"], x.get("category"),
                                                                       x.get("primary_muscles"))})
    sessions = list(dict.fromkeys([p["preset"] or p["name"] for p in planned
                                   if p.get("preset") or _is_training_item(ctx, p["exercise_id"], p.get("category"))]
                                  + [s["name"] for s in ctx.sessions_on(ctx.day)]))
    # recent hypoglycemia-like feeling on leg day / not enough eaten before training -> upgrade to warning
    symptoms = sorted({(d.isoformat(), k) for (d, k), v in ctx.journal.items()
                       if k in ("low_blood_sugar", "underfed_training") and v > 0
                       and ctx.day - timedelta(days=P.SYMPTOM_LOOKBACK_DAYS) <= d <= ctx.day})
    lo, hi = P.PRE_MEAL_CARBS_G
    plo, phi = P.PRE_MEAL_PROTEIN_G
    lines = [f"练前 1.5–2 小时吃一顿正餐：碳水 {lo}–{hi}g、蛋白 {plo}–{phi}g；离上一餐超过 4 小时，练前 30–60 分钟再加一根香蕉或一片吐司配蜂蜜。"]
    if legs:
        lines += ["不空腹练腿；手边备一根香蕉或一盒果汁（快糖）。",
                  "头晕、手抖、出冷汗、心慌：立刻停组坐下，吃 15–20g 快糖，15 分钟没缓解再吃一次，当天不再做高强度。"]
    lines.append(f"练前 2 小时喝 500ml 水，练中每 15–20 分钟小口喝 150–250ml，1 小时内不超过约 1L。")
    water = ctx.journal.get((ctx.day - timedelta(days=1), "water_l"))
    if water is not None and water < P.WATER_TARGET_L:
        lines.append(f"昨天只喝了 {water:g}L（吃肌酸期间目标 3–3.5L）。")
    if symptoms:
        lines.append("最近有过：" + "、".join(f"{d} {'低血糖感' if k == 'low_blood_sugar' else '练前没吃够'}" for d, k in symptoms) + "。")
    label = "、".join(sessions) or "训练"
    evidence = {"planned": [{"exercise_id": p["exercise_id"], "name": p["name"], "template": p["preset"]} for p in planned],
                "logged_today": [e["name"] for e in logged], "leg_exercises": legs,
                "recent_symptoms": [{"date": d, "key": k} for d, k in symptoms], "water_l_yesterday": water,
                "source": "body-management.md 4.2.1"}
    reminders = [Reminder(FUEL, "warning" if legs and symptoms else "info",
                          f"今天{label}：{'练腿，练前吃够、备快糖' if legs else '练前记得吃正餐'}",
                          "\n".join(lines), subject="", evidence=evidence)]
    food_today = [f for f in ctx.food or [] if f["date"] == ctx.day.isoformat()]
    if food_today:
        carbs = sum(f.get("carbs_g") or 0 for f in food_today)
        if carbs < P.DAY_CARBS_WARN_G:
            reminders.append(Reminder(
                FUEL, "warning", f"今天记录的碳水只有 {carbs:g}g",
                f"今天有{label}，饮食记录里全天碳水 {carbs:g}g，还不够练前一顿正餐（{lo}–{hi}g）。"
                f"10-15 练腿低血糖就是当天第一餐 14:30、没吃主食（4.2.1）。练前补一顿有主食的正餐。",
                subject="carbs", evidence={"carbs_g_logged": carbs, "entries": [
                    {"time": f.get("time"), "food": f.get("food"), "carbs_g": f.get("carbs_g")} for f in food_today],
                    "source": "body-management.md 4.2.1"}))
    return RuleResult(FUEL, "triggered", reminders=reminders, details={"leg_day": bool(legs)})


# ================= Frequency caps =================
def _clean(name: str) -> str:
    s = (name or "").lower()
    for x in P.RED_MEAT_EXCLUDE:
        s = s.replace(x.lower(), "")
    return s


RED_MEAT_MARK = re.compile(r"红肉\s*(\d+(?:\.\d+)?)\s*(?:g|克)")


def red_meat_estimate(food: str, quantity: float | None = None, unit: str | None = None) -> tuple[str, int] | None:
    """(matched keyword, estimated grams); None if not red meat. Serving units are multiplied by the count; for gram units the meat share is unknown, so count as one serving.
    If the name explicitly says "红肉 Ng" (red meat N g; how meal template entries are written), use that gram value directly."""
    if m := RED_MEAT_MARK.search(food or ""):
        return "标注", round(float(m.group(1)))
    s = _clean(food)
    for kw, grams in P.RED_MEAT_KEYWORDS:
        if kw.lower() in s:
            mult = 1
            if quantity and unit and unit.strip().lower() not in ("g", "克", "gram", "grams", "ml"):
                mult = min(max(round(quantity), 1), 3)
            return kw, grams * mult
    return None


def is_short_rib(food: str) -> bool:
    s = _clean(food)
    return any(k in s for k in P.SHORT_RIB_KEYWORDS)


def frequency_caps(ctx) -> RuleResult:
    if "food" in ctx.errors:
        return RuleResult(CAPS, "error", f"读不到饮食记录：{ctx.errors['food']}")
    window = days_back(ctx.day, 7)
    keys = {d.isoformat() for d in window}
    food = [f for f in ctx.food or [] if f["date"] in keys]
    journal_g = {d: ctx.journal[(d, "red_meat_g")] for d in window if (d, "red_meat_g") in ctx.journal}
    logged_days = sorted({f["date"] for f in food} | {d.isoformat() for d in journal_g})
    if not logged_days:
        why = "没连 SparkyFitness" if ctx.food is None else "这 7 天没有饮食记录"
        return RuleResult(CAPS, "insufficient_data", f"{why}，日记里也没记红肉克数")
    per_day, meals, ribs, matched = {}, set(), [], []
    for d in window:
        iso = d.isoformat()
        hits = []
        for f in (x for x in food if x["date"] == iso):
            if (est := red_meat_estimate(f.get("food") or "", f.get("quantity"), f.get("unit"))) is not None:
                hits.append({"food": f.get("food"), "meal_type": f.get("meal_type"), "keyword": est[0], "grams_est": est[1]})
                meals.add((iso, f.get("meal_type") or f.get("time") or f.get("food")))
            if is_short_rib(f.get("food") or ""):
                ribs.append({"date": iso, "food": f.get("food")})
        if d in journal_g:
            per_day[iso] = {"grams": journal_g[d], "from": "日记 red_meat_g"}
            if journal_g[d] > 0 and not hits:
                meals.add((iso, "journal"))
        elif hits:
            per_day[iso] = {"grams": sum(h["grams_est"] for h in hits), "from": "饮食记录估算"}
        matched += [{"date": iso, **h} for h in hits]
    grams = sum(v["grams"] for v in per_day.values())
    coverage = f"这 7 天有 {len(logged_days)} 天有饮食记录" + ("" if len(logged_days) == 7 else "，没记的天不算，实际可能更多")
    evidence = {"window": [window[0].isoformat(), window[-1].isoformat()], "red_meat_g": grams, "per_day": per_day,
                "matched_entries": matched, "short_rib": ribs, "red_meat_meals": len(meals),
                "logged_days": logged_days, "source": "body-management.md 5.3.1、5.3.2"}
    reminders = []
    cap = P.RED_MEAT_G_PER_WEEK
    if grams > cap:
        reminders.append(Reminder(CAPS, "warning", f"近 7 天红肉约 {grams:g}g，超过 {cap}g",
                                  f"尿酸偏高、脂肪肝：红肉每周 ≤ {cap}g（5.3.1）。接下来几天蛋白换成鸡、鱼、蛋、奶、豆腐。{coverage}。",
                                  subject="red_meat_g", evidence=evidence))
    elif grams >= cap * P.RED_MEAT_WARN_FRACTION:
        reminders.append(Reminder(CAPS, "info", f"近 7 天红肉约 {grams:g}g，快到 {cap}g 了",
                                  f"还剩约 {cap - grams:g}g；接下来优先鸡、鱼、蛋、奶、豆腐。{coverage}。",
                                  subject="red_meat_g", evidence=evidence))
    if len(ribs) > P.SHORT_RIB_PER_WEEK:
        reminders.append(Reminder(CAPS, "warning", f"近 7 天吃了 {len(ribs)} 次牛肋条，超过每周 {P.SHORT_RIB_PER_WEEK} 次",
                                  "牛肋条是高脂部位，对脂肪肝和甘油三酯不友好（5.3.1）；想吃这道菜换成 rump / topside 牛排切条。",
                                  subject="short_rib", evidence=evidence))
    elif len(ribs) == P.SHORT_RIB_PER_WEEK:
        nxt = date.fromisoformat(ribs[-1]["date"]) + timedelta(days=7)
        reminders.append(Reminder(CAPS, "info", "这周已经吃过牛肋条了",
                                  f"{ribs[-1]['date']} 吃过一次；每周最多 1 次（5.3.1），下一次 {nxt.isoformat()} 以后。",
                                  subject="short_rib", evidence=evidence))
    if len(meals) > P.RED_MEAT_MEALS_PER_WEEK:
        reminders.append(Reminder(CAPS, "warning", f"近 7 天吃了 {len(meals)} 顿红肉，超过每周 {P.RED_MEAT_MEALS_PER_WEEK} 顿",
                                  f"牛肉 / 猪肉（瘦部位）每周合计 ≤ {P.RED_MEAT_MEALS_PER_WEEK} 顿（5.3.2），培根、火腿等加工肉也算。{coverage}。",
                                  subject="red_meat_meals", evidence=evidence))
    details = {"red_meat_g": grams, "short_rib": len(ribs), "red_meat_meals": len(meals), "logged_days": len(logged_days)}
    if reminders:
        return RuleResult(CAPS, "triggered", coverage, reminders, details)
    return RuleResult(CAPS, "not_triggered",
                      f"近 7 天红肉约 {grams:g}g、牛肋条 {len(ribs)} 次、红肉 {len(meals)} 顿，都没到上限；{coverage}", details=details)
