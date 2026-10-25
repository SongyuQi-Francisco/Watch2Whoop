"""Scoring engine: turns the inputs (inputs.Inputs) day by day into daily_scores rows, then writes them to the database.

Every run computes from the user's scoring start (the first day with a resting heart rate = when they started wearing the watch) up to end, so results depend only on the data itself
(CTL / ATL, the 60-day baseline, 14 nights of sleep debt and the 365-day HRmax all need earlier history); --start / --end only limit which days are written.
"""
import statistics
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from . import params as P
from . import recovery as R
from . import sleep as S
from . import strain as L
from . import hrv_kind as HK
from .inputs import Inputs, Night, load, segments_in
from .muscle import daily_muscle_load
from .. import sparky_client
from ..cardio import hrr as cardio_hrr

COLUMNS = ["sleep_score", "sleep_need_min", "sleep_debt_min", "sleep_performance", "sleep_actual_min",
           "hrv_kind", "hrv_night_ln", "rhr_night", "resp_rate_night", "recovery",
           "trimp", "strain", "hr_max", "muscle_load", "ctl", "atl", "tsb", "flags", "details", "algo_version"]
WRITE_LOCK_NS = 0x4453   # advisory lock namespace ("DS") for (namespace, user_id): one daily_scores write per user at a time


def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _between(samples, start, end, times=None):
    """samples sorted by time; take those within [start, end]. times: [t for t, _ in samples], when the caller has it."""
    times = [t for t, _ in samples] if times is None else times
    return samples[bisect_left(times, start):bisect_right(times, end)]


class _Segments:
    """The sleep-stage segments (sorted by start) with their start times, so a night's segments are found by bisection
    instead of a scan of the whole history for every night (same segments, same order)."""

    def __init__(self, segments):
        self.all = segments
        self.starts = [s[0] for s in segments]
        self.max_len = max((s[1] - s[0] for s in segments), default=timedelta(0))

    def starting(self, lo, hi):
        """The segments whose start is within [lo, hi], in order."""
        return self.all[bisect_left(self.starts, lo):bisect_right(self.starts, hi)]

    def within(self, start, end):
        """= inputs.segments_in(segments, start, end)."""
        return [s for s in self.starting(start, end) if s[1] <= end]


def _r(x, nd=3):
    return None if x is None else round(x, nd)


