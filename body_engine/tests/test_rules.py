"""Pure-function tests for the rule engine: trigger / non-trigger boundaries of each rule. No database, no SparkyFitness; Ctx is built directly."""
from datetime import date, timedelta

import pytest

from body_engine.rules import nutrition, params as P, progression, safety
from body_engine.rules.context import Ctx
from body_engine.rules.engine import evaluate_ctx
from body_engine.scores.muscle import daily_muscle_load

FRI = date(2026, 10, 16)          # Friday: planned lower body B
SAT = date(2026, 10, 17)          # Saturday: basketball
SUN = date(2026, 10, 18)          # Sunday: nothing scheduled
MON = date(2026, 10, 19)          # Monday: upper body A

EX = {   # id -> SparkyFitness exercise
    "pull": dict(name="高位下拉", category="strength", modality="weight_reps", primary_muscles=["lats"], description=""),
    "face": dict(name="绳索面拉 + 外旋（轻重量）", category="strength", modality="weight_reps",
                 primary_muscles=["shoulders"], description="【注意】轻重量"),
    "hip": dict(name="杠铃臀推", category="strength", modality="weight_reps", primary_muscles=["glutes"], description=""),
    "bss": dict(name="保加利亚分腿蹲", category="strength", modality="weight_reps", primary_muscles=["quadriceps"],
                description="【注意】前脚离凳一大步"),
    "upright": dict(name="杠铃提拉 / 直立划船（禁用）", category="strength", modality="weight_reps",
                    primary_muscles=["traps"], description="【禁用】永久不做"),
    "incline": dict(name="上斜卧推（器械未注明）", category="strength", modality="weight_reps", primary_muscles=["chest"],
                    description="【注意】器械未注明"),
    "plank": dict(name="平板支撑", category="strength", modality="duration", primary_muscles=["abdominals"], description=""),
    "ball": dict(name="篮球", category="cardio", modality="duration", primary_muscles=[], description=""),
    "wall": dict(name="前锯肌墙滑", category="strength", modality="reps_only", primary_muscles=["shoulders"], description=""),
}
META = [
    dict(exercise_ref="lat_pulldown", app_exercise_id="pull", status="active", aliases=[], reason=None, cues="先沉肩"),
    dict(exercise_ref="cable_face_pull", app_exercise_id="face", status="caution", aliases=["面拉"],
         reason="大重量面拉不做", cues="轻重量"),
    dict(exercise_ref="barbell_hip_thrust", app_exercise_id="hip", status="active", aliases=[], reason=None, cues=None),
    dict(exercise_ref="bulgarian_split_squat", app_exercise_id="bss", status="caution", aliases=[], reason="膝盖",
         cues="膝盖和脚尖同方向"),
    dict(exercise_ref="upright_row", app_exercise_id="upright", status="banned", aliases=["直立划船"],
         reason="练大上斜方", cues="用侧平举代替"),
]


def _rx(ex_id, sets, low, high, weight, inc=2.5, mode="rep_goal"):
    return {"exercise_id": ex_id, "name": EX[ex_id]["name"], "category": EX[ex_id]["category"], "progression_mode": mode,
            "rep_goal": high, "increment_value": inc,
            "sets": [{"set": i + 1, "type": "Working Set", "reps": low, "weight_kg": weight} for i in range(sets)]}


def presets():
    return {
        1: {"id": 1, "name": "上肢 A", "exercises": [_rx("pull", 4, 8, 12, 39), _rx("face", 3, 15, 15, 18, mode="manual")]},
        2: {"id": 2, "name": "下肢 B", "exercises": [_rx("hip", 4, 8, 12, 40, inc=5), _rx("bss", 3, 8, 10, None)]},
        3: {"id": 3, "name": "肩部 routine", "exercises": [_rx("wall", 2, 10, 10, None, mode="manual")]},
    }


def plan(**kw):
    return {"id": 1, "name": "V3.0", "start_date": "2026-10-15", "end_date": None, "is_active": True,
            "schedule_type": "weekly", "assignments": [
                {"day_of_week": 1, "sort_order": 0, "workout_preset_id": 1, "workout_preset_name": "上肢 A"},
                {"day_of_week": 3, "sort_order": 0, "workout_preset_id": 3, "workout_preset_name": "肩部 routine"},
                {"day_of_week": 5, "sort_order": 0, "workout_preset_id": 2, "workout_preset_name": "下肢 B"},
                {"day_of_week": 6, "sort_order": 0, "workout_preset_id": None, "exercise_id": "ball", "exercise_name": "篮球"},
            ], **kw}


