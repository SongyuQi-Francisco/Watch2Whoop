"""Heart rate recovery after workouts (HRR60).

HRR60 = peak heart rate in the last 2 minutes of the workout − heart rate 60 s after the workout ended.
The watch keeps recording about every 5 s for a few minutes after a workout (owner's data: 28 of 30 workouts since 2026-06 have samples there),
so this works for every workout, not just the ones Apple writes heartRateRecoveryOneMinute for.
Only meaningful when the workout ended hard: the peak must reach PEAK_MIN_PCT of HRmax, otherwise the row is kept with valid = false
(e.g. a walk that wound down slowly). Reference: Cole et al., NEJM 1999 (≤ 12 bpm in the first minute was abnormal after an exercise test);
we only look at personal trends, never diagnose.
"""
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta

import psycopg

ALGO = "hrr60-v1"
PEAK_WINDOW = timedelta(minutes=2)
AFTER = timedelta(seconds=60)
TOLERANCE = timedelta(seconds=20)        # a sample this close to end + 60 s is good enough; bracketing samples are interpolated
PEAK_MIN_PCT = 0.75
TREND_DAYS = 30


def hrr60(hr: list[tuple[datetime, float]], end: datetime, hr_max: float | None) -> dict:
    """hr sorted by time. -> {peak_hr, hr_60s, hrr60, valid, reason}."""
    times = [t for t, _ in hr]
    peak_vals = [v for _, v in hr[bisect_left(times, end - PEAK_WINDOW):bisect_right(times, end + timedelta(seconds=5))]]
    out = {"peak_hr": None, "hr_60s": None, "hrr60": None, "valid": False, "reason": None}
    if not peak_vals:
        out["reason"] = "训练最后 2 分钟没有心率"
        return out
    peak = max(peak_vals)
    target = end + AFTER
    i = bisect_left(times, target)
    before = hr[i - 1] if i > 0 and hr[i - 1][0] > end + timedelta(seconds=30) else None
    after = hr[i] if i < len(hr) else None
    h60 = None
    if before and after and after[0] - before[0] <= 2 * TOLERANCE:
        span = (after[0] - before[0]).total_seconds()
        w = (target - before[0]).total_seconds() / span if span else 0.0
        h60 = before[1] + (after[1] - before[1]) * w
    else:
        near = [x for x in (before, after) if x and abs(x[0] - target) <= TOLERANCE]
        if near:
            h60 = min(near, key=lambda x: abs(x[0] - target))[1]
    out["peak_hr"] = round(peak, 1)
    if h60 is None:
        out["reason"] = "结束后 60 秒附近没有心率（手表可能没在高频记录）"
        return out
    out.update(hr_60s=round(h60, 1), hrr60=round(peak - h60, 1))
    if hr_max and peak < PEAK_MIN_PCT * hr_max:
        out["reason"] = f"结束前最高心率只有最大心率的 {peak / hr_max:.0%}（要 ≥ {PEAK_MIN_PCT:.0%}），这次的下降不代表心肺恢复"
        return out
    out["valid"] = True
    return out


def compute(conn: psycopg.Connection, user_id: int, hr: list[tuple[datetime, float]], hr_max_by_day: dict,
            start, end) -> dict:
    """Recompute workout_hrr for the user's workouts that started in [start, end] (local dates). Idempotent."""
    wos = conn.execute("""SELECT id, local_date, end_at FROM workouts
                          WHERE user_id = %s AND dup_of IS NULL AND excluded IS NULL AND local_date BETWEEN %s AND %s
                          ORDER BY start_at""", (user_id, start, end)).fetchall()
    rows = []
    for w in wos:
        hmax = hr_max_by_day.get(w["local_date"])
        r = hrr60(hr, w["end_at"], hmax)
        rows.append((w["id"], user_id, w["local_date"], w["end_at"], r["peak_hr"], r["hr_60s"], r["hrr60"], hmax,
                     r["valid"], r["reason"], ALGO))
    with conn.transaction():
        conn.execute("""DELETE FROM workout_hrr WHERE user_id = %s AND local_date BETWEEN %s AND %s""", (user_id, start, end))
        if rows:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO workout_hrr (workout_id, user_id, local_date, end_at, peak_hr, hr_60s, hrr60, hr_max_used,
                                                valid, reason, algo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (workout_id) DO UPDATE SET local_date = EXCLUDED.local_date, end_at = EXCLUDED.end_at,
                         peak_hr = EXCLUDED.peak_hr, hr_60s = EXCLUDED.hr_60s, hrr60 = EXCLUDED.hrr60,
                         hr_max_used = EXCLUDED.hr_max_used, valid = EXCLUDED.valid, reason = EXCLUDED.reason,
                         algo = EXCLUDED.algo, computed_at = now()""", rows)
    return {"workouts": len(rows), "valid": sum(r[8] for r in rows)}
