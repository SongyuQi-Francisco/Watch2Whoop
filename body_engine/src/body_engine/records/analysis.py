"""Journal analysis, first version (system design section 7): comparing the two group means of a next-day outcome for a behavior.

- The behavior is recorded on the day D it happened; the outcome is D+1's daily metrics.
- Outcomes: next-day recovery score (daily_scores.recovery, dated the day of waking, so behavior on D looks at D+1's morning recovery; the primary outcome in the design),
  next-day resting heart rate, next-day ln HRV (natural log of the daily mean SDNN).
- No record = missing, not "no": only days explicitly logged yes (value > threshold) or no (value <= threshold) are used.
- Fewer than 5 days in either the yes or no group -> no result; at least 15 days in each to mark reliable. The interval is a normal approximation of Welch standard error x 1.96, too narrow with small samples.
- Second version (regression=True, records/regression.py, docs/scoring-validation.md section 2.2): adds a "regression" field to
  each outcome: OLS of the outcome on the behavior (1 = yes, 0 = no), controlling for the strain of day D (the day before the
  outcome), the sleep duration of the night D -> D+1 (hours) and the weekday of D; Newey-West (HAC, 7-day lag) standard
  errors; with analyze_all, Benjamini-Hochberg across the behaviors of each outcome. Only days with all covariates are used.
  The first version's fields stay exactly as they were.
"""
import math
from datetime import date, timedelta
from statistics import fmean, stdev

import psycopg

from .. import queries
from . import analysis_params as AP
from . import regression as RG

MIN_N = AP.MIN_N
RELIABLE_N = AP.RELIABLE_N

# Outcomes: name -> (column in the query below, transform, description, higher is better?). recovery comes from daily_scores, the rest from daily_metrics
OUTCOMES = {
    "next_recovery": ("recovery", lambda v: v, "次日恢复分（0–100）", True),
    "next_rhr": ("resting_heart_rate", lambda v: v, "次日静息心率（bpm）", False),
    "next_ln_hrv": ("heart_rate_variability_sdnn", lambda v: math.log(v) if v > 0 else None,
                    "次日 ln HRV（SDNN）", True),
}


def _group_stats(xs: list[float]) -> dict:
    return {"n": len(xs), "mean": round(fmean(xs), 4) if xs else None,
            "sd": round(stdev(xs), 4) if len(xs) >= 2 else None}


def compare(yes: list[float], no: list[float], higher_is_better: bool) -> dict:
    res = {"yes": _group_stats(yes), "no": _group_stats(no)}
    if len(yes) < MIN_N or len(no) < MIN_N:
        res["status"] = "insufficient"
        res["message"] = f"样本不足：是 {len(yes)} 天、否 {len(no)} 天，各需要至少 {MIN_N} 天"
        return res
    diff = fmean(yes) - fmean(no)
    se = math.sqrt(stdev(yes) ** 2 / len(yes) + stdev(no) ** 2 / len(no))
    pooled = math.sqrt(((len(yes) - 1) * stdev(yes) ** 2 + (len(no) - 1) * stdev(no) ** 2) / (len(yes) + len(no) - 2))
    res.update({
        "status": "ok",
        "diff_yes_minus_no": round(diff, 4),
        "ci95_approx": [round(diff - 1.96 * se, 4), round(diff + 1.96 * se, 4)] if se > 0 else None,
        "welch_t": round(diff / se, 3) if se > 0 else None,
        "cohens_d": round(diff / pooled, 3) if pooled > 0 else None,
        "reliable": min(len(yes), len(no)) >= RELIABLE_N,
    })
    if se > 0 and abs(diff / se) >= 1.96:
        better = (diff > 0) == higher_is_better
        res["direction"] = "better" if better else "worse"
    else:
        res["direction"] = "unclear"
    return res


WEEKDAYS = ["tue", "wed", "thu", "fri", "sat", "sun"]   # dummies of the behavior day's weekday; Monday is the reference


