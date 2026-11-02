"""Findings for the first screen and for ordering my Today (#279, design section 4.3): a few rules over what the scores already
computed. No AI call: free, the same every time, and each finding says what it is based on. Labels are in the page's language
(X-Body-Lang); `area` / `value` / `label` are short, the page shows them as they are."""
import math
import statistics
from datetime import date, timedelta

import psycopg

from ..i18n import L
from . import params as P


def _f(key, module, area, value, label, tone, weight) -> dict:
    return {"key": key, "module": module, "area": area, "value": value, "label": label, "tone": tone, "weight": round(weight, 3)}


def _hm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60}:{m % 60:02d}"


def _pct(x: float) -> str:
    return f"{'+' if x > 0 else '−'}{abs(round(x * 100))}%"


def findings(conn: psycopg.Connection, user_id: int, today: date) -> list[dict]:
    """The findings worth saying today, most important first. Days with no score count as missing, never as zero."""
    since = today - timedelta(days=P.BASE_DAYS + P.RECENT_DAYS)
    rows = conn.execute("""SELECT date, recovery, hrv_night_ln, sleep_actual_min, sleep_need_min, strain FROM daily_scores
                           WHERE user_id = %s AND date > %s AND date <= %s ORDER BY date""", (user_id, since, today)).fetchall()
    out = []
    recent_from = today - timedelta(days=P.RECENT_DAYS)

    # HRV this week against the person's own 60 days before it
    hrv_now = [math.exp(r["hrv_night_ln"]) for r in rows if r["date"] > recent_from and r["hrv_night_ln"] is not None]
    hrv_base = [math.exp(r["hrv_night_ln"]) for r in rows if r["date"] <= recent_from and r["hrv_night_ln"] is not None]
    if len(hrv_now) >= P.MIN_RECENT_DAYS and len(hrv_base) >= P.MIN_BASE_DAYS:
        d = statistics.fmean(hrv_now) / statistics.fmean(hrv_base) - 1
        if d <= -P.HRV_CHANGE:
            out.append(_f("hrv_low", "health_monitor", L("恢复", "Recovery"), _pct(d), L("HRV 这周比你平时低", "HRV is lower than usual this week"), "mid", -d / P.HRV_CHANGE))
        elif d >= P.HRV_CHANGE:
            out.append(_f("hrv_high", "health_monitor", L("恢复", "Recovery"), _pct(d), L("HRV 这周比你平时高", "HRV is higher than usual this week"), "good", 0.6 * d / P.HRV_CHANGE))

    # Recovery this month against the month before
    month = today - timedelta(days=P.RECOVERY_DAYS)
    rec_now = [r["recovery"] for r in rows if r["date"] > month and r["recovery"] is not None]
    rec_before = [r["recovery"] for r in rows if month - timedelta(days=P.RECOVERY_DAYS) < r["date"] <= month and r["recovery"] is not None]
    if len(rec_now) >= P.MIN_BASE_DAYS and len(rec_before) >= P.MIN_BASE_DAYS:
        d = statistics.fmean(rec_now) - statistics.fmean(rec_before)
        if abs(d) >= P.RECOVERY_CHANGE:
            out.append(_f("recovery_up" if d > 0 else "recovery_down", None, L("恢复", "Recovery"), f"{round(statistics.fmean(rec_now))}",
                          L(f"30 天平均，比上个月 {'+' if d > 0 else '−'}{abs(round(d))}", f"30-day average, {'+' if d > 0 else '−'}{abs(round(d))} on the month before"),
                          "good" if d > 0 else "mid", abs(d) / P.RECOVERY_CHANGE * (0.7 if d > 0 else 1.0)))

    # Sleep against the night's own need, last 7 nights
    pairs = [(r["sleep_actual_min"], r["sleep_need_min"]) for r in rows
             if r["date"] > recent_from and r["sleep_actual_min"] is not None and r["sleep_need_min"]]
    if len(pairs) >= P.MIN_RECENT_DAYS:
        short = statistics.fmean(n - a for a, n in pairs)
        if short >= P.SLEEP_SHORT_MIN:
            out.append(_f("sleep_short", "sleep_goal", L("睡眠", "Sleep"), _hm(statistics.fmean(a for a, _ in pairs)),
                          L(f"近 7 晚平均，比需要少 {_hm(short)}", f"7-night average, {_hm(short)} under your need"), "mid", short / P.SLEEP_SHORT_MIN))

    # Hard days in a row, up to yesterday (today is still going)
    streak = 0
    by_day = {r["date"]: r["strain"] for r in rows}
    d = today - timedelta(days=1)
    while by_day.get(d) is not None and by_day[d] >= P.STRAIN_HIGH:
        streak += 1
        d -= timedelta(days=1)
    if streak >= P.STRAIN_STREAK:
        out.append(_f("strain_high", "strain_coach", L("负荷", "Strain"), str(streak),
                      L(f"连续 {streak} 天负荷 ≥ {P.STRAIN_HIGH:g}", f"{streak} days in a row with strain ≥ {P.STRAIN_HIGH:g}"), "mid", streak / P.STRAIN_STREAK))

    # Bedtime spread over the last main sleeps (minutes after 18:00 local, so midnight does not wrap)
    starts = conn.execute("""SELECT start_at AT TIME ZONE COALESCE((SELECT timezone FROM users WHERE id = %s), 'Australia/Sydney') AS t
                             FROM sleep_sessions WHERE user_id = %s AND is_main AND wake_date > %s AND wake_date <= %s
                             ORDER BY wake_date DESC LIMIT %s""",
                          (user_id, user_id, today - timedelta(days=P.BEDTIME_NIGHTS * 2), today, P.BEDTIME_NIGHTS)).fetchall()
    mins = [((s["t"].hour * 60 + s["t"].minute) - 18 * 60) % (24 * 60) for s in starts]
    if len(mins) >= P.BEDTIME_MIN_NIGHTS:
        sd = statistics.pstdev(mins)
        if sd >= P.BEDTIME_SPREAD_MIN:
            out.append(_f("bedtime_irregular", "sleep_goal", L("睡眠", "Sleep"), f"±{round(sd)}′",
                          L("入睡时间每天差得多", "Your bedtime moves a lot from night to night"), "mid", sd / P.BEDTIME_SPREAD_MIN * 0.9))

    out.sort(key=lambda f: -f["weight"])
    return out


def stats(conn: psycopg.Connection, user_id: int) -> dict:
    """What the history covers: days with scores, main sleeps, workouts (the first screen's one line)."""
    r = conn.execute("""SELECT (SELECT count(*) FROM daily_scores WHERE user_id = %s) AS days,
                               (SELECT min(date) FROM daily_scores WHERE user_id = %s) AS since,
                               (SELECT count(*) FROM sleep_sessions WHERE user_id = %s AND is_main) AS nights,
                               (SELECT count(*) FROM workouts WHERE user_id = %s AND dup_of IS NULL) AS workouts""",
                     (user_id, user_id, user_id, user_id)).fetchone()
    return {"days": r["days"], "since": r["since"].isoformat() if r["since"] else None, "nights": r["nights"], "workouts": r["workouts"]}
