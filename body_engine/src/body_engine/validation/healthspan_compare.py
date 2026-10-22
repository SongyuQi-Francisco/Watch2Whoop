"""Body age v1.1 vs v1.2 side by side for one user (issue #118): the same inputs scored by both parameter sets, the weekly
history, a ±20% sensitivity of the v1.2 parameters and the inputs behind the activity domain. Reads the database only;
writes nothing. The output holds personal values: keep it out of git (the maintainer's report lives in the private dist/).

    uv run python -m body_engine.validation healthspan --user-id N [--weeks 26] [--no-sensitivity]
"""
from datetime import date, timedelta

from ..insights import healthspan_store as store
from ..insights import healthspan_params_v12 as H12
from ..insights.healthspan_params import override as override_v11
from ..insights.healthspan_params_v12 import override as override_v12


def _knots_pow(knots, f: float):
    """The same curve with ln(HR) scaled by f (f = 1.2: every hazard ratio 20% further from 1 on the log scale)."""
    return [(x, h ** f) for x, h in knots]


def sensitivity_specs(p=H12) -> list[tuple[str, dict]]:
    """(name, parameter changes) for each key v1.2 parameter at −20% / +20%."""
    out = []
    for f, tag in ((0.8, "−20%"), (1.2, "+20%")):
        out += [
            (f"步数曲线斜率 ln(HR) {tag}", {"STEPS_KNOTS_UNDER60": _knots_pow(p.STEPS_KNOTS_UNDER60, f),
                                         "STEPS_KNOTS_60PLUS": _knots_pow(p.STEPS_KNOTS_60PLUS, f)}),
            (f"步数参照点 {tag}", {"STEPS_ANCHOR_UNDER60": p.STEPS_ANCHOR_UNDER60 * f,
                                 "STEPS_ANCHOR_60PLUS": p.STEPS_ANCHOR_60PLUS * f}),
            (f"手腕步数系数 {tag}", {"WRIST_STEPS_FACTOR": p.WRIST_STEPS_FACTOR * f}),
            (f"训练折算的步频（步/分）{tag}", {"CADENCE_MET_KNOTS": [(m, st * f) for m, st in p.CADENCE_MET_KNOTS]}),
            (f"其他训练 MET {tag}", {"WORKOUT_MET": {k: v * f for k, v in p.WORKOUT_MET.items()}}),
            (f"力量训练曲线 ln(HR) {tag}", {"STRENGTH_KNOTS": _knots_pow(p.STRENGTH_KNOTS, f)}),
            (f"活动域上限 {tag}", {"ACTIVITY_CAP_YEARS": p.ACTIVITY_CAP_YEARS * f}),
            (f"合计收缩系数 {tag}", {"SHRINKAGE": p.SHRINKAGE * f}),
            (f"死亡率翻倍年数 {tag}", {"MORTALITY_DOUBLING_YEARS": p.MORTALITY_DOUBLING_YEARS * f}),
        ]
    out.append(("力量训练也折算成步数当量（Lee 2022 口径，与力量单项部分重复）", {"STRENGTH_IN_VOLUME": True}))
    return out


def _brief(snap: dict) -> dict:
    return {"as_of": snap["as_of"], "chrono_age": snap["chrono_age"], "body_age": snap["body_age"],
            "delta_years": snap["delta_years"], "pace": snap.get("pace_of_aging"), "pace_raw": snap.get("pace_raw"),
            "contributors": [{k: c.get(k) for k in ("key", "value", "unit", "hr", "years", "years_uncapped", "parts",
                                                    "data_days") if k in c} for c in snap["contributors"]],
            "insufficient": [{"key": i["key"], "reason": i["reason"]} for i in snap["insufficient"]]}


