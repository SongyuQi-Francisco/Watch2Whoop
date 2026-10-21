"""Aggregate data for the Today page (PWA): one request returns everything a screen needs, reusing functions from scores / rules / records / sparky_client.

Authorisation is per block (section): each block maps to a shares scope, and when viewing a family member's data an unauthorised block returns {"error": ..., "code": "forbidden"}
while other blocks display as usual; when SparkyFitness can't be read that block returns {"error": ..., "code": "sparky"}. Dates are local dates in the viewed user's timezone.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from .. import auth, queries, sparky_client
from ..config import settings
from ..i18n import L
from ..importers import ow as ow_imp, shortcut as sc_imp
from ..rules import params as RP, store as rules_store
from ..rules.context import Ctx
from ..scores import store as scores_store
from ..trends import series as trend_series

RECOVERY_GREEN = 67          # >= 67 green, 34-66 yellow, < 34 red (the bands in system design §7)
SCOPE_LABELS = {"scores": "评分", "reminders": "提醒", "training": "训练", "nutrition": "饮食", "sleep": "睡眠",
                "metrics": "每日指标", "weight": "体重", "journal": "日记", "events": "身体事件",
                "supplements": "补剂", "exercises": "动作限制"}

# When a score is empty, look through flags in this order to find the reason
FIELD_FLAGS = {
    "recovery": ("no_sleep", "hrv_missing", "baseline_insufficient"),
    "sleep_score": ("no_sleep",), "sleep_actual_min": ("no_sleep",), "sleep_need_min": ("no_sleep",),
    "sleep_debt_min": ("no_sleep",), "sleep_performance": ("no_sleep",),
    "hrv_night_ms": ("no_sleep", "hrv_missing"), "rhr_night": ("no_sleep",),
    "strain": ("no_raw_hr", "day_in_progress"), "muscle_load": ("muscle_load_unavailable",),
    "ctl": ("load_missing",), "atl": ("load_missing",), "tsb": ("load_missing",),
}
FIELD_FALLBACK = {"muscle_load": "这天 SparkyFitness 里没有力量训练记录"}
SCORE_FIELDS = ("recovery", "sleep_score", "sleep_performance", "sleep_actual_min", "sleep_need_min", "sleep_debt_min",
                "hrv_night_ms", "rhr_night", "strain", "muscle_load", "ctl", "atl", "tsb")


def user_tz(conn: psycopg.Connection, user_id: int) -> ZoneInfo:
    row = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    return ZoneInfo((row or {}).get("timezone") or "Australia/Sydney")


def local_today(conn: psycopg.Connection, user_id: int) -> date:
    return datetime.now(user_tz(conn, user_id)).date()


def forbidden(scope: str) -> dict:
    return {"error": f"没有授权查看{SCOPE_LABELS.get(scope, scope)}", "code": "forbidden"}


def section(conn, viewer_id: int, owner_id: int, scope: str, fn):
    """One block of data: check authorisation first; a SparkyFitness error only affects this block."""
    if not auth.can_view(conn, viewer_id, owner_id, scope):
        return forbidden(scope)
    try:
        return fn()
    except sparky_client.SparkyError as e:
        return {"error": str(e), "code": "sparky"}


def has_any_share(conn, viewer_id: int, owner_id: int) -> bool:
    return viewer_id == owner_id or conn.execute(
        "SELECT 1 FROM shares WHERE owner_id = %s AND viewer_id = %s AND (expires_at IS NULL OR expires_at > now())",
        (owner_id, viewer_id)).fetchone() is not None


def viewable(conn, viewer: dict) -> list[dict]:
    """People you can view: yourself + family members who granted you access in shares (not expired)."""
    out = [{"id": viewer["id"], "display_name": viewer["display_name"], "self": True, "scopes": None}]
    for r in conn.execute(
            """SELECT s.owner_id, u.display_name, s.scopes FROM shares s JOIN users u ON u.id = s.owner_id
               WHERE s.viewer_id = %s AND s.owner_id <> %s AND (s.expires_at IS NULL OR s.expires_at > now())
               ORDER BY u.display_name""", (viewer["id"], viewer["id"])):
        out.append({"id": r["owner_id"], "display_name": r["display_name"], "self": False, "scopes": r["scopes"]})
    return out


# ---------------------------------------------------------------- scores
def reasons(row: dict | None, fields=SCORE_FIELDS) -> dict[str, str]:
    """Each empty score -> its reason (the explanation in flags)."""
    out = {}
    for f in fields:
        if row is not None and row.get(f) is not None:
            continue
        if row is None:
            out[f] = "这天还没有计算评分（每小时 :10 自动算一次）"
            continue
        flags = row.get("flags") or {}
        hit = next((flags[c] for c in FIELD_FLAGS.get(f, ()) if c in flags), None)
        out[f] = hit or FIELD_FALLBACK.get(f) or "没算出来（flags 里没有写原因）"
    return out


def recovery_zone(v) -> str | None:
    if v is None:
        return None
    return "green" if v >= RECOVERY_GREEN else "yellow" if v >= RP.RECOVERY_RED else "red"


RECOVERY_WORD = {"green": "适合高强度", "yellow": "按计划训练", "red": "以恢复为主"}
LOAD_STATUS = {"fresh": "新鲜", "balanced": "平衡", "loading": "积累疲劳", "fatigued": "疲劳"}
# English twins of the labels the Today page shows verbatim (issue #42); reasons / load_text stay Chinese (AI-facing, or
# pattern-matched by the page)
RECOVERY_WORD_EN = {"green": "Ready to push", "yellow": "Train as planned", "red": "Focus on recovery"}
LOAD_STATUS_EN = {"fresh": "Fresh", "balanced": "Balanced", "loading": "Building fatigue", "fatigued": "Fatigued"}


def _recovery_word(zone: str | None) -> str | None:
    return L(RECOVERY_WORD[zone], RECOVERY_WORD_EN[zone]) if zone in RECOVERY_WORD else None


def load_status(row: dict | None) -> dict | None:
    """Small status label for CTL / ATL / TSB (the Today page shows only this; tap to see the numbers and load_text)."""
    if row is None or row.get("tsb") is None:
        return None
    tsb = row["tsb"]
    key = "fresh" if tsb >= 10 else "balanced" if tsb >= -10 else "loading" if tsb >= -30 else "fatigued"
    return {"key": key, "label": L(LOAD_STATUS[key], LOAD_STATUS_EN[key])}


def load_text(row: dict | None, missing_days: int) -> str:
    """One-sentence reading of CTL / ATL / TSB (uses yesterday's values = the state at the start of today)."""
    if row is None or row.get("tsb") is None:
        return "还没有 CTL / ATL：要有连续几周的负荷数据"
    ctl, atl, tsb = row["ctl"], row["atl"], row["tsb"]
    if tsb >= 10:
        s = "状态新鲜：最近一周的负荷低于长期水平，适合安排高质量训练"
    elif tsb >= -10:
        s = "负荷平衡：最近一周和长期水平差不多"
    elif tsb >= -30:
        s = "在积累疲劳：最近一周练得比平时多，属于正常训练期，注意睡眠和吃够"
    else:
        s = "疲劳偏高：最近一周负荷远高于长期水平，考虑减量或多休息一天"
    text = f"{s}（体能 CTL {ctl:.0f}、疲劳 ATL {atl:.0f}、TSB {tsb:+.0f}）"
    if missing_days:
        text += f"。最近 7 天有 {missing_days} 天没有负荷数据（CTL / ATL 沿用前一天），新鲜度可能被高估"
    return text


def scores_today(conn, owner_id: int, day: date) -> dict:
    rows = {r["date"]: r for r in scores_store.scores(conn, owner_id, day - timedelta(days=6), day)}
    today, yesterday = rows.get(day), rows.get(day - timedelta(days=1))
    last = conn.execute(
        """SELECT date, recovery, sleep_score FROM daily_scores
           WHERE user_id = %s AND date < %s AND recovery IS NOT NULL ORDER BY date DESC LIMIT 1""",
        (owner_id, day)).fetchone()
    last_sleep = conn.execute(
        """SELECT date, sleep_score, sleep_actual_min, sleep_need_min, sleep_debt_min FROM daily_scores
           WHERE user_id = %s AND date < %s AND sleep_score IS NOT NULL ORDER BY date DESC LIMIT 1""",
        (owner_id, day)).fetchone()
    computed = conn.execute("SELECT max(computed_at) AS t FROM daily_scores WHERE user_id = %s", (owner_id,)).fetchone()
    missing_load = sum(1 for d, r in rows.items() if d < day and "load_missing" in (r.get("flags") or {}))
    return {
        "date": day, "today": today, "yesterday": yesterday,
        "today_reasons": reasons(today), "yesterday_reasons": reasons(yesterday),
        "recovery_zone": recovery_zone((today or {}).get("recovery")),
        "recovery_word": _recovery_word(recovery_zone((today or {}).get("recovery"))),
        "load_status": load_status(yesterday),
        "last_recovery": None if (today or {}).get("recovery") is not None else
        (last and {**last, "zone": recovery_zone(last["recovery"])}),
        "last_sleep": None if (today or {}).get("sleep_score") is not None else last_sleep,
        "load_text": load_text(yesterday, missing_load), "load_missing_days": missing_load,
        "computed_at": computed["t"] if computed else None,
    }


# ---------------------------------------------------------------- training (SparkyFitness plan + exercise restrictions + load suggestions)
def _sets_text(sets: list[dict], rep_goal=None) -> str:
    working = [s for s in sets if (s.get("type") or "").lower() not in RP.WARMUP_SET_TYPES]
    if not working:
        return ""
    n = len(working)
    if any(s.get("duration_s") for s in working) and not any(s.get("reps") for s in working):
        secs = max(s.get("duration_s") or 0 for s in working)
        return L(f"{n} 组 × {secs:g} 秒", f"{n} sets × {secs:g} s")
    reps = [s["reps"] for s in working if s.get("reps")]
    lo, hi = (min(reps), max(rep_goal or 0, max(reps))) if reps else (None, None)
    text = f"{n} × {lo}" + (f"–{hi}" if hi and hi != lo else "") if lo else L(f"{n} 组", f"{n} sets")
    w = next((s["weight_kg"] for s in working if s.get("weight_kg")), None)
    return text + (f" · {w:g} kg" if w else "")


def _exercise_view(ctx: Ctx, ex_id: str, name: str | None, sets: list[dict], rep_goal, suggestions: dict,
                   with_meta: bool) -> dict:
    status, meta, basis = ctx.status_of(ex_id) if with_meta else ("active", None, "")
    if not with_meta:  # Without exercises authorisation, rely only on the description markers from SparkyFitness
        desc = ((ctx.exercises or {}).get(ex_id) or {}).get("description") or ""
        status = next((s for m, s in RP.SPARKY_STATUS_MARKERS.items() if desc.startswith(m)), "active")
    desc = ((ctx.exercises or {}).get(ex_id) or {}).get("description")
    return {"exercise_id": ex_id, "name": ctx.exercise_name(ex_id, name), "status": status,
            "reason": (meta or {}).get("reason"), "cues": (meta or {}).get("cues") or (desc if status != "active" else None),
            "sets_text": _sets_text(sets, rep_goal), "suggestions": suggestions.get(ex_id, [])}


def _training_ctx(conn, viewer_id: int, owner_id: int, day: date):
    """SparkyFitness exercise library, templates and weekly plan + exercise_meta (only with the exercises scope)."""
    client = sparky_client.client_for_user(conn, owner_id)
    ctx = Ctx(user_id=owner_id, day=day)
    ctx.exercises = {e["id"]: e for e in sparky_client.exercises(client)}
    ctx.presets = sparky_client.workout_presets(client)
    ctx.plans = sparky_client.workout_plans(client)
    with_meta = auth.can_view(conn, viewer_id, owner_id, "exercises")
    if with_meta:
        ctx.meta = conn.execute("SELECT * FROM exercise_meta WHERE user_id = %s", (owner_id,)).fetchall()
    return client, ctx, with_meta


def _load_suggestions(conn, viewer_id: int, owner_id: int, start: date, end: date) -> dict[str, list]:
    """Increase / decrease load suggestions from the rule engine (progression reminders), by exercise id."""
    out: dict[str, list] = {}
    if auth.can_view(conn, viewer_id, owner_id, "reminders"):
        for r in rules_store.list_reminders(conn, owner_id, start, end, ["new", "seen"], ["progression"]):
            ex_id = (r.get("evidence") or {}).get("exercise_id")
            if ex_id:
                out.setdefault(ex_id, []).append(
                    {"id": r["id"], "level": r["level"], "title": r["title"], "date": r.get("date"),
                     "exercise": (r.get("evidence") or {}).get("exercise"),
                     "next_weight_kg": (r.get("evidence") or {}).get("next_weight_kg")})
    return out


def _planned_view(ctx: Ctx, day: date, suggestions: dict, with_meta: bool) -> list[dict]:
    planned = []
    for item in ctx.planned_for(day):
        if item["kind"] == "preset":
            p = item["preset"]
            planned.append({"name": p["name"], "exercises": [
                _exercise_view(ctx, x["exercise_id"], x.get("name"), x["sets"], x.get("rep_goal"), suggestions, with_meta)
                for x in p["exercises"]]})
        else:
            planned.append({"name": item["name"], "exercises": [
                _exercise_view(ctx, item["exercise_id"], item["name"], [], None, suggestions, with_meta)]})
    return planned


def training_today(conn, viewer_id: int, owner_id: int, day: date) -> dict:
    from ..training import plans as tr_plans
    if tr_plans._active_plan_row(conn, owner_id) is not None:
        wk = tr_plans.get_week_plan(conn, viewer_id, owner_id, day, detail=True)
        day_entry = next((d for d in wk["days"] if d["date"] == day), {"planned": [], "rest_day": True})
        logged, sparky_error = [], None
        try:   # what was logged comes from SparkyFitness; when it cannot be read the Body Engine plan is still shown
            client, ctx, with_meta = _training_ctx(conn, viewer_id, owner_id, day)
            for s in sparky_client.training_log(client, day, day):
                logged.append({"name": s["name"], "notes": s["notes"], "exercises": [
                    {**_exercise_view(ctx, e["exercise_id"], e["name"], [], None, {}, with_meta),
                     "sets_text": ", ".join(f"{x['weight_kg']:g}×{x['reps']}" if x.get("weight_kg") and x.get("reps")
                                            else f"{x['reps']} 次" if x.get("reps")
                                            else f"{x['duration_s']:g} 秒" if x.get("duration_s") else "—"
                                            for x in e["sets"])}
                    for e in s["exercises"]]})
        except sparky_client.SparkyError as e:
            sparky_error = str(e)
        return {
            "plan": wk["plan"],
            "planned": day_entry["planned"],
            "rest_day": day_entry["rest_day"],
            "logged": logged,
            "readiness": wk.get("readiness"),
            **({"sparky_error": sparky_error} if sparky_error else {}),
        }
    client, ctx, with_meta = _training_ctx(conn, viewer_id, owner_id, day)
    suggestions = _load_suggestions(conn, viewer_id, owner_id, day, day)
    plan = ctx.active_plan()
    planned = _planned_view(ctx, day, suggestions, with_meta)
    logged = []
    for s in sparky_client.training_log(client, day, day):
        logged.append({"name": s["name"], "notes": s["notes"], "exercises": [
            {**_exercise_view(ctx, e["exercise_id"], e["name"], [], None, {}, with_meta),
             "sets_text": ", ".join(f"{x['weight_kg']:g}×{x['reps']}" if x.get("weight_kg") and x.get("reps")
                                    else L(f"{x['reps']} 次", f"{x['reps']} reps") if x.get("reps")
                                    else L(f"{x['duration_s']:g} 秒", f"{x['duration_s']:g} s") if x.get("duration_s") else "—"
                                    for x in e["sets"])}
            for e in s["exercises"]]})
    return {"plan": plan and {"name": plan["name"], "start_date": plan["start_date"]}, "planned": planned,
            "rest_day": plan is not None and not planned, "logged": logged}


def training_week(conn, viewer_id: int, owner_id: int, day: date) -> dict:
    """The Record tab's training page: this week's plan (Monday to Sunday, from training_plans or SparkyFitness weekly plan),
    the load suggestions of the last 7 days (one per exercise, newest first) and the caution / banned exercises in this week's plan.
    What was actually trained comes from /training/calendar (same week)."""
    from ..training import plans as tr_plans
    if tr_plans._active_plan_row(conn, owner_id) is not None:
        return tr_plans.get_week_plan(conn, viewer_id, owner_id, day, detail=True)
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    _client, ctx, with_meta = _training_ctx(conn, viewer_id, owner_id, day)
    sugg = _load_suggestions(conn, viewer_id, owner_id, day - timedelta(days=6), day)
    days, cautions, seen = [], [], set()
    for k in range(7):
        d = start + timedelta(days=k)
        planned = _planned_view(ctx, d, sugg, with_meta)
        days.append({"date": d, "planned": planned, "rest_day": ctx.active_plan(d) is not None and not planned})
        for p in planned:
            for x in p["exercises"]:
                if x["status"] != "active" and x["exercise_id"] not in seen:
                    seen.add(x["exercise_id"])
                    cautions.append({k2: x[k2] for k2 in ("exercise_id", "name", "status", "reason", "cues")})
    suggestions = []
    for ex_id, items in sugg.items():
        latest = max(items, key=lambda r: (str(r.get("date") or ""), r["id"]))
        suggestions.append({"exercise_id": ex_id, "name": ctx.exercise_name(ex_id, latest.get("exercise")), **latest})
    suggestions.sort(key=lambda r: (str(r.get("date") or ""), r["id"]), reverse=True)
    plan = ctx.active_plan(day)
    with_scores = auth.can_view(conn, viewer_id, owner_id, "scores")
    return {"start": start, "end": end, "date": day,
            "plan": plan and {"name": plan["name"], "start_date": plan["start_date"]},
            "days": days, "suggestions": suggestions, "cautions": cautions,
            "readiness": tr_plans.readiness_advice(conn, owner_id, day) if with_scores else None,
            "available_splits": tr_plans.list_splits()}


def nutrition_today(conn, owner_id: int, day: date) -> dict:
    """Goal = the version in effect that day (goals/nutrition.py); sugar totals carry coverage (nutrition/daily.py)."""
    from ..nutrition import daily
    n = daily.nutrition(conn, owner_id, day, day)
    d = n["days"][0]
    return {"date": day, "logged": d["logged"], "goal": d["goal"],
            "intake": {k: d[k] for k in ("calories", "protein_g", "carbs_g", "fat_g", "sugar_g", "added_sugar_g",
                                         "fiber_g", "sodium_mg")},
            "known": d["known"], "adherence": d["adherence"], "entries": n.get("entries", [])}


def today(conn, viewer: dict, owner_id: int, day: date | None = None) -> dict:
    day = day or local_today(conn, owner_id)
    vid = viewer["id"]
    owner = conn.execute("SELECT id, display_name, timezone FROM users WHERE id = %s", (owner_id,)).fetchone()
    return {
        "user": owner, "date": day, "self": vid == owner_id,
        "generated_at": datetime.now(user_tz(conn, owner_id)).isoformat(timespec="seconds"),
        "scores": section(conn, vid, owner_id, "scores", lambda: scores_today(conn, owner_id, day)),
        "sleep": section(conn, vid, owner_id, "sleep", lambda: sleep_today(conn, owner_id, day)),
        "reminders": section(conn, vid, owner_id, "reminders",
                             lambda: rules_store.list_reminders(conn, owner_id, day, day, ["new", "seen"], None, True)),
        "training": section(conn, vid, owner_id, "training", lambda: training_today(conn, vid, owner_id, day)),
        "nutrition": section(conn, vid, owner_id, "nutrition", lambda: nutrition_today(conn, owner_id, day)),
    }


# ---------------------------------------------------------------- Today page sleep: bedtime / wake time, minutes per stage, stage chart
HYPNO_STAGES = ("awake", "rem", "core", "deep", "unspecified")   # Stage chart top to bottom (the order on the Apple Watch sleep page)
MERGE_GAP = timedelta(seconds=90)                                # Adjacent segments of the same stage less than 90 s apart are merged into one
LATEST_LOOKBACK_DAYS = 7


def ow_segments(conn, owner_id: int, tz: ZoneInfo, start: datetime, end: datetime) -> list:
    """OW sleep-stage segments (uses importers/ow.sleep_segments like the scores module; XML duplicates of the same night are removed)."""
    row = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (owner_id,)).fetchone()
    if not row or not row["ow_user_id"]:
        return []
    from ..ow_client import OWClient
    sessions = list(OWClient(timeout=10).paginate(
        f"/users/{row['ow_user_id']}/events/sleep", include="stages", limit=100,
        start_date=(start.astimezone(tz).date() - timedelta(days=1)).isoformat(),
        end_date=(end.astimezone(tz).date() + timedelta(days=1)).isoformat()))
    return ow_imp.sleep_segments(sessions, tz)