def sess(d, *exercises, notes=None):
    return {"date": d.isoformat() if isinstance(d, date) else d, "name": "训练", "notes": notes,
            "total_duration_minutes": 0, "exercises": [
                {"exercise_id": ex_id, "name": EX[ex_id]["name"], "category": EX[ex_id]["category"],
                 "primary_muscles": EX[ex_id]["primary_muscles"], "duration_minutes": 0,
                 "sets": [{"set": i + 1, "type": "Working Set", "reps": r, "weight_kg": w, "rir": rir, "rpe": None,
                           "duration_s": None} for i, (r, w, rir) in enumerate(sets)]}
                for ex_id, sets in exercises]}


def S(n, reps, w, rir=None):
    return [(reps, w, rir)] * n


def ctx(day=FRI, **kw) -> Ctx:
    base = dict(user_id=1, day=day, sessions=[], exercises={k: {"id": k, **v} for k, v in EX.items()},
                presets=presets(), plans=[plan()], meta=META, food=[], goals=[], weights=[], scores={}, journal={})
    base.update(kw)
    return Ctx(**base)


def one(res, level=None):
    assert len(res.reminders) == 1, res
    if level:
        assert res.reminders[0].level == level
    return res.reminders[0]


# ================= double progression =================
def test_progression_all_sets_at_top_adds_template_increment():
    r = progression.evaluate(ctx(sessions=[sess(date(2026, 10, 10), ("pull", S(4, 12, 39)))]))
    rem = one(r, "info")
    assert r.status == "triggered" and rem.evidence["next_weight_kg"] == 41.5 and rem.sticky
    assert "39 kg → 41.5 kg" in rem.title and rem.subject == "pull:39:up"
    # the lower-body template's increment is 5
    r = progression.evaluate(ctx(sessions=[sess(date(2026, 10, 10), ("hip", S(4, 12, 40)))]))
    assert one(r).evidence["next_weight_kg"] == 45


def test_progression_boundaries_not_triggered():
    one_short = [(12, 39, None)] * 3 + [(11, 39, None)]
    assert progression.evaluate(ctx(sessions=[sess(date(2026, 10, 10), ("pull", one_short))])).status == "not_triggered"
    # only 3 sets done (4 planned) does not count
    assert progression.evaluate(ctx(sessions=[sess(date(2026, 10, 10), ("pull", S(3, 12, 39)))])).reminders == []
    # workouts after the evaluation day don't count
    assert progression.evaluate(ctx(day=date(2026, 10, 9), sessions=[sess(date(2026, 10, 10), ("pull", S(4, 12, 39)))])
                                ).status == "insufficient_data"


def test_progression_rir_zero_holds():
    r = progression.evaluate(ctx(sessions=[sess(date(2026, 10, 10), ("pull", S(3, 12, 39, 1) + [(12, 39, 0)]))]))
    rem = one(r, "info")
    assert rem.subject == "pull:39:hold" and "力竭" in rem.title
    r = progression.evaluate(ctx(sessions=[sess(date(2026, 10, 10), ("pull", S(4, 12, 39, 1)))]))
    assert one(r).subject == "pull:39:up"


def test_progression_consecutive_failures_reduce():
    fail = [(8, 41.5, None), (7, 41.5, None), (7, 41.5, None), (6, 41.5, None)]
    ok = S(4, 9, 41.5)
    two = [sess(date(2026, 10, 10), ("pull", fail)), sess(date(2026, 10, 13), ("pull", fail))]
    rem = one(progression.evaluate(ctx(sessions=two)), "warning")
    assert rem.evidence["next_weight_kg"] == 39 and rem.evidence["fail_streak"] == 2
    # only one failure / one non-failure in between / failures at different weights -> no reduction
    assert progression.evaluate(ctx(sessions=two[1:])).reminders == []
    assert progression.evaluate(ctx(sessions=[two[0], sess(date(2026, 10, 12), ("pull", ok)), two[1]])).reminders == []
    other = [(7, 39, None)] * 4
    assert progression.evaluate(ctx(sessions=[sess(date(2026, 10, 10), ("pull", other)), two[1]])).reminders == []


