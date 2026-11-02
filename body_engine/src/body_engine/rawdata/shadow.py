"""Apple sync engine, step S4 (docs/apple-sync-engine.md §4; issue #219): the shadow comparison. For each user whose
app uploads through the engine (flag apple_sync_engine: sync mode shadow, or app during the rollback period), every
closed day (ended more than SHADOW_CLOSED_AFTER ago) of the two sync paths is compared item by item and judged:

- daily values (daily_metric_sources 'ow' vs 'app', rawdata/side_by_side.py), only metrics the engine's registry reads:
  - cumulative (`sum`): |rel| ≤ SHADOW_SUM_REL ok; ≤ SHADOW_SUM_METHOD_REL explained `method` (OW guesses each sample's
    end, the app sends Apple's own 15-minute statistics); more = mismatch `value`;
  - non-cumulative (`avg`): mean, min and max within SHADOW_AVG_REL ok (equal counts) or explained (`ow_same_ts`: OW
    merges samples of one source with the same start, so it has fewer; `deleted_in_health`: OW keeps samples deleted in
    Health, so the app has fewer), when the counts differ by at most SHADOW_COUNT_EXPLAINED_REL; otherwise mismatch;
  - OW has it, the app not: missing_app (blocks the cut-over); the app has it, OW not: new (`missing_ow` when OW carries
    that type: the OW SDK did not deliver it), never judged.
- sleep, per wake date: the main nights (OW's stored one, the app's computed from its stage samples): start, end,
  asleep and every stage within SHADOW_SLEEP_MIN ok; within SHADOW_SLEEP_EXPLAINED_MIN when the sources differ or a side
  has more sessions that night: explained `source_priority` (OW's state machine merges sources, the app keeps them apart,
  rawdata/derive.py); otherwise mismatch.
- workouts: OW's kept ones paired with the app's by time overlap; duration, total energy and heart rate within the
  thresholds and the same type = ok (active energy is not compared: `active_kcal_method`, the app has the workout's own
  statistic); unpaired OW workout = missing_app; unpaired app workout = new.
- scores (with_scores): recovery, sleep score and strain recomputed without writing as the app mode would compute them
  (score_check: the engine's samples and nights first, OW filling the rest, rawdata/samples.py), against the stored
  scores; within SHADOW_SCORE_POINTS ok, larger and an input of that day explained = explained `input_diff`, else mismatch.

Results: sync_shadow_diffs (one row per user, day, kind, key; health values: own data only and the deployer's terminal)
and sync_shadow_summary (counts only: verdict counts, the clean streak, coverage, ready + blockers) which the cut-over
gates (rawdata/cutover.py), doctor and the private ops plugin read. `body-engine sync-shadow-compare` (nightly, after
import-ow) and `sync-shadow-report` (Chinese Markdown to the terminal or health_data/reports/, never git).
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..importers import ow as ow_imp
from . import engine as EN
from . import hk_types as T
from . import params as P
from . import side_by_side as SBS

log = logging.getLogger(__name__)

BAD = ("mismatch", "missing_app")
VERDICTS = ("ok", "explained", "mismatch", "missing_app", "new")
ENGINE_METRICS = {t.key for t in T.ENGINE_TYPES if t.kind == "quantity"}
OW_METRICS = {spec.metric for spec in ow_imp.SPECS.values()}
SLEEP_FIELDS = ("asleep_min", "core_min", "deep_min", "rem_min", "awake_min")
SCORE_KEYS = ("recovery", "sleep_score", "strain")


def _rel(ow: float | None, app: float | None) -> float | None:
    if ow is None or app is None:
        return None
    if ow == app:
        return 0.0
    return abs(app - ow) / abs(ow) if ow else float("inf")


def _item(kind: str, key: str, verdict: str, reason: str | None = None, ow=None, app=None, detail=None) -> dict:
    diff = None if ow is None or app is None else round(app - ow, 4)
    rel = _rel(ow, app)
    return {"kind": kind, "key": key, "verdict": verdict, "reason": reason, "ow_value": ow, "app_value": app,
            "diff": diff, "rel": None if rel is None or rel == float("inf") else round(rel, 5), "detail": detail}


# ---------------------------------------------------------------- judging (pure functions)
def judge_daily(row: dict) -> dict | None:
    """One metric-day of side_by_side.days()['daily'] -> an item, or None (a metric the engine does not read)."""
    metric = row["metric"]
    if metric not in ENGINE_METRICS:
        return None
    ow, app = row["ow"], row["app"]
    detail = {"agg": row["agg"], "unit": row["unit"], "ow": ow, "app": app}
    if ow is None and app is None:
        return None
    if app is None:
        return _item("daily", metric, "missing_app", "missing_app", ow["value"], None, detail)
    if ow is None:
        return _item("daily", metric, "new", "missing_ow" if metric in OW_METRICS else None, None, app["value"], detail)
    rel = _rel(ow["value"], app["value"])
    if row["agg"] == "sum":
        if rel <= P.SHADOW_SUM_REL:
            return _item("daily", metric, "ok", None, ow["value"], app["value"], detail)
        if rel <= P.SHADOW_SUM_METHOD_REL:
            return _item("daily", metric, "explained", "method", ow["value"], app["value"], detail)
        return _item("daily", metric, "mismatch", "value", ow["value"], app["value"], detail)
    rels = [rel, _rel(ow.get("min"), app.get("min")) or 0.0, _rel(ow.get("max"), app.get("max")) or 0.0]
    n_ow, n_app = ow.get("n") or 0, app.get("n") or 0
    same_n = n_ow == n_app
    count_rel = abs(n_app - n_ow) / max(n_ow, n_app, 1)
    if max(rels) <= P.SHADOW_AVG_REL and same_n:
        return _item("daily", metric, "ok", None, ow["value"], app["value"], detail)
    if not same_n and count_rel <= P.SHADOW_COUNT_EXPLAINED_REL:
        reason = "ow_same_ts" if n_ow < n_app else "deleted_in_health"
        return _item("daily", metric, "explained", reason, ow["value"], app["value"], detail)
    if max(rels) <= P.SHADOW_AVG_REL:
        return _item("daily", metric, "explained", "late_sample", ow["value"], app["value"], detail)
    return _item("daily", metric, "mismatch", "value", ow["value"], app["value"], detail)


def _minutes(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 60


def judge_sleep(day: date, nights: dict) -> dict | None:
    """The main nights of one wake date (side_by_side.days()['sleep'])."""
    ow = next((n for n in nights["ow"] if n.get("is_main")), None) or (nights["ow"][0] if nights["ow"] else None)
    app = next((n for n in nights["app"] if n.get("is_main")), None) or (nights["app"][0] if nights["app"] else None)
    if ow is None and app is None:
        return None
    if app is None:
        return _item("sleep", "night", "missing_app", "missing_app", ow["asleep_min"], None)
    if ow is None:
        return _item("sleep", "night", "new", "missing_ow", None, app["asleep_min"])
    diffs = {"start": _minutes(ow["start_at"], app["start_at"]), "end": _minutes(ow["end_at"], app["end_at"]),
             **{k: abs((ow.get(k) or 0) - (app.get(k) or 0)) for k in SLEEP_FIELDS}}
    worst = max(diffs.values())
    detail = {"diff_min": {k: round(v, 1) for k, v in diffs.items()}, "sessions": [len(nights["ow"]), len(nights["app"])],
              "devices": [ow.get("device"), app.get("device")]}
    if worst <= P.SHADOW_SLEEP_MIN:
        return _item("sleep", "night", "ok", None, ow["asleep_min"], app["asleep_min"], detail)
    several = len(nights["ow"]) != len(nights["app"]) or (ow.get("device") or "") != (app.get("device") or "")
    if worst <= P.SHADOW_SLEEP_EXPLAINED_MIN and several:
        return _item("sleep", "night", "explained", "source_priority", ow["asleep_min"], app["asleep_min"], detail)
    return _item("sleep", "night", "mismatch", "value", ow["asleep_min"], app["asleep_min"], detail)


def _overlap(a: dict, b: dict) -> float:
    lo, hi = max(a["start_at"], b["start_at"]), min(a["end_at"], b["end_at"])
    return max((hi - lo).total_seconds(), 0.0)


def judge_workouts(ws: dict) -> list[dict]:
    """The day's workouts of both paths (side_by_side.days()['workouts']): OW's kept ones (not duplicates) paired with
    the app's by the largest time overlap."""
    ow = [w for w in ws["ow"] if not w.get("dup_of")]
    app = [w for w in ws["app"] if w.get("excluded") in (None, "shadow")]
    out, used = [], set()
    for w in ow:
        key = f"{w['start_at']:%H:%M} {w['type']}"
        best = max(((i, _overlap(w, a)) for i, a in enumerate(app) if i not in used), key=lambda x: x[1], default=None)
        if best is None or best[1] <= 0:
            out.append(_item("workout", key, "missing_app", "missing_app", w["total_kcal"], None))
            continue
        used.add(best[0])
        a = app[best[0]]
        d = {"duration_min": abs((w["duration_min"] or 0) - (a["duration_min"] or 0)),
             "total_kcal_rel": _rel(w["total_kcal"], a["total_kcal"]) or 0.0,
             "avg_hr": abs((w["avg_hr"] or 0) - (a["avg_hr"] or 0)), "max_hr": abs((w["max_hr"] or 0) - (a["max_hr"] or 0))}
        detail = {"diff": {k: round(v, 4) for k, v in d.items()}, "type": [w["type"], a["type"]]}
        ok = (w["type"] == a["type"] and d["duration_min"] <= P.SHADOW_WORKOUT_MIN
              and d["total_kcal_rel"] <= P.SHADOW_WORKOUT_KCAL_REL
              and d["avg_hr"] <= P.SHADOW_WORKOUT_HR and d["max_hr"] <= P.SHADOW_WORKOUT_HR)
        if ok:
            reason = "active_kcal_method" if (w.get("active_kcal") or 0) != (a.get("active_kcal") or 0) else None
            out.append(_item("workout", key, "explained" if reason else "ok", reason, w["total_kcal"], a["total_kcal"],
                             detail))
        else:
            out.append(_item("workout", key, "mismatch", "value", w["total_kcal"], a["total_kcal"], detail))
    for i, a in enumerate(app):
        if i not in used:
            out.append(_item("workout", f"{a['start_at']:%H:%M} {a['type']}", "new", "missing_ow", None, a["total_kcal"]))
    return out


