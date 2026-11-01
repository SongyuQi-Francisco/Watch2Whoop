"""Unified training history: watch / Shortcut workouts (workouts table) + SparkyFitness strength records, merged by day and graded by intensity, for the calendar and the single-day detail.

Merge (SparkyFitness has only a date, no time, so pairing is by day):
- Of a SparkyFitness session that day, the strength exercises are attached to that day's longest watch strength-type workout (strength_training / generic …):
  the watch gives duration, heart rate and energy, SparkyFitness gives exercises and sets.
- Cardio exercises (e.g. "爬坡（跑步机）", incline on treadmill) are attached to the watch cardio workout (walking / running …) closest in duration that day, and the name from SparkyFitness is used
  (the watch records treadmill incline as walking, so this is how we know it was an incline).
- Unmatched ones (watch not worn / not synced) become a separate "SparkyFitness only" workout; watch workouts without SparkyFitness records are shown separately.
Intensity: see params.py (TRIMP -> TRIMP estimated from average heart rate -> strength volume -> duration); the day's intensity = that day's highest workout; no training = rest.
Authorization: the whole block needs shares training; scores (strain, muscle load, recovery) additionally need scores, daily activity (active energy, exercise minutes, steps) additionally needs metrics,
and ungranted fields are null and listed in hidden.
"""
import calendar as _cal
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from .. import auth, sparky_client
from ..i18n import is_en, tl
from ..rules import params as RP
from ..rules.context import Ctx
from . import hevy_import, params as P
from .importer import avg_hr_trimp, hr_max_for, type_name

MAX_RANGE_DAYS = 92
STATUS_ZH = {"banned": "禁用", "caution": "注意", "active": "正常"}
STATUS_EN = {"banned": "Banned", "caution": "Caution", "active": "OK"}
LEVEL_RANK = {lvl: i for i, lvl in enumerate(P.INTENSITY_LEVELS)}


def _tz(conn, user_id: int) -> ZoneInfo:
    r = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    return ZoneInfo((r or {}).get("timezone") or "Australia/Sydney")


def _level(value: float, thresholds) -> str:
    for lo, lvl in thresholds:
        if value >= lo:
            return lvl
    return "light"


def _max_level(*levels: str | None) -> str | None:
    levels = [x for x in levels if x]
    return max(levels, key=LEVEL_RANK.__getitem__) if levels else None


def _category(t: str | None) -> str:
    if t in P.SPORT_TYPES:
        return "sport"
    if t in P.STRENGTH_TYPES:
        return "strength"
    if t in P.CARDIO_TYPES:
        return "cardio"
    return "other"