def shortcut_segments(conn, owner_id: int, start: datetime, end: datetime) -> list:
    """Sleep-stage segments from this user's imported iOS Shortcut samples (table shortcut_samples), for nights OW lacks.
    Reads only the samples overlapping this night: re-parsing the whole upload archive took ~2 s per Today page load
    and was not scoped to the user."""
    rows = conn.execute(
        """SELECT type, start_at, end_at, value_text, source FROM shortcut_samples
           WHERE user_id = %s AND type = 'Sleep' AND start_at < %s AND end_at > %s ORDER BY start_at""",
        (owner_id, end, start)).fetchall()
    return sc_imp.build_segments(rows)


def export_segments(conn, owner_id: int, start: datetime, end: datetime) -> list:
    """Sleep-stage segments from this user's imported Apple Health history (table export_samples, history/), for nights
    neither OW nor the Shortcut has."""
    from ..importers.common import Segment
    rows = conn.execute(
        """SELECT start_at, end_at, stage, source FROM export_samples
           WHERE user_id = %s AND kind = 'sleep' AND start_at < %s AND end_at > %s ORDER BY start_at""",
        (owner_id, end, start)).fetchall()
    return [Segment(r["start_at"], r["end_at"], r["stage"], r["source"]) for r in rows]


def hypnogram(segments: list, start: datetime, end: datetime, tz: ZoneInfo) -> list[dict]:
    """Clip segments to [start, end] and merge adjacent same-stage ones; offset_min is minutes since falling asleep (for drawing the timeline)."""
    # Convert everything to UTC before comparing / subtracting: two datetimes with the same ZoneInfo subtract by wall-clock time, which is off by an hour on the DST night
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    utc = [(x.start.astimezone(timezone.utc), x.end.astimezone(timezone.utc), x.stage) for x in segments]
    out: list[dict] = []
    for s0, e0, stage in sorted((x for x in utc if x[1] > start and x[0] < end), key=lambda x: (x[0], x[1])):
        a, b = max(s0, start), min(e0, end)
        if b <= a:
            continue
        if out and out[-1]["stage"] == stage and a - out[-1]["_end"] <= MERGE_GAP:
            out[-1]["_end"] = max(out[-1]["_end"], b)
            continue
        if out and a < out[-1]["_end"]:   # Overlaps the previous segment (different devices): start from where the previous one ends
            a = out[-1]["_end"]
            if b <= a:
                continue
        out.append({"stage": stage, "_start": a, "_end": b})
    return [{"stage": x["stage"], "start": x["_start"].astimezone(tz).strftime("%H:%M"),
             "end": x["_end"].astimezone(tz).strftime("%H:%M"),
             "offset_min": round((x["_start"] - start).total_seconds() / 60, 1),
             "minutes": round((x["_end"] - x["_start"]).total_seconds() / 60, 1)} for x in out]