def test_progression_skips_manual_and_reports_off_plan_and_notes():
    sessions = [sess(date(2026, 10, 12), ("face", S(4, 15, 32)), ("incline", S(4, 12, 23))),
                sess(date(2026, 10, 15), ("bss", S(4, 12, 5)), notes="保加利亚蹲回到 3 × 8–10，先用 5kg；其他正常")]
    r = progression.evaluate(ctx(sessions=sessions))
    assert "绳索面拉 + 外旋（轻重量）" in r.details["no_progression_in_template"]
    assert r.details["logged_but_not_in_plan_28d"] == ["上斜卧推（器械未注明）"]
    rem = one(r)   # Bulgarian split squat: the template has no weight, logged 5kg, reaching the 10-rep cap -> 7.5, with the note sentence that mentions it attached
    assert rem.evidence["next_weight_kg"] == 7.5 and "「保加利亚蹲回到 3 × 8–10，先用 5kg」" in rem.message
    assert "其他正常" not in rem.message


def test_progression_missing_data():
    assert progression.evaluate(ctx(plans=[])).status == "insufficient_data"
    assert progression.evaluate(ctx(sessions=None, missing={"sparky": "没连"})).status == "insufficient_data"
    assert progression.evaluate(ctx(errors={"sessions": "timeout"})).status == "error"
    assert progression.evaluate(ctx(plans=[plan(start_date="2026-10-20")])).status == "insufficient_data"


# ================= calorie adjustment =================
def weights(this_week, last_week=None, two_weeks=None, day=FRI):
    out = []
    for offset, vals in ((0, this_week), (7, last_week), (14, two_weeks)):
        for i, v in enumerate(vals or []):
            out.append({"date": (day - timedelta(days=offset + i)).isoformat(), "weight_kg": v, "source": "sparkyfitness"})
    return sorted(out, key=lambda r: r["date"])


GOALS = [{"name": "训练日 V2.1（…）", "calories": 2400}, {"name": "休息日 V2.1（…）", "calories": 2200}]


def test_calories_fast_loss_boundary():
    assert nutrition.calorie_adjust(ctx(weights=weights([88, 88], [89, 89]))).status == "not_triggered"   # a drop of 1.0 does not count as exceeding
    r = nutrition.calorie_adjust(ctx(weights=weights([87.99, 87.99], [89, 89]), goals=GOALS))
    rem = one(r, "warning")
    assert rem.evidence["suggested_goals"] == [{"goal": "训练日 V2.1（…）", "calories": 2400, "suggested": 2600},
                                               {"goal": "休息日 V2.1（…）", "calories": 2200, "suggested": 2400}]
    assert "训练日 V2.1 2400 → 2600" in rem.message and rem.sticky


def test_calories_slow_loss_boundary():
    assert nutrition.calorie_adjust(ctx(weights=weights([88.2, 88.2], None, [89, 89]))).status == "not_triggered"  # 0.4/week
    r = nutrition.calorie_adjust(ctx(weights=weights([88.22, 88.22], None, [89, 89]), goals=GOALS))
    rem = one(r, "info")
    assert rem.evidence["suggested_goals"][0]["suggested"] == 2250 and "不先砍训练日碳水" in rem.message
    assert "反而升" in one(nutrition.calorie_adjust(ctx(weights=weights([90, 90], None, [89, 89])))).message


def test_calories_in_range_and_insufficient():
    r = nutrition.calorie_adjust(ctx(weights=weights([88.3, 88.3], [89, 89], [89.7, 89.7])))
    assert r.status == "not_triggered" and r.details["loss_kg_1wk"] == 0.7
    r = nutrition.calorie_adjust(ctx(weights=weights([88], [89, 89], [90, 90])))   # weighed only once this week
    assert r.status == "insufficient_data" and "近 7 天 1 次" in r.note
    assert nutrition.calorie_adjust(ctx(weights=None, errors={"weights": "x"})).status == "error"


