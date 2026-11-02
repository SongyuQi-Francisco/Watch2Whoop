"""Synthetic food / workout / weight records for the Quick Start demo user: meals, strength sessions and weigh-ins for
the last 3 weeks.

Runs inside the body-engine container (quickstart/run.py pipes it to `python -`, argv: user id) and writes through
Body Engine's write path (assistant/sparky_write.py), the same code the AI coach uses: into Body Engine's own tables
(own_food_only, the default), or into SparkyFitness with ./quickstart.sh --sparkyfitness.
All values are made up. Idempotent: entries that already exist are skipped.
"""
import random
import sys
from datetime import timedelta

from body_engine import db
from body_engine.assistant import sparky_write as sw

MEALS = {
    "breakfast": [("燕麦 + 牛奶 + 蓝莓 Oatmeal with milk and blueberries", "1 碗（燕麦 60g、牛奶 250ml）", 420, 20, 62, 10),
                  ("全麦吐司 + 2 个鸡蛋 Wholegrain toast with 2 eggs", "2 片 + 2 个", 390, 24, 34, 16)],
    "lunch": [("鸡胸肉糙米饭 + 西兰花 Chicken, brown rice and broccoli", "鸡胸 180g、米饭 200g", 610, 52, 70, 11),
              ("三文鱼沙拉 Salmon salad", "三文鱼 150g", 520, 36, 18, 32)],
    "dinner": [("番茄牛肉意面 Beef and tomato pasta", "1 盘（意面 100g 生重）", 680, 40, 82, 18),
               ("豆腐蔬菜炒饭 Tofu vegetable fried rice", "1 盘", 560, 26, 74, 16)],
    "snacks": [("希腊酸奶 Greek yoghurt", "170 g", 150, 17, 7, 4), ("苹果 Apple", "1 个（约 180g）", 95, 0, 25, 0)],
}
UPPER = [("Bench Press", 60.0), ("Bent Over Row", 55.0), ("Dumbbell Shoulder Press", 18.0)]
LOWER = [("Barbell Squat", 80.0), ("Romanian Deadlift", 70.0), ("Leg Press", 140.0)]


def main() -> None:
    user_id = int(sys.argv[1])
    rng = random.Random(11)
    conn = db.connect()
    today = sw.user_today(conn, user_id)
    client = sw.backend(conn, user_id)
    weight, n_food, n_workout, n_weight = 83.2, 0, 0, 0
    for back in range(20, -1, -1):
        d = today - timedelta(days=back)
        items = []
        for meal, options in MEALS.items():
            if meal == "snacks" and rng.random() < 0.4:
                continue
            food, amount, kcal, p, c, f = options[rng.randrange(len(options))]
            items.append({"date": d.isoformat(), "meal": meal, "time": None, "food": food, "amount": amount,
                          "calories": kcal, "protein_g": p, "carbs_g": c, "fat_g": f, "saturated_fat_g": None,
                          "alcohol_g": None, "notes": "demo"})
        if back == 0:
            items = items[:1]  # today: breakfast only so far
        n_food += len(sw.log_food(conn, user_id, items, allow_duplicates=False, client=client)["created"])
        if d.weekday() in (0, 3):  # Monday upper body, Thursday lower body (the watch logs these as strength sessions)
            plan, name = (UPPER, "Upper A") if d.weekday() == 0 else (LOWER, "Lower B")
            progress = 1 + 0.02 * ((20 - back) // 7)
            exercises = [{"exercise": ex, "exercise_id": None, "notes": None,
                          "sets": [{"reps": 8 + (i == 2), "weight_kg": round(kg * progress / 2.5) * 2.5, "rir": 2,
                                    "duration_s": None, "set_type": None, "notes": None} for i in range(3)]}
                         for ex, kg in plan]
            res = sw.log_workout(conn, user_id, {"date": d.isoformat(), "name": name, "exercises": exercises,
                                                 "allow_duplicate": False, "create_missing_exercises": True,
                                                 "notes": "demo"}, client=client)
            n_workout += res["created"] is not None
        if back % 2 == 0:
            weight += rng.gauss(-0.1, 0.2)
            res = sw.log_weight(conn, user_id, {"date": d.isoformat(), "weight_kg": round(weight, 1),
                                                "overwrite": False}, client=client)
            n_weight += res["written"] is not None
    print(f"demo data: {n_food} food entries, {n_workout} strength sessions, {n_weight} weigh-ins")


if __name__ == "__main__":
    main()