def recent_bedtimes(conn, owner_id: int, day: date, tz: ZoneInfo, nights: int = 7) -> list[dict]:
    """Bedtime / wake time of the main sleep for the last `nights` nights (by wake date day − nights + 1 … day), for the bedtime-goal mini chart.
    night = wake date − 1 (the date of that night); bed_offset_min = minutes counted from 18:00 of `night` (so a bedtime after midnight can still be compared with the goal)."""
    out = []
    for r in conn.execute(
            """SELECT DISTINCT ON (wake_date) wake_date, start_at, end_at FROM sleep_sessions
               WHERE user_id = %s AND is_main AND wake_date BETWEEN %s AND %s
               ORDER BY wake_date, asleep_min DESC NULLS LAST""", (owner_id, day - timedelta(days=nights - 1), day)):
        night = r["wake_date"] - timedelta(days=1)
        evening = datetime.combine(night, datetime.min.time(), tz) + timedelta(hours=18)
        out.append({"date": r["wake_date"], "night": night,
                    "bedtime": r["start_at"].astimezone(tz).strftime("%H:%M"),
                    "wake": r["end_at"].astimezone(tz).strftime("%H:%M"),
                    "bed_offset_min": round((r["start_at"] - evening).total_seconds() / 60)})
    return out


def sleep_today(conn, owner_id: int, day: date) -> dict:
    """The main sleep that ended on wake date `day` (if none, the most recent night within the last 7 days, latest=True): bedtime / wake time (user timezone),
    minutes per stage (sleep_sessions), stage chart (OW first, otherwise the Shortcut archive)."""
    tz = user_tz(conn, owner_id)
    row = conn.execute(
        """SELECT wake_date, start_at, end_at, asleep_min, deep_min, core_min, rem_min, awake_min, unspecified_min,
                  source, device FROM sleep_sessions
           WHERE user_id = %s AND is_main AND wake_date BETWEEN %s AND %s
           ORDER BY wake_date DESC, asleep_min DESC NULLS LAST LIMIT 1""",
        (owner_id, day - timedelta(days=LATEST_LOOKBACK_DAYS), day)).fetchone()
    recent = recent_bedtimes(conn, owner_id, day, tz)
    if row is None:
        return {"date": day, "main": None, "reason": "最近 7 天没有手表睡眠数据", "recent": recent}
    start, end = row["start_at"], row["end_at"]
    stages, source, problems = [], None, []
    for name, fetch in (("ow", lambda: ow_segments(conn, owner_id, tz, start, end)),
                        ("shortcut", lambda: shortcut_segments(conn, owner_id, start, end)),
                        ("export", lambda: export_segments(conn, owner_id, start, end))):
        try:
            stages = hypnogram(fetch(), start, end, tz)
        except Exception as e:  # OW is off, has no key, or the archive is unreadable: try the next source
            problems.append(f"{name}: {type(e).__name__}")
            continue
        if stages:
            source = name
            break
    return {"date": row["wake_date"], "latest": row["wake_date"] != day, "main": {
                "start": start.astimezone(tz).strftime("%H:%M"), "end": end.astimezone(tz).strftime("%H:%M"),
                "start_at": start.astimezone(tz).isoformat(), "end_at": end.astimezone(tz).isoformat(),
                "in_bed_min": round((end - start).total_seconds() / 60),
                **{k: row[k] for k in ("asleep_min", "deep_min", "core_min", "rem_min", "awake_min", "unspecified_min")},
                "source": row["source"]},
            "stages": stages, "stages_source": source, "recent": recent,
            "stages_reason": None if stages else ("这晚没有阶段明细" + (f"（{'；'.join(problems)}）" if problems else ""))}