def score_days(inp: Inputs, first: date, last: date, today: date | None = None) -> list[dict]:
    """Compute the scores day by day for [first, last]. Pure function: uses only the data in inp."""
    tz = inp.tz
    today = today or datetime.now(tz).date()

    # ---- sleep: the longest watch sleep segment per wake date is the main sleep, the rest are naps ----
    by_wake: dict[date, list[Night]] = defaultdict(list)
    for n in inp.nights:
        by_wake[n.wake_date].append(n)
    main = {d: max(ns, key=lambda n: n.asleep_min) for d, ns in by_wake.items()}
    naps = sorted((n for d, ns in by_wake.items() for n in ns if n is not main[d]), key=lambda n: n.start)
    mains_sorted = sorted(main.values(), key=lambda n: n.start)

    # ---- strain: heart rate grouped by local date; the minutes each sample represents are computed over the whole timeline ----
    hr_times = [t for t, _ in inp.hr]
    hr_minutes = L.sample_minutes(hr_times)
    hr_by_day: dict[date, list[int]] = defaultdict(list)   # date -> sample indexes
    for i, t in enumerate(hr_times):
        hr_by_day[t.astimezone(tz).date()].append(i)
    hist_by_day = {d: Counter(round(inp.hr[i][1]) for i in idx) for d, idx in hr_by_day.items()}
    window = L.HrHistogram()
    for d in _days(first - timedelta(days=P.HRMAX_WINDOW_DAYS), first - timedelta(days=1)):
        if d in hist_by_day:
            window.add(hist_by_day[d])

    actual: dict[date, float] = {}        # wake date -> actual sleep (main sleep + earlier naps)
    strain_by_day: dict[date, float] = {}
    feats: dict[str, dict[date, float]] = {k: {} for k in ("hrv", "rhr", "sleep_performance", "resp_rate", "temp")}
    feats["rhr_daily"] = dict(inp.daily_rhr)   # Apple's all-day resting heart rate used as a fallback, with its own baseline
    rows = []
    # time lists of the raw series and the indexed segments: built once, not once per night
    times = {k: [t for t, _ in getattr(inp, k)] for k in ("hr", "resp", "temp", "spo2")}
    ordered = inp.segments == sorted(inp.segments) and all(a <= b for a, b, _ in inp.segments)
    seg_index = _Segments(inp.segments) if ordered else None

    for d in _days(first, last):
        flags: dict = {}
        details: dict = {}
        row = {c: None for c in COLUMNS}
        row.update(date=d, hrv_kind=inp.hrv_kind, algo_version=P.ALGO_VERSION)
        details["hrv_kind"] = inp.hrv_kind   # also in the column; kept with the rest of the inputs (#117)
        if d == today:
            flags["day_in_progress"] = "今天还没过完，负荷是到目前为止的"

        # ================= strain =================
        if d in hist_by_day:
            window.add(hist_by_day[d])
        old = d - timedelta(days=P.HRMAX_WINDOW_DAYS)
        if old in hist_by_day:
            window.add(hist_by_day[old], -1)
        idx = hr_by_day.get(d, [])
        if idx:
            age = L.age_on(inp.birth_date, d) if inp.birth_date else None
            observed = window.percentile(P.HRMAX_PERCENTILE)
            hr_max = L.hrmax(age, observed) if age is not None else observed
            trimp, zones = L.edwards_trimp([inp.hr[i] for i in idx], [hr_minutes[i] for i in idx], hr_max)
            hours = len({inp.hr[i][0].astimezone(tz).hour for i in idx})
            details["load"] = {"zone_minutes": [round(z, 1) for z in zones], "hr_samples": len(idx), "hr_hours": hours,
                               "trimp_recorded": round(trimp, 1), "hrmax_observed_p995": observed,
                               "hrmax_formula": round(L.hrmax_formula(age), 1) if age is not None else None}
            row["hr_max"] = round(hr_max, 1)
            if hours >= P.MIN_HR_HOURS or d == today or trimp >= P.MIN_PARTIAL_TRIMP:
                row.update(trimp=round(trimp, 1), strain=round(L.strain(trimp), 2))
                strain_by_day[d] = row["strain"]
                if hours < P.MIN_HR_HOURS and d != today:
                    flags["hr_coverage_low"] = f"只有 {hours} 小时有心率，负荷可能偏低（录到的部分已经有训练，照常出分）"
            else:
                flags["no_raw_hr"] = (f"只有 {hours} 小时有心率（要 ≥ {P.MIN_HR_HOURS}），又没录到训练，负荷不出分"
                                      + ("；其余时间只有快捷指令数据（心率按小时求和，用不了）" if d in inp.shortcut_dates else ""))
        elif d in inp.shortcut_dates:
            flags["no_raw_hr"] = "这天只有快捷指令数据：心率是按小时求和的，算不了 TRIMP / strain"
        else:
            flags["no_raw_hr"] = "没有原始心率（手表没戴或没同步），算不了 TRIMP / strain"

        # ================= muscle load (SparkyFitness workout records) =================
        if d in inp.muscle:
            row["muscle_load"] = inp.muscle[d]["volume_kg"]
            details["muscle"] = inp.muscle[d]
        elif inp.muscle_error:
            flags["muscle_load_unavailable"] = f"读不到 SparkyFitness 训练记录，肌肉负荷没算：{inp.muscle_error}"

        # ================= sleep =================
        night = main.get(d)
        if night is None:
            flags["no_sleep"] = ("只有非手表来源的睡眠（没有阶段数据），不计" if d in inp.non_watch_dates
                                 else "没有手表睡眠数据")
        else:
            segs = seg_index.within(night.start, night.end) if seg_index else segments_in(inp.segments, night.start, night.end)
            if segs:
                awake_min, awakenings = S.waso(segs)
            else:
                awake_min, awakenings = night.awake_min, []
                flags["no_stage_segments"] = "拿不到阶段明细：中断分用整段清醒分钟，醒来次数按 0"
            # consistency: bedtimes of the latest 14 main sleeps within the previous 15 days (OW's window)
            hist = [n.start.astimezone(tz) for n in mains_sorted
                    if d - timedelta(days=P.CONSISTENCY_WINDOW_NIGHTS + 1) <= n.wake_date < d][-P.CONSISTENCY_WINDOW_NIGHTS:]
            if not hist:
                flags["consistency_no_history"] = "前 15 天没有主睡眠，规律性按 OW 规格给 0"
            sc = S.sleep_score(night.asleep_min, night.deep_min, night.rem_min, night.start.astimezone(tz), hist,
                               awake_min, awakenings)
            # actual sleep = main sleep + naps after the previous main sleep and before this one
            prev_mains = [n for n in mains_sorted if n.end <= night.start]
            lo = max(prev_mains[-1].end if prev_mains else night.start - timedelta(hours=24),
                     night.start - timedelta(hours=24))
            nap_min = sum(n.asleep_min for n in naps if n.start >= lo and n.end <= night.start)
            actual[d] = night.asleep_min + nap_min
            debt = S.sleep_debt([actual.get(d - timedelta(days=k)) for k in range(1, P.DEBT_NIGHTS + 1)])
            prev_strain = strain_by_day.get(d - timedelta(days=1))
            if prev_strain is None:
                flags["prev_strain_missing"] = "前一天没有负荷，睡眠需求不加负荷加成"
            need = S.sleep_need(debt, prev_strain)
            perf = S.performance(actual[d], need["need"])
            row.update(sleep_score=sc["score"], sleep_need_min=round(need["need"]), sleep_debt_min=round(debt),
                       sleep_performance=round(perf, 4), sleep_actual_min=round(actual[d]))
            details["sleep"] = {**{k: v for k, v in sc.items() if k != "score"},
                                "asleep_min": night.asleep_min, "nap_min": round(nap_min), "deep_min": night.deep_min,
                                "rem_min": night.rem_min, "waso_min": round(awake_min, 1),
                                "awakenings": sum(1 for a in awakenings if a > P.SIGNIFICANT_WAKE_MIN),
                                "bedtime": night.start.astimezone(tz).isoformat(timespec="minutes"),
                                "wake_time": night.end.astimezone(tz).isoformat(timespec="minutes"),
                                "need_from_debt": round(need["from_debt"]), "need_from_strain": round(need["from_strain"]),
                                "source": night.source, **_latency_efficiency(inp.segments, night, seg_index)}
            feats["sleep_performance"][d] = perf

        # ================= recovery =================
        if night is not None:
            hrv_ln, n_hrv = R.night_hrv_ln(inp.hrv, night.start, night.end)
            rhr, rhr_at = R.lowest_window(_between(inp.hr, night.start, night.end, times["hr"]), night.start, night.end)
            rhr_key = "rhr"
            if rhr is None and d in inp.daily_rhr:
                # Apple's all-day resting heart rate is a few bpm above the lowest during sleep, so it can only be compared with its own baseline
                rhr, rhr_key = inp.daily_rhr[d], "rhr_daily"
                flags["rhr_from_daily"] = "没有睡眠期原始心率，用当天的 resting_heart_rate（Apple 全天算法），和它自己的 60 天基线比"
            resp = [v for _, v in _between(inp.resp, night.start, night.end, times["resp"])]
            resp_night = statistics.median(resp) if resp else None
            temps = [v for _, v in _between(inp.temp, night.start - timedelta(hours=2), night.end + timedelta(hours=2),
                                            times["temp"])]
            temp = statistics.median(temps) if temps else None
            spo2 = [v for _, v in _between(inp.spo2, night.start, night.end, times["spo2"])]
            row.update(hrv_night_ln=_r(hrv_ln, 4), rhr_night=_r(rhr, 1), resp_rate_night=_r(resp_night, 2))

            past = [d - timedelta(days=k) for k in range(1, P.BASELINE_DAYS + 1)]
            base = {k: [feats[k][x] for x in past if x in feats[k]] for k in feats}
            today_vals = {"hrv": hrv_ln, "rhr": rhr, "sleep_performance": feats["sleep_performance"].get(d),
                          "resp_rate": resp_night}
            base_key = {"rhr": rhr_key}
            rec = {"hrv_readings": n_hrv, "baseline_nights": len(base["hrv"])}
            if rhr_at is not None:   # minutes after falling asleep when the lowest 5-minute heart rate started (Trends recovery index)
                rec["rhr_low_offset_min"] = round((rhr_at - night.start).total_seconds() / 60)
            hrv_base = R.baseline(base["hrv"])
            if hrv_ln is None:
                flags["hrv_missing"] = ("睡眠窗口内没有 HRV 读数，算不了恢复分" if inp.hrv_kind == "sdnn" else
                                        "睡眠窗口内没有逐拍数据算出的 RMSSD，算不了恢复分（这个人用 RMSSD，不拿 SDNN 补）")
            elif hrv_base is None:
                flags["baseline_insufficient"] = f"前 60 天只有 {len(base['hrv'])} 晚 HRV，满 {P.MIN_BASELINE_NIGHTS} 晚才出恢复分"
            else:
                z = {}
                for k, v in today_vals.items():
                    b = hrv_base if k == "hrv" else R.baseline(base[base_key.get(k, k)])
                    if v is not None and b is not None:
                        z[k] = P.RECOVERY_SIGNS[k] * R.zscore(v, b[0], b[1], P.SD_FLOOR[k])
                        rec[f"{k}_baseline"] = [round(b[0], 4), round(b[1], 4)]
                score, s = R.recovery_score(z)
                row["recovery"] = score
                rec.update(z={k: round(v, 3) for k, v in z.items()}, weighted_z=round(s, 3))
                rhr_base = R.baseline(base[rhr_key])
                if R.illness_warning(rhr, rhr_base[0] if rhr_base else None, hrv_ln, hrv_base[0]):
                    flags["illness_warning"] = "静息心率比基线高 5 以上且 HRV 低于基线 85%：可能在生病或过度疲劳"
            delta = R.wrist_temp_flag(temp, base["temp"])
            if delta is not None:
                flags["wrist_temp_deviation"] = f"手腕温度比 60 天基线{'高' if delta > 0 else '低'} {abs(delta):.1f}°C"
            if (low := R.spo2_flag(spo2)) is not None:
                flags["spo2_low"] = f"当晚血氧中位数 {low['median']}%，最低 {low['min']}%，低于 90% 的 {low['below_90']} 条"
            # raw values of that night used by the health monitor (insights/coach.py), not part of the score
            rec.update(spo2_night=_r(statistics.median(spo2), 1) if spo2 else None, spo2_readings=len(spo2),
                       wrist_temp=_r(temp, 2))
            details["recovery"] = rec
            for k, v in (("hrv", hrv_ln), (rhr_key, rhr), ("resp_rate", resp_night), ("temp", temp)):
                if v is not None:
                    feats[k][d] = v

        row["flags"], row["details"] = flags, details
        rows.append(row)

    # ================= CTL / ATL / TSB(computed only over consecutive dates after first) =================
    loads = L.training_load([r["trimp"] for r in rows])
    for i, (row, ld) in enumerate(zip(rows, loads)):
        row.update(ctl=_r(ld["ctl"], 1), atl=_r(ld["atl"], 1), tsb=_r(ld["tsb"], 1))
        if ld["missing"] and ld["ctl"] is not None:
            row["flags"]["load_missing"] = "这天没有完整的 TRIMP，CTL / ATL 沿用前一天"
        if i < P.CTL_DAYS:
            row["flags"]["ctl_warmup"] = f"评分起点后的前 {P.CTL_DAYS} 天，CTL 还没稳定"
    return rows


