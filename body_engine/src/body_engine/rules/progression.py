"""Double-progression weight increase / reduce weight after consecutive failures (body-management.md 4.5).

- Rep range, increment and planned set count come from the SparkyFitness workout templates referenced by the current weekly plan (rep_goal = top, per-set reps = bottom,
  increment_value = increment); exercises whose progression_mode is not rep_goal (cable face pull, bands, plank, etc.) get no weight-increase prompt.
- Look at the working sets (warm-up sets excluded) of each exercise's latest workout (<= evaluation day):
  - All working sets at reps >= top, and set count not below the planned count -> add one increment next time; if RIR is logged and the lowest < 1 (failure) -> hold first.
  - At the same weight, 2 workouts in a row have a working set below the bottom -> reduce one increment (inferred; not in body-management.md).
- Weight is as logged (dumbbells per single dumbbell, unilateral exercises per side).
"""
import re
from datetime import date, timedelta

from . import params as P
from .base import Reminder, RuleResult
from .common import need

KEY = "progression"


def _working(sets: list[dict]) -> list[dict]:
    return [s for s in sets if (s.get("type") or "").lower() not in P.WARMUP_SET_TYPES and s.get("reps") is not None]


def _history(ctx, exercise_id: str) -> list[dict]:
    """Each workout of this exercise (newest first): {date, sets, notes}; sets appearing several times on one day are merged."""
    by_day: dict[str, dict] = {}
    for s in ctx.sessions or []:
        if date.fromisoformat(s["date"][:10]) > ctx.day:
            continue
        for e in s["exercises"]:
            if e["exercise_id"] == exercise_id:
                d = by_day.setdefault(s["date"][:10], {"date": s["date"][:10], "sets": [], "notes": s.get("notes")})
                d["sets"] += _working(e["sets"])
    return [by_day[k] for k in sorted(by_day, reverse=True) if by_day[k]["sets"]]


def _top_weight(sets: list[dict]) -> float:
    return max((s.get("weight_kg") or 0) for s in sets)


def _note_about(ctx, ex_id: str, name: str, notes: str | None) -> str | None:
    """Sentences in the workout notes that mention this exercise (notes cover the whole workout; matched by the first 4 characters of the name / aliases)."""
    if not notes:
        return None
    meta = ctx.meta_by_app_id.get(ex_id) or {}
    keys = {k for k in [name.split("（")[0][:4], *(meta.get("aliases") or [])] if k and len(k) >= 2}
    hits = [x.strip() for x in re.split(r"[。；;\n]", notes) if any(k in x for k in keys)]
    return "；".join(hits) or None


def _fmt(w: float) -> str:
    return f"{w:g} kg" if w else "自重"


def _fmt_en(w: float) -> str:
    return f"{w:g} kg" if w else "bodyweight"


