"""Cross-language golden cases of package M6, rules part (docs/ios-native-2.0.md sections 2.1 and 4): every rule reminder
and the nutrition goal resolution on synthetic inputs, run by the Python reference (body_engine/tests/test_js_golden_rules.py,
which writes test/golden/rules.json and rules_local.json with BODY_WRITE_GOLDEN=1) and replayed on the bundle
(test/rules.test.js).

Standard library only, synthetic data only (a made-up person in Australia/Sydney with a weekly plan of three templates and
basketball on Saturdays; the exercise names are generic gym exercises). Inputs are plain JSON as the phone builds them:
- `runs`: the rules' input `data` (rules/context.to_data), each rule firing, not firing and short of data:
  double progression (top of the range, increments, a short set, too few sets, a later workout, RIR 0, two failures, notes,
  no plan), calories by weekly average weight (fast, slow, gaining, keep, too few weigh-ins, unreadable), pre-workout eating
  (leg day with symptoms and few carbs, rest day, upper body with the water log, basketball, a plan day change to another
  weekday and to a free label), red meat / food caps (none set, over, close, a food limit reached and exceeded, marked grams,
  portions, milk excluded, unreadable, no log), added sugar and sugary drinks, banned and caution exercises (in the plan, in
  the log, too heavy, by the SparkyFitness description marker, unmapped exercise rules), readiness (red on a heavy / rest
  day, illness, sleep debt, no score, no recovery, no plan), the bedtime goal (none, paused, before its start, tonight's
  plan, late nights, a stage and the final target reached, a late wake-up) and water (off, another day, morning, half
  done, short); `common.need` errors and missing SparkyFitness;
- `raw`: the phone's raw sources (rules/context.assemble): goal versions + SparkyFitness fallback goals, weigh-ins + body
  mass, sleep sessions across the 2026-10-04 change to daylight saving time, journal rows, plan day changes;
- `nutrition`: goals and day rows from the log's sums (goals/nutrition.goal_for / day_from_totals) in both languages.
"""
import json
import sys
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cases import dump  # noqa: E402

TZ_NAME = "Australia/Sydney"
FRI, SAT, SUN, MON = "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19"   # Fri = lower body B, Sat = basketball


def D(s: str) -> date:
    return date.fromisoformat(s)


def add(d: str, k: int) -> str:
    return (D(d) + timedelta(days=k)).isoformat()