# ---------------------------------------------------------------- log page: frequent journal items
def frequent_journal(conn, owner_id: int, end: date | None = None, days: int = 30, limit: int = 8,
                     min_items: int = 6) -> list[dict]:
    """Journal items logged most in the last `days` days (count of "yes" entries or entries with a value), up to `limit`; if fewer than min_items,
    pad in the items' default order (sort). Only enabled items count."""
    end = end or local_today(conn, owner_id)
    items = conn.execute("""SELECT key, label_zh, kind, unit, category, sort FROM journal_items
                            WHERE user_id = %s AND active ORDER BY sort, key""", (owner_id,)).fetchall()
    counts = {r["key"]: r["n"] for r in conn.execute(
        """SELECT key, count(*) AS n FROM journal_entries WHERE user_id = %s AND date BETWEEN %s AND %s AND value <> 0
           GROUP BY key""", (owner_id, end - timedelta(days=days - 1), end))}
    by_key = {i["key"]: i for i in items}
    ranked = sorted((k for k in counts if k in by_key), key=lambda k: (-counts[k], by_key[k]["sort"] or 0, k))[:limit]
    for i in items:
        if len(ranked) >= min_items:
            break
        if i["key"] not in ranked:
            ranked.append(i["key"])
    return [{**by_key[k], "count": counts.get(k, 0), "days": days} for k in ranked]