def regress(obs: list[dict], higher_is_better: bool) -> dict:
    """Second version for one outcome. obs: [{date, behavior (0/1), y, strain, sleep_h}] (complete days only).
    Returns the behavior's adjusted effect; p_adj / significant / direction are set here for a single test (q = p)
    and replaced by apply_bh() when several behaviors are tested together."""
    yes = sum(o["behavior"] for o in obs)
    no = len(obs) - yes
    res = {"n": len(obs), "n_yes": yes, "n_no": no,
           "controls": ["prev_strain", "sleep_hours", "weekday"], "hac_lag_days": AP.HAC_LAG_DAYS}
    if yes < AP.MIN_N or no < AP.MIN_N:
        return {**res, "status": "insufficient",
                "message": f"样本不足：有完整协变量的是 {yes} 天、否 {no} 天，各需要至少 {AP.MIN_N} 天"}
    wd = [o["date"].weekday() for o in obs]
    present = [i for i in range(1, 7) if any(w == i for w in wd)]   # a weekday with no observation has no dummy
    names = ["behavior", "prev_strain", "sleep_hours", *(f"wd_{WEEKDAYS[i - 1]}" for i in present)]
    x = [[o["behavior"], o["strain"], o["sleep_h"], *(1.0 if w == i else 0.0 for i in present)] for o, w in zip(obs, wd)]
    if len(obs) - (len(names) + 1) < AP.REG_MIN_DF:
        return {**res, "status": "insufficient",
                "message": f"样本不足：{len(obs)} 天、{len(names) + 1} 个系数，剩余自由度要 ≥ {AP.REG_MIN_DF}"}
    try:
        fit = RG.ols_hac([o["date"] for o in obs], [o["y"] for o in obs], x, names)
    except RG.Singular:
        return {**res, "status": "collinear", "message": "这个行为和星期几 / 负荷 / 睡眠完全重合，分不开"}
    b = fit["coef"]["behavior"]
    if b["p"] is None:
        return {**res, "status": "collinear", "message": "残差为 0，算不了标准误"}
    r4 = lambda v: round(v, 4)
    res.update({
        "status": "ok",
        "estimate": r4(b["estimate"]), "se_hac": r4(b["se"]), "z": round(b["z"], 3), "p": r4(b["p"]),
        "ci95": [r4(v) for v in b["ci95"]],
        "covariates": {k: r4(fit["coef"][k]["estimate"]) for k in ("prev_strain", "sleep_hours")},
        "reliable": min(yes, no) >= AP.RELIABLE_N,
        "_higher_is_better": higher_is_better,
    })
    _set_adjusted(res, b["p"], 1)
    return res


def _set_adjusted(res: dict, q: float, m: int) -> None:
    res.update(p_adj=round(q, 4), bh_tests=m, significant=q <= AP.FDR_Q)
    if res["significant"]:
        res["direction"] = "better" if (res["estimate"] > 0) == res["_higher_is_better"] else "worse"
    else:
        res["direction"] = "unclear"


def apply_bh(results: list[dict]) -> None:
    """Benjamini-Hochberg across the behaviors of each outcome (results from analyze(..., regression=True)), in place."""
    for name in OUTCOMES:
        regs = [o["regression"] for a in results if (o := a["outcomes"].get(name)) and "regression" in o
                and o["regression"]["status"] == "ok"]
        for r, q in zip(regs, RG.bh([r["p"] for r in regs])):
            _set_adjusted(r, q, len(regs))


def _clean(results: list[dict]) -> None:
    for a in results:
        for o in a["outcomes"].values():
            o.get("regression", {}).pop("_higher_is_better", None)


def analyze(conn: psycopg.Connection, user_id: int, key: str, end: date | None = None, days: int = 90,
            threshold: float = 0.0, regression: bool = False) -> dict:
    """end defaults to today; analyze the days in [end - days + 1, end] on which key was logged.
    regression=True adds the second version to every outcome (outcomes[name]["regression"]); nothing else changes."""
    res = _analyze(conn, user_id, key, end, days, threshold, regression)
    _clean([res])
    return res


