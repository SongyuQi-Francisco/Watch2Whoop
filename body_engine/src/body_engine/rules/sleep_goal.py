"""Reminders for the bedtime goal (goals/: plan and progress; parameters and rationale in goals/params.py, citing body-management.md 6.1, 6.2).

- Tonight: target bedtime + wind-down schedule (screens off, coffee, dinner / training, wake time and morning sunlight)
- 3 nights in a row more than 30 minutes later than that night's target -> deviation reminder
- Average of the last 7 nights reached this plan stage / the final target -> encouragement (once per stage)
Wording follows the Today page style: one-sentence title, one item per line in the body.
"""
from datetime import timedelta

from ..goals import params as G
from ..goals import sleep as S
from .base import Reminder, RuleResult

KEY = "sleep_goal"


def _caffeine_cutoff(t: int) -> int:
    """The earlier of 8 hours before the target and 12:00 noon (6.2.4); both are minutes relative to tonight's midnight (today noon = -720)."""
    return min(t - G.CAFFEINE_BEFORE_MIN, G.CAFFEINE_NOON_CUTOFF_MIN - 1440)


def evaluate(ctx) -> RuleResult:
    goal = ctx.sleep_goal
    if goal is None:
        return RuleResult(KEY, "insufficient_data", "还没设入睡时间目标")
    if goal["status"] != "active":
        return RuleResult(KEY, "not_triggered", "入睡目标已暂停")
    night, nights, pace = ctx.day, ctx.sleep_nights, goal["pace_min_per_step"]
    plan = goal["plan_rows"]
    t = S.target_for(plan, nights, night, pace)
    if t is None:
        return RuleResult(KEY, "not_triggered", f"计划从 {goal['start_date']} 那晚开始")
    final = S.to_off(goal["target_bedtime"])
    recent = S.window(nights, night)
    last = recent[-1] if recent else None
    wake = S.to_off(goal["target_wake"]) if goal["target_wake"] else t + (ctx.sleep_duration_min or G.DEFAULT_DURATION_MIN)
    cut = _caffeine_cutoff(t)
    woke = last["wake_off"] - 1440 if last else None   # when I got up this morning (relative to tonight's midnight)
    lines = [f"{S.fmt(t - G.SCREEN_OFF_BEFORE_MIN)} 起停屏、调暗灯光",
             "今天别再喝咖啡" if woke is not None and woke >= cut else f"咖啡 {S.fmt(cut)} 前",
             f"晚饭、训练 {S.fmt(t - G.DINNER_BEFORE_MIN)} 前结束",
             f"{S.fmt(wake)} 起床；{G.MORNING_LIGHT}"]
    stage = S.stage_for(plan, night)
    evidence = {"night": night.isoformat(), "tonight_target": S.fmt(t), "plan_stage": S.fmt(stage["bed_off"]),
                "final_target": goal["target_bedtime"], "wake_target": S.fmt(wake),
                "last_night": {"bedtime": last["bedtime"], "wake": last["wake"]} if last else None,
                "source": "body-management.md 6.1、6.2；goals/params.py"}
    reminders = [Reminder(KEY, "info", f"今晚 {S.fmt(t)} 前睡", "\n".join(lines), subject="", evidence=evidence)]

    # Consecutive deviation: the last 3 nights (with consecutive data) all more than 30 minutes later than that night's target
    streak = []
    for k in range(1, G.DEVIATION_NIGHTS + 1):
        d = night - timedelta(days=k)
        x = next((n for n in recent if n["night"] == d), None)
        tt = S.target_for(plan, nights, d, pace)
        if x is None or tt is None or x["bed_off"] <= tt + G.DEVIATION_MIN:
            break
        streak.append(x["bed_off"] - tt)
    if len(streak) >= G.DEVIATION_NIGHTS:
        reminders.append(Reminder(KEY, "warning", f"连续 {len(streak)} 晚比目标晚",
                                  f"平均晚 {round(sum(streak) / len(streak))} 分钟。\n今晚先守住 {S.fmt(t)}；按时起床、晒太阳。",
                                  subject="deviation", evidence={**evidence, "late_by_min": streak}))
    # Stage reached / final reached (once per stage: sticky + subject carries the stage time)
    a = S.avg(recent, "bed_off")
    if a is not None and a <= final + G.ON_TARGET_TOL_MIN:
        reminders.append(Reminder(KEY, "info", f"达成目标：7 晚平均 {S.fmt(a)}",
                                  f"目标 {goal['target_bedtime']}，保持住起床时间和早上晒太阳。",
                                  subject=f"final:{goal['target_bedtime']}", evidence=evidence, sticky=True))
    elif a is not None and stage["bed_off"] != final and stage is not plan[0] and a <= stage["bed_off"] + G.ON_TARGET_TOL_MIN:
        reminders.append(Reminder(KEY, "info", f"到 {S.fmt(stage['bed_off'])} 这一档了",
                                  f"7 晚平均 {S.fmt(a)}。按计划继续往前提，最终 {goal['target_bedtime']}。",
                                  subject=f"stage:{S.fmt(stage['bed_off'])}", evidence=evidence, sticky=True))
    return RuleResult(KEY, "triggered", reminders=reminders,
                      details={"tonight_target": S.fmt(t), "avg_bedtime_7d": S.fmt(a), "nights_7d": len(recent)})
