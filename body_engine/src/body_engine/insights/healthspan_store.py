"""Body age: read data -> compute weekly -> write healthspan_weekly -> read. Formulas are in healthspan.py, parameters in healthspan_params.py."""
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

_dumps = lambda o: json.dumps(o, default=str, ensure_ascii=False)   # contributions contain dates

from .. import sparky_client
from ..i18n import is_en
from ..scores.engine import scoring_origin
from . import healthspan as HS
from . import healthspan_params as H
from . import healthspan_params_v12 as H12
from . import sleep_detail

VERSIONS = {"v1.1": H, "v1.2": H12}
# The version written to healthspan_weekly (switched to v1.2 on 2026-10-23 by the maintainer's decision, issue #118).
# v1.1 stays for the comparison tool and as the rollback: set CURRENT = VERSIONS["v1.1"], deploy, recompute every user.
CURRENT = H12

LABELS = {  # key: (Chinese name, category, unit)
    "sleep_duration": ("平均睡眠时长", "sleep", "h"),
    "sleep_regularity": ("作息规律性（SRI）", "sleep", ""),
    "steps": ("日均步数", "activity", "步"),
    "mvpa": ("每周中高强度运动", "activity", "分钟"),
    "strength": ("每周力量训练", "activity", "分钟"),
    "vo2max": ("VO₂max", "fitness", "mL/kg/min"),
    "rhr": ("静息心率", "fitness", "bpm"),
    "body_comp": ("身体成分", "fitness", ""),
    "activity": ("活动（步数 + 训练）", "activity", "步当量"),   # v1.2: steps, exercise and strength as one domain
}
LABELS_EN = {  # key: (English name, unit) — the Today page in English (issue #42); stored rows keep the Chinese label
    "sleep_duration": ("Average sleep", "h"),
    "sleep_regularity": ("Sleep regularity (SRI)", ""),
    "steps": ("Daily steps", "steps"),
    "mvpa": ("Weekly moderate-to-vigorous activity", "min"),
    "strength": ("Weekly strength training", "min"),
    "vo2max": ("VO₂max", "mL/kg/min"),
    "rhr": ("Resting HR", "bpm"),
    "body_comp": ("Body composition", ""),
    "activity": ("Activity (steps + training)", "step equiv."),
}
PART_LABELS = {  # v1.2 activity-domain parts: key -> (Chinese, English, Chinese unit, English unit)
    "steps": ("手腕步数", "Wrist steps", "步", "steps"),
    "exercise_equiv": ("不产生步数的训练（折算）", "Training without steps (converted)", "步当量", "step equiv."),
    "strength": ("每周力量训练", "Weekly strength training", "分钟", "min"),
    "mvpa": ("每周中高强度运动（参考，不重复计入）", "Weekly moderate-to-vigorous activity (reference, not counted again)",
             "分钟", "min"),
}
STRENGTH_TYPES = {"strength_training", "traditional_strength_training", "functional_strength_training",
                  "core_training", "cross_training"}


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) AS t", (name,)).fetchone()["t"] is not None