# ---------------------------------------------------------------- sleep: last N nights
def sleep_nights(conn, viewer: dict, owner_id: int, end: date | None = None, days: int = 14) -> dict:
    end = end or local_today(conn, owner_id)
    start = end - timedelta(days=days - 1)
    tz = user_tz(conn, owner_id)
    sessions = queries.sleep_sessions(conn, owner_id, start, end)
    can_scores = auth.can_view(conn, viewer["id"], owner_id, "scores")
    scores = {r["date"]: r for r in scores_store.scores(conn, owner_id, start, end)} if can_scores else {}
    hm = lambda t: t.astimezone(tz).strftime("%H:%M")
    nights = []
    for k in range(days):
        d = start + timedelta(days=k)
        day_sessions = [s for s in sessions if s["wake_date"] == d]
        main = max((s for s in day_sessions if s["is_main"]), key=lambda s: s["asleep_min"] or 0, default=None)
        naps = [s for s in day_sessions if s is not main]
        sc = scores.get(d)
        night = {"date": d, "main": None, "nap_min": sum(s["asleep_min"] or 0 for s in naps),
                 "naps": [{"start": hm(s["start_at"]), "end": hm(s["end_at"]), "asleep_min": s["asleep_min"]} for s in naps],
                 "sleep_score": (sc or {}).get("sleep_score"), "sleep_actual_min": (sc or {}).get("sleep_actual_min"),
                 "sleep_need_min": (sc or {}).get("sleep_need_min"), "sleep_debt_min": (sc or {}).get("sleep_debt_min"),
                 "reason": None}
        if main is not None:
            night["main"] = {"start": hm(main["start_at"]), "end": hm(main["end_at"]),
                             "start_at": main["start_at"].astimezone(tz).isoformat(),
                             "end_at": main["end_at"].astimezone(tz).isoformat(),
                             **{k2: main[k2] for k2 in ("asleep_min", "deep_min", "core_min", "rem_min", "awake_min",
                                                        "unspecified_min")},
                             "source": main["source"], "device": main["device"]}
        if can_scores and night["sleep_score"] is None:
            night["reason"] = reasons(sc, ("sleep_score",))["sleep_score"]
        elif main is None:
            night["reason"] = "没有手表睡眠数据" if night["sleep_score"] is None else "有睡眠分，但没有睡眠段明细"
        nights.append(night)
    return {"start": start, "end": end, "nights": nights, "scores_visible": can_scores}