# ================= pre-workout fueling =================
def test_fuel_leg_day_with_recent_symptom_is_warning():
    j = {(date(2026, 10, 15), "low_blood_sugar"): 1.0}
    rem = one(nutrition.pre_workout_fuel(ctx(journal=j)), "warning")
    assert "练腿" in rem.title and "快糖" in rem.message and rem.evidence["leg_exercises"]
    # symptoms older than 14 days don't count -> info
    old = {(FRI - timedelta(days=15), "low_blood_sugar"): 1.0}
    assert one(nutrition.pre_workout_fuel(ctx(journal=old))).level == "info"
    # a logged "no" (0) doesn't count
    assert one(nutrition.pre_workout_fuel(ctx(journal={(date(2026, 10, 15), "low_blood_sugar"): 0.0}))).level == "info"


def test_fuel_upper_day_rest_day_and_basketball():
    rem = one(nutrition.pre_workout_fuel(ctx(day=MON)), "info")
    assert "练腿" not in rem.title and "快糖" not in rem.message
    assert nutrition.pre_workout_fuel(ctx(day=SUN)).status == "not_triggered"
    assert nutrition.pre_workout_fuel(ctx(day=date(2026, 10, 21))).status == "not_triggered"   # Wednesday's shoulder routine doesn't count as training
    assert one(nutrition.pre_workout_fuel(ctx(day=SAT))).title.startswith("今天篮球：")         # basketball
    # planned rest but a leg workout was logged that day -> remind too
    r = nutrition.pre_workout_fuel(ctx(day=SUN, sessions=[sess(SUN, ("bss", S(3, 10, 5)))]))
    assert "练腿" in one(r).title


def test_fuel_low_carbs_boundary():
    food = lambda c: [{"date": FRI.isoformat(), "time": "14:30", "food": "吐司", "carbs_g": c}]
    r = nutrition.pre_workout_fuel(ctx(food=food(49)))
    assert [x.subject for x in r.reminders] == ["", "carbs"] and r.reminders[1].level == "warning"
    assert len(nutrition.pre_workout_fuel(ctx(food=food(50))).reminders) == 1
    assert len(nutrition.pre_workout_fuel(ctx(food=[])).reminders) == 1     # no judgement without nutrition logged


def test_fuel_water_line():
    r = nutrition.pre_workout_fuel(ctx(journal={(FRI - timedelta(days=1), "water_l"): 2.5}))
    assert "2.5L" in one(r).message
    assert "L（" not in one(nutrition.pre_workout_fuel(ctx(journal={(FRI - timedelta(days=1), "water_l"): 3.0}))).message


# ================= frequency cap =================
def test_red_meat_estimates_and_exclusions():
    assert nutrition.red_meat_estimate("盐葱酱牛肋条") == ("牛肋条", 135)
    assert nutrition.red_meat_estimate("吐司 2 片、煎蛋 2 个、培根 2 片") == ("培根", 30)
    assert nutrition.red_meat_estimate("黑椒牛柳", 2, "份") == ("牛", 300)
    assert nutrition.red_meat_estimate("黑椒牛柳", 200, "g") == ("牛", 150)
    for food in ("A2 全脂牛奶 250ml", "牛油果吐司", "燕麦 + 牛奶", "蘑菇烧鸡", "Greek yoghurt"):
        assert nutrition.red_meat_estimate(food) is None, food
    assert nutrition.is_short_rib("盐葱酱牛肋条") and not nutrition.is_short_rib("黑椒牛柳")


def _j(g, day=FRI):
    return {(day, "red_meat_g"): g}


def test_red_meat_grams_boundaries():
    assert nutrition.frequency_caps(ctx(journal=_j(199))).status == "not_triggered"
    assert one(nutrition.frequency_caps(ctx(journal=_j(200))), "info").subject == "red_meat_g"
    assert one(nutrition.frequency_caps(ctx(journal=_j(250))), "info").subject == "red_meat_g"
    rem = one(nutrition.frequency_caps(ctx(journal=_j(251))), "warning")
    assert "超过 250g" in rem.title and "没记的天不算" in rem.message
    # 7 days ago (day 8) doesn't count
    assert nutrition.frequency_caps(ctx(journal={**_j(100), **_j(200, FRI - timedelta(days=7))})).status == "not_triggered"
    assert nutrition.frequency_caps(ctx(journal={**_j(100), **_j(200, FRI - timedelta(days=6))})).status == "triggered"