def _latency_efficiency(segments, night: Night, index: "_Segments | None" = None) -> dict:
    """Sleep latency ≈ the awake segment immediately before the first asleep segment (Apple Watch records awake time from the start of Sleep Focus; there is no "in bed" data,
    left empty when there is no such segment); sleep efficiency = asleep / (latency start → last wake)."""
    t, latency = night.start, 0.0
    lo = night.start - timedelta(hours=3)
    # a segment ending in [lo, night.start] starts in [lo - longest segment, night.start]: only those are looked at
    near = index.starting(lo - index.max_len, night.start) if index else segments
    for a, b, stage in reversed([s for s in near if s[1] <= night.start and s[1] >= lo]):
        if stage != "awake" or (t - b).total_seconds() > 60:
            break
        latency += (b - a).total_seconds() / 60
        t = a
    span = (night.end - t).total_seconds() / 60
    return {"latency_min": round(latency, 1) if latency else None,
            "efficiency": round(min(night.asleep_min / span, 1.0), 3) if span > 0 else None}


def scoring_origin(conn: psycopg.Connection, user_id: int) -> date | None:
    """Scoring start: the first day with a resting heart rate (only the watch writes it, equivalent to when the user started wearing the watch)."""
    r = conn.execute("SELECT min(date) AS d FROM daily_metrics WHERE user_id = %s AND metric = 'resting_heart_rate'",
                     (user_id,)).fetchone()
    return r["d"]


