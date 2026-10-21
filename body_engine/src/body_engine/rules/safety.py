"""Training safety: banned / caution exercises (body-management.md 4.3, exercise_meta), lowering intensity combined with scores (system design section 7)."""
from datetime import date, timedelta

from . import params as P
from .base import Reminder, RuleResult
from .common import need

EXERCISES = "exercise_restrictions"
READINESS = "readiness"


def _meta_text(meta: dict | None, key: str) -> str:
    """A text field of exercise_meta, with a full stop appended."""
    t = ((meta or {}).get(key) or "").strip()
    return t + "。" if t and t[-1] not in "。！？.!?" else t


def exercise_restrictions(ctx) -> RuleResult:
    if (r := need(ctx, EXERCISES, "plans", "presets", "exercises", "sessions")) is not None:
        return r
    reminders = []
    # 1) The current plan (all templates + standalone exercises) contains a banned exercise
    plan = ctx.active_plan()
    in_plan = {}
    if plan is not None:
        for a in plan["assignments"]:
            items = ((ctx.presets or {}).get(a.get("workout_preset_id")) or {}).get("exercises", []) \
                if a.get("workout_preset_id") is not None else [{"exercise_id": a["exercise_id"], "name": a.get("exercise_name")}]
            for x in items:
                in_plan.setdefault(x["exercise_id"], {"name": ctx.exercise_name(x["exercise_id"], x.get("name")),
                                                      "template": a.get("workout_preset_name")})
    for ex_id, info in in_plan.items():
        status, meta, basis = ctx.status_of(ex_id)
        if status == "banned":
            reminders.append(Reminder(
                EXERCISES, "alert", f"训练计划里有禁用动作：{info['name']}",
                f"「{info['template'] or plan['name']}」里有 {info['name']}，它是永久不做的动作（4.3）。"
                f"{_meta_text(meta, 'reason')}请在 SparkyFitness 里把它从模板删掉。{_meta_text(meta, 'cues')}",
                subject=f"plan:{ex_id}", sticky=True,
                evidence={"exercise_id": ex_id, "exercise": info["name"], "template": info["template"],
                          "status_basis": basis, "source": "body-management.md 4.3"},
                # exercise / template names and the exercise_meta reason / cues are the user's own text (kept as written)
                title_en=f"Banned exercise in your training plan: {info['name']}",
                message_en=f"“{info['template'] or plan['name']}” contains {info['name']}, an exercise you never do (4.3). "
                           f"{_meta_text(meta, 'reason')}Remove it from the template in SparkyFitness. "
                           f"{_meta_text(meta, 'cues')}".rstrip()))
    # 2) A banned exercise was done in recent training logs / a caution exercise was too heavy
    caps = {ctx.meta_by_ref[ref]["app_exercise_id"]: (ref, kg) for ref, kg in P.CAUTION_MAX_WEIGHT_KG.items()
            if ref in ctx.meta_by_ref and ctx.meta_by_ref[ref].get("app_exercise_id")}
    banned_since = (ctx.day - timedelta(days=P.BANNED_LOG_LOOKBACK_DAYS - 1)).isoformat()
    caution_since = (ctx.day - timedelta(days=P.CAUTION_LOG_LOOKBACK_DAYS - 1)).isoformat()
    for s in ctx.sessions or []:
        d = s["date"][:10]
        if d > ctx.day.isoformat():
            continue
        for e in s["exercises"]:
            status, meta, basis = ctx.status_of(e["exercise_id"])
            name = ctx.exercise_name(e["exercise_id"], e.get("name"))
            sets = [{"reps": x.get("reps"), "weight_kg": x.get("weight_kg")} for x in e["sets"]]
            if status == "banned" and d >= banned_since:
                reminders.append(Reminder(
                    EXERCISES, "warning", f"{d} 做了禁用动作：{name}",
                    f"{name} 是永久不做的动作（4.3）。{_meta_text(meta, 'reason')}下次换成：{_meta_text(meta, 'cues') or '计划里的替代动作。'}",
                    subject=f"log:{e['exercise_id']}:{d}", sticky=True,
                    evidence={"exercise_id": e["exercise_id"], "exercise": name, "date": d, "sets": sets,
                              "status_basis": basis, "source": "body-management.md 4.3"},
                    title_en=f"Banned exercise on {d}: {name}",
                    message_en=f"{name} is an exercise you never do (4.3). {_meta_text(meta, 'reason')}Next time do instead: "
                               f"{_meta_text(meta, 'cues') or 'the alternative in your plan.'}"))
            if e["exercise_id"] in caps and d >= caution_since:
                ref, cap = caps[e["exercise_id"]]
                heavy = max((x.get("weight_kg") or 0) for x in e["sets"]) if e["sets"] else 0
                if heavy > cap:
                    reminders.append(Reminder(
                        EXERCISES, "warning", f"{d} {name} 用了 {heavy:g} kg，超过 {cap:g} kg",
                        f"大重量绳索面拉在练上斜方，4.3 列为永久不做；计划是 {cap:g} kg 左右的轻重量（4.4）。"
                        f"{_meta_text(meta, 'cues')}",
                        subject=f"weight:{e['exercise_id']}:{d}", sticky=True,
                        evidence={"exercise_id": e["exercise_id"], "exercise": name, "date": d, "sets": sets,
                                  "max_kg": cap, "source": "body-management.md 4.3、4.4"},
                        title_en=f"{d} {name}: {heavy:g} kg, over {cap:g} kg",
                        message_en=f"Heavy cable face pulls train the upper traps, and 4.3 lists them as never-do; the plan is "
                                   f"a light weight around {cap:g} kg (4.4). {_meta_text(meta, 'cues')}".rstrip()))
    # 3) Caution exercises in today's plan: list the key points
    caution = []
    for p in ctx.planned_exercises():
        status, meta, basis = ctx.status_of(p["exercise_id"])
        if status == "caution" and p["exercise_id"] not in {c["exercise_id"] for c in caution}:
            desc = ((ctx.exercises or {}).get(p["exercise_id"]) or {}).get("description") or ""
            caution.append({"exercise_id": p["exercise_id"], "exercise": p["name"],
                            "reason": _meta_text(meta, "reason") or None,
                            "cues": _meta_text(meta, "cues") or desc.removeprefix("【注意】").split("（迁移自")[0],
                            "status_basis": basis})
    if caution:
        reminders.append(Reminder(
            EXERCISES, "info", f"今天计划里有 {len(caution)} 个要小心的动作",
            "\n".join(f"· {c['exercise']}：{c['cues']}" + (f"（{c['reason']}）" if c["reason"] else "") for c in caution),
            subject="", evidence={"exercises": caution, "source": "body-management.md 4.3、exercise_meta"},
            title_en=f"Exercises to take care with in today's plan: {len(caution)}",
            message_en="\n".join(f"· {c['exercise']}: {c['cues']}" + (f" ({c['reason']})" if c["reason"] else "")
                                 for c in caution)))
    details = {"plan_exercises": len(in_plan), "unmapped_meta": sum(1 for m in ctx.meta if not m.get("app_exercise_id"))}
    if reminders:
        return RuleResult(EXERCISES, "triggered", reminders=reminders, details=details)
    note = "计划和最近的训练记录里没有禁用动作，今天也没有要小心的动作"
    if details["unmapped_meta"]:
        note += f"（{details['unmapped_meta']} 个 exercise_meta 还没对上 SparkyFitness 的动作 ID，先跑 link-exercises）"
    return RuleResult(EXERCISES, "not_triggered", note, details=details)


