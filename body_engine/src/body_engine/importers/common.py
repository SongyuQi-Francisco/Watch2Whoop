"""Logic shared by imports: metric naming, sleep segmentation, choosing each day's value among sources."""
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

import psycopg

# Source priority for sleep and for the non-cumulative daily metrics (heart rate, HRV, weight ...): higher wins, lower never overwrites higher.
# Also the tie-break between cumulative candidates with the same value. Cumulative metrics are chosen by resolve_daily (below), not by this order.
# "app" = the types the Watch2Whoop app reads from HealthKit itself (rawdata/hk_types.py, rawdata/quantities.py): straight from
# HealthKit with the export import's daily definitions, while Open Wearables has these types only from an old export.xml copy.
SOURCE_PRIORITY = {"shortcut": 1, "apple_export": 2, "ow": 3, "app": 4}

# ---- choosing cumulative daily values (docs/shortcut-sync-spec.md §5) ----
# Every source de-duplicates devices itself (OW: HealthKit-style overlap; export.xml / Shortcut raw samples: largest single device),
# so on one day the complete sources agree to within a few %, and one that is clearly lower missed part of the day. Hence: among the
# complete, de-duplicated sources take the largest; "degraded" candidates (below) are used only when there is no clean one,
# again the largest (an incomplete total is a lower bound and an hour-summed total an upper bound of the real value).
# Degraded:
# - flagged by the importer: hourly_sum = the Shortcut grouped samples by hour, which adds iPhone + Watch together (steps ~2x);
# - incomplete: that source's energy for the day was only partly synced (2026-10-14/15 the OW iOS SDK 0.14 gave basal 532 / 185 kcal,
#   active 72 / 133), detected by INCOMPLETE_BASAL_SHARE (basal energy is recorded around the clock, so a day far below the person's
#   usual one is partial) or by the training calendar's daily_incomplete rule (whole-day active energy below that day's workouts).
#   Either marks both energy metrics of that source-day (ENERGY_METRICS, written by the same watch computation). Steps and the other
#   cumulative types have no such reference: a de-duplicated source is taken as complete (the same OW days had plausible steps:
#   8,260 / 6,818, about half of the hour-summed iPhone + Watch 14,416 / 14,039, as on the days both devices are in export.xml).
DEGRADED_FLAGS = {"hourly_sum", "incomplete"}
ENERGY_METRICS = {"active_energy_burned", "basal_energy_burned"}
INCOMPLETE_BASAL_SHARE = 0.5      # basal energy < 50% of the usual day -> incomplete (real low days, watch off for hours, were >= 68%)
BASAL_REF_DAYS = 60               # "usual day" = median of the previous 60 days ...
BASAL_REF_MIN_DAYS = 7            # ... needs at least 7 of them, otherwise this rule is off
WORKOUT_KCAL_SLACK = 20           # active energy more than 20 kcal below that day's workouts -> incomplete (same as training/history.py)

SLEEP_GAP = timedelta(minutes=60)     # a gap over 60 minutes splits into two segments
WAKE_DAY_SHIFT = timedelta(hours=4)   # assigned to the day of (wake − 4h)
MIN_ASLEEP_MIN = 15
STAGES = {"core", "deep", "rem", "awake", "unspecified"}


def snake(name: str) -> str:
    """HealthKit type name -> metric name: HeartRateVariabilitySDNN -> heart_rate_variability_sdnn."""
    for prefix in ("HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier", "HKDataType"):
        name = name.removeprefix(prefix)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def normalize(metric: str, value: float, unit: str | None) -> tuple[float, str | None]:
    if unit == "kJ":
        return value / 4.184, "kcal"
    if metric == "oxygen_saturation" and value <= 1.0:
        return value * 100, "%"
    return value, unit


@dataclass
class Segment:
    start: datetime   # time-zone aware
    end: datetime
    stage: str        # core / deep / rem / awake / unspecified
    device: str | None = None


@dataclass
class SleepSession:
    wake_date: date
    start: datetime
    end: datetime
    minutes: Counter
    device: str | None

    @property
    def asleep_min(self) -> int:
        return round(sum(self.minutes[s] for s in ("core", "deep", "rem", "unspecified")))