def judge_scores(stored: dict | None, app: dict | None, inputs_differ: bool) -> list[dict]:
    """Stored (OW-path) scores of one day vs the app-mode recompute."""
    out = []
    for k in SCORE_KEYS:
        a, b = (stored or {}).get(k), (app or {}).get(k)
        if a is None and b is None:
            continue
        if b is None:
            out.append(_item("score", k, "missing_app", "missing_app", a, None))
            continue
        if a is None:
            out.append(_item("score", k, "new", "missing_ow", None, b))
            continue
        tol = P.SHADOW_STRAIN_POINTS if k == "strain" else P.SHADOW_SCORE_POINTS
        d = abs(b - a)
        if d <= tol:
            out.append(_item("score", k, "ok", None, a, b))
        elif inputs_differ and d <= P.SHADOW_SCORE_EXPLAINED * (21 / 100 if k == "strain" else 1):
            out.append(_item("score", k, "explained", "input_diff", a, b))
        else:
            out.append(_item("score", k, "mismatch", "input_diff" if inputs_differ else "value", a, b))
    return out


# ---------------------------------------------------------------- scores as the app mode would compute them
def app_scores(conn: psycopg.Connection, client, user_id: int, start: date, end: date) -> dict[date, dict]:
    """Recovery / sleep score / strain of [start, end] recomputed without writing, from the inputs the app mode reads
    (scores/inputs.load with sync_mode='app': the engine's samples first, OW filling; the app's nights from its stage
    samples replace the stored ones where it has a night; the app's resting heart rate where it has one)."""
    from ..scores import engine as SE, hrv_kind as HK, inputs as SI, params as SP
    origin = SE.scoring_origin(conn, user_id)
    if origin is None:
        return {}
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    kind, _ = HK.choose(conn, user_id, tz, start, end)
    lo = origin - timedelta(days=SP.HRMAX_WINDOW_DAYS)
    inp = SI.load(conn, client, user_id, lo, end, None, hrv_kind=kind, ow_cache="use" if client is not None else None,
                  sync_mode="app")
    swap_in_app_nights(conn, user_id, inp, max(lo, start - timedelta(days=P.CUTOVER_REDERIVE_DAYS)), end)
    rows = SE.score_days(inp, origin, end, datetime.now(tz).date())
    conn.rollback()   # the reads may have stored OW samples in the local copy: harmless, but this function writes nothing
    return {r["date"]: {k: r[k] for k in SCORE_KEYS} for r in rows if start <= r["date"] <= end}