# ---------------------------------------------------------------- read
def load_workouts(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    """Watch / Shortcut workouts for [start, end] (duplicates and excluded ones removed), by start time."""
    return conn.execute(
        """SELECT * FROM workouts WHERE user_id = %s AND local_date BETWEEN %s AND %s
           AND dup_of IS NULL AND excluded IS NULL ORDER BY start_at""", (user_id, start, end)).fetchall()


def load_sparky(conn: psycopg.Connection, user_id: int, start: date, end: date, with_exercises: bool = False):
    """SparkyFitness workout records (and the exercise library). Returns (sessions, exercises, error): when not connected / unreadable sessions is empty and error holds the reason."""
    try:
        client = sparky_client.reader_for_user(conn, user_id)
        sessions = sparky_client.training_log(client, start, end)
        exercises = {e["id"]: e for e in sparky_client.exercises(client)} if with_exercises and sessions else {}
        return sessions, exercises, None
    except sparky_client.SparkyError as e:
        return [], {}, str(e)
    except Exception as e:  # network or format change: the watch part is shown as usual
        return [], {}, f"读不到 SparkyFitness：{type(e).__name__}: {e}"


# ---------------------------------------------------------------- SparkyFitness exercises and sets
def _working(sets: list[dict]) -> list[dict]:
    return [s for s in sets if (s.get("type") or "").lower() not in RP.WARMUP_SET_TYPES]


def _is_cardio(e: dict) -> bool:
    return (e.get("category") or "strength") == "cardio"


def _volume(exercises: list[dict]) -> tuple[float, int]:
    """Σ reps × weight (kg) over the working sets of strength exercises, and the working set count (same as scores/muscle.py)."""
    vol, n = 0.0, 0
    for e in exercises:
        if _is_cardio(e) or (e.get("category") or "strength") != "strength":
            continue
        for s in _working(e["sets"]):
            if s.get("reps") is None and s.get("duration_s") is None:
                continue
            n += 1
            if s.get("reps") and s.get("weight_kg"):
                vol += s["reps"] * s["weight_kg"]
    return round(vol, 1), n


def _exercise_view(e: dict, ctx: Ctx | None) -> dict:
    status = "active"
    if ctx is not None and e.get("exercise_id"):
        status = ctx.status_of(e["exercise_id"])[0]
    return {"exercise_id": e.get("exercise_id"), "name": e.get("name"), "category": e.get("category"),
            "status": STATUS_EN.get(status, "OK") if is_en() else STATUS_ZH.get(status, "正常"), "status_key": status, "notes": e.get("notes"),
            "duration_min": e.get("duration_minutes") or None,
            "sets": [{"set": s.get("set"), "type": s.get("type"), "reps": s.get("reps"), "weight_kg": s.get("weight_kg"),
                      "rir": s.get("rir"), "rpe": s.get("rpe"), "duration_s": s.get("duration_s"),
                      "distance_km": s.get("distance_km"), "notes": s.get("notes")} for s in e["sets"]],
            **({"slug": e["slug"]} if e.get("slug") else {}), **({"unmatched": True} if e.get("unmatched") else {})}


# ---------------------------------------------------------------- merge
def source_label(w: dict) -> str:
    if w["source"] == "shortcut":
        return "快捷指令"
    if w["source"] == "hevy":
        return "Hevy"
    if "watch" in (w.get("device") or "").lower():
        return "Apple Watch"
    if w.get("origin") and w["origin"] != "apple_health_xml":
        return w["origin"]          # third-party app, e.g. Keep
    return "Apple 健康"


def _from_workout(w: dict, tz: ZoneInfo) -> dict:
    src_label = source_label(w)
    return {"id": f"w{w['id']}", "workout_id": w["id"], "source": w["source"], "source_label": src_label,
            "type": w["type"], "type_name": type_name(w["type"]), "name": w["name_zh"],
            "start": w["start_at"].astimezone(tz).isoformat(timespec="minutes"),
            "end": w["end_at"].astimezone(tz).isoformat(timespec="minutes"),
            "duration_min": round(w["duration_min"]), "active_kcal": _r(w["active_kcal"]), "total_kcal": _r(w["total_kcal"]),
            "distance_km": None if w["distance_km"] is None else round(w["distance_km"], 2),
            "avg_hr": _r(w["avg_hr"]), "max_hr": _r(w["max_hr"]), "hr_zone_min": w["hr_zone_min"], "trimp": w["trimp"],
            "hr_max": w["hr_max_used"], "device": w.get("device"),
            "flags": {k: v for k, v in (w["flags"] or {}).items() if k not in ("computed", "basal_kcal", "note")},
            "exercises": [], "volume_kg": None, "working_sets": None, "notes": None, "sparky_id": None,
            "_cat": _category(w["type"])}


def _from_sparky(s: dict, exercises: list[dict]) -> dict:
    minutes = s.get("total_duration_minutes") or sum(e.get("duration_minutes") or 0 for e in exercises) or None
    cardio_only = exercises and all(_is_cardio(e) for e in exercises)
    t = "cardio_training" if cardio_only else "strength_training"
    return {"id": f"s{s['id']}", "workout_id": None, "source": "sparkyfitness", "source_label": "SparkyFitness",
            "type": t, "type_name": type_name(t), "name": s.get("name") or type_name(t), "start": None, "end": None,
            "duration_min": minutes, "active_kcal": None, "total_kcal": None, "distance_km": None, "avg_hr": None,
            "max_hr": None, "hr_zone_min": None, "trimp": None, "hr_max": None, "device": None, "flags": {},
            "exercises": exercises, "volume_kg": None, "working_sets": None, "notes": s.get("notes"),
            "sparky_id": s.get("id"), "_cat": "cardio" if cardio_only else "strength"}


def _attach(sess: dict, s: dict, exercises: list[dict], rename: str | None = None) -> None:
    sess["exercises"] += exercises
    sess["sparky_id"] = sess["sparky_id"] or s.get("id")
    sess["source"] = f"{sess['source']}+sparkyfitness" if "sparkyfitness" not in sess["source"] else sess["source"]
    sess["source_label"] = f"{sess['source_label']} + SparkyFitness" if "SparkyFitness" not in sess["source_label"] \
        else sess["source_label"]
    if s.get("notes") and s["notes"] not in (sess["notes"] or ""):
        sess["notes"] = (sess["notes"] + "\n" if sess["notes"] else "") + s["notes"]
    if rename:
        sess["name"] = rename


def _attach_imported(sessions: list[dict], imported: list[dict], targets: dict[str, int]) -> None:
    """Imported (Hevy) sets go onto their own workouts row, or onto the watch copy it duplicates (targets: external_id -> workouts id)."""
    by_id = {x["workout_id"]: x for x in sessions if x["workout_id"]}
    for s in imported:
        sess = by_id.get(targets.get(s["workout"]))
        if sess is None:
            continue   # the workout was hidden by the user
        sess["exercises"] += s["exercises"]
        if sess["source"] != "hevy" and "hevy" not in sess["source"]:
            sess["source"] = f"{sess['source']}+hevy"
            sess["source_label"] = f"{sess['source_label']} + Hevy"
            if s.get("name"):
                sess["name"] = f"{sess['type_name']} · {s['name']}"
        if s.get("notes") and s["notes"] not in (sess["notes"] or ""):
            sess["notes"] = (sess["notes"] + "\n" if sess["notes"] else "") + s["notes"]


def merge_day(workouts: list[dict], sparky: list[dict], tz: ZoneInfo, imported: list[dict] | None = None,
              targets: dict[str, int] | None = None) -> list[dict]:
    """One day's watch workouts + SparkyFitness workouts (+ imported Hevy sets) -> merged workout list (by start time; SparkyFitness-only ones last)."""
    sessions = [_from_workout(w, tz) for w in workouts]
    if imported:
        _attach_imported(sessions, imported, targets or {})
    extra = []
    used_strength, used_cardio = set(), set()
    for s in sparky:
        strength = [e for e in s["exercises"] if not _is_cardio(e)]
        cardio = [e for e in s["exercises"] if _is_cardio(e)]
        left = []
        if strength:
            cands = [x for x in sessions if x["_cat"] == "strength" and x["id"] not in used_strength]
            if cands:
                target = max(cands, key=lambda x: x["duration_min"] or 0)
                used_strength.add(target["id"])
                label = s.get("name")
                _attach(target, s, strength, f"{target['type_name']} · {label}" if label else None)
            else:
                left += strength
        for e in cardio:
            cands = [x for x in sessions if x["_cat"] == "cardio" and x["id"] not in used_cardio]
            if cands:
                want = e.get("duration_minutes") or 0
                target = min(cands, key=lambda x: (abs((x["duration_min"] or 0) - want) if want else -(x["duration_min"] or 0)))
                used_cardio.add(target["id"])
                _attach(target, s, [e], e.get("name"))
            else:
                left.append(e)
        if left:
            extra.append(_from_sparky(s, left))
    out = sessions + extra
    for sess in out:
        if any(not _is_cardio(e) for e in sess["exercises"]):
            sess["volume_kg"], sess["working_sets"] = _volume(sess["exercises"])
    return out


# ---------------------------------------------------------------- intensity
def classify(sess: dict, hr_max: float | None) -> None:
    """Decide a workout's intensity (written in place: intensity / intensity_label / intensity_basis / intensity_value)."""
    basis, value, level = None, None, None
    hr_max = sess.get("hr_max") or hr_max
    if sess.get("trimp") is not None:
        level, basis, value = _level(sess["trimp"], P.TRIMP_THRESHOLDS), "trimp", sess["trimp"]
    elif sess.get("avg_hr") and hr_max and sess.get("duration_min"):
        est = avg_hr_trimp(sess["avg_hr"], sess["duration_min"], hr_max) * P.AVG_HR_TRIMP_FACTOR
        level, basis, value = _level(est, P.TRIMP_THRESHOLDS), "avg_hr", round(est, 1)
    if sess.get("working_sets"):
        vol_level = _max_level(_level(sess["volume_kg"] or 0, P.VOLUME_THRESHOLDS),
                               _level(sess["working_sets"], P.SETS_THRESHOLDS))
        if level is None or LEVEL_RANK[vol_level] > LEVEL_RANK[level]:
            level, basis, value = vol_level, "volume", sess["volume_kg"]
    if level is None:
        minutes = sess.get("duration_min") or 0
        level = _level(minutes, P.DURATION_THRESHOLDS[sess["_cat"]]) if minutes else "light"
        basis, value = ("duration", minutes) if minutes else ("none", None)
    if (sess.get("duration_min") or P.MIN_SESSION_MIN) < P.MIN_SESSION_MIN:
        level = "light"
    sess.update(intensity=level, intensity_label=intensity_label(level), intensity_basis=basis,
                intensity_value=value)


BASIS_ZH = {"trimp": "按心率（TRIMP）", "avg_hr": "按平均心率估", "volume": "按力量训练量", "duration": "按时长", "none": "没有数据"}
BASIS_EN = {"trimp": "By heart rate (TRIMP)", "avg_hr": "Estimated from average HR", "volume": "By strength volume",
            "duration": "By duration", "none": "No data"}

# ---------------------------------------------------------------- display language (issue #42)
# Stored names stay Chinese (name_zh, AI tools); an English request gets the type part of a name in English. A name that is
# not a type name (a user's override, a SparkyFitness session or exercise name) is the user's own text and stays as is.
_TYPE_OF_ZH = {zh: t for t, zh in P.TYPE_NAMES.items()}
SOURCE_EN = {"快捷指令": "Shortcut", "Apple 健康": "Apple Health"}


def intensity_label(level: str) -> str:
    return P.INTENSITY_LABELS_EN[level] if is_en() else P.INTENSITY_LABELS[level]


def display_name(name: str | None) -> str | None:
    """'力量训练' / '力量训练 · Push day' -> 'Strength training' / 'Strength training · Push day' when the request is English."""
    if not name or not is_en():
        return name
    head, sep, rest = name.partition(" · ")
    t = _TYPE_OF_ZH.get(head)
    return P.TYPE_NAMES_EN.get(t, head) + sep + rest if t else name


def _localize(sess: dict) -> None:
    if not is_en():
        return
    sess["name"] = display_name(sess["name"])
    sess["type_name"] = P.TYPE_NAMES_EN.get(sess["type"], sess["type_name"])
    head, sep, rest = sess["source_label"].partition(" + ")
    sess["source_label"] = tl(head, SOURCE_EN) + sep + rest


def _r(x, nd=0):
    return None if x is None else (round(x) if nd == 0 else round(x, nd))


# ---------------------------------------------------------------- per-day summary
DAILY_METRICS = {"active_energy_burned": "active_kcal", "basal_energy_burned": "basal_kcal",
                 "apple_exercise_time": "exercise_min", "step_count": "steps"}


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def workout_intensity(w: dict) -> str:
    """The intensity of one workouts row on its own (no strength sets merged): `classify` on the calendar's view of it
    (2.0.0: the per-workout intensity the phone uploads, importer.workout_days)."""
    sess = {"trimp": w.get("trimp"), "avg_hr": _r(w.get("avg_hr")), "hr_max": w.get("hr_max_used"),
            "duration_min": round(w["duration_min"]), "working_sets": None, "volume_kg": None, "_cat": _category(w["type"])}
    classify(sess, None)
    return sess["intensity"]


def _instant(x) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromtimestamp(x, timezone.utc)


def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


class _Status:
    """The rules' exercise status (Ctx.status_of) of the exercises the loader saw: {exercise_id: status}."""

    def __init__(self, by_id: dict[str, str]):
        self.by_id = by_id

    def status_of(self, exercise_id: str) -> tuple[str, None, str]:
        return self.by_id.get(exercise_id, "active"), None, ""


def load(conn: psycopg.Connection, viewer_id: int, owner_id: int, start: date, end: date,
         with_exercises: bool = False) -> dict:
    """Everything `compute` needs for [start, end], read from the database and SparkyFitness (2.0.0: the phone builds the
    same from its local tables and `ext_records`). The viewer's shares decide what is read (scores, metrics, exercises)."""
    tz = _tz(conn, owner_id)
    can_scores = auth.can_view(conn, viewer_id, owner_id, "scores")
    can_metrics = auth.can_view(conn, viewer_id, owner_id, "metrics")
    workouts = load_workouts(conn, owner_id, start, end)
    sparky, exercises, sparky_error = load_sparky(conn, owner_id, start, end, with_exercises)
    imported = hevy_import.imported_sessions(conn, owner_id, start, end)
    targets = {r["external_id"]: r["dup_of"] or r["id"] for r in conn.execute(
        """SELECT id, external_id, dup_of FROM workouts WHERE user_id = %s AND source = 'hevy' AND local_date BETWEEN %s AND %s""",
        (owner_id, start, end))} if imported else {}
    status = None
    if with_exercises:
        ctx = Ctx(user_id=owner_id, day=end, exercises=exercises)
        if auth.can_view(conn, viewer_id, owner_id, "exercises"):
            ctx.meta = conn.execute("SELECT * FROM exercise_meta WHERE user_id = %s", (owner_id,)).fetchall()
        ids = {e["exercise_id"] for s in [*sparky, *imported] for e in s["exercises"] if e.get("exercise_id")}
        status = {i: ctx.status_of(i)[0] for i in sorted(ids)}
    metrics: dict[date, dict] = {}
    if can_metrics:
        for r in conn.execute("""SELECT date, metric, value FROM daily_metrics WHERE user_id = %s AND date BETWEEN %s AND %s
                                 AND metric = ANY(%s)""", (owner_id, start, end, list(DAILY_METRICS))):
            metrics.setdefault(r["date"], {})[DAILY_METRICS[r["metric"]]] = r["value"]
    scores: dict[date, dict] = {}
    if can_scores:
        scores = {r["date"]: r for r in conn.execute(
            """SELECT date, strain, muscle_load, recovery, trimp, hr_max FROM daily_scores
               WHERE user_id = %s AND date BETWEEN %s AND %s""", (owner_id, start, end + timedelta(days=1)))}
    hr_max = {r["date"]: float(r["hr_max"]) for r in conn.execute(
        "SELECT date, hr_max FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s AND hr_max IS NOT NULL",
        (owner_id, start, end))}
    birth = (conn.execute("SELECT birth_date FROM users WHERE id = %s", (owner_id,)).fetchone() or {}).get("birth_date")
    return {"timezone": str(tz), "workouts": workouts, "sparky": sparky, "sparky_error": sparky_error,
            "imported": imported, "targets": targets, "status": status, "metrics": metrics, "scores": scores,
            "hr_max": hr_max, "birth_date": birth, "can_scores": can_scores, "can_metrics": can_metrics}


def _hr_max_fallback(data: dict, d: date) -> float | None:
    """importer.hr_max_for on the loaded data: the day's scores' HRmax, else 208 − 0.7 × age."""
    hm = {_date(k): v for k, v in (data.get("hr_max") or {}).items()}
    if hm.get(d):
        return float(hm[d])
    if data.get("birth_date"):
        from ..scores import strain as SL
        return SL.hrmax_formula(SL.age_on(_date(data["birth_date"]), d))
    return None


def build(conn: psycopg.Connection, viewer_id: int, owner_id: int, start: date, end: date,
          with_exercises: bool = False) -> dict:
    """Per-day merged workouts + daily metrics + scores for [start, end]. Shared by the calendar and the single-day detail."""
    data = load(conn, viewer_id, owner_id, start, end, with_exercises)
    return compute(data, start, end, datetime.now(ZoneInfo(data["timezone"])).date())


def compute(data: dict, start: date, end: date, today: date) -> dict:
    """The pure part of build (2.0.0: the twin of src/31_workouts.js `training.compute`): data as `load` returns it, also
    JSON-like (instants as UTC seconds, dates as YYYY-MM-DD). Texts follow the request language (i18n)."""
    tz = ZoneInfo(data["timezone"])
    start, end, today = _date(start), _date(end), _date(today)
    metrics = {_date(k): v for k, v in (data.get("metrics") or {}).items()}
    scores = {_date(k): v for k, v in (data.get("scores") or {}).items()}
    targets = data.get("targets") or {}
    status = data.get("status")
    ctx = _Status(status) if status is not None else None
    imported_by_day: dict[date, list] = {}
    for s in data.get("imported") or []:
        imported_by_day.setdefault(_date(s["date"]), []).append(s)
    by_day_w: dict[date, list] = {}
    for w in data.get("workouts") or []:
        w = {**w, "start_at": _instant(w["start_at"]), "end_at": _instant(w["end_at"]), "local_date": _date(w["local_date"])}
        by_day_w.setdefault(w["local_date"], []).append(w)
    by_day_s: dict[date, list] = {}
    for s in data.get("sparky") or []:
        by_day_s.setdefault(date.fromisoformat(s["date"][:10]), []).append(s)
    days = []
    for d in _days(start, end):
        sessions = merge_day(by_day_w.get(d, []), by_day_s.get(d, []), tz, imported_by_day.get(d), targets)
        hr_max = (scores.get(d) or {}).get("hr_max")
        if hr_max is None and any(s.get("avg_hr") and not s.get("hr_max") for s in sessions):
            hr_max = _hr_max_fallback(data, d)
        for s in sessions:
            classify(s, hr_max)
            if ctx is not None:
                s["exercises"] = [_exercise_view(e, ctx) for e in s["exercises"]]
            else:
                s["exercises"] = [{"name": e.get("name"), "category": e.get("category")} for e in s["exercises"]]
            s["intensity_basis_label"] = (BASIS_EN if is_en() else BASIS_ZH).get(s["intensity_basis"])
            s.pop("_cat", None)
            _localize(s)
        m, sc = metrics.get(d, {}), scores.get(d) or {}
        intensity = _max_level(*(s["intensity"] for s in sessions)) or "rest"
        workout_kcal = sum(s["active_kcal"] or 0 for s in sessions)
        flags = {}
        if m.get("active_kcal") is not None and workout_kcal > m["active_kcal"] + 20:
            flags["daily_incomplete"] = (f"全天活动能量 {m['active_kcal']:.0f} kcal 比训练本身的 {workout_kcal:.0f} 还少："
                                         "这天的每日数据只同步了一部分")
        days.append({
            "date": d, "weekday": "一二三四五六日"[d.weekday()], "future": d > today, "today": d == today,
            "sessions": sessions, "sessions_count": len(sessions),
            "duration_min": sum(s["duration_min"] or 0 for s in sessions),
            "workout_kcal": _r(workout_kcal) if any(s["active_kcal"] for s in sessions) else None,
            "active_kcal": _r(m.get("active_kcal")), "basal_kcal": _r(m.get("basal_kcal")),
            "total_kcal": _r(m["active_kcal"] + m["basal_kcal"]) if m.get("active_kcal") is not None and m.get("basal_kcal") else None,
            "exercise_min": _r(m.get("exercise_min")), "steps": _r(m.get("steps")),
            "strain": sc.get("strain"), "muscle_load": sc.get("muscle_load"), "recovery": sc.get("recovery"),
            "recovery_next_day": (scores.get(d + timedelta(days=1)) or {}).get("recovery"),
            "intensity": intensity, "intensity_label": intensity_label(intensity),
            "types": list(dict.fromkeys(s["type"] for s in sessions)),
            "labels": list(dict.fromkeys(s["name"] for s in sessions)), "flags": flags,
        })
    hidden = [x for x, ok in (("scores", data.get("can_scores", True)), ("metrics", data.get("can_metrics", True))) if not ok]
    return {"days": days, "sparky_error": data.get("sparky_error"), "hidden": hidden, "timezone": str(tz)}


def summarize(days: list[dict]) -> dict:
    past = [d for d in days if not d["future"]]
    trained = [d for d in past if d["sessions_count"]]
    streak = best = 0
    for d in past:
        streak = streak + 1 if d["sessions_count"] else 0
        best = max(best, streak)
    strains = [d["strain"] for d in past if d["strain"] is not None]
    by_type: dict[str, int] = {}
    for d in past:
        for s in d["sessions"]:
            by_type[s["type_name"]] = by_type.get(s["type_name"], 0) + 1
    by_intensity = {lvl: sum(1 for d in past if d["intensity"] == lvl) for lvl in P.INTENSITY_LEVELS}
    kcal = [d["active_kcal"] for d in past if d["active_kcal"] is not None]
    return {"days": len(past), "training_days": len(trained), "rest_days": len(past) - len(trained),
            "sessions": sum(d["sessions_count"] for d in past),
            "total_duration_min": sum(d["duration_min"] for d in past),
            "total_active_kcal": round(sum(kcal)) if kcal else None,
            "avg_active_kcal": round(sum(kcal) / len(kcal)) if kcal else None,
            "total_workout_kcal": round(sum(d["workout_kcal"] or 0 for d in past)),
            "avg_strain": round(sum(strains) / len(strains), 1) if strains else None,
            "longest_streak": best, "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
            "by_intensity": by_intensity}


# ---------------------------------------------------------------- shared by the API / tools
CALENDAR_DAY_KEYS = ("date", "weekday", "today", "future", "sessions_count", "duration_min", "active_kcal", "workout_kcal",
                     "total_kcal", "exercise_min", "steps", "strain", "muscle_load", "recovery", "intensity",
                     "intensity_label", "types", "labels", "flags")


def month_range(month: str | None, tz: ZoneInfo) -> tuple[date, date, str]:
    if month:
        try:
            y, m = (int(x) for x in month.split("-"))
            first = date(y, m, 1)
        except ValueError:
            raise ValueError("month 要写成 YYYY-MM")
    else:
        t = datetime.now(tz).date()
        first = t.replace(day=1)
    last = first.replace(day=_cal.monthrange(first.year, first.month)[1])
    return first, last, f"{first:%Y-%m}"


def calendar(conn: psycopg.Connection, viewer_id: int, owner_id: int, month: str | None = None,
             start: date | None = None, end: date | None = None) -> dict:
    tz = _tz(conn, owner_id)
    if start or end:
        end = end or datetime.now(tz).date()
        start = start or end - timedelta(days=27)
        if end < start:
            raise ValueError("end 不能早于 start")
        if (end - start).days >= MAX_RANGE_DAYS:
            raise ValueError(f"一次最多查 {MAX_RANGE_DAYS} 天")
        label = None
    else:
        start, end, label = month_range(month, tz)
    return calendar_view(build(conn, viewer_id, owner_id, start, end), start, end, label)


def calendar_view(res: dict, start: date, end: date, label: str | None) -> dict:
    """The calendar's answer from build's (pure; the phone's `trainingCalendar`)."""
    days = []
    for d in res["days"]:
        row = {k: d[k] for k in CALENDAR_DAY_KEYS}
        row["sessions"] = [{"id": s["id"], "name": s["name"], "type": s["type"], "start": s["start"],
                            "duration_min": s["duration_min"], "active_kcal": s["active_kcal"],
                            "intensity": s["intensity"], "source": s["source"]} for s in d["sessions"]]
        days.append(row)
    return {"month": label, "start": start, "end": end, "timezone": res["timezone"], "days": days,
            "summary": summarize(res["days"]), "sparky_error": res["sparky_error"], "hidden": res["hidden"],
            "intensity_levels": [{"key": k, "label": intensity_label(k)} for k in P.INTENSITY_LEVELS],
            "version": P.TRAINING_VERSION}


def day(conn: psycopg.Connection, viewer_id: int, owner_id: int, d: date) -> dict:
    return day_view(build(conn, viewer_id, owner_id, d, d, with_exercises=True), d)


def day_view(res: dict, d: date) -> dict:
    """The single-day detail from build's answer for that day (pure; the phone's `trainingDay`)."""
    x = res["days"][0]
    daily = {k: x[k] for k in ("active_kcal", "basal_kcal", "total_kcal", "workout_kcal", "exercise_min", "steps",
                               "strain", "muscle_load", "recovery", "recovery_next_day", "intensity", "intensity_label",
                               "duration_min", "flags")}
    return {"date": d, "weekday": x["weekday"], "timezone": res["timezone"], "sessions": x["sessions"], "daily": daily,
            "sparky_error": res["sparky_error"], "hidden": res["hidden"], "version": P.TRAINING_VERSION}