EX = {   # id -> exercise (the user's library)
    "pull": dict(name="高位下拉", category="strength", modality="weight_reps", primary_muscles=["lats"], description=""),
    "face": dict(name="绳索面拉 + 外旋（轻重量）", category="strength", modality="weight_reps",
                 primary_muscles=["shoulders"], description="【注意】轻重量"),
    "hip": dict(name="杠铃臀推", category="strength", modality="weight_reps", primary_muscles=["glutes"], description=""),
    "bss": dict(name="保加利亚分腿蹲", category="strength", modality="weight_reps", primary_muscles=["quadriceps"],
                description="【注意】前脚离凳一大步"),
    "upright": dict(name="杠铃提拉 / 直立划船（禁用）", category="strength", modality="weight_reps",
                    primary_muscles=["traps"], description="【禁用】永久不做"),
    "incline": dict(name="上斜卧推（器械未注明）", category="strength", modality="weight_reps", primary_muscles=["chest"],
                    description="【注意】器械未注明（迁移自旧计划）"),
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


def _rx(ex_id, sets, low, high, weight, inc=2.5, mode="rep_goal", warmup=False):
    out = [{"set": i + 1, "type": "Working Set", "reps": low, "weight_kg": weight} for i in range(sets)]
    if warmup:
        out.insert(0, {"set": 0, "type": "Warm-up", "reps": 15, "weight_kg": 10})
    return {"exercise_id": ex_id, "name": EX[ex_id]["name"], "category": EX[ex_id]["category"], "progression_mode": mode,
            "rep_goal": high, "increment_value": inc, "sets": out}


def presets(banned=False):
    up = [_rx("pull", 4, 8, 12, 39, warmup=True), _rx("face", 3, 15, 15, 18, mode="manual")]
    if banned:
        up.append(_rx("upright", 3, 10, 12, 20))
    return [
        {"id": 1, "name": "上肢 A", "exercises": up},
        {"id": 2, "name": "下肢 B", "exercises": [_rx("hip", 4, 8, 12, 40, inc=5), _rx("bss", 3, 8, 10, None, inc=None)]},
        {"id": 3, "name": "肩部 routine", "exercises": [_rx("wall", 2, 10, 10, None, mode="manual")]},
    ]


def plan(**kw):
    return {"id": 1, "name": "V3.0", "start_date": "2026-10-01", "end_date": None, "is_active": True,
            "schedule_type": "weekly", "assignments": [
                {"day_of_week": 1, "sort_order": 0, "workout_preset_id": 1, "workout_preset_name": "上肢 A"},
                {"day_of_week": 3, "sort_order": 0, "workout_preset_id": 3, "workout_preset_name": "肩部 routine"},
                {"day_of_week": 5, "sort_order": 1, "workout_preset_id": 3, "workout_preset_name": "肩部 routine"},
                {"day_of_week": 5, "sort_order": 0, "workout_preset_id": 2, "workout_preset_name": "下肢 B"},
                {"day_of_week": 6, "sort_order": 0, "workout_preset_id": None, "exercise_id": "ball", "exercise_name": "篮球"},
            ], **kw}


def sess(d, *exercises, notes=None, name="训练"):
    return {"date": d, "name": name, "notes": notes, "total_duration_minutes": 0, "exercises": [
        {"exercise_id": ex_id, "name": EX[ex_id]["name"], "category": EX[ex_id]["category"],
         "primary_muscles": EX[ex_id]["primary_muscles"], "duration_minutes": 0,
         "sets": [{"set": i + 1, "type": "Working Set", "reps": r, "weight_kg": w, "rir": rir, "rpe": None, "duration_s": None}
                  for i, (r, w, rir) in enumerate(sets)]}
        for ex_id, sets in exercises]}


def S(n, reps, w, rir=None):
    return [(reps, w, rir)] * n


def food(d, name, carbs=40.0, meal="lunch", qty=1, unit="份", **kw):
    row = {"id": f"{d}:{name}:{meal}", "date": d, "time": "12:30", "meal_type": meal, "food": name, "quantity": qty,
           "unit": unit, "calories": 500.0, "protein_g": 30.0, "carbs_g": carbs, "fat_g": 15.0, "saturated_fat_g": 4.0,
           "sugar_g": None, "added_sugar_g": None, "fiber_g": None, "sodium_mg": None, "sugary_drink": False}
    row.update(kw)
    return row


def goal(d, name, kcal, cap=25, default=True):
    return {"date": d, "source": "body_engine", "version_id": 1, "effective_from": "2026-09-01", "day_type": "training",
            "name": name, "calories": kcal, "protein_g": 160, "carbs_g": 220, "fat_g": 60, "sat_fat_max_g": 20,
            "added_sugar_max_g": cap, "fiber_min_g": None, "added_sugar_cap_default": default}


def weights(start: str, n: int, first: float, per_day: float, every: int = 1):
    return [{"date": add(start, k), "weight_kg": round(first + per_day * k, 2), "source": "sparkyfitness"}
            for k in range(n) if k % every == 0]


def score(**kw):
    row = {"recovery": 55, "sleep_score": 80, "sleep_debt_min": 40, "sleep_actual_min": 430, "sleep_need_min": 470,
           "strain": 9.5, "flags": {}}
    row.update(kw)
    return row


def nights_for(day: str, beds: list[int], wakes: list[int] | None = None):
    """sleep_nights rows (goals.sleep.nights_from_sessions shape) for the nights before `day`, newest last."""
    out = []
    n = len(beds)
    for k, b in enumerate(beds):
        night = add(day, -(n - k))
        w = (wakes or [450] * n)[k]
        out.append({"night": night, "bed_off": b, "wake_off": w, "asleep_min": w - b - 20,
                    "bedtime": f"{(b % 1440) // 60:02d}:{b % 60:02d}", "wake": f"{(w % 1440) // 60:02d}:{w % 60:02d}"})
    return out


def base(day=FRI, **kw) -> dict:
    d = {"day": day, "tz": TZ_NAME, "now": f"{day}T16:30:00+11:00", "sessions": [],
         "exercises": {k: {"id": k, **v} for k, v in EX.items()}, "presets": presets(), "plans": [plan()], "meta": META,
         "food": [], "goals": [], "weights": [], "scores": {}, "journal": [], "sleep_goal": None, "sleep_nights": [],
         "sleep_duration_min": None, "overrides": {}, "diet": None, "water": None, "water_goal": None,
         "errors": {}, "missing": {}}
    d.update(deepcopy(kw))
    return d


DIET = [
    {"kind": "food_times_per_week", "target": "炸鸡", "value": 1.0, "basis": "示例依据", "match": ["fried chicken"]},
    {"kind": "low_purine", "target": "", "value": None, "basis": None, "match": None},
    {"kind": "red_meat_g_per_week", "target": "", "value": 300.0, "basis": "示例依据", "match": None},
    {"kind": "red_meat_meals_per_week", "target": "", "value": 2.0, "basis": None, "match": None},
]
GOAL_ROW = {"status": "active", "start_date": "2026-10-05", "pace_min_per_step": 20, "step_days": 3,
            "start_bedtime_off": 120, "target_bedtime": "23:30", "target_wake": None, "target_duration_min": None}


def runs() -> list[dict]:
    r = []

    def run(name, **kw):
        r.append({"name": name, "data": base(**kw)})

    # ---- double progression
    run("prog_up", sessions=[sess("2026-10-10", ("pull", S(4, 12, 39)))])
    run("prog_increment_5", sessions=[sess("2026-10-10", ("hip", S(4, 12, 40)))])
    run("prog_one_short", sessions=[sess("2026-10-10", ("pull", S(3, 12, 39) + [(11, 39, None)]))])
    run("prog_too_few_sets", sessions=[sess("2026-10-10", ("pull", S(3, 12, 39)))])
    run("prog_later_workout", day="2026-10-09", sessions=[sess("2026-10-10", ("pull", S(4, 12, 39)))])
    run("prog_rir_hold", sessions=[sess("2026-10-10", ("pull", S(3, 12, 39, 1) + [(12, 39, 0)]))])
    fail = [(8, 41.5, None), (7, 41.5, None), (7, 41.5, None), (6, 41.5, None)]
    run("prog_two_failures", sessions=[sess("2026-10-10", ("pull", fail)), sess("2026-10-13", ("pull", fail))])
    run("prog_notes_off_plan", sessions=[
        sess("2026-10-12", ("face", S(4, 15, 32)), ("incline", S(4, 12, 23))),
        sess("2026-10-15", ("bss", S(4, 12, 5)), notes="保加利亚蹲回到 3 × 8–10，先用 5kg；其他正常")])
    run("prog_no_plan", plans=[])
    run("need_error", errors={"sessions": "HTTPError: 502 Bad Gateway"})
    run("need_missing", sessions=None, exercises=None, presets=None, plans=None, food=None, goals=None, weights=None,
        missing={"sparky": "还没有连 SparkyFitness"})
    # ---- calories by weekly average weight
    g2 = [goal(add(FRI, k), "训练日（减脂）" if k % 2 == 0 else "休息日（减脂）", 2300 if k % 2 == 0 else 2050) for k in range(7)]
    run("cal_fast", weights=weights(add(FRI, -20), 21, 78.0, -0.2), goals=g2)
    run("cal_slow", weights=weights(add(FRI, -20), 21, 76.0, -0.02), goals=g2)
    run("cal_gaining", weights=weights(add(FRI, -20), 21, 74.0, 0.03, every=2), goals=[])
    run("cal_keep", weights=weights(add(FRI, -20), 21, 77.0, -0.1))
    run("cal_few_weighins", weights=weights(add(FRI, -20), 21, 77.0, -0.1, every=6))
    run("cal_unreadable", weights=None, errors={"weights": "TimeoutError: timed out"})
    # ---- pre-workout eating
    run("fuel_leg_day", journal=[[add(FRI, -5), "low_blood_sugar", 1.0], [add(FRI, -1), "water_l", 2.1],
                                 [add(FRI, -30), "low_blood_sugar", 1.0]],
        food=[food(FRI, "燕麦", carbs=20.0, meal="breakfast"), food(FRI, "鸡胸肉沙拉", carbs=12.5)])
    run("fuel_rest_day", day=SUN)
    run("fuel_upper_water_log", day=MON, water={add(MON, -1): 2450, MON: 300}, water_goal=2600,
        food=[food(MON, "米饭鸡腿", carbs=80.0)], sessions=[sess(MON, ("pull", S(2, 10, 39)), name="上肢 A")])
    run("fuel_basketball", day=SAT)
    run("fuel_day_change", day=SUN, overrides={SUN: {"sessions": [{"dow": 5}, {"label": "游泳 30 分钟"}]}})
    run("fuel_label_only", day=SAT, overrides={SAT: {"sessions": [{"label": "休息"}]}})
    # ---- red meat / food caps
    meat = [food(add(FRI, -6), "炸鸡", meal="dinner"), food(add(FRI, -5), "Fried Chicken 桶", meal="dinner"),
            food(add(FRI, -5), "牛肋条", meal="lunch"),
            food(add(FRI, -4), "培根三明治", meal="breakfast"), food(add(FRI, -3), "牛奶燕麦", meal="breakfast"),
            food(add(FRI, -2), "红烧排骨", meal="dinner", qty=2, unit="份"), food(add(FRI, -1), "牛肉面", meal="lunch", qty=300, unit="g"),
            food(FRI, "午餐便当（红肉 120g）", meal="lunch")]
    run("caps_none")
    run("caps_over", diet=DIET, food=meat, journal=[[add(FRI, -3), "red_meat_g", 80.0]])
    run("caps_close", diet=[dict(DIET[2], value=500.0), dict(DIET[0], value=2.0)],
        food=[food(add(FRI, -6), "炸鸡", meal="dinner"), food(add(FRI, -1), "炸鸡", meal="dinner"),
              food(add(FRI, -3), "牛肋条", meal="dinner"), food(add(FRI, -4), "羊肉串", meal="dinner"),
              food(add(FRI, -2), "steak", meal="dinner")])
    run("caps_unreadable", diet=DIET, food=None, errors={"food": "HTTPError: 500"})
    run("caps_no_log", diet=DIET, food=[food(add(FRI, -9), "牛排")])
    run("caps_no_sparky", diet=DIET, food=None)
    # ---- added sugar and sugary drinks
    sweet = [food(FRI, "奶茶", carbs=55.0, sugar_g=45.0, added_sugar_g=38.0, sugary_drink=True),
             food(FRI, "蛋糕", carbs=40.0, sugar_g=30.0, added_sugar_g=20.5),
             food(FRI, "鸡蛋", carbs=1.0, sugar_g=0.5),
             food(add(FRI, -2), "可乐", carbs=35.0, sugar_g=35.0, sugary_drink=True),
             food(add(FRI, -4), "橙汁", carbs=26.0, sugar_g=22.0, sugary_drink=True)]
    run("sugar_over", food=sweet, goals=[goal(FRI, "训练日", 2300)])
    run("sugar_own_cap", food=[food(FRI, "酸奶", carbs=20.0, sugar_g=18.0, added_sugar_g=40.0)],
        goals=[goal(FRI, "训练日", 2300, cap=30, default=False)])
    run("sugar_not_triggered", food=[food(FRI, "米饭", carbs=60.0, added_sugar_g=0.0)], goals=[goal(FRI, "训练日", 2300)])
    run("sugar_no_food_today", food=[food(add(FRI, -1), "米饭")], goals=[])
    run("sugar_no_sparky", food=None)
    # ---- banned / caution exercises
    run("ex_banned_everywhere", presets=presets(banned=True), sessions=[
        sess(add(FRI, -3), ("upright", S(3, 10, 20))), sess(add(FRI, -2), ("face", [(15, 18, None), (12, 22.5, None)])),
        sess(add(FRI, -20), ("upright", S(3, 10, 20)))])
    run("ex_quiet", day=SUN, meta=META + [dict(exercise_ref="dips", app_exercise_id=None, status="banned", aliases=[],
                                                 reason="肩前侧", cues=None)])
    run("ex_marker_caution", day=MON, plans=[plan(assignments=[
        {"day_of_week": 1, "sort_order": 0, "workout_preset_id": None, "exercise_id": "incline", "exercise_name": "上斜卧推"},
        {"day_of_week": 1, "sort_order": 1, "workout_preset_id": 9, "workout_preset_name": "旧模板"}])])
    # ---- readiness
    run("ready_red_heavy", scores={FRI: score(recovery=22, sleep_debt_min=250,
                                              flags={"illness_warning": "静息心率比基线高 6、HRV 低于基线 80%"})})
    run("ready_red_rest", day=SUN, scores={SUN: score(recovery=30, sleep_debt_min=130, sleep_need_min=None)})
    run("ready_no_row")
    run("ready_no_recovery", scores={FRI: score(recovery=None, sleep_debt_min=None,
                                                flags={"baseline_insufficient": "基线不够", "x": "y", "no_sleep": "没睡"})})
    run("ready_no_plan", plans=None, scores={FRI: score(recovery=70)})
    run("ready_fine", scores={FRI: score(recovery=70, sleep_debt_min=30)})
    # ---- bedtime goal
    run("sleep_no_goal")
    run("sleep_paused", sleep_goal=dict(GOAL_ROW, status="paused"))
    run("sleep_before_start", day="2026-10-03", sleep_goal=GOAL_ROW)
    run("sleep_tonight_late", sleep_goal=GOAL_ROW, sleep_duration_min=465,
        sleep_nights=nights_for(FRI, [60, 60, 60, 60, 200, 210, 220]))
    run("sleep_wake_target", sleep_goal=dict(GOAL_ROW, target_wake="07:15"),
        sleep_nights=nights_for(FRI, [60, 50, 40, 70, 30], [480, 460, 470, 900, 900]))
    run("sleep_final_reached", sleep_goal=dict(GOAL_ROW, target_bedtime="00:30"),
        sleep_nights=nights_for(FRI, [20, 30, 25, 40, 35, 15, 30]))
    run("sleep_stage_reached", day="2026-10-11", sleep_goal=dict(GOAL_ROW, target_bedtime="22:30", start_bedtime_off=150),
        sleep_nights=nights_for("2026-10-11", [100, 95, 105, 90, 100, 98, 102]))
    # ---- water
    run("water_off")
    run("water_other_day", water={FRI: 500}, water_goal=2600, now="2026-10-17T16:30:00+11:00")
    run("water_morning", water={FRI: 500}, water_goal=2600, now="2026-10-16T09:00:00+11:00")
    run("water_half", water={FRI: 1400, add(FRI, -1): 2000}, water_goal=2600)
    run("water_short", water={FRI: 900}, water_goal=3100, now="2026-10-16T15:00:00+11:00")
    return r


# ---------------------------------------------------------------- raw (rules/context.assemble)
VERSIONS = [   # newest effective_from first, as goals/nutrition.versions returns them
    {"id": 3, "effective_from": "2026-10-14", "name": "减脂 V2",
     "training": {"calories": 2300, "protein_g": 170, "carbs_g": 230, "fat_g": 62, "sat_fat_max_g": 18, "added_sugar_max_g": None,
                  "fiber_min_g": 30},
     "rest": {"calories": 2000, "protein_g": 165, "carbs_g": 170, "fat_g": 60, "sat_fat_max_g": 18, "added_sugar_max_g": 20,
              "fiber_min_g": 30}, "training_weekdays": [1, 3, 5]},
    {"id": 2, "effective_from": "2026-09-20", "name": None,
     "training": {"calories": 2400, "protein_g": 160, "carbs_g": 260, "fat_g": 65, "sat_fat_max_g": None, "added_sugar_max_g": None,
                  "fiber_min_g": None},
     "rest": {"calories": 2100, "protein_g": 160, "carbs_g": 200, "fat_g": 62, "sat_fat_max_g": None, "added_sugar_max_g": None,
              "fiber_min_g": None}, "training_weekdays": [1, 2, 4, 6]},
]


def _sessions(last: str, n: int) -> list[dict]:
    """Main sleep sessions (and a nap) of the n nights up to `last`'s morning, times in the zone's offset of the day."""
    out = []
    for k in range(n):
        wake = add(last, -(n - 1 - k))
        night = add(wake, -1)
        off_n = "+11:00" if night >= "2026-10-04" else "+10:00"
        off_w = "+11:00" if wake >= "2026-10-04" else "+10:00"
        h, m = (23 + (k % 3), (k * 17) % 60) if k % 4 else (0, 40)
        start = f"{night}T{h:02d}:{m:02d}:00{off_n}" if h < 24 else f"{wake}T{h - 24:02d}:{m:02d}:00{off_w}"
        if h == 0:
            start = f"{wake}T00:{m:02d}:00{off_w}"
        out.append({"wake_date": wake, "start_at": start, "end_at": f"{wake}T07:{(k * 7) % 60:02d}:00{off_w}",
                    "asleep_min": 380 + (k * 13) % 90, "is_main": True})
        if k % 5 == 2:
            out.append({"wake_date": wake, "start_at": f"{wake}T14:00:00{off_w}", "end_at": f"{wake}T14:30:00{off_w}",
                        "asleep_min": 25, "is_main": False})
    return out


def raw_base(day: str, **kw) -> dict:
    data = base(day)
    raw = {k: data[k] for k in ("day", "tz", "now", "sessions", "exercises", "presets", "plans", "meta", "food", "diet",
                                "water", "water_goal", "errors", "missing")}
    raw.update({"nutrition_versions": deepcopy(VERSIONS), "sparky_goals": {}, "sparky_weights": weights(add(day, -20), 21, 76.0, -0.12, 2),
                "body_mass": [{"date": add(day, -k), "value": round(75.2 + 0.05 * k, 2)} for k in range(0, 21, 3)],
                "scores": [{"date": add(day, -k), **score(recovery=40 + 7 * k)} for k in range(0, 8)],
                "journal": [{"date": add(day, -1), "key": "water_l", "value": 2.4},
                            {"date": add(day, -3), "key": "underfed_training", "value": 1.0},
                            {"date": add(day, -3), "key": "alcohol", "value": 2.0}],
                "sleep_goal": GOAL_ROW, "sleep_sessions": _sessions(add(day, 1), 24), "sleep_need": 470,
                "overrides": [{"date": add(day, 2), "sessions": [{"dow": 1}]}]})
    raw.update(deepcopy(kw))
    return raw


def raw_runs() -> list[dict]:
    return [
        {"name": "raw_after_versions", "raw": raw_base(FRI)},
        {"name": "raw_dst_fallback_goals", "raw": raw_base("2026-10-06", nutrition_versions=deepcopy(VERSIONS[:1]),
                                                           sparky_goals={"2026-10-06": {"name": "默认", "calories": 2200,
                                                                                        "protein_g": 140, "carbs_g": 250, "fat_g": 70},
                                                                         "2026-10-07": None},
                                                           sleep_need=None)},
        {"name": "raw_no_sparky", "raw": raw_base("2026-10-20", nutrition_versions=None, sparky_weights=None, sessions=None,
                                                  exercises=None, presets=None, plans=None, food=None,
                                                  missing={"sparky": "还没有连 SparkyFitness"}, sleep_goal=None)},
        {"name": "raw_weights_error", "raw": raw_base(FRI, sparky_weights=None, body_mass=[],
                                                      errors={"weights": "TimeoutError: timed out"})},
    ]


# ---------------------------------------------------------------- nutrition goals / day rows from the log's sums
def nutrition_case() -> dict:
    totals = {"2026-09-30": {"entries": 4, "calories": 2210.5, "protein_g": 150.0, "carbs_g": 240.0, "fat_g": 70.0,
                             "saturated_fat_g": 20.0, "fiber_g": 25.0, "sugar_g": 50.0, "sodium_mg": 2100.0},
              "2026-10-13": {"entries": 3, "calories": 1980.0, "protein_g": 160.5, "carbs_g": 190.0, "fat_g": 55.0,
                             "saturated_fat_g": None, "fiber_g": None, "sugar_g": None, "sodium_mg": None},
              "2026-10-14": {"entries": 5, "calories": 2650.0, "protein_g": 140.0, "carbs_g": 300.0, "fat_g": 80.0,
                             "saturated_fat_g": 25.0, "fiber_g": 20.0, "sugar_g": 60.0, "sodium_mg": 3000.0},
              "2026-10-15": {"entries": 2, "calories": None, "protein_g": None, "carbs_g": None, "fat_g": None,
                             "saturated_fat_g": None, "fiber_g": None, "sugar_g": None, "sodium_mg": None},
              "2026-10-16": {"entries": 0, "calories": None, "protein_g": None, "carbs_g": None, "fat_g": None,
                             "saturated_fat_g": None, "fiber_g": None, "sugar_g": None, "sodium_mg": None}}
    sparky = {"2026-09-18": {"name": "默认", "calories": 2200, "protein_g": 140, "carbs_g": 250, "fat_g": 70}}
    return {"versions": VERSIONS, "totals": totals, "sparky": sparky,
            "calls": [{"from": "2026-09-17", "to": "2026-10-17", "lang": lang} for lang in ("zh", "en")]}


def all_cases() -> dict:
    return {"tz": TZ_NAME, "runs": runs(), "raw": raw_runs(), "nutrition": nutrition_case()}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--dump":
        Path(sys.argv[2]).write_text(json.dumps(dump(all_cases()), ensure_ascii=False))
    else:
        print(json.dumps(dump(all_cases()), ensure_ascii=False)[:2000])