def write(conn: psycopg.Connection, user_id: int, rows: list[dict], start: date, end: date) -> int:
    """Delete the old rows in [start, end] before inserting (no stale rows when the algorithm changes), idempotent.
    Two recomputes of the same user can overlap (the hourly batch, the per-minute sync-refresh-run, a manual run): the
    writes of one user are serialised with a transaction-level advisory lock, and the insert is an upsert, so the second
    writer replaces the first one's rows instead of failing with UniqueViolation on daily_scores_pkey."""
    rows = [r for r in rows if start <= r["date"] <= end]
    cols = ["user_id", "date", *COLUMNS]
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (WRITE_LOCK_NS, user_id))
        conn.execute("DELETE FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s", (user_id, start, end))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO daily_scores ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
                f" ON CONFLICT (user_id, date) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in COLUMNS)}",
                [[user_id, r["date"], *[Jsonb(r[c]) if c in ("flags", "details") else r[c] for c in COLUMNS]]
                 for r in rows])
    return len(rows)


def muscle_sessions(conn: psycopg.Connection, user_id: int, start: date, end: date, sparky=None) -> tuple[list[dict], str | None, bool]:
    """Strength records for the muscle load in [start, end]: SparkyFitness workouts + imported ones (Hevy, training/hevy_import.py).
    Returns (sessions, error, linked): error = linked to SparkyFitness but unreadable; linked = SparkyFitness is linked."""
    from ..training import hevy_import
    imported = hevy_import.imported_sessions(conn, user_id, start, end)
    try:
        sparky = sparky or sparky_client.client_for_user(conn, user_id)
    except sparky_client.SparkyError:
        return imported, None, False
    try:
        return sparky_client.training_log(sparky, start, end) + imported, None, True
    except Exception as e:  # connected but unreadable
        return imported, f"{type(e).__name__}: {e}", True


