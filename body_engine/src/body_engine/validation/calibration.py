"""Calibration decisions (docs/scoring-calibration.md, issue #118): replay stored nightly inputs through the library's own
pure functions with changed parameters, measure each candidate, and apply the written rules. Reads files, writes nothing.

Pipeline: `export_queries` (one read-only query per table for ONE user) -> files in a private directory -> `load_dir` ->
`run` -> `markdown`. On real data the output holds personal health values: keep it and the exported files out of git.
Nothing in this module changes a parameter; a rule can only say a candidate is `eligible` for the maintainer to look at.
"""
import csv
import json
import math
import random
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ..rawdata import hrv as H
from ..scores import params as P
from ..scores import recovery as R
from ..scores import sleep as S
from ..scores import strain as L
from . import beats as B
from . import calibration_params as C
from . import study

GREEN, RED = study.GREEN, study.RED
SDNN_HK = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"


# ---------------------------------------------------------------- data
@dataclass
class Reading:
    start: datetime
    origin: str = ""                          # 'app' = exact beat times, 'apple_export' = 10 ms
    beats_ms: list[int] | None = None
    gaps: list[bool] | None = None
    apple_sdnn: float | None = None
    rmssd: float | None = None                # as stored


@dataclass
class Day:
    date: date
    night_start: datetime
    night_end: datetime
    rhr: float | None = None
    rhr_from_daily: bool = False
    resp: float | None = None
    perf: float | None = None                 # stored sleep performance
    actual_min: float | None = None
    trimp: float | None = None
    strain: float | None = None
    debt_min: float | None = None
    stored_recovery: int | None = None
    stored_hrv_ln: float | None = None


@dataclass
class Person:
    days: dict[date, Day] = field(default_factory=dict)      # only wake dates with a main watch sleep
    readings: list[Reading] = field(default_factory=list)    # beat series, sorted by start
    sdnn: list[tuple[datetime, float]] = field(default_factory=list)   # Apple's SDNN readings, sorted by time
    daily_rhr: dict[date, float] = field(default_factory=dict)
    hrr: dict[date, float] = field(default_factory=dict)     # mean 60-second heart-rate recovery of that day's workouts
    stored_kind: str = "rmssd"
    train_days: dict[date, tuple[float, float]] = field(default_factory=dict)   # date -> (trimp, stored strain), also days without sleep


def export_queries(user_id: int) -> dict[str, str]:
    """File name -> SELECT over this one user's rows. Every statement filters on the user id; run each read-only."""
    u = int(user_id)
    return {
        "beats.csv": f"""SELECT start_at, end_at, source, origin, to_json(beats_ms) AS beats_ms, to_json(gaps) AS gaps,
                                apple_sdnn, rmssd, n_beats, n_valid
                         FROM heartbeat_series WHERE user_id = {u} ORDER BY start_at""",
        "sdnn.csv": f"""SELECT 'hk' AS src, start_at AS ts, value FROM hk_samples
                          WHERE user_id = {u} AND type = '{SDNN_HK}'
                        UNION ALL
                        SELECT 'ow', to_timestamp(t / 1000000.0), v FROM ow_sample_cache c,
                               unnest(c.ts, c.val) AS x(t, v)
                          WHERE c.user_id = {u} AND c.type = 'heart_rate_variability_sdnn'
                        UNION ALL
                        SELECT 'shortcut', start_at, value FROM shortcut_samples
                          WHERE user_id = {u} AND type = 'Heart Rate Variability' AND value IS NOT NULL""",
        "nights.csv": f"""SELECT wake_date, start_at, end_at, asleep_min, core_min, deep_min, rem_min, device
                          FROM sleep_sessions WHERE user_id = {u} ORDER BY start_at""",
        "scores.csv": f"""SELECT date, sleep_performance, sleep_actual_min, sleep_debt_min, hrv_kind, hrv_night_ln, rhr_night,
                                 resp_rate_night, recovery, trimp, strain, flags ? 'rhr_from_daily' AS rhr_from_daily,
                                 flags ? 'illness_warning' AS illness
                          FROM daily_scores WHERE user_id = {u} ORDER BY date""",
        "daily_rhr.csv": f"""SELECT date, value FROM daily_metrics
                             WHERE user_id = {u} AND metric = 'resting_heart_rate' ORDER BY date""",
        "hrr.csv": f"""SELECT local_date, hrr60 FROM workout_hrr
                       WHERE user_id = {u} AND valid AND hrr60 IS NOT NULL ORDER BY local_date""",
    }


def _f(x: str | None) -> float | None:
    return float(x) if x not in (None, "") else None