# ---------------------------------------------------------------- trends (all series and status cards in trends/series.py)
weights = trend_series.weights   # also used by explain/personal.py


def trends(conn, viewer: dict, owner_id: int, end: date | None = None, days: int = 90) -> dict:
    today = local_today(conn, owner_id)
    return trend_series.build(conn, viewer, owner_id, end or today, days, today, user_tz(conn, owner_id))


# ---------------------------------------------------------------- look-back calendar: which days of a month have data
DAYS_SOURCES = (  # (scope, field, SQL for the earliest date, SQL for dates with data in this month)
    ("sleep", "sleep",
     "SELECT min(wake_date) AS d FROM sleep_sessions WHERE user_id = %s AND is_main",
     "SELECT DISTINCT wake_date AS d FROM sleep_sessions WHERE user_id = %s AND is_main AND wake_date BETWEEN %s AND %s"),
    ("training", "training",
     "SELECT min(local_date) AS d FROM workouts WHERE user_id = %s AND dup_of IS NULL AND excluded IS NULL",
     """SELECT DISTINCT local_date AS d FROM workouts
        WHERE user_id = %s AND dup_of IS NULL AND excluded IS NULL AND local_date BETWEEN %s AND %s"""),
    ("metrics", "metrics",
     "SELECT min(date) AS d FROM daily_metrics WHERE user_id = %s",
     "SELECT DISTINCT date AS d FROM daily_metrics WHERE user_id = %s AND date BETWEEN %s AND %s"),
)


