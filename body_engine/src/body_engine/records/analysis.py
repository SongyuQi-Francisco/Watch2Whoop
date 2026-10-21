"""Journal analysis, first version (system design section 7): comparing the two group means of a next-day outcome for a behavior.

- The behavior is recorded on the day D it happened; the outcome is D+1's daily metrics.
- Outcomes: next-day recovery score (daily_scores.recovery, dated the day of waking, so behavior on D looks at D+1's morning recovery; the primary outcome in the design),
  next-day resting heart rate, next-day ln HRV (natural log of the daily mean SDNN).
- No record = missing, not "no": only days explicitly logged yes (value > threshold) or no (value <= threshold) are used.
- Fewer than 5 days in either the yes or no group -> no result; at least 15 days in each to mark reliable. The interval is a normal approximation of Welch standard error x 1.96, too narrow with small samples.
- Later (second version): OLS controlling for previous-day strain, sleep duration and weekday, Newey-West standard errors, BH correction when looking at several behaviors together.
"""
import math
from datetime import date, timedelta
from statistics import fmean, stdev

import psycopg

from .. import queries

MIN_N = 5
RELIABLE_N = 15

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


def analyze(conn: psycopg.Connection, user_id: int, key: str, end: date | None = None, days: int = 90,
            threshold: float = 0.0) -> dict:
    """end defaults to today; analyze the days in [end - days + 1, end] on which key was logged."""
    item = conn.execute("SELECT key, label_zh, kind, unit FROM journal_items WHERE user_id = %s AND key = %s",
                        (user_id, key)).fetchone()
    if item is None:
        raise ValueError(f"没有这个日记项：{key}")
    end = end or queries.local_today(conn, user_id)
    start = end - timedelta(days=days - 1)
    rows = conn.execute(
        """SELECT e.date, e.value, rhr.value AS resting_heart_rate, hrv.value AS heart_rate_variability_sdnn,
                  sc.recovery
           FROM journal_entries e
           LEFT JOIN daily_metrics rhr ON rhr.user_id = e.user_id AND rhr.date = e.date + 1
                                      AND rhr.metric = 'resting_heart_rate'
           LEFT JOIN daily_metrics hrv ON hrv.user_id = e.user_id AND hrv.date = e.date + 1
                                      AND hrv.metric = 'heart_rate_variability_sdnn'
           LEFT JOIN daily_scores sc ON sc.user_id = e.user_id AND sc.date = e.date + 1
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
    return {
        "item": item, "start": start, "end": end, "threshold": threshold,
        "days_recorded": len(rows),
        "days_yes": sum(r["value"] > threshold for r in rows), "days_no": sum(r["value"] <= threshold for r in rows),
        "outcomes": outcomes,
        "method": "两组均值比较（行为日 D → D+1 的恢复分 / 指标）；没记录的日子不算；各 ≥ 5 天才出结果，≥ 15 天才算可信；"
                  "区间为正态近似，没有控制负荷、睡眠和星期几，只能当线索看。",
    }


def analyze_all(conn, user_id: int, end: date | None = None, days: int = 90) -> list[dict]:
    keys = [r["key"] for r in conn.execute(
        "SELECT key FROM journal_items WHERE user_id = %s AND active ORDER BY sort, key", (user_id,))]
    return [analyze(conn, user_id, k, end, days) for k in keys]