def load_muscle(conn: psycopg.Connection, inp: Inputs, user_id: int, start: date, end: date, sparky=None) -> str:
    """Compute daily muscle load from SparkyFitness workout records and imported (Hevy) sets into inp.muscle; returns a one-line note."""
    sessions, error, linked = muscle_sessions(conn, user_id, start, end, sparky)
    if error:  # flag muscle_load_unavailable each day, other scores still computed
        inp.muscle_error = error
        return f"读不到 SparkyFitness 训练记录：{inp.muscle_error}"
    inp.muscle = daily_muscle_load(sessions)
    if not linked and not sessions:
        return "没连 SparkyFitness，肌肉负荷不算"
    return f"{len(inp.muscle)} 天有训练记录"


def run(conn: psycopg.Connection, client, user_id: int, start: date | None = None, end: date | None = None,
        archive_dir: Path | None = None, sparky=None, ow_cache: str | None = None) -> dict:
    """ow_cache: how the OW raw samples are read (importers/ow_cache.py): None = all from OW, "use" = through the local
    copy, "refresh" = all from OW and replace the copy."""
    origin = scoring_origin(conn, user_id)
    if origin is None:
        return {"rows": 0, "note": "这个用户还没有手表数据（没有 resting_heart_rate）"}
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    today = datetime.now(tz).date()
    end = end or today
    start = max(start or origin, origin)
    # HRV kind of this user (hrv_kind.py): SDNN unless switched on for RMSSD with enough beat data; never mixed
    kind, kind_reason = HK.choose(conn, user_id, tz, start, end)
    # pull one extra year back: the 365-day HRmax window; one extra day for sleep (the night before the first day)
    inp = load(conn, client, user_id, origin - timedelta(days=P.HRMAX_WINDOW_DAYS), end, archive_dir, hrv_kind=kind,
               ow_cache=ow_cache)
    muscle_note = load_muscle(conn, inp, user_id, origin, end, sparky)
    rows = score_days(inp, origin, end, today)
    n = write(conn, user_id, rows, start, end)
    written = [r for r in rows if start <= r["date"] <= end]
    # heart rate recovery after each workout, from the same raw heart rate (cardio/hrr.py); a failure does not affect the scores
    try:
        hrr = cardio_hrr.compute(conn, user_id, inp.hr, {r["date"]: r["hr_max"] for r in rows if r["hr_max"]}, start, end)
    except Exception as e:  # noqa: BLE001
        hrr = {"error": f"{type(e).__name__}: {e}"}
    return {"origin": origin, "start": start, "end": end, "rows": n, "algo_version": P.ALGO_VERSION,
            "hrv_kind": kind, "hrv_kind_reason": kind_reason,
            "with_sleep_score": sum(r["sleep_score"] is not None for r in written),
            "with_recovery": sum(r["recovery"] is not None for r in written),
            "with_strain": sum(r["strain"] is not None for r in written),
            "with_muscle_load": sum(r["muscle_load"] is not None for r in written), "muscle_load": muscle_note,
            "hrr": hrr}
