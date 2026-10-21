"""Water (issue #125): one reminder on today's date when it is afternoon and less than half of the day's goal is in.

Rules run every hour (ops hourly job), so the reminder appears in the first run after REMIND_FROM_HOUR and disappears
by itself once half the goal is reached (reminders are derived data). One per day: subject "" and the (user, date, rule,
subject) key; a dismissed one stays dismissed for the rest of the day. Parameters and sources: water/params.py.
"""
from ..water import params as WP
from .base import Reminder, RuleResult

KEY = "water_halfway"


def evaluate(ctx) -> RuleResult:
    if ctx.water is None or ctx.water_goal is None:
        return RuleResult(KEY, "insufficient_data", "喝水记录没开放或没有目标")
    now = ctx.now_local()
    if ctx.day != now.date():
        return RuleResult(KEY, "not_triggered", "只提醒当天")
    if now.hour < WP.REMIND_FROM_HOUR:
        return RuleResult(KEY, "not_triggered", f"{WP.REMIND_FROM_HOUR}:00 之前不提醒")
    total, goal = ctx.water.get(ctx.day, 0), ctx.water_goal
    details = {"total_ml": total, "goal_ml": goal}
    if total >= goal * WP.REMIND_SHARE:
        return RuleResult(KEY, "not_triggered", "已经喝到一半", details=details)
    return RuleResult(KEY, "triggered", details=details, reminders=[Reminder(
        KEY, "info", "下午了，水还没喝到一半", f"今天喝了 {total} ml，目标 {goal} ml。",
        evidence=details, title_en="Afternoon, and less than half your water",
        message_en=f"You have had {total} ml today; your goal is {goal} ml.")])