def month_days(conn, viewer: dict, owner_id: int, month: str) -> dict:
    """Month calendar for the Today page date picker: for each day in `month` (YYYY-MM) whether scores / sleep / training / daily metrics exist, plus the recovery score and colour.
    Each category is authorised by its own shares scope (unauthorised fields are null and listed in `hidden`); `first` is the earliest day among the data you may see
    (Apple Health history imports can reach back several years), and the frontend uses it to limit paging backwards. Reads only this database, not SparkyFitness."""
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    end = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    vid, today = viewer["id"], local_today(conn, owner_id)
    hidden, firsts = [], []
    scores = {}
    if auth.can_view(conn, vid, owner_id, "scores"):
        scores = {r["date"]: r for r in conn.execute(
            """SELECT date, recovery, sleep_score, strain FROM daily_scores
               WHERE user_id = %s AND date BETWEEN %s AND %s""", (owner_id, start, end))}
        firsts.append(conn.execute("SELECT min(date) AS d FROM daily_scores WHERE user_id = %s", (owner_id,)).fetchone()["d"])
    else:
        hidden.append("scores")
    present = {}
    for scope, field, first_sql, month_sql in DAYS_SOURCES:
        if not auth.can_view(conn, vid, owner_id, scope):
            hidden.append(scope)
            continue
        present[field] = {r["d"] for r in conn.execute(month_sql, (owner_id, start, end))}
        firsts.append(conn.execute(first_sql, (owner_id,)).fetchone()["d"])
    out = []
    for k in range((end - start).days + 1):
        d = start + timedelta(days=k)
        sc = scores.get(d)
        row = {"date": d, "future": d > today,
               "scores": None if "scores" in hidden else sc is not None,
               **{f: (None if f not in present else d in present[f]) for _, f, _, _ in DAYS_SOURCES},
               "recovery": (sc or {}).get("recovery"), "sleep_score": (sc or {}).get("sleep_score"),
               "strain": (sc or {}).get("strain"), "zone": recovery_zone((sc or {}).get("recovery"))}
        row["has_data"] = any(row[f] for f in ("scores", "sleep", "training", "metrics"))
        out.append(row)
    firsts = [f for f in firsts if f is not None]
    return {"month": month, "start": start, "end": end, "today": today,
            "first": min(firsts) if firsts else None, "days": out, "hidden": hidden}