def test_journal_grams_override_food_estimate():
    food = [{"date": FRI.isoformat(), "meal_type": "dinner", "food": "黑椒牛柳", "quantity": 1, "unit": "份"}]
    assert nutrition.frequency_caps(ctx(food=food)).details["red_meat_g"] == 150
    assert nutrition.frequency_caps(ctx(food=food, journal=_j(120))).details["red_meat_g"] == 120


def test_short_rib_and_meal_counts():
    rib = lambda d, meal="dinner": {"date": d.isoformat(), "meal_type": meal, "food": "盐葱酱牛肋条", "quantity": 1, "unit": "份"}
    rem = one(nutrition.frequency_caps(ctx(food=[rib(FRI - timedelta(days=2))])), "info")
    assert rem.subject == "short_rib" and "2026-10-21" in rem.message
    r = nutrition.frequency_caps(ctx(food=[rib(FRI - timedelta(days=5)), rib(FRI)]))
    assert {x.subject: x.level for x in r.reminders} == {"red_meat_g": "warning", "short_rib": "warning"}   # 270g
    beef = lambda d, meal: {"date": d.isoformat(), "meal_type": meal, "food": "黑椒牛柳（rump）", "quantity": 1, "unit": "g"}
    two = [beef(FRI, "lunch"), beef(FRI - timedelta(days=1), "dinner")]
    assert "red_meat_meals" not in {x.subject for x in nutrition.frequency_caps(ctx(food=two)).reminders}
    r = nutrition.frequency_caps(ctx(food=two + [{"date": FRI.isoformat(), "meal_type": "breakfast", "food": "培根", "unit": "片"}]))
    assert "red_meat_meals" in {x.subject for x in r.reminders}


def test_caps_insufficient_and_error():
    assert nutrition.frequency_caps(ctx()).status == "insufficient_data"
    assert nutrition.frequency_caps(ctx(food=None)).status == "insufficient_data"
    assert nutrition.frequency_caps(ctx(errors={"food": "x"})).status == "error"
    food = [{"date": FRI.isoformat(), "meal_type": "lunch", "food": "蘑菇烧鸡", "quantity": 1, "unit": "份"}]
    assert nutrition.frequency_caps(ctx(food=food)).status == "not_triggered"


# ================= banned / caution exercises =================
def test_banned_in_plan_is_alert():
    p = presets()
    p[1]["exercises"].append(_rx("upright", 3, 12, 12, 8))
    r = safety.exercise_restrictions(ctx(day=SUN, presets=p))
    rem = [x for x in r.reminders if x.subject == "plan:upright"]
    assert rem and rem[0].level == "alert" and "练大上斜方。" in rem[0].message


def test_banned_in_log_lookback_boundary():
    log = lambda days: [sess(FRI - timedelta(days=days), ("upright", S(4, 12, 7)))]
    r = safety.exercise_restrictions(ctx(day=SUN, sessions=log(13 - 2)))   # SUN - 13 days
    assert one(r, "warning").subject.startswith("log:upright:")
    assert safety.exercise_restrictions(ctx(day=SUN, sessions=log(14 - 2))).status == "not_triggered"


def test_face_pull_weight_cap():
    at = lambda w: safety.exercise_restrictions(ctx(day=SUN, sessions=[sess(FRI, ("face", S(3, 15, w)))]))
    assert at(18).status == "not_triggered"
    rem = one(at(18.5), "warning")
    assert rem.subject == f"weight:face:{FRI.isoformat()}" and rem.evidence["max_kg"] == 18


def test_caution_in_todays_plan_and_description_fallback():
    rem = one(safety.exercise_restrictions(ctx()), "info")   # Friday lower body B: Bulgarian split squat (exercise_meta caution)
    assert rem.evidence["exercises"][0]["exercise"] == "保加利亚分腿蹲" and "膝盖和脚尖同方向" in rem.message
    # An exercise absent from exercise_meta: look at the 【注意】/【禁用】 markers in the SparkyFitness description
    c = ctx(meta=[m for m in META if m["exercise_ref"] != "bulgarian_split_squat"])
    assert c.status_of("bss") == ("caution", None, "SparkyFitness 动作描述")
    assert one(safety.exercise_restrictions(c)).evidence["exercises"][0]["status_basis"] == "SparkyFitness 动作描述"
    assert c.status_of("pull")[0] == "active"