def sessionize(segments: list[Segment]) -> list[SleepSession]:
    """Cut stage segments into sleep segments; segments with under 15 minutes asleep are dropped.
    Only the gaps between "asleep" segments count (> 60 minutes splits): the watch occasionally records an "awake" stretch of several hours,
    which would join the main sleep to a later nap, so awake segments take no part in cutting and are only counted into the sleep segment they fall in."""
    asleep = sorted((s for s in segments if s.stage != "awake"), key=lambda s: s.start)
    awake = [s for s in segments if s.stage == "awake"]
    sessions, cur = [], []
    for seg in asleep:
        if cur and seg.start - max(s.end for s in cur) > SLEEP_GAP:
            sessions.append(cur)
            cur = []
        cur.append(seg)
    if cur:
        sessions.append(cur)
    for segs in sessions:
        lo, hi = segs[0].start, max(s.end for s in segs)
        segs += [a for a in awake if a.start >= lo and a.end <= hi]
        segs.sort(key=lambda s: s.start)
    out = []
    for segs in sessions:
        minutes = Counter()
        for s in segs:
            minutes[s.stage] += (s.end - s.start).total_seconds() / 60
        end = max(s.end for s in segs)
        devices = Counter(s.device for s in segs)
        sess = SleepSession((end - WAKE_DAY_SHIFT).date(), segs[0].start, end, minutes, devices.most_common(1)[0][0])
        if sess.asleep_min >= MIN_ASLEEP_MIN:
            out.append(sess)
    return out


def upsert_daily(conn: psycopg.Connection, user_id: int, source: str, rows: list[dict],
                 today: date | None = None) -> int:
    """rows: date, metric, value, unit, agg, n, min_value, max_value[, flags]. Stores this source's value per (date, metric) in
    daily_metric_sources (a newer import of the same source replaces it), then re-chooses daily_metrics for those dates."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO daily_metric_sources (user_id, date, metric, source, value, unit, agg, n, min_value, max_value, flags)
               VALUES (%(user_id)s, %(date)s, %(metric)s, %(source)s, %(value)s, %(unit)s, %(agg)s, %(n)s,
                       %(min_value)s, %(max_value)s, %(flags)s)
               ON CONFLICT (user_id, date, metric, source) DO UPDATE SET
                 value = EXCLUDED.value, unit = EXCLUDED.unit, agg = EXCLUDED.agg, n = EXCLUDED.n,
                 min_value = EXCLUDED.min_value, max_value = EXCLUDED.max_value, flags = EXCLUDED.flags, updated_at = now()""",
            [{**r, "user_id": user_id, "source": source, "flags": json.dumps(r.get("flags") or {}, ensure_ascii=False)}
             for r in rows])
    resolve_daily(conn, user_id, {r["date"] for r in rows}, today)
    return len(rows)


def _basal_reference(conn: psycopg.Connection, user_id: int, first: date, last: date) -> dict[date, float]:
    """The usual basal energy before each day: median over the previous BASAL_REF_DAYS days of that day's largest
    candidate that was not summed by hour."""
    per_day = {r["date"]: r["v"] for r in conn.execute(
        """SELECT date, max(value) AS v FROM daily_metric_sources
           WHERE user_id = %s AND metric = 'basal_energy_burned' AND date >= %s AND date < %s AND value > 0
             AND NOT flags ? 'hourly_sum' GROUP BY date""",
        (user_id, first - timedelta(days=BASAL_REF_DAYS), last))}
    out = {}
    d = first
    while d <= last:
        xs = [v for k, v in per_day.items() if d - timedelta(days=BASAL_REF_DAYS) <= k < d]
        if len(xs) >= BASAL_REF_MIN_DAYS:
            out[d] = median(xs)
        d += timedelta(days=1)
    return out


def incomplete_days(cands: list[dict], basal_ref: dict[date, float], workout_kcal: dict[date, float],
                    today: date) -> dict[tuple[date, str], str]:
    """(date, source) -> why that source's energy for the day is partial (applies to ENERGY_METRICS).
    Only days before today (today is partial for every source)."""
    out = {}
    for c in cands:
        d, src = c["date"], c["source"]
        if d >= today or c["agg"] != "sum":
            continue
        ref = basal_ref.get(d)
        if c["metric"] == "basal_energy_burned" and ref and c["value"] < INCOMPLETE_BASAL_SHARE * ref:
            out[(d, src)] = f"{src} 这天的基础代谢只有 {c['value']:.0f} kcal（平常约 {ref:.0f}）：只同步了一部分"
        wk = workout_kcal.get(d)
        if c["metric"] == "active_energy_burned" and wk and c["value"] + WORKOUT_KCAL_SLACK < wk:
            out.setdefault((d, src), f"{src} 这天的全天活动能量 {c['value']:.0f} kcal 比训练本身的 {wk:.0f} 还少：只同步了一部分")
    return out


def choose(cands: list[dict], incomplete: dict[tuple[date, str], str]) -> tuple[dict, dict]:
    """One (date, metric): returns (the chosen candidate, flags for daily_metrics)."""
    def prio(c):
        return SOURCE_PRIORITY.get(c["source"], 0)

    if not any(c["agg"] == "sum" for c in cands):
        win = max(cands, key=prio)
        return win, dict(win["flags"] or {})

    def flags_of(c):
        f = dict(c["flags"] or {})
        if c["metric"] in ENERGY_METRICS and (c["date"], c["source"]) in incomplete:
            f["incomplete"] = incomplete[(c["date"], c["source"])]
        return f

    clean = [c for c in cands if not DEGRADED_FLAGS & set(flags_of(c))]
    win = max(clean or cands, key=lambda c: (c["value"], prio(c)))
    return win, flags_of(win)


