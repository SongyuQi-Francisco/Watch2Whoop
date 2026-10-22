"""Leaderboards on the Friends tab (docs/friends-social-plan.md §3.4): five boards, each 0–100 against the person's own
baseline or goals (weights, windows, thresholds and their sources in params.py).

- body: 7-day mean recovery + sleep performance
- activity: acute:chronic workload ratio (sweet spot, penalised when ramping up too fast) + training days
- lifestyle: on-time bedtime + nutrition goals met (n / 7)
- recomp: weight / body fat toward the person's goal + strength volume vs their baseline + protein met
- longevity (4 weeks): change of the body-age gap + current pace of aging

A person's numbers do not depend on who looks, so they are computed once per (person, board, local day) and cached for
params.CACHE_SECONDS. Who may see them does: a row is "not_shared" unless the viewer holds every scope of the board from
that person; "insufficient" when there is not enough data. Neither gets a rank (never shown as last).
SparkyFitness reads (food, strength sets, weigh-ins) are behind small functions tests replace.
"""
from __future__ import annotations

import statistics
import time
from datetime import date, timedelta

import psycopg

from ..avatars import urls as avatar_store
from ..goals import store as sleep_goals
from ..web import views
from . import cards, params as P

_CACHE: dict[tuple[int, str, date], tuple[float, dict]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------- data that lives in SparkyFitness (replaceable in tests)
def _nutrition_days(conn, uid: int, start: date, end: date) -> list[dict] | None:
    """[{date, logged, calories_ok, protein_ok}] or None when SparkyFitness cannot be read for this person."""
    from ..nutrition import daily
    try:
        days = daily.nutrition(conn, uid, start, end, include_entries=False)["days"]
    except Exception:  # noqa: BLE001  a board row degrades to "insufficient", never fails the page
        return None
    return [{"date": d["date"], "logged": d["logged"], "calories_ok": d["adherence"]["calories"] is True,
             "protein_ok": d["adherence"]["protein"] is True} for d in days]


def _strength_volume(conn, uid: int, start: date, end: date) -> dict[date, float] | None:
    """Strength volume (Σ reps × kg of working sets) per day, or None when SparkyFitness cannot be read."""
    from ..training import history
    sessions, _, err = history.load_sparky(conn, uid, start, end)
    if err:
        return None
    out: dict[date, float] = {}
    for s in sessions:
        d = date.fromisoformat(str(s["date"])[:10])
        out[d] = out.get(d, 0.0) + history._volume(s.get("exercises") or [])[0]
    return out


def _weights(conn, uid: int, start: date, end: date) -> dict[date, float]:
    from ..trends.series import weights
    try:
        return weights(conn, uid, start, end)
    except Exception:  # noqa: BLE001
        return {r["date"]: r["value"] for r in conn.execute(
            """SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'body_mass' AND unit = 'kg'
               AND date BETWEEN %s AND %s""", (uid, start, end))}


# ---------------------------------------------------------------- scoring pieces (pure)
def acwr_score(r: float) -> float:
    if r < P.ACWR_FULL_LOW:
        return _clip(100 * r / P.ACWR_FULL_LOW)
    if r <= P.ACWR_PENALTY_FROM:
        return 100.0
    return _clip(100 * (1 - (r - P.ACWR_PENALTY_FROM) / (P.ACWR_ZERO_AT - P.ACWR_PENALTY_FROM)))


def band_score(x: float, band: tuple[float, float]) -> float:
    """x = weekly change toward the goal in % of body weight; full marks inside the band, too fast loses points."""
    lo, hi = band
    if x <= 0:
        return 0.0
    if x < lo:
        return _clip(100 * x / lo)
    if x <= hi:
        return 100.0
    return _clip(100 * (1 - (x - hi) / (P.RATE_ZERO_AT - hi)))


def maintain_score(x: float) -> float:
    x = abs(x)
    if x <= P.MAINTAIN_MAX:
        return 100.0
    return _clip(100 * (1 - (x - P.MAINTAIN_MAX) / (P.RATE_ZERO_AT - P.MAINTAIN_MAX)))


def pace_score(pace: float) -> float:
    if pace <= P.PACE_BEST:
        return 100.0
    if pace <= P.PACE_MID:
        return 100 - 50 * (pace - P.PACE_BEST) / (P.PACE_MID - P.PACE_BEST)
    return _clip(50 - 50 * (pace - P.PACE_MID) / (P.PACE_WORST - P.PACE_MID))


def _ok(score: float, sub: dict) -> dict:
    return {"status": "ok", "score": round(score, 1), "sub": sub}


INSUFFICIENT = {"status": "insufficient", "score": None, "sub": {}}


# ---------------------------------------------------------------- boards
def _scores(conn, uid: int, start: date, end: date) -> dict[date, dict]:
    return {r["date"]: r for r in conn.execute(
        """SELECT date, recovery, strain, sleep_performance, sleep_actual_min FROM daily_scores
           WHERE user_id = %s AND date BETWEEN %s AND %s""", (uid, start, end))}


def board_body(conn, uid: int, today: date) -> dict:
    rows = _scores(conn, uid, today - timedelta(days=P.WINDOW_DAYS - 1), today)
    recs = [r["recovery"] for r in rows.values() if r["recovery"] is not None]
    if len(recs) < P.BODY_MIN_DAYS:
        return INSUFFICIENT
    perf = [min(1.0, r["sleep_performance"]) * 100 for r in rows.values() if r["sleep_performance"] is not None]
    rec = statistics.fmean(recs)
    score = P.BODY_W_RECOVERY * rec + P.BODY_W_SLEEP * statistics.fmean(perf) if perf else rec
    sleep = [r["sleep_actual_min"] for r in rows.values() if r["sleep_actual_min"] is not None]
    return _ok(score, {"recovery_avg": round(rec), "green_days": sum(1 for v in recs if views.recovery_zone(v) == "green"),
                       "sleep_avg_min": round(statistics.fmean(sleep)) if sleep else None})


def board_activity(conn, uid: int, today: date) -> dict:
    start7 = today - timedelta(days=P.WINDOW_DAYS - 1)
    rows = _scores(conn, uid, start7 - timedelta(days=28), today)
    last = [r["strain"] for d, r in rows.items() if d >= start7 and r["strain"] is not None]
    base = [r["strain"] for d, r in rows.items() if d < start7 and r["strain"] is not None]
    if len(last) < P.ACT_MIN_DAYS or len(base) < P.ACT_MIN_BASELINE_DAYS:
        return INSUFFICIENT
    chronic = statistics.fmean(base) * 7
    if chronic <= 0:
        return INSUFFICIENT
    ratio = statistics.fmean(last) * 7 / chronic
    days = conn.execute("""SELECT count(DISTINCT local_date) AS n FROM workouts WHERE user_id = %s
                           AND local_date BETWEEN %s AND %s AND dup_of IS NULL AND excluded IS NULL""",
                        (uid, start7, today)).fetchone()["n"]
    score = P.ACT_W_LOAD * acwr_score(ratio) + P.ACT_W_DAYS * min(1.0, days / P.ACT_FULL_DAYS) * 100
    return _ok(score, {"acwr": round(ratio, 2), "training_days": days})


def _bedtime_hits(conn, uid: int) -> int | None:
    """On-time nights among the last 7: against the bedtime goal when one is active, else within ± BEDTIME_TOL_MIN of
    the person's own median bedtime over the 28 nights before."""
    goal = sleep_goals.get_goal(conn, uid)
    if goal and goal["status"] == "active":
        nights = sleep_goals.view(conn, uid)["recent_nights"]
        return sum(1 for n in nights if n["on_target"])
    tz = sleep_goals.user_tz(conn, uid)
    night = sleep_goals.tonight(tz)
    nights = sleep_goals.load_nights(conn, uid, tz, night, days=P.WINDOW_DAYS + P.BEDTIME_BASELINE_NIGHTS)
    cut = night - timedelta(days=P.WINDOW_DAYS)
    base = [n["bed_off"] for n in nights if n["night"] < cut]
    if len(base) < P.BEDTIME_MIN_BASELINE:
        return None
    mid = statistics.median(base)
    return sum(1 for n in nights if n["night"] >= cut and abs(n["bed_off"] - mid) <= P.BEDTIME_TOL_MIN)


def board_lifestyle(conn, uid: int, today: date) -> dict:
    sleep_hits = _bedtime_hits(conn, uid)
    # food: the 7 finished days (today is not over yet)
    food = _nutrition_days(conn, uid, today - timedelta(days=P.WINDOW_DAYS), today - timedelta(days=1))
    if sleep_hits is None or food is None:
        return INSUFFICIENT
    food_hits = sum(1 for d in food if d["calories_ok"] and d["protein_ok"])
    n = P.WINDOW_DAYS
    score = 100 * (P.LIFE_W_SLEEP * sleep_hits / n + P.LIFE_W_FOOD * food_hits / n)
    return _ok(score, {"on_time": sleep_hits, "food_met": food_hits, "days": n})


def _goal_weight(conn, uid: int) -> float | None:
    from ..profile import store as profile
    return profile.get(conn, uid)["goal_weight_kg"]


def board_recomp(conn, uid: int, today: date) -> dict:
    start7 = today - timedelta(days=P.WINDOW_DAYS - 1)
    prev7 = start7 - timedelta(days=P.WINDOW_DAYS)
    w = _weights(conn, uid, prev7, today)
    now = [v for d, v in w.items() if d >= start7]
    before = [v for d, v in w.items() if prev7 <= d < start7]
    if len(now) < P.WEIGH_INS_MIN or len(before) < P.WEIGH_INS_MIN:
        return INSUFFICIENT
    m_now, m_before = statistics.fmean(now), statistics.fmean(before)
    rate = (m_now - m_before) / m_before * 100              # % of body weight per week, + = gaining
    goal = _goal_weight(conn, uid)
    mode = ("lose" if goal is not None and goal < m_now - P.GOAL_NEAR_KG
            else "gain" if goal is not None and goal > m_now + P.GOAL_NEAR_KG else "maintain")
    parts = [band_score(-rate, P.LOSS_BAND) if mode == "lose" else band_score(rate, P.GAIN_BAND) if mode == "gain"
             else maintain_score(rate)]
    fat = {r["date"]: r["value"] for r in conn.execute(
        """SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'body_fat_percentage'
           AND date BETWEEN %s AND %s""", (uid, prev7, today))}
    f_now = [v for d, v in fat.items() if d >= start7]
    f_before = [v for d, v in fat.items() if d < start7]
    if len(f_now) >= P.WEIGH_INS_MIN and len(f_before) >= P.WEIGH_INS_MIN:
        dpp = statistics.fmean(f_now) - statistics.fmean(f_before)
        parts.append(100.0 if mode == "gain" and dpp <= 0 else _clip(50 - 50 * dpp / P.FAT_PP_FULL))
    comp = statistics.fmean(parts)
    vol = _strength_volume(conn, uid, start7 - timedelta(days=28), today)
    if not vol:
        return INSUFFICIENT
    v_last = sum(v for d, v in vol.items() if d >= start7)
    v_base = sum(v for d, v in vol.items() if d < start7) / 4
    if v_base <= 0:
        return INSUFFICIENT
    v_ratio = v_last / v_base
    food = _nutrition_days(conn, uid, today - timedelta(days=P.WINDOW_DAYS), today - timedelta(days=1))
    if food is None:
        return INSUFFICIENT
    protein = sum(1 for d in food if d["protein_ok"])
    score = (P.RECOMP_W_WEIGHT * comp + P.RECOMP_W_TRAIN * min(1.0, v_ratio / P.TRAIN_VOLUME_FULL) * 100
             + P.RECOMP_W_PROTEIN * 100 * protein / P.WINDOW_DAYS)
    return _ok(score, {"mode": mode, "rate_pct_week": round(rate, 2), "volume_ratio": round(v_ratio, 2),
                       "protein_met": protein, "days": P.WINDOW_DAYS})


def board_longevity(conn, uid: int, today: date) -> dict:
    rows = conn.execute("""SELECT as_of, delta_years, pace_of_aging FROM healthspan_weekly WHERE user_id = %s
                           AND as_of <= %s AND delta_years IS NOT NULL ORDER BY as_of DESC""", (uid, today)).fetchall()
    if not rows:
        return INSUFFICIENT
    latest = rows[0]
    if latest["as_of"] < today - timedelta(days=14):     # no fresh weekly snapshot
        return INSUFFICIENT
    earlier = next((r for r in rows if r["as_of"] <= latest["as_of"] - timedelta(days=P.LONGEVITY_WINDOW_DAYS)), None)
    if earlier is None:
        return INSUFFICIENT
    change = latest["delta_years"] - earlier["delta_years"]          # − = got younger
    gap = _clip(50 - 50 * change / P.GAP_YEARS_FULL)
    pace = latest["pace_of_aging"]
    score = gap if pace is None else P.LONG_W_GAP * gap + P.LONG_W_PACE * pace_score(pace)
    return _ok(score, {"gap_change_years": round(change, 2), "pace": round(pace, 2) if pace is not None else None})


BOARD_FN = {"body": board_body, "activity": board_activity, "lifestyle": board_lifestyle, "recomp": board_recomp,
            "longevity": board_longevity}


def member_board(conn, uid: int, board: str) -> dict:
    today = views.local_today(conn, uid)
    key = (uid, board, today)
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    res = BOARD_FN[board](conn, uid, today)
    _CACHE[key] = (time.monotonic() + P.CACHE_SECONDS, res)
    return res


# ---------------------------------------------------------------- what a viewer sees
def board(conn: psycopg.Connection, viewer_id: int, name: str) -> dict:
    """One board for the viewer: me + everyone sharing a circle with me, ranked among those with a score."""
    if name not in P.BOARDS:
        raise ValueError(name)
    need = set(P.BOARD_SCOPES[name])
    me = conn.execute("SELECT display_name FROM users WHERE id = %s", (viewer_id,)).fetchone()
    people = [{"id": viewer_id, "display_name": me["display_name"], "self": True}] + \
             [{"id": m["id"], "display_name": m["display_name"], "self": False} for m in cards.co_members(conn, viewer_id)]
    avatars = avatar_store.urls_for(conn, viewer_id, [p["id"] for p in people])
    for p in people:
        p["avatar"] = avatars.get(p["id"])
    rows = []
    for p in people:
        if not p["self"] and not need <= cards.share_with(conn, viewer_id, p["id"]):
            rows.append({**p, "status": "not_shared", "score": None, "sub": {}, "rank": None})
            continue
        rows.append({**p, **member_board(conn, p["id"], name), "rank": None})
    ranked = sorted((r for r in rows if r["status"] == "ok"), key=lambda r: -r["score"])
    prev, rank = None, 0
    for i, r in enumerate(ranked, 1):
        if round(r["score"]) != prev:
            rank, prev = i, round(r["score"])
        r["rank"] = rank
        r["score"] = round(r["score"])
    others = [r for r in rows if r["status"] != "ok"]
    window = P.LONGEVITY_WINDOW_DAYS if name == "longevity" else P.WINDOW_DAYS
    return {"board": name, "window_days": window, "scopes": sorted(need), "boards": list(P.BOARDS),
            "rows": ranked + sorted(others, key=lambda r: (r["status"] != "insufficient", r["display_name"]))}


def ai_overview(conn: psycopg.Connection, viewer_id: int) -> dict:
    """get_friends_overview: each circle member's shared status + every board rank (what the viewer may see only)."""
    ov = cards.overview(conn, viewer_id)
    boards = {b: {r["id"]: r for r in board(conn, viewer_id, b)["rows"]} for b in P.BOARDS}
    out = []
    for m in [ov["me"], *ov["members"]]:
        sc = m["scores"]
        week = [d["recovery"] for d in sc["week"] if d["recovery"] is not None] if sc else []
        row = {"name": m["display_name"], "self": m["self"]}
        if sc:
            row.update(status=sc["label"], recovery=sc["recovery"], strain=sc["strain"],
                       recovery_7d=round(statistics.fmean(week)) if week else None,
                       last_sync=sc["last_date"] if not sc["synced_today"] else None)
        if m["trained"] is not None:
            row["trained_today"] = m["trained"]
        ranks = {}
        for b, by_id in boards.items():
            r = by_id.get(m["id"])
            if r and r["status"] == "ok":
                ranks[b] = f"#{r['rank']} · {r['score']}"
            elif r:
                ranks[b] = r["status"]
        row["boards"] = ranks
        if not m["self"]:
            row["not_shared"] = m["hidden"]
        out.append(row)
    return {"members": out, "boards": "body=身体状态 activity=运动 lifestyle=认真生活 recomp=瘦体增肌 longevity=抗衰老(4周)；分数和自己基线比"}