def _analyze(conn: psycopg.Connection, user_id: int, key: str, end: date | None, days: int, threshold: float,
             regression: bool) -> dict:
    item = conn.execute("SELECT key, label_zh, kind, unit FROM journal_items WHERE user_id = %s AND key = %s",
                        (user_id, key)).fetchone()
    if item is None:
        raise ValueError(f"没有这个日记项：{key}")
    end = end or queries.local_today(conn, user_id)
    start = end - timedelta(days=days - 1)
    rows = conn.execute(
        """SELECT e.date, e.value, rhr.value AS resting_heart_rate, hrv.value AS heart_rate_variability_sdnn,
                  sc.recovery, sc.sleep_actual_min, prev.strain AS prev_strain
           FROM journal_entries e
           LEFT JOIN daily_metrics rhr ON rhr.user_id = e.user_id AND rhr.date = e.date + 1
                                      AND rhr.metric = 'resting_heart_rate'
           LEFT JOIN daily_metrics hrv ON hrv.user_id = e.user_id AND hrv.date = e.date + 1
                                      AND hrv.metric = 'heart_rate_variability_sdnn'
           LEFT JOIN daily_scores sc ON sc.user_id = e.user_id AND sc.date = e.date + 1
           LEFT JOIN daily_scores prev ON prev.user_id = e.user_id AND prev.date = e.date
           WHERE e.user_id = %s AND e.key = %s AND e.date BETWEEN %s AND %s ORDER BY e.date""",
        (user_id, key, start, end)).fetchall()
    outcomes = {}
    for name, (metric, f, label, higher_is_better) in OUTCOMES.items():
        yes, no = [], []
        for r in rows:
            if r[metric] is None or (y := f(r[metric])) is None:
                continue
            (yes if r["value"] > threshold else no).append(y)
        outcomes[name] = {"label": label, **compare(yes, no, higher_is_better)}
        if regression:
            obs = [{"date": r["date"], "behavior": 1.0 if r["value"] > threshold else 0.0, "y": y,
                    "strain": r["prev_strain"], "sleep_h": r["sleep_actual_min"] / 60}
                   for r in rows if r[metric] is not None and (y := f(r[metric])) is not None
                   and r["prev_strain"] is not None and r["sleep_actual_min"] is not None]
            outcomes[name]["regression"] = regress(obs, higher_is_better)
    res = {
        "item": item, "start": start, "end": end, "threshold": threshold,
        "days_recorded": len(rows),
        "days_yes": sum(r["value"] > threshold for r in rows), "days_no": sum(r["value"] <= threshold for r in rows),
        "outcomes": outcomes,
        "method": "两组均值比较（行为日 D → D+1 的恢复分 / 指标）；没记录的日子不算；各 ≥ 5 天才出结果，≥ 15 天才算可信；"
                  "区间为正态近似，没有控制负荷、睡眠和星期几，只能当线索看。",
    }
    if regression:
        res["regression_method"] = (
            "第二版：OLS，结局 = D+1 的指标，自变量 = 行为（是 1 / 否 0），控制 D 的负荷（strain）、D→D+1 那晚的睡眠时长（小时）、"
            f"D 的星期几；Newey-West 标准误（滞后 {AP.HAC_LAG_DAYS} 天）；只用协变量齐全的日子；同时看多个行为时按结局做 "
            f"Benjamini-Hochberg（FDR {AP.FDR_Q:.0%}），p_adj ≤ {AP.FDR_Q} 才算有方向。睡眠时长也可能是行为影响恢复的途径，"
            "控制它以后看到的是「睡眠以外」的影响。")
    return res


def analyze_all(conn, user_id: int, end: date | None = None, days: int = 90, regression: bool = False) -> list[dict]:
    """Every active journal item. regression=True: second version too, with Benjamini-Hochberg across the items."""
    keys = [r["key"] for r in conn.execute(
        "SELECT key FROM journal_items WHERE user_id = %s AND active ORDER BY sort, key", (user_id,))]
    res = [_analyze(conn, user_id, k, end, days, 0.0, regression) for k in keys]
    if regression:
        apply_bh(res)
        _clean(res)
    return res