def resolve_daily(conn: psycopg.Connection, user_id: int, dates, today: date | None = None) -> int:
    """Re-choose daily_metrics for these dates from daily_metric_sources (idempotent). Returns the number of (date, metric) chosen."""
    dates = sorted(set(dates))
    if not dates:
        return 0
    if today is None:
        tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
        today = datetime.now(ZoneInfo(tz)).date()
    cands = conn.execute("SELECT * FROM daily_metric_sources WHERE user_id = %s AND date = ANY(%s)",
                         (user_id, dates)).fetchall()
    workout_kcal = {r["local_date"]: r["kcal"] for r in conn.execute(
        """SELECT local_date, sum(active_kcal) AS kcal FROM workouts WHERE user_id = %s AND local_date = ANY(%s)
           AND dup_of IS NULL AND excluded IS NULL AND active_kcal IS NOT NULL GROUP BY local_date""", (user_id, dates))}
    incomplete = incomplete_days(cands, _basal_reference(conn, user_id, dates[0], dates[-1]), workout_kcal, today)
    groups = defaultdict(list)
    for c in cands:
        groups[(c["date"], c["metric"])].append(c)
    out = []
    for cs in groups.values():
        win, flags = choose(cs, incomplete)
        out.append({k: win[k] for k in ("user_id", "date", "metric", "value", "unit", "agg", "n", "min_value",
                                         "max_value", "source")} | {"flags": json.dumps(flags, ensure_ascii=False)})
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, min_value, max_value, source, flags)
               VALUES (%(user_id)s, %(date)s, %(metric)s, %(value)s, %(unit)s, %(agg)s, %(n)s,
                       %(min_value)s, %(max_value)s, %(source)s, %(flags)s)
               ON CONFLICT (user_id, date, metric) DO UPDATE SET
                 value = EXCLUDED.value, unit = EXCLUDED.unit, agg = EXCLUDED.agg, n = EXCLUDED.n,
                 min_value = EXCLUDED.min_value, max_value = EXCLUDED.max_value, source = EXCLUDED.source,
                 flags = EXCLUDED.flags, updated_at = now()""", out)
    return len(out)


def replace_sleep(conn: psycopg.Connection, user_id: int, source: str, sessions: list[SleepSession]) -> dict:
    """Rewrite one source's sleep: first delete that source's old records within the covered dates, then insert (no stale segments remain when the algorithm changes).
    If a higher-priority source already has sleep on a day, do not insert; lower-priority sources' records on those days are deleted. Finally recompute is_main."""
    if not sessions:
        return {"inserted": 0, "skipped": 0}
    prio = SOURCE_PRIORITY[source]
    higher = [s for s, p in SOURCE_PRIORITY.items() if p > prio]
    lower = [s for s, p in SOURCE_PRIORITY.items() if p < prio]
    first = min(s.wake_date for s in sessions)
    last = max(s.wake_date for s in sessions)
    taken = {r["wake_date"] for r in conn.execute(
        "SELECT DISTINCT wake_date FROM sleep_sessions WHERE user_id = %s AND source = ANY(%s) "
        "AND wake_date BETWEEN %s AND %s", (user_id, higher, first, last))}
    keep = [s for s in sessions if s.wake_date not in taken]
    dates = sorted({s.wake_date for s in keep})
    conn.execute("DELETE FROM sleep_sessions WHERE user_id = %s AND source = %s AND wake_date BETWEEN %s AND %s",
                 (user_id, source, first, last))
    conn.execute("DELETE FROM sleep_sessions WHERE user_id = %s AND source = ANY(%s) AND wake_date = ANY(%s)",
                 (user_id, lower, dates))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min,
                 rem_min, awake_min, unspecified_min, is_main, source, device)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s)""",
            [(user_id, s.wake_date, s.start, s.end, s.asleep_min, round(s.minutes["core"]),
              round(s.minutes["deep"]), round(s.minutes["rem"]), round(s.minutes["awake"]),
              round(s.minutes["unspecified"]), source, s.device) for s in keep])
    # the longest sleep of each day is the main sleep, the rest are naps
    conn.execute(
        """UPDATE sleep_sessions s SET is_main = (s.id = m.id)
           FROM (SELECT DISTINCT ON (wake_date) wake_date, id FROM sleep_sessions
                 WHERE user_id = %s AND wake_date BETWEEN %s AND %s
                 ORDER BY wake_date, asleep_min DESC, id) m
           WHERE s.user_id = %s AND s.wake_date = m.wake_date""", (user_id, first, last, user_id))
    return {"inserted": len(keep), "skipped": len(sessions) - len(keep)}