def swap_in_app_nights(conn: psycopg.Connection, user_id: int, inp, start: date, end: date) -> int:
    """Replace the stored nights of the wake dates the app has nights for (as replace_sleep does in the app mode) and
    use the app's resting heart rate where it has one. Returns how many wake dates were replaced."""
    from ..scores.inputs import Night, is_watch_sleep
    from . import derive as DV
    dates = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    app = DV.nights(conn, user_id, dates, inp.tz)
    have = {n.wake_date for n in app}
    if not have:
        return 0
    nights = [n for n in inp.nights if n.wake_date not in have]
    for n in app:
        m = n.minutes
        row = {"device": n.device, "core_min": m["core"], "deep_min": m["deep"], "rem_min": m["rem"]}
        if is_watch_sleep(row):
            nights.append(Night(n.wake_date, n.start, n.end, n.asleep_min, m["deep"], m["rem"], m["awake"], "app"))
        else:
            inp.non_watch_dates.add(n.wake_date)
    inp.nights = sorted(nights, key=lambda n: n.start)
    inp.non_watch_dates -= {n.wake_date for n in inp.nights}
    for r in conn.execute("""SELECT date, value FROM daily_metric_sources WHERE user_id = %s AND source = 'app'
                             AND metric = 'resting_heart_rate' AND date BETWEEN %s AND %s""", (user_id, start, end)):
        inp.daily_rhr[r["date"]] = r["value"]
    return len(have)