class Data:
    """All inputs of one person over [start, end] (indexed by date), reused repeatedly when computing weekly snapshots."""

    def __init__(self, conn: psycopg.Connection, user_id: int, start: date, end: date, sparky=None):
        u = conn.execute("SELECT timezone, birth_date, sex, height_cm FROM users WHERE id = %s", (user_id,)).fetchone()
        self.tz, self.birth, self.height = ZoneInfo(u["timezone"]), u["birth_date"], u["height_cm"]
        self.sex = {"m": "male", "male": "male", "男": "male", "f": "female", "female": "female", "女": "female"}.get(
            (u["sex"] or "").strip().lower())
        self.notes: list[str] = []
        rows = conn.execute("""SELECT date, sleep_actual_min, strain, details->'load'->'zone_minutes' AS z
                               FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s""",
                            (user_id, start, end)).fetchall()
        self.sleep = {r["date"]: r["sleep_actual_min"] / 60 for r in rows if r["sleep_actual_min"] is not None}
        # moderate-to-vigorous: Z2–Z3 (60–80% HRmax) count as moderate, Z4–Z5 (>= 80%) as vigorous, vigorous × 2 -> moderate-equivalent minutes / day
        self.mvpa = {}
        self.zones = {r["date"]: list(r["z"]) for r in rows if r["strain"] is not None and r["z"]}   # v1.2 MVPA reference
        for r in rows:
            if r["strain"] is not None and r["z"]:
                z = r["z"]
                self.mvpa[r["date"]] = (z[1] + z[2]) + H.VIGOROUS_FACTOR * (z[3] + z[4])
        metric = lambda m: {r["date"]: r["value"] for r in conn.execute(
            "SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = %s AND date BETWEEN %s AND %s",
            (user_id, m, start, end))}
        self.steps, self.rhr, self.vo2 = metric("step_count"), metric("resting_heart_rate"), metric("vo2_max")
        self.weight = metric("body_mass")
        self.waist: dict[date, float] = {}
        try:   # the user's own goal weight (profile / status card), only for the body-composition tip
            from ..profile import store as profile
            self.goal_kg = profile.get(conn, user_id)["goal_weight_kg"]
        except Exception:  # noqa: BLE001
            self.goal_kg = None
        # sleep regularity: minute-by-minute state for each wake date
        mains, intervals = sleep_detail.load_intervals(conn, user_id, start, end)
        self.sleep_days = {d: sleep_detail.asleep_minutes(intervals, d, self.tz) for d in mains if start <= d <= end}
        # strength-training minutes: watch workout records (workouts table, Hevy imports included), SparkyFitness workout records and
        # imported sessions (Hevy); per day the largest of them (SparkyFitness sessions carry no start time, so they cannot be
        # matched workout by workout; the larger value never counts one workout twice and never drops a source)
        self.strength: dict[date, float] = defaultdict(float)
        # v1.2: every watch workout by day, [(type, minutes, heart-rate zone minutes)], for the activity domain
        # (strength minutes still come from self.strength, the larger of watch and SparkyFitness)
        self.workouts: dict[date, list] = defaultdict(list)
        first_track = []
        if _table_exists(conn, "workouts"):
            for r in conn.execute("""SELECT local_date, type, duration_min, hr_zone_min FROM workouts WHERE user_id = %s
                                     AND dup_of IS NULL AND excluded IS NULL AND local_date BETWEEN %s AND %s""",
                                  (user_id, start, end)):
                if r["type"] in STRENGTH_TYPES:
                    self.strength[r["local_date"]] += r["duration_min"]
                self.workouts[r["local_date"]].append((r["type"], r["duration_min"], r["hr_zone_min"]))
        sparky_days: dict[date, float] = {}
        try:
            sparky = sparky or sparky_client.client_for_user(conn, user_id)
            for s in sparky_client.training_log(sparky, start, end):
                minutes = session_strength_minutes(s)
                if minutes is None:
                    continue
                d = date.fromisoformat(s["date"][:10])
                sparky_days[d] = sparky_days.get(d, 0) + minutes
            for r in sparky_client.weight(sparky, start, end):
                d = date.fromisoformat(r["date"])
                if "weight_kg" in r:
                    self.weight[d] = r["weight_kg"]   # manual weigh-ins take priority over Apple Health
                if "waist_cm" in r:
                    self.waist[d] = r["waist_cm"]
                if "height_cm" in r and not self.height:
                    self.height = r["height_cm"]
        except Exception as e:  # SparkyFitness not connected / unreadable: strength training uses only watch records
            self.notes.append(f"SparkyFitness 读不到（{type(e).__name__}），力量训练只看手表训练记录、体重只看 Apple 健康")
        # imported workouts (Hevy CSV, issue #210): most are already in `workouts` as source 'hevy', but one that is a duplicate
        # of a watch copy whose type is not counted above (e.g. fitness_equipment) was lost; same per-day rule as SparkyFitness
        hevy_days = imported_strength_days(conn, user_id, start, end)
        for days in (sparky_days, hevy_days):
            for d, m in days.items():
                self.strength[d] = max(self.strength.get(d, 0.0), m)
        if self.strength:
            first_track.append(min(self.strength))
        origin = scoring_origin(conn, user_id)
        if origin:
            first_track.append(origin)
        # before the first recorded day it is unknown whether the user trained; from when they started wearing the watch (or first logged a workout), days without records count as 0 minutes
        self.strength_from = min(first_track) if first_track else None
        # evidence that the user trained (journal, active plan), only to label the strength item (strength_coverage)
        self.strength_journal = {r["date"] for r in conn.execute(
            """SELECT DISTINCT date FROM journal_entries WHERE user_id = %s AND key = ANY(%s) AND value > 0
               AND date BETWEEN %s AND %s""", (user_id, list(H.STRENGTH_JOURNAL_KEYS), start, end))}
        self.strength_plan = conn.execute(
            """SELECT days_per_week, start_date FROM training_plans WHERE user_id = %s AND active
               ORDER BY updated_at DESC, id DESC LIMIT 1""", (user_id,)).fetchone()