# ================= recovery score / sleep debt =================
def score(recovery=60, debt=30, **flags):
    return {"recovery": recovery, "sleep_score": 70, "sleep_debt_min": debt, "sleep_actual_min": 400,
            "sleep_need_min": 500, "strain": 5, "flags": flags}


def test_readiness_red_recovery_boundaries():
    assert safety.readiness(ctx(scores={FRI: score(34)})).status == "not_triggered"
    rem = one(safety.readiness(ctx(scores={FRI: score(33)})), "warning")
    assert rem.subject == "red_heavy" and rem.evidence["planned_heavy"] == ["下肢 B"]
    assert one(safety.readiness(ctx(day=SAT, scores={SAT: score(20)}))).subject == "red_heavy"   # basketball counts too
    assert one(safety.readiness(ctx(day=SUN, scores={SUN: score(20)})), "info").subject == "red"
    assert one(safety.readiness(ctx(day=date(2026, 10, 21), scores={date(2026, 10, 21): score(20)}))).subject == "red"


def test_readiness_sleep_debt_and_illness():
    assert safety.readiness(ctx(scores={FRI: score(debt=119)})).status == "not_triggered"
    assert one(safety.readiness(ctx(scores={FRI: score(debt=120)})), "info").subject == "sleep_debt"
    assert one(safety.readiness(ctx(scores={FRI: score(debt=240)})), "warning").subject == "sleep_debt"
    r = safety.readiness(ctx(scores={FRI: score(illness_warning="静息心率高")}))
    assert one(r, "warning").subject == "illness"


def test_readiness_missing():
    assert safety.readiness(ctx()).status == "insufficient_data"
    r = safety.readiness(ctx(scores={FRI: score(None, no_sleep="没有手表睡眠数据")}))
    assert r.status == "insufficient_data" and "没有手表睡眠数据" in r.note


# ================= overall =================
def test_evaluate_ctx_isolates_rule_errors(monkeypatch):
    def boom(c):
        raise KeyError("x")
    monkeypatch.setattr("body_engine.rules.engine.RULES", [("a", boom), ("progression", progression.evaluate)])
    res = evaluate_ctx(ctx())
    assert [r.status for r in res] == ["error", "insufficient_data"]


def test_params_have_sources():
    src = open(P.__file__, encoding="utf-8").read()
    assert src.count("body-management.md") >= 5 and "Inferred" in src


# ================= muscle load =================
def test_daily_muscle_load():
    sessions = [
        sess(date(2026, 10, 15), ("incline", S(4, 12, 23)), ("bss", S(4, 12, 5)), ("plank", [(None, None, None)])),
        {"date": "2026-10-15", "name": "爬坡", "total_duration_minutes": 25, "exercises": [
            {"exercise_id": "w", "category": "cardio", "duration_minutes": 25,
             "sets": [{"type": "Working Set", "reps": None, "weight_kg": None, "duration_s": 1500, "rpe": 4}]}]},
        sess(date(2026, 10, 16), ("pull", [(10, 20, None)])),
    ]
    sessions[0]["exercises"][0]["sets"].insert(0, {"type": "Warm-up", "reps": 10, "weight_kg": 10})
    sessions[0]["exercises"][2]["sets"][0]["duration_s"] = 40
    out = daily_muscle_load(sessions)
    d = out[date(2026, 10, 15)]
    assert d["volume_kg"] == 4 * 12 * 23 + 4 * 12 * 5          # warm-up sets and planks don't count toward volume
    assert d["working_sets"] == 9 and d["bodyweight_sets"] == 1
    assert d["sets_by_muscle"] == {"chest": 4, "quadriceps": 4, "abdominals": 1}
    assert d["srpe"] == 100.0                                    # incline walk RPE 4 x 25 minutes
    assert out[date(2026, 10, 16)]["volume_kg"] == 200 and out[date(2026, 10, 16)]["srpe"] is None