def stored_scores(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[date, dict]:
    return {r["date"]: {k: r[k] for k in SCORE_KEYS} for r in conn.execute(
        f"SELECT date, {', '.join(SCORE_KEYS)} FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s",
        (user_id, start, end))}


# ---------------------------------------------------------------- one user, everyone
def last_closed_day(tz: ZoneInfo, now: datetime | None = None) -> date:
    """The newest day whose end (local midnight) is at least SHADOW_CLOSED_AFTER ago."""
    now = now or datetime.now(timezone.utc)
    return (now - P.SHADOW_CLOSED_AFTER).astimezone(tz).date() - timedelta(days=1)


def compare_user(conn: psycopg.Connection, user_id: int, start: date | None = None, end: date | None = None,
                 client=None, with_scores: bool = False, now: datetime | None = None) -> dict:
    """Compare [start, end] (default: the last SHADOW_DAYS closed days), replace those days' rows in sync_shadow_diffs,
    refresh sync_shadow_summary. Commits. Returns {days, counts, summary}."""
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    end = min(end or last_closed_day(tz, now), last_closed_day(tz, now))
    start = start or end - timedelta(days=P.SHADOW_DAYS - 1)
    items: dict[date, list[dict]] = {}
    d = start
    while d <= end:
        chunk_end = min(d + timedelta(days=SBS.MAX_DAYS - 1), end)
        for day in SBS.days(conn, user_id, d, chunk_end)["days"]:
            got = [x for r in day["daily"] if (x := judge_daily(r)) is not None]
            if (s := judge_sleep(day["date"], day["sleep"])) is not None:
                got.append(s)
            got += judge_workouts(day["workouts"])
            items[day["date"]] = got
        d = chunk_end + timedelta(days=1)
    if with_scores and items:
        stored = stored_scores(conn, user_id, start, end)
        try:
            app = app_scores(conn, client, user_id, start, end)
        except Exception as e:  # noqa: BLE001  a failing recompute must not stop the input comparison
            log.warning("shadow score check failed for user %s: %s", user_id, e)
            conn.rollback()
            app = None
        if app is not None:
            for day, got in items.items():
                differ = any(x["verdict"] != "ok" and x["kind"] != "score" for x in got)
                got += judge_scores(stored.get(day), app.get(day), differ)
    with conn.transaction():
        conn.execute("DELETE FROM sync_shadow_diffs WHERE user_id = %s AND date BETWEEN %s AND %s", (user_id, start, end))
        rows = [(user_id, day, x["kind"], x["key"], x["ow_value"], x["app_value"], x["diff"], x["rel"], x["verdict"],
                 x["reason"], Jsonb(_jsonable(x["detail"])) if x["detail"] is not None else None)
                for day, got in items.items() for x in got]
        if rows:
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO sync_shadow_diffs (user_id, date, kind, key, ow_value, app_value, diff, rel,
                                                                  verdict, reason, detail)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                   ON CONFLICT (user_id, date, kind, key) DO UPDATE SET ow_value = EXCLUDED.ow_value,
                                     app_value = EXCLUDED.app_value, diff = EXCLUDED.diff, rel = EXCLUDED.rel,
                                     verdict = EXCLUDED.verdict, reason = EXCLUDED.reason, detail = EXCLUDED.detail,
                                     computed_at = now()""", rows)
        summary = summarize(conn, user_id, end)
    conn.commit()
    return {"user_id": user_id, "start": start, "end": end,
            "counts": dict(Counter(x["verdict"] for got in items.values() for x in got)), "summary": summary}


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, float) and x == float("inf"):
        return None
    return x


def summarize(conn: psycopg.Connection, user_id: int, last_day: date) -> dict:
    """sync_shadow_summary from every stored day up to last_day (in the caller's transaction)."""
    rows = conn.execute("""SELECT date, verdict, kind, count(*) AS n FROM sync_shadow_diffs
                           WHERE user_id = %s AND date <= %s GROUP BY date, verdict, kind ORDER BY date DESC""",
                        (user_id, last_day)).fetchall()
    by_day: dict[date, Counter] = {}
    counts: Counter = Counter()
    for r in rows:
        by_day.setdefault(r["date"], Counter())[r["verdict"]] += r["n"]
    window = sorted(by_day, reverse=True)
    recent = window[:max(P.SHADOW_DAYS, P.CUTOVER_CLEAN_DAYS)]
    for d in recent:
        counts.update(by_day[d])
    streak, expect = 0, last_day
    for d in window:
        if d != expect or any(by_day[d][v] for v in BAD):
            break
        streak += 1
        expect = d - timedelta(days=1)
    cov = conn.execute("""SELECT count(*) FILTER (WHERE ow_value IS NOT NULL) AS ow,
                                 count(*) FILTER (WHERE ow_value IS NOT NULL AND app_value IS NOT NULL) AS both_
                          FROM sync_shadow_diffs WHERE user_id = %s AND kind = 'daily' AND date = ANY(%s)""",
                       (user_id, recent)).fetchone()
    coverage = round(cov["both_"] / cov["ow"], 4) if cov["ow"] else None
    blockers = []
    if not window:
        blockers.append("no_data")
    if streak < P.CUTOVER_CLEAN_DAYS:
        blockers.append("streak")
    if coverage is None or coverage < P.CUTOVER_COVERAGE:
        blockers.append("coverage")
    out = {"first_day": min(window) if window else None, "last_day": max(window) if window else None,
           "counts": dict(counts), "clean_streak": streak, "coverage": coverage, "ready": not blockers,
           "blockers": blockers}
    conn.execute("""INSERT INTO sync_shadow_summary (user_id, first_day, last_day, counts, clean_streak, coverage, ready,
                                                     blockers)
                    VALUES (%(u)s, %(first_day)s, %(last_day)s, %(c)s, %(clean_streak)s, %(coverage)s, %(ready)s, %(blockers)s)
                    ON CONFLICT (user_id) DO UPDATE SET computed_at = now(), first_day = EXCLUDED.first_day,
                      last_day = EXCLUDED.last_day, counts = EXCLUDED.counts, clean_streak = EXCLUDED.clean_streak,
                      coverage = EXCLUDED.coverage, ready = EXCLUDED.ready, blockers = EXCLUDED.blockers""",
                 {"u": user_id, **out, "c": Jsonb(out["counts"])})
    return out


def summary(conn: psycopg.Connection, user_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM sync_shadow_summary WHERE user_id = %s", (user_id,)).fetchone()
    return None if r is None else {k: r[k] for k in ("computed_at", "first_day", "last_day", "counts", "clean_streak",
                                                     "coverage", "ready", "blockers")}


def users(conn: psycopg.Connection) -> list[int]:
    """Users the comparison covers: the engine is on for them and they uploaded engine data."""
    ids = [r["user_id"] for r in conn.execute("SELECT DISTINCT user_id FROM hk_sources ORDER BY user_id")]
    return [u for u in ids if EN.enabled(conn, u)]


def run(conn: psycopg.Connection, user_id: int | None = None, client=None, with_scores: bool = True,
        start: date | None = None, end: date | None = None) -> list[dict]:
    """`body-engine sync-shadow-compare`: every covered user (or one). One user's failure does not stop the others."""
    from ..profile import consent
    out = []
    for uid in ([user_id] if user_id is not None else users(conn)):
        if consent.gate(conn, uid):
            conn.commit()
            out.append({"user_id": uid, "skipped": "consent"})
            continue
        try:
            out.append(compare_user(conn, uid, start, end, client, with_scores))
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            log.warning("sync-shadow-compare failed for user %s: %s", uid, e)
            out.append({"user_id": uid, "error": str(e)[:300]})
    return out


def diffs(conn: psycopg.Connection, user_id: int, start: date, end: date, only_bad: bool = False) -> list[dict]:
    return conn.execute(f"""SELECT date, kind, key, ow_value, app_value, diff, rel, verdict, reason, detail
                            FROM sync_shadow_diffs WHERE user_id = %s AND date BETWEEN %s AND %s
                            {"AND verdict <> 'ok'" if only_bad else ""} ORDER BY date, kind, key""",
                        (user_id, start, end)).fetchall()


# ---------------------------------------------------------------- terminal report (Chinese, like hrv-compare)
VERDICT_ZH = {"ok": "一致", "explained": "可解释", "mismatch": "不一致", "missing_app": "app 缺", "new": "仅 app"}
REASON_ZH = {"method": "累计口径（OW 推断结束时间）", "ow_same_ts": "OW 合并同一时刻的样本", "deleted_in_health": "健康里删掉的样本 OW 还留着",
             "late_sample": "迟到的样本", "source_priority": "多来源睡眠的切段规则", "active_kcal_method": "活动能量口径",
             "input_diff": "当天输入有差异", "missing_app": "app 没有", "missing_ow": "OW 没同步到", "value": "数值不同"}


def markdown(conn: psycopg.Connection, user_ids: list[int], since: date | None = None) -> str:
    """Chinese Markdown with health values: the terminal or health_data/reports/ only (never git)."""
    out = ["# 影子对比报告（自建同步引擎 vs Open Wearables）", ""]
    for uid in user_ids:
        s = summary(conn, uid)
        out.append(f"## user {uid}（当前模式 {EN.mode(conn, uid)}）")
        if s is None:
            out += ["", "还没有对比结果（先跑 `body-engine sync-shadow-compare`）。", ""]
            continue
        c = s["counts"]
        cov = "—" if s["coverage"] is None else f"{s['coverage']:.1%}"
        out += ["", f"- 对比到 {s['last_day']}，连续 {s['clean_streak']} 天没有不一致，覆盖率 {cov}；"
                    + "；".join(f"{VERDICT_ZH[v]} {c.get(v, 0)}" for v in VERDICTS),
                f"- 可以切换：{'是' if s['ready'] else '否（' + '、'.join(s['blockers']) + '）'}", ""]
        lo = since or (s["last_day"] or date.today()) - timedelta(days=P.SHADOW_DAYS - 1)
        bad = diffs(conn, uid, lo, s["last_day"] or date.today(), only_bad=True)
        if not bad:
            out += ["这段时间全部一致。", ""]
            continue
        out += ["| 日期 | 类别 | 项 | OW | app | 相对差 | 判定 | 原因 |", "|---|---|---|---|---|---|---|---|"]
        for r in bad:
            rel = "—" if r["rel"] is None else f"{r['rel']:+.2%}"
            out.append(f"| {r['date']} | {r['kind']} | {r['key']} | {SBS._fmt(r['ow_value'])} | {SBS._fmt(r['app_value'])}"
                       f" | {rel} | {VERDICT_ZH.get(r['verdict'], r['verdict'])} | {REASON_ZH.get(r['reason'], r['reason'] or '')} |")
        out.append("")
    return "\n".join(out)


def doctor_lines(conn: psycopg.Connection) -> list[dict]:
    """Counts only (no health values): one entry per covered user for doctor / the ops plugin."""
    return [{"user_id": r["user_id"], "computed_at": r["computed_at"], "last_day": r["last_day"], "ready": r["ready"],
             "clean_streak": r["clean_streak"], "coverage": r["coverage"], "blockers": r["blockers"],
             "bad": sum((r["counts"] or {}).get(v, 0) for v in BAD)}
            for r in conn.execute("SELECT * FROM sync_shadow_summary ORDER BY user_id")]