def session_strength_minutes(s: dict) -> float | None:
    """Strength minutes of one session in the shape of sparky_client.training_log (SparkyFitness or an import):
    its duration, else the duration of its strength exercises, else H.STRENGTH_MIN_PER_SET per set; None without strength exercises."""
    ex = [e for e in s["exercises"] if (e.get("category") or "strength") == "strength"]
    if not ex:
        return None
    minutes = s.get("total_duration_minutes") or sum(e.get("duration_minutes") or 0 for e in ex)
    if not minutes:   # no duration: estimate from the working set count
        minutes = H.STRENGTH_MIN_PER_SET * sum(len(e["sets"]) for e in ex)
    return minutes


def _imported(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    """Imported sessions (training/hevy_import.imported_sessions) with a strength part; an implausible duration (an unfinished
    workout, which the workouts table excludes) is replaced by the set estimate."""
    from ..training import hevy_import, params as TP   # imported here: training imports insights modules indirectly
    if not _table_exists(conn, "training_session_logs"):
        return []
    out = []
    for s in hevy_import.imported_sessions(conn, user_id, start, end):
        if (s.get("total_duration_minutes") or 0) > TP.MAX_DURATION.total_seconds() / 60:
            s = {**s, "total_duration_minutes": None}
        if (m := session_strength_minutes(s)) is not None:
            out.append({**s, "strength_min": m})
    return out


def imported_strength_days(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[date, float]:
    days: dict[date, float] = {}
    for s in _imported(conn, user_id, start, end):
        d = date.fromisoformat(s["date"])
        days[d] = days.get(d, 0.0) + s["strength_min"]
    return days


def strength_sources(conn: psycopg.Connection, user_id: int, start: date, end: date, sparky=None,
                     timeout: float | None = None) -> dict:
    """Strength-training records in [start, end] by source, for the data status page (issue #210): sessions per source
    (watch = workouts not imported from Hevy, not duplicates, not excluded), the days with any record, and whether
    SparkyFitness could be read (connected / not_linked / unreadable). timeout: seconds for SparkyFitness when the client is made here."""
    days: set[date] = set()
    watch = 0
    for r in conn.execute("""SELECT local_date, type FROM workouts WHERE user_id = %s AND dup_of IS NULL AND excluded IS NULL
                             AND source <> 'hevy' AND local_date BETWEEN %s AND %s""", (user_id, start, end)):
        if r["type"] in STRENGTH_TYPES:
            watch += 1
            days.add(r["local_date"])
    hevy = _imported(conn, user_id, start, end)
    days |= {date.fromisoformat(s["date"]) for s in hevy}
    sparky_n, state = None, "connected"
    if sparky is None and sparky_client.link_state(conn, user_id)["state"] == "not_linked":
        state = "not_linked"
    else:
        try:
            if sparky is None:
                sparky = sparky_client.client_for_user(conn, user_id)
                if timeout is not None:
                    sparky.timeout = timeout
            sessions = [s for s in sparky_client.training_log(sparky, start, end) if session_strength_minutes(s) is not None]
            sparky_n = len(sessions)
            days |= {date.fromisoformat(s["date"][:10]) for s in sessions}
        except Exception:  # noqa: BLE001 - SparkyFitness down or the key broken: the rest still counts
            state = "unreadable"
    return {"days": len(days), "sessions": {"watch": watch, "sparky": sparky_n, "hevy": len(hevy)}, "sparky": state}


def strength_coverage(data: Data, as_of: date) -> str | None:
    """Why the strength records of the last H.STRENGTH_COVERAGE_WEEKS weeks look incomplete (Chinese, for the AI), or None.
    Only a label: the minutes are not changed (healthspan_params.py, issue #210)."""
    weeks = H.STRENGTH_COVERAGE_WEEKS
    lo = as_of - timedelta(days=7 * weeks - 1)
    recorded = {d for d, m in data.strength.items() if lo <= d <= as_of and m > 0}
    missed = [d for d in getattr(data, "strength_journal", ()) if lo <= d <= as_of and d not in recorded]
    if len(missed) >= H.STRENGTH_JOURNAL_MISSED_MIN:
        return f"近 {weeks} 周日记里有 {len(missed)} 天记了力量训练，这些天没有训练记录"
    plan = getattr(data, "strength_plan", None)
    if plan and plan["start_date"] <= lo and len(recorded) < H.STRENGTH_PLAN_MIN_SHARE * plan["days_per_week"] * weeks:
        return f"训练计划每周练 {plan['days_per_week']} 天，近 {weeks} 周只有 {len(recorded)} 天有力量训练记录"
    return None


def _sri_weighted(days: dict[date, bytearray], as_of: date, params=H) -> tuple[float | None, int]:
    lo, recent = as_of - timedelta(days=params.WINDOW_DAYS - 1), as_of - timedelta(days=params.RECENT_DAYS - 1)
    same = total = 0.0
    pairs = 0
    for d, cur in days.items():
        nxt = days.get(d + timedelta(days=1))
        if nxt is None or not (lo <= d and d + timedelta(days=1) <= as_of):
            continue
        w = params.RECENT_WEIGHT if d >= recent else 1.0
        same += w * sum(1 for x, y in zip(cur, nxt) if x == y)
        total += w * sleep_detail.DAY_MIN
        pairs += 1
    return (200 * same / total - 100 if total else None), pairs


def _latest(values: dict[date, float], as_of: date, max_age: int) -> tuple[date, float] | None:
    xs = [(d, v) for d, v in values.items() if as_of - timedelta(days=max_age) <= d <= as_of]
    return max(xs) if xs else None


def _tracked_days(data: Data, as_of: date, params=H) -> list[date]:
    """Days from when training is known to be recorded (first workout / watch day) within the window; empty if unknown.
    On these days no record means 0 minutes."""
    if data.strength_from is None:
        return []
    lo = max(data.strength_from, as_of - timedelta(days=params.WINDOW_DAYS - 1))
    return [lo + timedelta(days=k) for k in range((as_of - lo).days + 1)]


def _activity_domain(data: Data, as_of: date, age: float | None, params, add, missing) -> None:
    """v1.2: steps, exercise and strength training as one domain with one cap (healthspan.hr_activity).
    The HR-zone MVPA is kept as a reference part (counted = false): its walking is in the steps and its exercise in the
    step equivalents, so counting it again would be the double counting v1.1 shrank with × 0.6."""
    if age is None:
        missing("activity", "没有生日，没法对照同龄人")
        return
    steps, n = HS.weighted_mean(data.steps, as_of, params=params)
    has_steps = n >= params.MIN_DAYS
    days = _tracked_days(data, as_of, params)
    workouts = getattr(data, "workouts", {})
    eq, strength, mvpa = {}, {}, {}
    for d in days:
        s_min = data.strength.get(d, 0.0)
        ws = workouts.get(d, ())
        strength[d] = s_min
        eq[d] = sum(HS.step_equivalents(m, params.WORKOUT_MET[t], params=params) for t, m, _ in ws if t in params.NONSTEP_TYPES)
        if params.STRENGTH_IN_VOLUME:   # off by default: strength already has its own curve (see the parameters)
            eq[d] += HS.step_equivalents(s_min, params.STRENGTH_MET, params=params)
    # MVPA reference: the day's heart-rate zone minutes, with strength and non-step workouts counted at their MET instead
    # of their heart rate (questionnaires such as Lee 2022 class activities by absolute MET; Lee counts weightlifting as moderate)
    for d, z in getattr(data, "zones", {}).items():
        ws = [w for w in workouts.get(d, ()) if w[0] in STRENGTH_TYPES or w[0] in params.NONSTEP_TYPES]
        out = [max(0.0, z[i] - sum((wz or [0.0] * 5)[i] for _, _, wz in ws)) for i in range(5)]
        mvpa[d] = (out[1] + out[2]) + params.VIGOROUS_FACTOR * (out[3] + out[4]) \
            + HS.moderate_equiv_minutes(data.strength.get(d, 0.0), params.STRENGTH_MET, params=params) \
            + sum(HS.moderate_equiv_minutes(m, params.WORKOUT_MET[t], params=params)
                  for t, m, _ in ws if t in params.NONSTEP_TYPES)
    eq_mean, n_eq = HS.weighted_mean(eq, as_of, params=params)
    tracked = n_eq >= params.MIN_DAYS
    str_week = HS.weighted_mean(strength, as_of, params=params)[0] * 7 if tracked else None
    if not has_steps and not tracked:
        missing("activity", f"只有 {n} 天步数（要 ≥ {params.MIN_DAYS}），也还没有训练记录")
        return
    raw = lambda hr: HS.years_from_hr(hr, math.inf, params=params)
    parts = []
    if has_steps:
        wrist = steps * params.WRIST_STEPS_FACTOR
        total = wrist + (eq_mean if tracked else 0.0)
        r = HS.hr_activity(total, str_week, age, params=params)
        y_steps = raw(HS.hr_steps(wrist, age, params=params))
        parts.append({"key": "steps", "value": round(steps), "unit": PART_LABELS["steps"][2], "data_days": n,
                      "years": round(y_steps, 2), **({"wrist_factor": params.WRIST_STEPS_FACTOR}
                                                     if params.WRIST_STEPS_FACTOR != 1 else {})})
    else:   # volume unknown (HR 1): the domain is the strength curve alone, as v1.1's strength item was without steps
        missing("steps", f"只有 {n} 天步数（要 ≥ {params.MIN_DAYS}），活动只看力量训练")
        total = None
        hs = HS.hr_strength(str_week, params=params)
        r = {"hr": hs, "hr_volume": 1.0, "hr_strength": hs}
    if tracked:
        if has_steps:
            parts.append({"key": "exercise_equiv", "value": round(eq_mean), "unit": PART_LABELS["exercise_equiv"][2],
                          "data_days": n_eq, "years": round(raw(r["hr_volume"]) - y_steps, 2)})
        parts.append({"key": "strength", "value": round(str_week), "unit": PART_LABELS["strength"][2],
                      "data_days": n_eq, "hr": round(r["hr_strength"], 3), "years": round(raw(r["hr_strength"]), 2)})
    else:
        missing("strength", f"开始记录训练只有 {n_eq} 天（要 ≥ {params.MIN_DAYS}），活动只看步数")
    # issue #210: strength records that look incomplete label the domain (and its strength part); the minutes are unchanged
    why = strength_coverage(data, as_of) if tracked else None
    cover = {"coverage": "possibly_incomplete", "coverage_reason": why} if why else {}
    if why:
        next(p for p in parts if p["key"] == "strength").update(cover)
    v_m, n_m = HS.weighted_mean(mvpa, as_of, params=params)
    if n_m >= params.MIN_DAYS:
        parts.append({"key": "mvpa", "value": round(v_m * 7), "unit": PART_LABELS["mvpa"][2], "data_days": n_m,
                      "counted": False})
    for pt in parts:
        pt["label"] = PART_LABELS[pt["key"]][0]
    add("activity", None if total is None else round(total), r["hr"], n if has_steps else n_eq,
        {"years_uncapped": round(raw(r["hr"]), 2), "parts": parts, **cover},
        years=HS.years_from_hr(r["hr"], params.ACTIVITY_CAP_YEARS, params=params),
        tip=H.TIPS["strength_incomplete"] if why else None)


def snapshot(data: Data, as_of: date, params=CURRENT) -> dict:
    """Body age on one day (window = 180 days back from as_of). params: a VERSIONS entry (default CURRENT, v1.2)."""
    items, insufficient = [], []

    def add(key, value, hr, data_days, extra=None, years=None, goal_kg=None, tip=None):
        label, cat, unit = LABELS[key]
        years = HS.years_from_hr(hr, params=params) if years is None else years
        items.append({"key": key, "label": label, "category": cat, "value": value, "unit": unit, "hr": round(hr, 3),
                      "years": round(years, 2), "direction": "younger" if years < 0 else "older" if years > 0 else "neutral",
                      "tip": tip or HS.tip(key, years, value, goal_kg), "data_days": data_days, **(extra or {})})

    def missing(key, reason):
        insufficient.append({"key": key, "label": LABELS[key][0], "category": LABELS[key][1], "reason": reason})

    # ---- sleep
    v, n = HS.weighted_mean(data.sleep, as_of, params=params)
    if n >= params.MIN_DAYS:
        add("sleep_duration", round(v, 2), HS.hr_sleep_duration(v, params=params), n,
            {"self_report_equiv_h": round(HS.self_report_hours(v, params=params), 2)})
    else:
        missing("sleep_duration", f"180 天里只有 {n} 晚手表睡眠（要 ≥ {params.MIN_DAYS}）")
    v, n = _sri_weighted(data.sleep_days, as_of, params)
    if n >= params.MIN_DAYS:
        add("sleep_regularity", round(v, 1), HS.hr_regularity(v, params=params), n)
    else:
        missing("sleep_regularity", f"只有 {n} 对相邻两晚都有手表睡眠（要 ≥ {params.MIN_DAYS}）")
    # ---- activity
    age = HS.chrono_age(data.birth, as_of) if data.birth else None
    if getattr(params, "ACTIVITY_MODEL", "items") == "domain":
        _activity_domain(data, as_of, age, params, add, missing)
    else:
        v, n = HS.weighted_mean(data.steps, as_of, params=params)
        if n >= params.MIN_DAYS and age is not None:
            add("steps", round(v), HS.hr_steps(v, age, params=params), n)
        else:
            missing("steps", f"只有 {n} 天步数（要 ≥ {params.MIN_DAYS}）")
        v, n = HS.weighted_mean(data.mvpa, as_of, params=params)
        if n >= params.MIN_DAYS:
            add("mvpa", round(v * 7), HS.hr_mvpa(v * 7, params=params), n)
        else:
            missing("mvpa", f"只有 {n} 天有足够的原始心率（要 ≥ {params.MIN_DAYS}）")
        days = _tracked_days(data, as_of, params)
        v, n = HS.weighted_mean({d: data.strength.get(d, 0.0) for d in days}, as_of, params=params) if days else (None, 0)
        if n >= params.MIN_DAYS:
            add("strength", round(v * 7), HS.hr_strength(v * 7, params=params), n)
            if why := strength_coverage(data, as_of):   # label only: the value stays what the records show
                items[-1].update(coverage="possibly_incomplete", coverage_reason=why, tip=H.TIPS["strength_incomplete"])
        else:
            missing("strength", f"开始记录训练只有 {n} 天（要 ≥ {params.MIN_DAYS}）")
    # ---- fitness
    vo2 = [(d, x) for d, x in data.vo2.items() if as_of - timedelta(days=params.WINDOW_DAYS - 1) <= d <= as_of]
    latest = max(vo2)[0] if vo2 else None
    norm = HS.vo2_norm(age, data.sex, params=params) if age is not None and data.sex else None
    if data.sex is None:
        missing("vo2max", "没填性别（users.sex），没法对照同龄常模")
    elif len(vo2) < params.VO2_MIN_READINGS or (as_of - latest).days > params.VO2_MAX_AGE_DAYS:
        missing("vo2max", f"180 天里只有 {len(vo2)} 次 VO₂max 读数或最近一次超过 {params.VO2_MAX_AGE_DAYS} 天"
                          f"（要 ≥ {params.VO2_MIN_READINGS} 次；户外走路 / 跑步 20 分钟以上手表才会估）")
    else:
        v, n = HS.weighted_mean(dict(vo2), as_of, params=params)
        add("vo2max", round(v, 1), HS.hr_vo2(v, norm, params=params), n,
            {"norm_median": round(norm, 1), "readings": len(vo2),
             "percentile": HS.vo2_percentile(v, age, data.sex, params=params)})
    v, n = HS.weighted_mean(data.rhr, as_of, params=params)
    if n >= params.MIN_DAYS:
        add("rhr", round(v, 1), HS.hr_rhr(v, params=params), n)
    else:
        missing("rhr", f"只有 {n} 天静息心率（要 ≥ {params.MIN_DAYS}）")
    w = _latest(data.weight, as_of, params.WEIGHT_MAX_AGE_DAYS)
    waist = _latest(data.waist, as_of, params.WINDOW_DAYS)
    goal = getattr(data, "goal_kg", None)
    goal = goal if goal and w and goal < w[1] else None   # "lose down to the goal" only when the goal is below the weight
    if waist and data.height:
        whtr = waist[1] / data.height
        add("body_comp", round(whtr, 3), HS.hr_whtr(whtr, params=params), 1,
            {"measure": "waist_to_height", "measured_on": waist[0]}, goal_kg=goal)
    elif w and data.height:
        bmi = w[1] / (data.height / 100) ** 2
        add("body_comp", round(bmi, 1), HS.hr_bmi(bmi, params=params), 1,
            {"measure": "bmi", "measured_on": w[0], "weight_kg": w[1], "note": "没有腰围，用 BMI（分不清肌肉和脂肪）"},
            goal_kg=goal)
    else:
        missing("body_comp", f"最近 {params.WEIGHT_MAX_AGE_DAYS} 天没有体重或没有身高" if data.height else "没有身高")
    delta = HS.combine(items, params=params) if items else None
    return {"as_of": as_of, "chrono_age": round(age, 2) if age is not None else None,
            "delta_years": None if delta is None else round(delta, 2),
            "body_age": None if delta is None or age is None else round(age + delta, 1),
            "contributors": sorted(items, key=lambda i: -abs(i["years"])), "insufficient": insufficient}


def week_ends(first: date, today: date) -> list[tuple[date, date]]:
    """[(week_start Monday, as_of)]: one per Sunday, the last being the week containing today (as_of = today)."""
    out = []
    monday = first - timedelta(days=first.weekday())
    while monday <= today:
        out.append((monday, min(monday + timedelta(days=6), today)))
        monday += timedelta(days=7)
    return out


def load(conn: psycopg.Connection, user_id: int, today: date | None = None, sparky=None) -> tuple[Data, date, date] | None:
    """(inputs, scoring origin, today), or None without watch data / birth date. The inputs serve every version."""
    origin = scoring_origin(conn, user_id)
    user = conn.execute("SELECT birth_date, timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if origin is None or user["birth_date"] is None:
        return None
    today = today or datetime.now(ZoneInfo(user["timezone"])).date()
    return Data(conn, user_id, origin - timedelta(days=1), today, sparky), origin, today


def weekly(data: Data, origin: date, today: date, params=CURRENT) -> list[dict]:
    rows, history = [], []
    for week_start, as_of in week_ends(origin + timedelta(days=params.MIN_DAYS), today):
        snap = snapshot(data, as_of, params)
        history.append((as_of, snap["delta_years"]))
        p = HS.pace(history, as_of, params=params)
        snap.update(pace_of_aging=p["pace"], pace_raw=p["pace_raw"], pace_window_weeks=p["window_weeks"])
        snap["week_start"] = week_start
        snap["notes"] = data.notes
        rows.append(snap)
    return rows


def compute(conn: psycopg.Connection, user_id: int, today: date | None = None, sparky=None, params=CURRENT) -> list[dict]:
    loaded = load(conn, user_id, today, sparky)
    return weekly(*loaded, params) if loaded else []


def write(conn: psycopg.Connection, user_id: int, rows: list[dict], params=CURRENT) -> int:
    with conn.transaction():
        conn.execute("DELETE FROM healthspan_weekly WHERE user_id = %s", (user_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO healthspan_weekly (user_id, week_start, as_of, chrono_age, body_age, delta_years,
                     pace_of_aging, pace_raw, pace_window_weeks, contributors, insufficient, algo_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(user_id, r["week_start"], r["as_of"], r["chrono_age"], r["body_age"], r["delta_years"],
                  r["pace_of_aging"], r["pace_raw"], r["pace_window_weeks"], Jsonb(r["contributors"], _dumps), Jsonb(r["insufficient"], _dumps), params.ALGO_VERSION) for r in rows])
    return len(rows)


def read(conn: psycopg.Connection, user_id: int, weeks: int = 26) -> dict | None:
    rows = conn.execute("""SELECT * FROM healthspan_weekly WHERE user_id = %s ORDER BY week_start DESC LIMIT %s""",
                        (user_id, weeks)).fetchall()
    if not rows:
        return None
    cur = rows[0]
    return {"body_age": cur["body_age"], "chrono_age": cur["chrono_age"], "delta_years": cur["delta_years"],
            "pace_of_aging": cur["pace_of_aging"], "pace_raw": cur["pace_raw"],
            "window_weeks": cur["pace_window_weeks"], "as_of": cur["as_of"], "algo_version": cur["algo_version"],
            "history": [{"week": r["week_start"], "as_of": r["as_of"], "body_age": r["body_age"],
                         "delta_years": r["delta_years"], "pace": r["pace_of_aging"], "pace_raw": r["pace_raw"],
                         "window_weeks": r["pace_window_weeks"]} for r in reversed(rows)],
            "contributors": _localized(cur["contributors"]), "insufficient": _localized(cur["insufficient"])}


def _localized(items: list[dict] | None) -> list[dict] | None:
    """Stored contributor / insufficient rows with label (and unit) in the request's language, by key."""
    if not items or not is_en():
        return items
    out = []
    for it in items:
        en = LABELS_EN.get(it.get("key"))
        it = {**it, "label": en[0], **({"unit": en[1]} if "unit" in it else {})} if en else it
        if it.get("parts"):
            it["parts"] = [{**pt, "label": PART_LABELS[pt["key"]][1], "unit": PART_LABELS[pt["key"]][3]}
                           if pt.get("key") in PART_LABELS else pt for pt in it["parts"]]
        out.append(it)
    return out