def evaluate(ctx) -> RuleResult:
    if (r := need(ctx, KEY, "sessions", "presets", "plans", "exercises")) is not None:
        return r
    plan = ctx.active_plan()
    if plan is None:
        return RuleResult(KEY, "insufficient_data", "SparkyFitness 里没有当天生效的每周训练计划，不知道次数区间")
    rx = ctx.plan_prescriptions()
    reminders, never, checked, skipped = [], [], [], []
    for ex_id, p in rx.items():
        if p["progression_mode"] != "rep_goal" or not p["rep_high"]:
            skipped.append(p["name"])
            continue
        hist = _history(ctx, ex_id)
        if not hist:
            never.append(p["name"])
            continue
        last = hist[0]
        sets, w = last["sets"], _top_weight(last["sets"])
        low, high = p["rep_low"] or p["rep_high"], p["rep_high"]
        inc = p["increment_kg"] or P.INCREMENT_FALLBACK_KG["lower" if ctx.is_leg_exercise(ex_id) else "upper"]
        reps = [s["reps"] for s in sets]
        rirs = [s["rir"] for s in sets if s.get("rir") is not None]
        checked.append(p["name"])
        evidence = {"exercise_id": ex_id, "exercise": p["name"], "template": p["preset"], "last_session": last["date"],
                    "sets": [{"reps": s["reps"], "weight_kg": s.get("weight_kg"), "rir": s.get("rir")} for s in sets],
                    "rep_range": [low, high], "planned_sets": p["planned_sets"], "increment_kg": inc,
                    "session_notes": (last["notes"] or "")[:300] or None, "source": "body-management.md 4.5"}
        enough_sets = not P.REQUIRE_PLANNED_SETS or len(sets) >= p["planned_sets"]
        if min(reps) >= high and enough_sets:
            if rirs and min(rirs) < P.MIN_RIR_FOR_INCREASE:
                reminders.append(Reminder(
                    KEY, "info", f"{p['name']}：到上限但做到力竭，先保持 {_fmt(w)}",
                    f"{last['date']} 每组都做到 {high} 次，但最低 RIR {min(rirs)}（力竭）。4.3 要求每组留 1–2 次余力"
                    f"（力竭时容易耸肩代偿），先在 {_fmt(w)} 做到留 1 次余力再加重。",
                    subject=f"{ex_id}:{w:g}:hold", evidence=evidence, sticky=True,
                    title_en=f"{p['name']}: top of the range but to failure, stay at {_fmt_en(w)}",
                    message_en=f"On {last['date']} every set reached {high} reps, but the lowest RIR was {min(rirs)} (failure). "
                               "4.3 asks for 1–2 reps in reserve on every set (at failure you tend to shrug to compensate); "
                               f"stay at {_fmt_en(w)} until you have 1 rep in reserve, then add weight."))
            else:
                nxt = w + inc
                note = _note_about(ctx, ex_id, p["name"], last["notes"])
                reminders.append(Reminder(
                    KEY, "info", f"下次加重：{p['name']} {_fmt(w)} → {_fmt(nxt)}",
                    f"{last['date']} 的 {len(sets)} 个工作组都做到了区间上限 {high} 次（{'/'.join(map(str, reps))}）。"
                    f"按双进阶，下次用 {_fmt(nxt)}，回到区间下限 {low} 次重新练（哑铃按单只、单侧动作按每侧）。"
                    + (f"\n那次训练的备注提到这个动作：「{note}」。和这条建议不一致时以备注和你的感觉为准。" if note else ""),
                    subject=f"{ex_id}:{w:g}:up", evidence={**evidence, "next_weight_kg": nxt}, sticky=True,
                    title_en=f"Add weight next time: {p['name']} {_fmt_en(w)} → {_fmt_en(nxt)}",
                    message_en=f"On {last['date']} all {len(sets)} working sets reached the top of the range, {high} reps "
                               f"({'/'.join(map(str, reps))}). Double progression: next time use {_fmt_en(nxt)} and start again "
                               f"at the bottom of the range, {low} reps (dumbbells per dumbbell, one-sided exercises per side)."
                               + (f"\nThe notes of that workout mention this exercise: “{note}”. If they disagree with this "
                                  "advice, go by the notes and how you feel." if note else "")))
            continue
        # Consecutive failure: the last few workouts were all at the same weight with a set below the bottom
        streak = 0
        for h in hist:
            if _top_weight(h["sets"]) != w or min(x["reps"] for x in h["sets"]) >= low:
                break
            streak += 1
        if streak >= P.FAIL_STREAK:
            nxt = max(w - inc, 0)
            reminders.append(Reminder(
                KEY, "warning", f"连续没做到下限：{p['name']} 建议减到 {_fmt(nxt)}",
                f"最近 {streak} 次（{'、'.join(h['date'] for h in hist[:streak])}）在 {_fmt(w)} 都有工作组没做到 {low} 次。"
                f"减一档到 {_fmt(nxt)}，动作做标准、每组留 1–2 次余力，重新从区间下限往上加。",
                subject=f"{ex_id}:{w:g}:down", evidence={**evidence, "fail_streak": streak, "next_weight_kg": nxt},
                sticky=True,
                title_en=f"Missed the bottom of the range again: {p['name']}, drop to {_fmt_en(nxt)}",
                message_en=f"In the last {streak} workouts ({', '.join(h['date'] for h in hist[:streak])}) at {_fmt_en(w)} a "
                           f"working set fell short of {low} reps. Drop one step to {_fmt_en(nxt)}, keep strict form with 1–2 "
                           "reps in reserve per set, and build back up from the bottom of the range."))
    recent_cut = (ctx.day - timedelta(days=28)).isoformat()
    off_plan = sorted({e["name"] for s in ctx.sessions or [] if recent_cut <= s["date"][:10] <= ctx.day.isoformat()
                       for e in s["exercises"] if e["exercise_id"] not in rx and (e.get("category") or "strength") == "strength"})
    details = {"checked": checked, "never_logged": never, "no_progression_in_template": skipped,
               "logged_but_not_in_plan_28d": off_plan}
    if reminders:
        return RuleResult(KEY, "triggered", f"检查了 {len(checked)} 个动作", reminders, details)
    if not checked:
        return RuleResult(KEY, "insufficient_data", "当前计划里的动作还没有训练记录", details=details)
    return RuleResult(KEY, "not_triggered", f"检查了 {len(checked)} 个动作，都还没到区间上限、也没有连续失败", details=details)
