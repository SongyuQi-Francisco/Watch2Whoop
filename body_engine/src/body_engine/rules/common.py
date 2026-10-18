"""Small helpers shared by rules."""
from datetime import date, timedelta

from .base import RuleResult

SOURCE_LABELS = {"sessions": "训练记录", "presets": "训练模板", "plans": "每周训练计划", "exercises": "动作库",
                 "food": "饮食记录", "goals": "饮食目标", "weights": "体重"}


def need(ctx, rule_key: str, *sources: str) -> RuleResult | None:
    """Return a result directly when the required SparkyFitness data cannot be read: read failure = error (keep old reminders), not connected = insufficient data."""
    failed = [s for s in sources if s in ctx.errors]
    if failed:
        return RuleResult(rule_key, "error", "读不到 " + "、".join(f"{SOURCE_LABELS.get(s, s)}（{ctx.errors[s]}）"
                                                                 for s in failed))
    if any(getattr(ctx, s) is None for s in sources):
        reason = ctx.missing.get("sparky") or "没有数据"
        return RuleResult(rule_key, "insufficient_data", f"没有 SparkyFitness 数据：{reason}")
    return None


def days_back(day: date, n: int) -> list[date]:
    """[day − n + 1, day]"""
    return [day - timedelta(days=k) for k in range(n - 1, -1, -1)]