def _inputs(data, as_of: date, conn, user_id: int) -> dict:
    """Averages behind the activity domain over the 180-day window (plain means, not the 30-day weighting)."""
    lo = as_of - timedelta(days=H12.WINDOW_DAYS - 1)
    inwin = lambda d: lo <= d <= as_of
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None
    days = [d for d in data.zones if inwin(d)]
    z = [data.zones[d] for d in days]
    ex = {r["date"]: r["value"] for r in conn.execute(
        """SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'apple_exercise_time'
           AND date BETWEEN %s AND %s""", (user_id, lo, as_of))}
    workouts = [(t, m) for d, ws in data.workouts.items() if inwin(d) for t, m, _ in ws]
    by_type: dict[str, list[float]] = {}
    for t, m in workouts:
        by_type.setdefault(t, []).append(m)
    tracked = store._tracked_days(data, as_of, H12)
    return {
        "window": [lo, as_of], "hr_zone_days": len(days),
        "zone_min_per_day": [round(mean([x[i] for x in z]), 1) for i in range(5)] if z else None,
        "hr_mvpa_week_v11": round(7 * mean([data.mvpa[d] for d in days if d in data.mvpa]), 0) if days else None,
        "apple_exercise_min_week": round(7 * mean(list(ex.values())), 0) if ex else None,
        "apple_exercise_days": len(ex),
        "strength_min_week_tracked": round(7 * sum(data.strength.get(d, 0.0) for d in tracked) / len(tracked), 0)
        if tracked else None, "tracked_days": len(tracked),
        "workouts_by_type": {t: {"n": len(ms), "min": round(sum(ms))} for t, ms in sorted(by_type.items())},
        "steps_mean": round(mean([v for d, v in data.steps.items() if inwin(d)]) or 0),
        "notes": data.notes,
    }


def compare(conn, user_id: int, weeks: int = 26, sensitivity: bool = True, today: date | None = None) -> dict | None:
    loaded = store.load(conn, user_id, today)
    if loaded is None:
        return None
    data, origin, today = loaded
    out = {"user_id": user_id, "versions": {}}
    for name, params in store.VERSIONS.items():
        rows = store.weekly(data, origin, today, params)
        if not rows:
            return None
        out["versions"][name] = {"algo_version": params.ALGO_VERSION, "current": _brief(rows[-1]),
                                 "history": [{"as_of": r["as_of"], "delta_years": r["delta_years"],
                                              "body_age": r["body_age"], "pace": r["pace_of_aging"]}
                                             for r in rows[-weeks:]]}
    out["inputs"] = _inputs(data, today, conn, user_id)
    if sensitivity:
        base = out["versions"]["v1.2"]["current"]["delta_years"]
        out["sensitivity"] = []
        for name, changes in sensitivity_specs():
            snap = store.snapshot(data, today, override_v12(**changes))
            act = next((c["years"] for c in snap["contributors"] if c["key"] == "activity"), None)
            out["sensitivity"].append({"name": name, "delta_years": snap["delta_years"], "activity_years": act,
                                       "change": None if base is None or snap["delta_years"] is None
                                       else round(snap["delta_years"] - base, 2)})
        # v1.1 for reference under the same two global parameters
        for f, tag in ((0.8, "−20%"), (1.2, "+20%")):
            snap = store.snapshot(data, today, override_v11(SHRINKAGE=0.6 * f))
            out["sensitivity"].append({"name": f"v1.1 合计收缩系数 {tag}", "delta_years": snap["delta_years"]})
    return out


def markdown(res: dict) -> str:
    """A short table per user (aggregates only)."""
    v11, v12 = res["versions"]["v1.1"]["current"], res["versions"]["v1.2"]["current"]
    lines = [f"### 用户 {res['user_id']}", "", "| | v1.1 | v1.2 |", "|---|---|---|",
             f"| 身体年龄 | {v11['body_age']} | {v12['body_age']} |", f"| Δ 年 | {v11['delta_years']} | {v12['delta_years']} |",
             f"| 衰老速度 | {v11['pace']} | {v12['pace']} |", ""]
    for v in (v11, v12):
        lines.append(" · ".join(f"{c['key']} {c['years']:+}" for c in v["contributors"]))
    return "\n".join(lines) + "\n"