def _rows(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _is_watch(r: dict) -> bool:
    """= scores/inputs.is_watch_sleep on the exported columns."""
    if r["device"] and "watch" in r["device"].lower():
        return True
    return (_f(r["core_min"]) or 0) + (_f(r["deep_min"]) or 0) + (_f(r["rem_min"]) or 0) > 0


def _merge_sdnn(rows: list[dict]) -> list[tuple[datetime, float]]:
    """One value per reading, in the order scores/inputs.load merges the sources. The same reading can arrive from two of them
    with different precision (microseconds from the app's sync, whole seconds from the others), so readings are matched on the
    whole second; matching on the exact time stamp would count it twice, which a one-reading-per-night metric would notice."""
    rank = {"hk": 0, "ow": 1, "shortcut": 2}
    seen: dict[datetime, tuple[datetime, float]] = {}
    for r in sorted(rows, key=lambda x: rank.get(x["src"], 9)):
        t = datetime.fromisoformat(r["ts"])
        key = t.replace(microsecond=0)
        if key not in seen and _f(r["value"]) is not None:
            seen[key] = (t, float(r["value"]))
    return sorted(seen.values())


def load_dir(path: Path) -> Person:
    """The exported files -> Person. Night windows follow the engine: the longest watch sleep of each wake date."""
    p = Person()
    mains: dict[date, dict] = {}
    for r in _rows(path / "nights.csv"):
        if not _is_watch(r):
            continue
        d = date.fromisoformat(r["wake_date"])
        if d not in mains or float(r["asleep_min"]) > float(mains[d]["asleep_min"]):
            mains[d] = r
    scores = {date.fromisoformat(r["date"]): r for r in _rows(path / "scores.csv")}
    kinds = {r["hrv_kind"] for r in scores.values() if r["hrv_kind"]}
    p.stored_kind = kinds.pop() if len(kinds) == 1 else "rmssd"
    for d, r in scores.items():
        if _f(r["trimp"]) is not None and _f(r["strain"]) is not None:
            p.train_days[d] = (float(r["trimp"]), float(r["strain"]))
    first = min(scores) if scores else None   # the scoring start: the engine holds no earlier nights in its baselines
    for d, n in sorted(mains.items()):
        if first is None or d < first:
            continue
        s = scores.get(d, {})
        p.days[d] = Day(d, datetime.fromisoformat(n["start_at"]), datetime.fromisoformat(n["end_at"]),
                        rhr=_f(s.get("rhr_night")), rhr_from_daily=s.get("rhr_from_daily") == "t",
                        resp=_f(s.get("resp_rate_night")), perf=_f(s.get("sleep_performance")),
                        actual_min=_f(s.get("sleep_actual_min")), trimp=_f(s.get("trimp")), strain=_f(s.get("strain")),
                        debt_min=_f(s.get("sleep_debt_min")),
                        stored_recovery=int(s["recovery"]) if s.get("recovery") else None,
                        stored_hrv_ln=_f(s.get("hrv_night_ln")))
    for r in _rows(path / "beats.csv"):
        p.readings.append(Reading(datetime.fromisoformat(r["start_at"]), r["origin"], json.loads(r["beats_ms"]),
                                  json.loads(r["gaps"]) if r["gaps"] else None, _f(r["apple_sdnn"]), _f(r["rmssd"])))
    p.readings.sort(key=lambda x: x.start)
    p.sdnn = _merge_sdnn(_rows(path / "sdnn.csv"))
    p.daily_rhr = {date.fromisoformat(r["date"]): float(r["value"]) for r in _rows(path / "daily_rhr.csv")}
    hrr: dict[date, list[float]] = {}
    for r in _rows(path / "hrr.csv"):
        hrr.setdefault(date.fromisoformat(r["local_date"]), []).append(float(r["hrr60"]))
    p.hrr = {d: statistics.fmean(v) for d, v in hrr.items()}
    return p


# ---------------------------------------------------------------- nightly HRV series
@dataclass
class Series:
    """Readings in ln units per night; the nightly value is their median, exactly as in scoring."""
    label: str
    nightly: dict[date, float]
    readings: dict[date, list[float]]
    per_reading: dict[datetime, float] = field(default_factory=dict)   # ln value by reading start, beat-based series only
    n_readings: int = 0


def _nightly(person: Person, samples: list[tuple[datetime, float]], label: str) -> Series:
    samples = sorted((t, v) for t, v in samples if v and v > 0)
    times = [t for t, _ in samples]
    nightly, readings = {}, {}
    for d, day in person.days.items():
        xs = [math.log(v) for _, v in samples[bisect_left(times, day.night_start):bisect_right(times, day.night_end)]]
        if xs:
            nightly[d], readings[d] = statistics.median(xs), xs
    return Series(label, nightly, readings, n_readings=len(samples))


def sdnn_series(person: Person) -> Series:
    return _nightly(person, person.sdnn, "SDNN")


def stored_rmssd_series(person: Person) -> Series:
    return _nightly(person, [(r.start, r.rmssd) for r in person.readings if r.rmssd], "RMSSD (stored)")


def _analyze(r: Reading, params, quantise_s: float | None = None, drop_gaps: bool = False) -> float | None:
    beats = [b / 1000 for b in r.beats_ms or []]
    if len(beats) < 2:
        return None
    if quantise_s:
        beats = B.quantise(beats, quantise_s)
    return H.analyze(beats, None if drop_gaps else r.gaps, params=params).rmssd


def rmssd_series(person: Person, label: str, *, max_deviation: float | None = None, select=None,
                 rmssd_fn=None, quantise_s: float | None = None, drop_gaps: bool = False) -> Series:
    """RMSSD recomputed from the beat times. max_deviation None = the production filter; rmssd_fn replaces the filter."""
    params = H if max_deviation is None else H.override(MAX_DEVIATION=max_deviation)
    samples, per = [], {}
    for r in person.readings:
        if select is not None and not select(r):
            continue
        v = rmssd_fn(r) if rmssd_fn is not None else _analyze(r, params, quantise_s, drop_gaps)
        if v and v > 0:
            samples.append((r.start, v))
            per[r.start] = math.log(v)
    s = _nightly(person, samples, label)
    s.per_reading = per
    return s


def local_median_rmssd(r: Reading, deviation: float = 0.20, half_window: int = 5) -> float | None:
    """Exploratory filter (D3): an interval is rejected when it deviates from the median of its neighbours (a local rule,
    so slow drifts within the minute are not trimmed and a mildly premature beat stands out). Not a candidate for adoption."""
    beats = [b / 1000 for b in r.beats_ms or []]
    if len(beats) < 2:
        return None
    rr = H.rr_intervals(beats, r.gaps)
    ok = [x if x is not None and H.MIN_RR_MS <= x <= H.MAX_RR_MS else None for x in rr]
    kept: list[float | None] = []
    for i, x in enumerate(ok):
        if x is None:
            kept.append(None)
            continue
        lo = max(0, i - half_window)
        near = [y for j, y in enumerate(ok[lo:i + half_window + 1], lo) if y is not None and j != i]
        mid = statistics.median(near) if near else None
        kept.append(x if mid is not None and abs(x - mid) <= deviation * mid else None)
    diffs = [b - a for a, b in zip(kept, kept[1:]) if a is not None and b is not None]
    return math.sqrt(sum(d * d for d in diffs) / len(diffs)) if len(diffs) >= H.MIN_VALID_DIFFS else None


# ---------------------------------------------------------------- replay
def recomputed_perf(person: Person, *, params=P, k: float | None = None) -> tuple[dict[date, float], dict[date, float]]:
    """Sleep performance and strain replayed from stored actual sleep and TRIMP (K and the need parameters can change).
    Returns ({date: performance}, {date: strain})."""
    strain = {d: round(L.strain(t, k, params=params), 2) for d, (t, _) in person.train_days.items()}
    actual = {d: x.actual_min for d, x in person.days.items() if x.actual_min is not None}
    perf = {}
    for d in sorted(actual):
        debt = S.sleep_debt([actual.get(d - timedelta(days=i)) for i in range(1, params.DEBT_NIGHTS + 1)], params=params)
        need = S.sleep_need(debt, strain.get(d - timedelta(days=1)), params=params)
        perf[d] = S.performance(actual[d], need["need"])
    return perf, strain


def replay(person: Person, series: Series, *, params=P, perf: dict[date, float] | None = None) -> list[dict]:
    """Recovery day by day, the recovery block of scores/engine.score_days on stored nightly inputs. Each row: date, recovery,
    weighted_z, z (by item), hrv_night_ln, rhr_night, plus what study.load_regression reads."""
    feats: dict[str, dict[date, float]] = {k: {} for k in ("hrv", "rhr", "sleep_performance", "resp_rate")}
    feats["rhr_daily"] = dict(person.daily_rhr)
    rows = []
    for d in sorted(person.days):
        day = person.days[d]
        value = perf.get(d) if perf is not None else day.perf
        if value is not None:
            feats["sleep_performance"][d] = value
        hrv_ln = series.nightly.get(d)
        rhr_key = "rhr_daily" if day.rhr_from_daily else "rhr"
        past = [d - timedelta(days=k) for k in range(1, params.BASELINE_DAYS + 1)]
        base = {k: [feats[k][x] for x in past if x in feats[k]] for k in feats}
        row = {"date": d, "recovery": None, "weighted_z": None, "z": None, "hrv_night_ln": hrv_ln, "rhr_night": day.rhr,
               "strain": day.strain, "sleep_actual_min": day.actual_min, "illness": False, "hrv_base": None, "flags": {}}
        hrv_base = R.baseline(base["hrv"], params=params)
        if hrv_ln is not None and hrv_base is not None:
            z = {}
            for k, v in (("hrv", hrv_ln), ("rhr", day.rhr), ("sleep_performance", value), ("resp_rate", day.resp)):
                b = hrv_base if k == "hrv" else R.baseline(base[rhr_key if k == "rhr" else k], params=params)
                if v is not None and b is not None:
                    z[k] = params.RECOVERY_SIGNS[k] * R.zscore(v, b[0], b[1], params.SD_FLOOR[k], params=params)
            row["recovery"], row["weighted_z"] = R.recovery_score(z, params=params)
            row["z"], row["hrv_base"] = z, hrv_base
            rhr_base = R.baseline(base[rhr_key], params=params)
            row["illness"] = R.illness_warning(day.rhr, rhr_base[0] if rhr_base else None, hrv_ln, hrv_base[0], params=params)
            if row["illness"]:
                row["flags"] = {"illness_warning": True}
        rows.append(row)
        for k, v in (("hrv", hrv_ln), (rhr_key, day.rhr), ("resp_rate", day.resp)):
            if v is not None:
                feats[k][d] = v
    return rows


def rule_a(person: Person, stored: Series, rows: list[dict]) -> dict:
    """Rule A: the replay reproduces the stored recovery and nightly ln HRV."""
    by = {r["date"]: r for r in rows}
    diffs = [abs(by[d]["recovery"] - x.stored_recovery) for d, x in person.days.items()
             if x.stored_recovery is not None and by.get(d, {}).get("recovery") is not None]
    only_stored = sum(1 for d, x in person.days.items() if x.stored_recovery is not None and by[d]["recovery"] is None)
    only_replay = sum(1 for d, x in person.days.items() if x.stored_recovery is None and by[d]["recovery"] is not None)
    ln = [abs(stored.nightly[d] - x.stored_hrv_ln) for d, x in person.days.items()
          if x.stored_hrv_ln is not None and d in stored.nightly]
    within1 = sum(x <= 1 for x in diffs) / len(diffs) if diffs else 0.0
    ln_ok = sum(x <= C.NIGHT_LN_TOL for x in ln) / len(ln) if ln else 0.0
    return {"days": len(diffs), "exact": sum(x == 0 for x in diffs) / len(diffs) if diffs else 0.0, "within_1": within1,
            "max_diff": max(diffs, default=None), "only_stored": only_stored, "only_replay": only_replay,
            "nights_ln": len(ln), "night_ln_ok": ln_ok,
            "ok": bool(diffs) and within1 >= C.REPLAY_WITHIN_1 and max(diffs) <= C.REPLAY_MAX_POINTS and ln_ok >= C.NIGHT_LN_SHARE}


# ---------------------------------------------------------------- additive statistics with prefix sums (fast block bootstrap)
def _r_from(s) -> float | None:
    n, sx, sy, sxx, syy, sxy = s
    if n < 5:
        return None
    den = (n * sxx - sx * sx) * (n * syy - sy * sy)
    return (n * sxy - sx * sy) / math.sqrt(den) if den > 0 else None


def _icc_from(s) -> float | None:
    """ICC(1) from one-way ANOVA sums; unequal group sizes use n0 = (N − Σn²/N) / (k − 1)."""
    k, n, sx, sxx, between, n2 = s
    if k < 5 or n <= k:
        return None
    ssb = between - sx * sx / n
    ssw = sxx - between
    msb, msw = ssb / (k - 1), ssw / (n - k)
    n0 = (n - n2 / n) / (k - 1)
    den = msb + (n0 - 1) * msw
    return (msb - msw) / den if den > 0 else None


class _Stat:
    """A statistic that is a function of sums over calendar days: the sum over any list of blocks costs O(blocks)."""

    def __init__(self, per_day: list[tuple | None], size: int, fn, sign: float = 1.0):
        self.fn, self.sign, self.size = fn, sign, size
        self.prefix = [[0.0] * (len(per_day) + 1) for _ in range(size)]
        for i, v in enumerate(per_day):
            for j in range(size):
                self.prefix[j][i + 1] = self.prefix[j][i] + (v[j] if v is not None else 0.0)

    def blocks(self, starts: list[int], length: int) -> float | None:
        s = [sum(p[a + length] - p[a] for a in starts) for p in self.prefix]
        v = self.fn(s)
        return None if v is None else self.sign * v

    def span(self, a: int, b: int) -> float | None:
        v = self.fn([p[b] - p[a] for p in self.prefix])
        return None if v is None else self.sign * v


def _pair(x: float, y: float) -> tuple:
    return (1.0, x, y, x * x, y * y, x * y)


def _group(xs: list[float]) -> tuple:
    n, s = len(xs), sum(xs)
    return (1.0, float(n), s, sum(v * v for v in xs), s * s / n, float(n * n))


def _calendar(days) -> list[date]:
    days = sorted(days)
    return [days[0] + timedelta(days=i) for i in range((days[-1] - days[0]).days + 1)] if days else []


def primary_stats(series: Series, days: set[date], rhr: dict[date, float], cal: list[date]) -> dict[str, _Stat]:
    """P1 lag-1, P2 −r with same-night resting HR, P3 ICC(1) of readings; only over `days`. Oriented so higher is better."""
    x = series.nightly
    one = timedelta(days=1)
    p1 = [_pair(x[d], x[d + one]) if d in days and d + one in days else None for d in cal]
    p2 = [_pair(x[d], rhr[d]) if d in days and d in rhr else None for d in cal]
    p3 = [_group(series.readings[d]) if d in days and len(series.readings.get(d, [])) >= 2 else None for d in cal]
    return {"P1": _Stat(p1, 6, _r_from), "P2": _Stat(p2, 6, _r_from, -1.0), "P3": _Stat(p3, 6, _icc_from)}


def bootstrap_diffs(cur: dict[str, _Stat], cand: dict[str, _Stat], n_days: int, *, n: int = C.BOOTSTRAP, seed: int = C.SEED,
                    block: int = C.BLOCK_DAYS) -> dict[str, tuple[float, float] | None]:
    """Paired moving-block bootstrap: percentile 95% interval of candidate − current for each statistic."""
    rng = random.Random(seed)
    block = min(block, n_days)
    count = math.ceil(n_days / block)
    out: dict[str, list[float]] = {k: [] for k in cur}
    for _ in range(n):
        starts = [rng.randrange(0, n_days - block + 1) for _ in range(count)]
        for k in cur:
            a, b = cur[k].blocks(starts, block), cand[k].blocks(starts, block)
            if a is not None and b is not None:
                out[k].append(b - a)
    return {k: _interval(v, n) for k, v in out.items()}


def _interval(xs: list[float], n: int) -> tuple[float, float] | None:
    if len(xs) < 0.9 * n:   # too many resamples without a value: no interval
        return None
    xs.sort()
    lo = (1 - C.CI) / 2
    return xs[int(lo * (len(xs) - 1))], xs[int((1 - lo) * (len(xs) - 1))]


# ---------------------------------------------------------------- history impact
def impact(a: list[dict], b: list[dict]) -> dict:
    """What moving from a to b does to the recovery history."""
    out = study.agreement(a, b)
    va, vb = study._by_date(a, "recovery"), study._by_date(b, "recovery")
    both = sorted(set(va) & set(vb))
    out.update(moved_5=sum(abs(va[d] - vb[d]) >= 5 for d in both), moved_10=sum(abs(va[d] - vb[d]) >= 10 for d in both))
    out["zone_change"] = round(1 - out["zone_agree"], 3) if "zone_agree" in out else None
    months: dict[str, list[tuple[int, int]]] = {}
    for d in both:
        months.setdefault(d.strftime("%Y-%m"), []).append((va[d], vb[d]))
    out["monthly"] = [{"month": m, "days": len(v), "a": round(statistics.fmean(x for x, _ in v), 1),
                       "b": round(statistics.fmean(y for _, y in v), 1)} for m, v in sorted(months.items())]
    return out


def zone_shares(rows: list[dict]) -> dict:
    rec = [r["recovery"] for r in rows if r["recovery"] is not None]
    n = len(rec)
    return {"days": n, "green": sum(r >= GREEN for r in rec) / n if n else None,
            "yellow": sum(RED <= r < GREEN for r in rec) / n if n else None,
            "red": sum(r < RED for r in rec) / n if n else None}


def secondary(person: Person, series: Series, rows: list[dict]) -> dict:
    """Reported, gate nothing unless a rule names them."""
    x = series.nightly
    rec = study._by_date(rows, "recovery")
    apple = [(x[d], person.daily_rhr[d]) for d in sorted(x) if d in person.daily_rhr]
    hrr = [(rec[d], person.hrr[d]) for d in sorted(rec) if d in person.hrr]
    reg = study.load_regression(rows, weekday=True, outcomes=("hrv_night_ln", "rhr_night"))
    zone_by = {d: study.zone(v) for d, v in rec.items()}
    pairs = [d for d in zone_by if d + timedelta(days=1) in zone_by]
    counts = [len(v) for v in series.readings.values()]
    within = [statistics.pstdev(v) for v in series.readings.values() if len(v) >= 2]
    return {"nights": len(x), "readings": series.n_readings,
            "readings_per_night": round(statistics.fmean(counts), 2) if counts else None,
            "single_reading_nights": round(sum(c == 1 for c in counts) / len(counts), 3) if counts else None,
            "median_ms": round(math.exp(statistics.median(x.values())), 1) if x else None,
            "within_between": round(statistics.median(within) / statistics.pstdev(list(x.values())), 2) if len(within) >= 5 else None,
            "r_apple_rhr": study.corr([a for a, _ in apple], [b for _, b in apple]),
            "r_recovery_hrr": study.corr([a for a, _ in hrr], [b for _, b in hrr]) if len(hrr) >= C.MIN_HRR_PAIRS else None,
            "hrr_pairs": len(hrr), "recovery_days": len(rec), "recovery_lag1": study.lag1(rows, "recovery")[0],
            "recovery_mean": round(statistics.fmean(rec.values()), 1) if rec else None,
            "recovery_sd": round(statistics.stdev(rec.values()), 1) if len(rec) > 1 else None,
            "zone_flip_day_to_day": round(sum(zone_by[d] != zone_by[d + timedelta(days=1)] for d in pairs) / len(pairs), 3) if pairs else None,
            "illness_days": sum(r["illness"] for r in rows), "zones": zone_shares(rows),
            "strain_on_ln_hrv": reg.get("hrv_night_ln", {}).get("strain"), "strain_on_rhr": reg.get("rhr_night", {}).get("strain")}


# ---------------------------------------------------------------- D2 / D3: comparing two HRV series
def _sub(a, b):
    return None if a is None or b is None else a - b


def _point(st: dict[str, _Stat], a: int, b: int) -> dict[str, float | None]:
    return {k: v.span(a, b) for k, v in st.items()}


def compare(person: Person, cur: Series, cand: Series, *, n_boot: int = C.BOOTSTRAP, seed: int = C.SEED, era=None,
            extra: dict[str, dict] | None = None, base_rows: list[dict] | None = None, cand_rows: list[dict] | None = None) -> dict:
    """The rules of section 5.1 for candidate vs current. `era`: nights of the newest source. `extra`: further named gates
    ({name: {"ok": bool, ...}}). Returns metrics, intervals, each gate and the verdict."""
    rows_a = base_rows if base_rows is not None else replay(person, cur)
    rows_b = cand_rows if cand_rows is not None else replay(person, cand)
    paired = sorted(set(cur.nightly) & set(cand.nightly))
    out: dict = {"label": cand.label, "reference": cur.label, "paired_nights": len(paired),
                 "secondary_current": secondary(person, cur, rows_a), "secondary_candidate": secondary(person, cand, rows_b),
                 "impact": impact(rows_a, rows_b)}
    gates: dict[str, dict] = {}
    n_dev = int(len(paired) * C.DEV_SHARE)
    n_hold = len(paired) - n_dev
    gates["B_enough_data"] = {"ok": len(paired) >= C.MIN_PAIRED_NIGHTS and n_hold >= C.MIN_HOLDOUT_NIGHTS,
                              "paired": len(paired), "holdout": n_hold}
    if not gates["B_enough_data"]["ok"]:
        return {**out, "gates": gates, "verdict": "insufficient data"}
    cal = _calendar(paired)
    index = {d: i for i, d in enumerate(cal)}
    rhr = {d: person.days[d].rhr for d in paired if person.days[d].rhr is not None and not person.days[d].rhr_from_daily}
    days = set(paired)
    st_a, st_b = primary_stats(cur, days, rhr, cal), primary_stats(cand, days, rhr, cal)
    split = index[paired[n_dev]]
    windows = {"all": (0, len(cal)), "development": (0, split), "holdout": (split, len(cal))}
    era_days = sorted(set(era or ()) & days)
    if len(era_days) >= C.MIN_ERA_NIGHTS:
        windows["newest_era"] = (index[era_days[0]], len(cal))
    out["era_nights"] = len(era_days)
    metrics = {w: {"current": _point(st_a, a, b), "candidate": _point(st_b, a, b)} for w, (a, b) in windows.items()}
    out["metrics"] = metrics
    ci = bootstrap_diffs(st_a, st_b, len(cal), n=n_boot, seed=seed)
    out["ci"] = ci
    diff = {k: _sub(metrics["all"]["candidate"][k], metrics["all"]["current"][k]) for k in st_a}
    out["diff"] = diff
    a_days, b_days = out["secondary_current"]["recovery_days"], out["secondary_candidate"]["recovery_days"]
    gates["C_coverage"] = {"ok": a_days > 0 and b_days / a_days >= C.COVERAGE_MIN, "ratio": round(b_days / a_days, 3) if a_days else None}
    harm = {k: diff[k] is not None and diff[k] >= -C.NO_HARM_MARGIN and (ci[k] is None or ci[k][1] >= 0) for k in st_a}
    gates["D_no_harm"] = {"ok": all(harm.values()), "by_metric": harm}
    improved = []
    for k in st_a:
        if ci[k] is None or ci[k][0] <= 0:
            continue
        signs = [_sub(metrics[w]["candidate"][k], metrics[w]["current"][k]) for w in windows if w != "all"]
        if all(v is not None and v > 0 for v in signs):
            improved.append(k)
    gates["E_improvement"] = {"ok": bool(improved), "metrics": improved}
    imp = out["impact"]
    mae, zone = imp.get("mean_abs_diff") or 0.0, imp.get("zone_change") or 0.0
    gates["F_practical"] = {"ok": mae >= C.PRACTICAL_MAE or zone >= C.PRACTICAL_ZONE, "mae": mae, "zone_change": zone}
    gates["G_churn"] = {"ok": zone <= C.CHURN_ZONE or len(improved) >= 2, "zone_change": zone, "improved": len(improved)}
    gates.update(extra or {})
    out["gates"] = gates
    order = ["C_coverage", "D_no_harm", "E_improvement", *(extra or {}), "G_churn"]
    verdict = "eligible"
    if any(not gates[g]["ok"] for g in order):
        verdict = "keep"
    elif not gates["F_practical"]["ok"]:
        verdict = "keep (no practical effect)"
    out["verdict"] = verdict
    return out


# ---------------------------------------------------------------- D3 extras
def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def filter_extras(cur: Series, cand: Series, n_total: int) -> dict[str, dict]:
    """Reading loss and across-the-board shift of a tighter artifact filter; `n_total` = all readings with beats."""
    lost = (len(cur.per_reading) - len(cand.per_reading)) / n_total if n_total else 0.0
    both = set(cur.per_reading) & set(cand.per_reading)
    diffs = [cand.per_reading[t] - cur.per_reading[t] for t in both]
    med = statistics.median(diffs) if diffs else 0.0
    return {"reading_loss": {"ok": lost <= C.READING_LOSS_MAX, "lost": round(lost, 4), "usable_cur": len(cur.per_reading),
                             "usable_cand": len(cand.per_reading), "changed": sum(abs(x) > 1e-9 for x in diffs)},
            "no_overfilter": {"ok": med >= -C.OVERFILTER_MAX, "median_ln_shift": round(med, 4),
                              "p5": round(_pct(diffs, 0.05), 4) if diffs else None,
                              "p95": round(_pct(diffs, 0.95), 4) if diffs else None}}


def synthetic_filter_check(max_deviation: float, n: int = 150, seed: int = 11) -> dict:
    """Rule H, the pass criteria of scoring-validation 2.5 on synthetic beat series, for one threshold."""
    filters = (("candidate", {"MAX_DEVIATION": max_deviation}), ("range", {"MAX_DEVIATION": 10.0}))
    rows = B.run(n=n, seed=seed, filters=filters, levels=B.LEVELS[:3], rates=(0.0, 0.02))
    clean = [r for r in rows if r["filter"] == "candidate" and r["rate"] == 0.0]
    cand = next(r for r in rows if r["filter"] == "candidate" and r["rate"] == 0.02 and r["level"] == "normal")
    only_range = next(r for r in rows if r["filter"] == "range" and r["rate"] == 0.02 and r["level"] == "normal")
    ok = all(r["rejected"] <= 0.01 for r in clean) and cand["rmssd_err_median"] <= 0.05 \
        and cand["rmssd_err_median"] < only_range["rmssd_err_median"]
    return {"ok": ok, "max_false_rejection": max(r["rejected"] for r in clean), "median_error_2pct": cand["rmssd_err_median"],
            "range_only_median_error_2pct": only_range["rmssd_err_median"]}


# ---------------------------------------------------------------- D4: logistic
def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def tercile_mapping(zs: list[float]) -> tuple[float, float] | None:
    """Slope and offset putting the terciles of weighted z on the 34 and 67 boundaries. Equal thirds is a design choice."""
    if len(zs) < 3:
        return None
    q1, q2 = statistics.quantiles(zs, n=3)
    if q2 <= q1:
        return None
    slope = (_logit(GREEN / 100) - _logit(RED / 100)) / (q2 - q1)
    return slope, _logit(GREEN / 100) - slope * q2


def _map_score(z: float, slope: float, offset: float) -> int:
    return int(round(100 / (1 + math.exp(-(slope * z + offset)))))


def rolling_scores(rows: list[dict]) -> list[dict]:
    """The rolling per-person mapping: each day's slope and offset from the trailing nights before it (no look-ahead)."""
    hist: list[float] = []
    out = []
    for r in rows:
        row = dict(r)
        z = r["weighted_z"]
        if z is not None:
            past = hist[-C.ROLLING_NIGHTS:]
            m = tercile_mapping(past) if len(past) >= C.ROLLING_MIN else None
            if m is not None:
                slope = min(C.ROLLING_SLOPE[1], max(C.ROLLING_SLOPE[0], m[0]))
                q2 = statistics.quantiles(past, n=3)[1]
                row["map"] = (slope, _logit(GREEN / 100) - slope * q2)
                row["recovery"] = _map_score(z, *row["map"])
            hist.append(z)
        out.append(row)
    return out


def _weighted(z: dict[str, float], params=P) -> float:
    total = sum(params.RECOVERY_WEIGHTS[k] for k in z)
    return sum(params.RECOVERY_WEIGHTS[k] * v for k, v in z.items()) / total


def flip_rate(series: Series, rows: list[dict], *, slope: float | None = None, offset: float | None = None, params=P) -> dict:
    """Among nights with ≥ 2 readings and a recovery: share where leaving out one reading can change the colour.
    A row's own mapping (`map`, the rolling one) wins over slope and offset."""
    slope = params.RECOVERY_SLOPE if slope is None else slope
    offset = params.RECOVERY_OFFSET if offset is None else offset
    n = flips = 0
    for r in rows:
        xs = series.readings.get(r["date"], [])
        if r["recovery"] is None or len(xs) < 2 or r["hrv_base"] is None:
            continue
        a, b = r.get("map") or (slope, offset)
        base = _map_score(_weighted(r["z"], params), a, b)
        n += 1
        for i in range(len(xs)):
            z1 = dict(r["z"])
            z1["hrv"] = params.RECOVERY_SIGNS["hrv"] * R.zscore(statistics.median(xs[:i] + xs[i + 1:]), r["hrv_base"][0],
                                                               r["hrv_base"][1], params.SD_FLOOR["hrv"], params=params)
            if study.zone(_map_score(_weighted(z1, params), a, b)) != study.zone(base):
                flips += 1
                break
    return {"nights": n, "flip": round(flips / n, 3) if n else None}


def _with_mapping(rows: list[dict], slope: float, offset: float) -> list[dict]:
    return [{**r, "recovery": None if r["weighted_z"] is None else _map_score(r["weighted_z"], slope, offset)} for r in rows]


def scale_check(rows: list[dict]) -> dict:
    shares = zone_shares(rows)
    zs = [r["weighted_z"] for r in rows if r["weighted_z"] is not None]
    lo, hi = C.ZONE_SHARE_BAND
    mean_z = statistics.fmean(zs) if zs else None
    in_band = shares["days"] > 0 and all(lo <= shares[k] <= hi for k in ("green", "yellow", "red"))
    return {**shares, "mean_z": None if mean_z is None else round(mean_z, 3), "sd_z": round(statistics.stdev(zs), 3) if len(zs) > 1 else None,
            "ok": bool(in_band and mean_z is not None and abs(mean_z) <= C.ZBAR_DRIFT_MAX)}


def _split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    have = [r for r in rows if r["weighted_z"] is not None]
    if not have:
        return [], []
    cut = have[int(len(have) * C.DEV_SHARE)]["date"]
    return [r for r in rows if r["date"] < cut], [r for r in rows if r["date"] >= cut]


def _monthly_green_sd(rows: list[dict]) -> float | None:
    months: dict[str, list[int]] = {}
    for r in rows:
        if r["recovery"] is not None:
            months.setdefault(r["date"].strftime("%Y-%m"), []).append(r["recovery"])
    shares = [sum(x >= GREEN for x in v) / len(v) for v in months.values() if len(v) >= 10]
    return round(statistics.stdev(shares), 3) if len(shares) >= 3 else None


def decide_logistic(series: Series, rows: list[dict]) -> dict:
    """Section 5.2. The fixed mapping stays unless it fails the scale check in both windows."""
    dev, hold = _split_rows(rows)
    out = {"development": scale_check(dev), "holdout": scale_check(hold), "all": scale_check(rows),
           "flip": {f"slope x{f}": flip_rate(series, rows, slope=P.RECOVERY_SLOPE * f) for f in C.SLOPE_FACTORS},
           "fixed_lag1": study.lag1(rows, "recovery")[0], "fixed_monthly_green_sd": _monthly_green_sd(rows)}
    fit = tercile_mapping([r["weighted_z"] for r in dev if r["weighted_z"] is not None])
    alt = {}
    if fit:
        h = _with_mapping(hold, *fit)
        alt["single_fit"] = {"slope": round(fit[0], 3), "offset": round(fit[1], 3), "holdout": scale_check(h),
                             "impact": impact(hold, h), "flip": flip_rate(series, hold, slope=fit[0], offset=fit[1])}
    roll = rolling_scores(rows)
    roll_hold = _split_rows(roll)[1]
    alt["rolling"] = {"holdout": scale_check(roll_hold), "impact": impact(rows, roll), "flip": flip_rate(series, roll_hold),
                      "lag1": study.lag1(roll, "recovery")[0], "monthly_green_sd": _monthly_green_sd(roll)}
    out["alternatives"] = alt
    verdict = "keep"
    if not (out["development"]["ok"] or out["holdout"]["ok"]):
        limit = (flip_rate(series, hold)["flip"] or 0.0) + C.FLIP_MARGIN
        if fit and alt["single_fit"]["holdout"]["ok"] and (alt["single_fit"]["flip"]["flip"] or 0.0) <= limit:
            verdict = "eligible (single fit)"
        elif alt["rolling"]["holdout"]["ok"] and (alt["rolling"]["flip"]["flip"] or 0.0) <= limit:
            verdict = "eligible (rolling)"
    out["verdict"] = verdict
    return out


# ---------------------------------------------------------------- D5: weights
ITEMS = ("hrv", "rhr", "sleep_performance", "resp_rate")


def pc1_weights(rows: list[dict]) -> dict[str, float] | None:
    """Non-negative loadings of the first principal component of the four z-scores, summing to 1 (power iteration)."""
    data = [[r["z"][k] for k in ITEMS] for r in rows if r["z"] and all(k in r["z"] for k in ITEMS)]
    if len(data) < 30:
        return None
    means = [statistics.fmean(c) for c in zip(*data)]
    m = len(ITEMS)
    cov = [[sum((row[i] - means[i]) * (row[j] - means[j]) for row in data) / (len(data) - 1) for j in range(m)] for i in range(m)]
    v = [1.0] * m
    for _ in range(200):
        w = [sum(cov[i][j] * v[j] for j in range(m)) for i in range(m)]
        norm = math.sqrt(sum(x * x for x in w))
        if norm == 0:
            return None
        v = [x / norm for x in w]
    sign = 1.0 if sum(v) >= 0 else -1.0
    v = [max(0.0, sign * x) for x in v]
    total = sum(v)
    return {k: round(x / total, 3) for k, x in zip(ITEMS, v)} if total else None


def _reweight(rows: list[dict], weights: dict[str, float]) -> list[dict]:
    params = P.override(RECOVERY_WEIGHTS=weights)
    out = []
    for r in rows:
        if r["z"]:
            score, z = R.recovery_score(r["z"], params=params)
            out.append({**r, "recovery": score, "weighted_z": z})
        else:
            out.append(r)
    return out


def _criteria_stats(person: Person, rows: list[dict], cal: list[date]) -> dict[str, _Stat]:
    rec = study._by_date(rows, "recovery")
    one = timedelta(days=1)
    lag = [_pair(rec[d], rec[d + one]) if d in rec and d + one in rec else None for d in cal]
    apple = [_pair(rec[d], person.daily_rhr[d]) if d in rec and d in person.daily_rhr else None for d in cal]
    hrr = [_pair(rec[d], person.hrr[d]) if d in rec and d in person.hrr else None for d in cal]
    return {"recovery_lag1": _Stat(lag, 6, _r_from), "minus_r_apple_rhr": _Stat(apple, 6, _r_from, -1.0), "r_hrr": _Stat(hrr, 6, _r_from)}


def decide_weights(person: Person, rows: list[dict], *, n_boot: int = C.BOOTSTRAP, seed: int = C.SEED) -> dict:
    """Section 5.3. Fitted on development, judged on the hold-out only."""
    dev, hold = _split_rows(rows)
    out: dict = {"current": dict(P.RECOVERY_WEIGHTS), "candidates": {}}
    days = [r["date"] for r in hold]
    if not days:
        return {**out, "verdict": "insufficient data"}
    cands = {"equal": {k: 0.25 for k in ITEMS}}
    pc = pc1_weights(dev)
    if pc:
        cands["pc1_development"] = pc
    cal = _calendar(days)
    base = _criteria_stats(person, hold, cal)
    hrr_pairs = sum(1 for r in hold if r["recovery"] is not None and r["date"] in person.hrr)
    out["holdout_hrr_pairs"] = hrr_pairs
    verdict = "keep"
    for name, w in cands.items():
        stats = _criteria_stats(person, _reweight(hold, w), cal)
        keys = [k for k in base if k != "r_hrr" or hrr_pairs >= C.MIN_HRR_PAIRS]
        ci = bootstrap_diffs({k: base[k] for k in keys}, {k: stats[k] for k in keys}, len(cal), n=n_boot, seed=seed)
        point = {k: _sub(stats[k].span(0, len(cal)), base[k].span(0, len(cal))) for k in keys}
        wins = [k for k in keys if ci[k] is not None and ci[k][0] > 0]
        harm = [k for k in keys if ci[k] is not None and ci[k][1] < 0]
        ok = len(wins) >= C.MIN_WEIGHT_WINS and not harm
        out["candidates"][name] = {"weights": w, "point": point, "ci": ci, "wins": wins, "harm": harm, "eligible": ok,
                                   "impact": impact(rows, _reweight(rows, w))}
        if ok and name != "equal":   # equal weights are a bracket, not a candidate for adoption
            verdict = "eligible"
    out["verdict"] = verdict
    return out


# ---------------------------------------------------------------- D6, D7, D8
def _k_for_median(trimp: float) -> float:
    return trimp / -math.log(1 - C.OLD_K_TARGET / P.STRAIN_MAX)


def decide_k(person: Person, series: Series, base_rows: list[dict]) -> dict:
    """Section 5.4. K is a scale constant: checked against the scale, never fitted."""
    days = sorted(person.train_days)
    all_trimp = [person.train_days[d][0] for d in days]
    tr = [person.train_days[d][0] for d in days if person.train_days[d][0] >= C.TRAINING_TRIMP]
    strains = [L.strain(t) for t in tr]
    cut = int(len(tr) * C.DEV_SHARE)
    lo, hi = C.TRAINING_STRAIN_BAND
    median_strain = statistics.median(strains) if strains else None
    sat = sum(L.strain(t) >= C.SATURATION_STRAIN for t in all_trimp) / len(all_trimp) if all_trimp else None
    ref_k = C.REF_TRIMP / -math.log(1 - C.REF_STRAIN / P.STRAIN_MAX)
    out = {"days": len(days), "training_days": len(tr), "median_training_trimp": round(statistics.median(tr), 1) if tr else None,
           "median_training_strain": None if median_strain is None else round(median_strain, 2),
           "share_saturated": None if sat is None else round(sat, 4), "reference_session_strain_now": round(L.strain(C.REF_TRIMP), 2),
           "reference_k": round(ref_k, 1), "old_definition_k_all": round(_k_for_median(statistics.median(tr)), 1) if tr else None,
           "old_definition_k_development": round(_k_for_median(statistics.median(tr[:cut])), 1) if cut >= 10 else None,
           "old_definition_k_holdout": round(_k_for_median(statistics.median(tr[cut:])), 1) if len(tr) - cut >= 10 else None}
    out["checks_ok"] = bool(median_strain is not None and lo <= median_strain <= hi and sat is not None and sat <= C.SATURATION_MAX)
    what_if = {}
    for k in (ref_k, 0.8 * P.STRAIN_K, 1.2 * P.STRAIN_K):
        perf, strain_new = recomputed_perf(person, k=k)
        rows = replay(person, series, perf=perf)
        changes = [abs(strain_new[d] - person.train_days[d][1]) for d in days]
        what_if[f"{k:.1f}"] = {"strain_mae": round(statistics.fmean(changes), 2) if changes else None,
                               "recovery_impact": {x: y for x, y in impact(base_rows, rows).items() if x != "monthly"}}
    out["what_if"] = what_if
    out["verdict"] = "keep (relabel: design choice anchored on the reference session)" if out["checks_ok"] else "eligible (reference session)"
    return out


def report_sleep_need(person: Person) -> dict:
    """Section 5.5: how long this person slept on calm nights. Not a measure of need; report only."""
    prev = {d: person.train_days[d][1] for d in person.train_days}
    calm = [x.actual_min for d, x in person.days.items()
            if x.actual_min is not None and x.debt_min is not None and x.debt_min <= C.CALM_DEBT_MAX
            and prev.get(d - timedelta(days=1), 0.0) <= C.CALM_STRAIN_MAX]
    nights = [x.actual_min for x in person.days.values() if x.actual_min is not None]
    return {"calm_nights": len(calm), "median_calm_min": round(statistics.median(calm)) if calm else None, "all_nights": len(nights),
            "median_all_min": round(statistics.median(nights)) if nights else None, "current_baseline_min": P.SLEEP_NEED_BASELINE_MIN,
            "verdict": "report only"}


def app_era(person: Person) -> set[date]:
    """Nights holding a reading from the newest source (exact beat times)."""
    app = sorted(r.start for r in person.readings if r.origin == "app")
    return {d for d, x in person.days.items() if app[bisect_left(app, x.night_start):bisect_right(app, x.night_end)]}


def mixed_sources(person: Person, exact: Series, rows: list[dict]) -> dict:
    """Section 5.6: newest-source nights re-processed the way an older export gives them (10 ms beat times, no gap flags)."""
    def is_app(r: Reading) -> bool:
        return r.origin == "app"
    a = rmssd_series(person, "exact", select=is_app)
    b = rmssd_series(person, "10 ms", select=is_app, quantise_s=C.EXPORT_STEP_S, drop_gaps=True)
    days = sorted(set(a.nightly) & set(b.nightly))
    diffs = [b.nightly[d] - a.nightly[d] for d in days]
    per = [b.per_reading[t] - a.per_reading[t] for t in set(a.per_reading) & set(b.per_reading)]
    in_days = set(days)
    sds = [r["hrv_base"][1] for r in rows if r["hrv_base"] and r["date"] in in_days]
    sd = statistics.median(sds) if sds else None
    out = {"nights": len(days), "readings": len(per), "median_abs_diff": round(statistics.median(abs(x) for x in diffs), 4) if diffs else None,
           "mean_shift": round(statistics.fmean(diffs), 4) if diffs else None, "baseline_sd": None if sd is None else round(sd, 4),
           "reading_p95_abs": round(_pct([abs(x) for x in per], 0.95), 4) if per else None}
    out["shift_in_sd"] = round(out["mean_shift"] / sd, 3) if diffs and sd else None
    out["ok"] = bool(diffs) and out["median_abs_diff"] <= C.MIXED_MEDIAN_MAX \
        and (out["shift_in_sd"] is None or abs(out["shift_in_sd"]) <= C.MIXED_SHIFT_SD_MAX)
    era = app_era(person)
    if era:   # descriptive only, confounded with real change
        first = min(era)
        before = [v for d, v in exact.nightly.items() if first - timedelta(days=60) <= d < first]
        after = [v for d, v in exact.nightly.items() if d in era]
        if len(before) >= 10 and len(after) >= 10:
            out["level_before_after"] = (round(statistics.fmean(before), 3), round(statistics.fmean(after), 3))
    return out


# ---------------------------------------------------------------- everything
def run(person: Person, *, n_boot: int = C.BOOTSTRAP, seed: int = C.SEED, thresholds=(0.15, 0.20, 0.30),
        synthetic_check_n: int = 150) -> dict:
    res: dict = {"stored_kind": person.stored_kind, "days": len(person.days), "readings": len(person.readings),
                 "first": min(person.days).isoformat() if person.days else None,
                 "last": max(person.days).isoformat() if person.days else None}
    rmssd = rmssd_series(person, "RMSSD 25%")
    stored = stored_rmssd_series(person)
    sdnn = sdnn_series(person)
    fidelity_series = stored if person.stored_kind == "rmssd" else sdnn
    res["rule_a"] = rule_a(person, fidelity_series, replay(person, fidelity_series))
    rm_rows = replay(person, rmssd)
    res["recomputed_vs_stored_rmssd"] = {k: v for k, v in impact(replay(person, stored), rm_rows).items() if k != "monthly"}
    era = app_era(person)
    res["era_nights"] = len(era)
    res["d2_rmssd_vs_sdnn"] = compare(person, sdnn, rmssd, n_boot=n_boot, seed=seed, era=era, cand_rows=rm_rows)
    same = [r for r in person.readings if r.apple_sdnn and r.rmssd]   # each reading's stored Apple SDNN against its RMSSD
    same_sdnn = _nightly(person, [(r.start, r.apple_sdnn) for r in same], "SDNN (same readings)")
    same_rmssd = _nightly(person, [(r.start, r.rmssd) for r in same], "RMSSD (same readings)")
    res["d2_same_readings"] = compare(person, same_sdnn, same_rmssd, n_boot=n_boot, seed=seed, era=era)
    n_beats = sum(1 for r in person.readings if r.beats_ms and len(r.beats_ms) >= 2)
    res["d3"] = {}
    for th in thresholds:
        key = f"{th:.0%}"
        cand = rmssd_series(person, f"RMSSD {key}", max_deviation=th)
        extra = filter_extras(rmssd, cand, n_beats)
        named = abs(th - 0.20) < 1e-9
        if named:
            extra["H_synthetic"] = synthetic_filter_check(th, n=synthetic_check_n)
        res["d3"][key] = compare(person, rmssd, cand, n_boot=n_boot, seed=seed, era=era, extra=extra, base_rows=rm_rows)
        if not named:   # section 2: shown for the trend, cannot become eligible
            res["d3"][key]["verdict"] = f"trend only ({res['d3'][key]['verdict']})"
    local = rmssd_series(person, "local median 20% (exploratory)", rmssd_fn=local_median_rmssd)
    res["d3"]["local"] = compare(person, rmssd, local, n_boot=n_boot, seed=seed, era=era,
                                 extra=filter_extras(rmssd, local, n_beats), base_rows=rm_rows)
    res["d3"]["local"]["verdict"] = f"exploratory ({res['d3']['local']['verdict']})"
    chosen, chosen_rows = (rmssd, rm_rows) if person.stored_kind == "rmssd" else (sdnn, replay(person, sdnn))
    res["d4_logistic"] = decide_logistic(chosen, chosen_rows)
    res["d5_weights"] = decide_weights(person, chosen_rows, n_boot=n_boot, seed=seed)
    res["d6_k"] = decide_k(person, chosen, chosen_rows)
    res["d7_sleep_need"] = report_sleep_need(person)
    res["d8_mixed_sources"] = mixed_sources(person, rmssd, rm_rows)
    if not res["rule_a"]["ok"]:   # rule A: the what-if numbers cannot be trusted, so nothing may be decided from them
        no = "no decision (rule A failed)"
        for c in (res["d2_rmssd_vs_sdnn"], res["d2_same_readings"], *res["d3"].values(), res["d4_logistic"], res["d5_weights"], res["d6_k"]):
            c["verdict"] = no
    return res


def _fmt(x, nd=3) -> str:
    if x is None:
        return "—"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def _ci(x) -> str:
    return "—" if x is None else f"[{x[0]:+.3f}, {x[1]:+.3f}]"


def _compare_md(c: dict) -> list[str]:
    out = [f"### {c['label']} against {c['reference']}: **{c['verdict']}**", ""]
    if "ci" not in c:
        return out + [f"Paired nights {c['paired_nights']}.", ""]
    out += [f"Paired nights {c['paired_nights']}, newest source {c.get('era_nights')}.", "",
            "| metric | current | candidate | difference | 95% interval | development | hold-out | newest source |",
            "|---|---|---|---|---|---|---|---|"]
    m = c["metrics"]
    for k in ("P1", "P2", "P3"):
        d = [_sub(m[w]["candidate"][k], m[w]["current"][k]) if w in m else None for w in ("development", "holdout", "newest_era")]
        out.append(f"| {k} | {_fmt(m['all']['current'][k])} | {_fmt(m['all']['candidate'][k])} | {_fmt(c['diff'][k])} | {_ci(c['ci'][k])} | "
                   f"{_fmt(d[0])} | {_fmt(d[1])} | {_fmt(d[2])} |")
    out += ["", "Gates: " + ", ".join(f"{g} {'ok' if v['ok'] else 'FAIL'}" for g, v in c["gates"].items()), ""]
    imp = c["impact"]
    out += [f"History: {imp.get('both')} days, MAE {imp.get('mean_abs_diff')}, mean difference {imp.get('mean_diff')}, limits {imp.get('loa')}, "
            f"colour agreement {imp.get('zone_agree')}, ≥ 5 points {imp.get('moved_5')}, ≥ 10 points {imp.get('moved_10')}, "
            f"lost {imp.get('a_only')}, gained {imp.get('b_only')}.", ""]
    return out


def markdown(res: dict) -> str:
    a = res["rule_a"]
    out = ["# Calibration report (issue #118; holds personal values on real data, keep out of git)", "",
           f"{res['days']} nights, {res['readings']} beat series, scored with {res['stored_kind']}.", "",
           f"**Rule A**: {'ok' if a['ok'] else 'FAIL'}; {a['days']} days, exact {a['exact']:.1%}, within ±1 {a['within_1']:.1%}, largest {a['max_diff']}; "
           f"nightly ln HRV ok on {a['night_ln_ok']:.1%} of {a['nights_ln']}.", "", "## D2 RMSSD vs SDNN", ""]
    out += _compare_md(res["d2_rmssd_vs_sdnn"]) + _compare_md(res["d2_same_readings"]) + ["## D3 artifact threshold", ""]
    for v in res["d3"].values():
        out += _compare_md(v)

    def block(title: str, value: dict) -> list[str]:
        return [f"## {title}", "", "```", json.dumps(value, indent=1, default=str), "```", ""]
    d4, d5, d6, d7, d8 = (res[k] for k in ("d4_logistic", "d5_weights", "d6_k", "d7_sleep_need", "d8_mixed_sources"))
    out += block(f"D4 logistic: {d4['verdict']}", {k: v for k, v in d4.items() if k != "alternatives"})
    out += block(f"D5 weights: {d5['verdict']}", d5) + block(f"D6 K: {d6['verdict']}", d6)
    out += block(f"D7 sleep need: {d7['verdict']}", d7) + block(f"D8 mixed sources: {'ok' if d8['ok'] else 'FAIL'}", d8)
    return "\n".join(out)