def _heavy_day(ctx) -> list[str]:
    """Strength templates in that day's plan (those with weighted exercises) and basketball."""
    out = []
    for item in ctx.planned_for():
        if item["kind"] == "preset":
            if any(((ctx.exercises or {}).get(x["exercise_id"]) or {}).get("modality") == "weight_reps"
                   for x in item["preset"]["exercises"]):
                out.append(item["preset"]["name"])
        elif any(k in (item["name"] or "").lower() for k in P.HEAVY_CARDIO_KEYWORDS):
            out.append(item["name"])
    return out


def readiness(ctx) -> RuleResult:
    row = ctx.scores.get(ctx.day)
    if row is None:
        return RuleResult(READINESS, "insufficient_data", "daily_scores 里没有这天（先跑 compute-scores）")
    flags = row.get("flags") or {}
    reminders = []
    evidence = {"date": ctx.day.isoformat(), "recovery": row["recovery"], "sleep_score": row["sleep_score"],
                "sleep_debt_min": row["sleep_debt_min"], "sleep_actual_min": row["sleep_actual_min"],
                "flags": flags, "source": "系统设计第 7 节、body-management.md 4.3、6.2"}
    heavy = _heavy_day(ctx) if ctx.plans is not None else []
    if row["recovery"] is not None and row["recovery"] < P.RECOVERY_RED:
        if heavy:
            reminders.append(Reminder(
                READINESS, "warning", f"恢复分 {row['recovery']}（红），今天降强度",
                f"今天计划「{'、'.join(heavy)}」。恢复红的日子：每个动作少做 1 组、每组多留 1–2 次余力，不冲重量；"
                "或者换成爬坡 / 肩部 routine。身体不舒服就休息。",
                subject="red_heavy", evidence={**evidence, "planned_heavy": heavy},
                title_en=f"Recovery {row['recovery']} (red): lower the intensity today",
                message_en=f"Today's plan: “{', '.join(heavy)}”. On a red day do one set less per exercise, keep 1–2 more reps "
                           "in reserve per set and don't push the weight; or switch to incline walking / the shoulder routine. "
                           "If you feel unwell, rest."))
        else:
            reminders.append(Reminder(
                READINESS, "info", f"恢复分 {row['recovery']}（红）",
                "今天计划里没有大重量训练；以散步、灵活度为主，早点睡。", subject="red", evidence=evidence,
                title_en=f"Recovery {row['recovery']} (red)",
                message_en="No heavy training in today's plan; keep to walking and mobility, and go to bed early."))
    if "illness_warning" in flags:
        reminders.append(Reminder(READINESS, "warning", "可能在生病或过度疲劳",
                                  flags["illness_warning"] + "。今天别做高强度，注意体温和症状；连续几天这样考虑看 GP。",
                                  subject="illness", evidence=evidence,
                                  title_en="Possibly getting sick or overreaching",
                                  # the flag text is fixed (scores/engine.py illness_warning)
                                  message_en="Resting HR more than 5 above baseline and HRV below 85% of baseline. No high "
                                             "intensity today; watch your temperature and symptoms, and if it lasts several "
                                             "days, consider seeing a GP."))
    debt = row["sleep_debt_min"]
    if debt is not None and debt >= P.SLEEP_DEBT_HIGH_MIN:
        reminders.append(Reminder(
            READINESS, "warning" if debt >= 2 * P.SLEEP_DEBT_HIGH_MIN else "info",
            f"睡眠债 {debt / 60:.1f} 小时",
            f"最近 14 晚加权平均少睡 {debt} 分钟（睡眠需求 {row['sleep_need_min'] or '—'} 分钟）。今晚提前上床；"
            "午睡最多 20 分钟、下午 3 点后不睡，12 点后不喝咖啡（6.2）。",
            subject="sleep_debt", evidence=evidence,
            title_en=f"Sleep debt {debt / 60:.1f} h",
            message_en=f"Over the last 14 nights you slept {debt} min less than you need on a weighted average (sleep need "
                       f"{row['sleep_need_min'] or '—'} min). Go to bed earlier tonight; nap at most 20 min and not after 3 pm, "
                       "and no coffee after 12 noon (6.2)."))
    if reminders:
        return RuleResult(READINESS, "triggered", reminders=reminders)
    if row["recovery"] is None:
        why = "；".join(f"{k}：{v}" for k, v in flags.items() if k in ("no_sleep", "hrv_missing", "baseline_insufficient"))
        return RuleResult(READINESS, "insufficient_data",
                          f"今天没有恢复分（{why or '见 flags'}）；睡眠债 {debt if debt is not None else '—'} 分钟没到 {P.SLEEP_DEBT_HIGH_MIN}")
    return RuleResult(READINESS, "not_triggered",
                      f"恢复分 {row['recovery']}、睡眠债 {debt} 分钟，没到提醒线（红 < {P.RECOVERY_RED}、睡眠债 ≥ {P.SLEEP_DEBT_HIGH_MIN}）"
                      + ("" if ctx.plans is not None else "；没连 SparkyFitness，不知道今天的计划"))
