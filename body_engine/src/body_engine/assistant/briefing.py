"""Opening briefing for an AI conversation (tool get_briefing, docs/ai-assistant-plan.md §9.13).

One call instead of get_profile + get_memory + get_scores + get_reminders + get_training_calendar + get_exercise_rules at the
start of every conversation: who the user is, the latest scores, the last 7 days, active reminders, the status card, the gist of the
profile card and recent observations, banned / caution exercises. About 1–1.5k tokens; details come from the specific tools.

- Authorization per section (like the Today page): viewing a family member only shows the sections their shares allow.
- Cache: only the caller's own briefing, in this process, per user. A cached copy is used while (local date, data fingerprint) is
  unchanged and it is younger than CACHE_TTL_S; any write tool of that user drops it (assistant/tools.run_tool). The fingerprint
  covers the tables the briefing reads; the TTL covers SparkyFitness (strength sessions, weight in the status card), which is outside
  the database. "now" is filled in on every call.
- Root rules (ai/root_rules.md): every briefing carries `system_rules` (version, the caller's feedback mode, full text) — the one
  place every AI reliably reads — and, for one's own briefing, `feedback_updates` (status changes of the caller's feedback
  reports, each shown once). Both are added outside the cache.
"""
import threading
import time as _time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth, queries, sparky_client
from ..ai import root_rules
from ..feedback import store as feedback_store
from ..records import store as records_store
from ..rules import store as rules_store
from . import memory, status

CACHE_TTL_S = 600
CACHE_MAX_USERS = 512
# Profile card items shown (their first N characters) -- the ones nearly every answer depends on; the rest are listed by key
# (full text: get_memory)
CARD_KEYS = {"communication": 90, "health_constraints": 90, "injuries": 90, "current_plan": 90, "goals": 60}
OBS_RECENT = 5
OBS_CHARS = 60
RECOVERY_GREEN, RECOVERY_RED = 67, 34

_lock = threading.Lock()
_cache: "OrderedDict[int, tuple[tuple, float, dict]]" = OrderedDict()


def invalidate(user_id: int) -> None:
    with _lock:
        _cache.pop(user_id, None)


def clear() -> None:
    with _lock:
        _cache.clear()


def _cut(text: str | None, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rstrip("，、；。 ") + "…"


def _zone(recovery) -> str | None:
    if recovery is None:
        return None
    return "绿" if recovery >= RECOVERY_GREEN else "红" if recovery < RECOVERY_RED else "黄"


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _fingerprint(conn: psycopg.Connection, user_id: int, since: date) -> tuple:
    row = conn.execute(
        """SELECT
             (SELECT row(display_name, timezone, birth_date, sex, height_cm, language)::text FROM users WHERE id = %(u)s) AS u,
             (SELECT row(count(*), max(computed_at))::text FROM daily_scores WHERE user_id = %(u)s AND date >= %(s)s) AS sc,
             (SELECT row(count(*), max(id), max(end_at))::text FROM sleep_sessions WHERE user_id = %(u)s AND wake_date >= %(s)s) AS sl,
             (SELECT row(count(*), max(updated_at))::text FROM workouts WHERE user_id = %(u)s AND start_at >= %(s)s::date - 1) AS w,
             (SELECT row(count(*), max(updated_at), max(status_changed_at))::text FROM reminders
               WHERE user_id = %(u)s AND date >= %(s)s) AS r,
             (SELECT row(count(*), max(updated_at))::text FROM assistant_profile WHERE user_id = %(u)s) AS p,
             (SELECT row(count(*), max(id), max(valid_until))::text FROM assistant_observations WHERE user_id = %(u)s) AS o,
             (SELECT row(count(*), max(updated_at))::text FROM assistant_status WHERE user_id = %(u)s) AS st,
             (SELECT row(count(*), max(updated_at))::text FROM exercise_meta WHERE user_id = %(u)s) AS ex,
             (SELECT row(count(*), max(id), max(effective_to))::text FROM diet_constraints WHERE user_id = %(u)s) AS dc,
             (SELECT row(count(*), max(updated_at), max(best_before))::text FROM pantry_items WHERE user_id = %(u)s) AS pa""",
        {"u": user_id, "s": since}).fetchone()
    return tuple(row.values())


# ---------------------------------------------------------------- sections
def _me(conn, uid: int) -> dict:
    p = queries.profile(conn, uid) or {}
    return {k: p.get(k) for k in ("display_name", "age", "sex", "height_cm", "language")}


def _scores(conn, uid: int, today: date) -> dict:
    rows = conn.execute(
        """SELECT date, recovery, sleep_score, sleep_actual_min, sleep_debt_min,
                  round(exp(hrv_night_ln)::numeric, 1)::float AS hrv_ms, rhr_night, strain, tsb
           FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date""",
        (uid, today - timedelta(days=67), today)).fetchall()
    last7 = [r for r in rows if r["date"] > today - timedelta(days=7)]
    base = [r for r in rows if r["date"] <= today - timedelta(days=7)]
    latest = next((r for r in reversed(rows) if r["recovery"] is not None), None)
    out: dict = {}
    if latest:
        out["latest"] = {"date": latest["date"], "recovery": latest["recovery"], "zone": _zone(latest["recovery"]),
                         "sleep_h": round(latest["sleep_actual_min"] / 60, 1) if latest["sleep_actual_min"] else None,
                         "sleep_debt_min": latest["sleep_debt_min"], "hrv_ms": latest["hrv_ms"], "rhr": latest["rhr_night"],
                         "tsb": latest["tsb"]}
        if latest["date"] != today:
            out["latest"]["note"] = "今天还没出分（没睡 / 没同步），这是最近一天"
    out["days"] = [{"date": f"{r['date']:%m-%d}", "rec": r["recovery"], "sleep_h": round(r["sleep_actual_min"] / 60, 1)
                    if r["sleep_actual_min"] else None, "debt": r["sleep_debt_min"], "hrv": r["hrv_ms"],
                    "rhr": r["rhr_night"], "strain": round(r["strain"], 1) if r["strain"] is not None else None}
                   for r in last7]
    out["avg_7d_vs_60d"] = {k: [_mean(r[f] for r in last7), _mean(r[f] for r in base)]
                            for k, f in (("recovery", "recovery"), ("hrv_ms", "hrv_ms"), ("rhr", "rhr_night"),
                                         ("strain", "strain"))}
    return out


def _bedtimes(conn, uid: int, today: date, tz: ZoneInfo) -> dict:
    return {f"{s['wake_date']:%m-%d}": f"{s['start_at'].astimezone(tz):%H:%M}–{s['end_at'].astimezone(tz):%H:%M}"
            for s in queries.sleep_sessions(conn, uid, today - timedelta(days=6), today, main_only=True)}


def _training(conn, viewer: int, uid: int, today: date) -> dict:
    from ..training import history  # imported here: training imports assistant modules indirectly
    cal = history.calendar(conn, viewer, uid, start=today - timedelta(days=27), end=today)
    s = cal["summary"]
    streak = 0
    for d in reversed(cal["days"]):
        if d["date"] == today and not d["sessions_count"]:
            continue  # today not trained (yet) does not break the streak
        if not d["sessions_count"]:
            break
        streak += 1
    last7 = []
    for d in cal["days"][-7:]:
        what = "、".join(f"{x['name']} {x['duration_min'] or 0:.0f}min" for x in d["sessions"]) or "休息"
        last7.append(f"{d['date']:%m-%d} 周{d['weekday']} {what}" + (f"（{d['intensity']}）" if d["sessions_count"] else ""))
    out = {"days_28": {"training": s["training_days"], "rest": s["rest_days"], "by_type": s["by_type"],
                       "by_intensity": {k: v for k, v in s["by_intensity"].items() if v and k != "rest"}},
           "streak_days": streak, "last_7": last7}
    if cal.get("sparky_error"):   # issue #66 FB-4: say it plainly so the AI tells the user (no admin commands)
        link = sparky_client.link_state(conn, uid)
        out["training_log"] = {"state": link["state"], "tell_user": link.get("hint") or cal["sparky_error"][:200]}
    return out


def _reminders(conn, uid: int, today: date) -> list[str]:
    rows = rules_store.list_reminders(conn, uid, today, today, ["new", "seen"], evidence=False)
    return [f"#{r['id']} [{r['level']}] {r['title']}" for r in rows]


STATUS_IN_SCORES = ("recovery", "sleep_debt", "bedtime", "sleep_hours")  # auto status items the scores section already has


def _memory(conn, uid: int, skip: tuple = (), viewer_id: int | None = None) -> dict:
    st = [s for s in status.list_status(conn, uid, viewer_id) if s["key"] not in skip]
    m = memory.get_memory(conn, uid)   # expired recent notes are already left out
    obs = [o for o in m["observations"] if o["kind"] != "recent"]
    recent = [f"#{o['id']} 到 {o['valid_until']:%m-%d} {_cut(o['text'], OBS_CHARS)}"
              for o in m["observations"] if o["kind"] == "recent"]
    return {
        "status": [f"{s['label']} {s['value']}{s.get('unit') or ''}" + (f"（{s['note']}）" if s.get("note") else "")
                   + (f" [{s['tone']}]" if s.get("tone") in ("warn", "bad") else "") for s in st],
        "profile_card": {c["key"]: _cut(c["value"], CARD_KEYS[c["key"]]) for c in m["profile_card"] if c["key"] in CARD_KEYS},
        "profile_card_more": [c["key"] for c in m["profile_card"] if c["key"] not in CARD_KEYS],
        "observations": {"total": len(obs), "recent": [f"#{o['id']} {_cut(o['text'], OBS_CHARS)}" for o in obs[-OBS_RECENT:]]},
        **({"recent_context": recent} if recent else {}),
    }


def _exercises(conn, uid: int) -> dict:
    rules = records_store.exercise_rules(conn, uid)
    return {"banned": [r["name_zh"] for r in rules["banned"]], "caution": [r["name_zh"] for r in rules["caution"]]}


def _diet(conn, uid: int) -> list[str]:
    """The user's own diet constraints in effect (issue #76); [] = none, nothing to limit."""
    from ..nutrition import constraints
    return [c["text"] for c in constraints.listing(conn, uid)["constraints"]]


def _water(conn, uid: int, today) -> dict | None:
    """Today's water so far vs the goal (issue #125); None while the rollout flag is off for this user."""
    from .. import water
    if not water.enabled(conn, uid):
        return None
    from ..water import store as water_store
    d = water_store.day_view(conn, uid, today)
    return {"ml": d["total_ml"], "goal_ml": d["goal"]["ml"]}


def _pantry_expiring(conn, uid: int) -> list[str]:
    from ..grocery import pantry_auto   # grocery imports assistant
    return pantry_auto.briefing_lines(conn, uid)


SECTIONS = (  # (key, scope)
    ("me", "profile"), ("scores", "scores"), ("bedtimes", "sleep"), ("training", "training"),
    ("reminders", "reminders"), ("memory", "memory"), ("exercises", "exercises"), ("diet_constraints", "nutrition"),
    ("water", "nutrition"),
    ("pantry_expiring", "nutrition"),
)
NEXT = "档案卡只有要点（其余项见 profile_card_more），全文 get_memory；饮食、体重不在摘要里"


def build(conn: psycopg.Connection, viewer_id: int, owner_id: int) -> dict:
    tz = ZoneInfo((conn.execute("SELECT timezone FROM users WHERE id = %s", (owner_id,)).fetchone() or {}).get("timezone")
                  or "UTC")
    today = datetime.now(tz).date()
    allowed = {scope: auth.can_view(conn, viewer_id, owner_id, scope) for _, scope in SECTIONS}
    fns = {
        "me": lambda: _me(conn, owner_id),
        "scores": lambda: _scores(conn, owner_id, today),
        "bedtimes": lambda: _bedtimes(conn, owner_id, today, tz),
        "training": lambda: _training(conn, viewer_id, owner_id, today),
        "reminders": lambda: _reminders(conn, owner_id, today),
        "memory": lambda: _memory(conn, owner_id, STATUS_IN_SCORES if allowed["scores"] else (), viewer_id),
        "exercises": lambda: _exercises(conn, owner_id),
        "diet_constraints": lambda: _diet(conn, owner_id),
        "water": lambda: _water(conn, owner_id, today),
        "pantry_expiring": lambda: _pantry_expiring(conn, owner_id),
    }
    out: dict = {"timezone": str(tz), "date": today}
    hidden = []
    for key, scope in SECTIONS:
        if not allowed[scope]:
            hidden.append(key)
            continue
        try:
            with conn.transaction():  # one failing section (e.g. SparkyFitness down) does not spoil the rest
                out[key] = fns[key]()
        except Exception as e:
            out[key] = {"error": f"{type(e).__name__}: {e}"}
    if not out.get("pantry_expiring"):   # only when something is about to expire (issue #104)
        out.pop("pantry_expiring", None)
    beds = out.pop("bedtimes", None)
    if isinstance(beds, dict) and isinstance(out.get("scores"), dict) and "error" not in beds:
        for row in out["scores"].get("days", []):  # bed–wake as one more column of the 7-day table
            row["bed"] = beds.get(row["date"])
    elif beds is not None:
        out["bedtimes"] = beds
    if hidden:
        out["hidden"] = hidden  # no shares for these sections
    out["next"] = NEXT
    return out


def _now(tz: str) -> str:
    now = datetime.now(ZoneInfo(tz))
    return f"{now:%Y-%m-%d %H:%M} 周{'一二三四五六日'[now.weekday()]}"


def _rules(conn: psycopg.Connection, viewer_id: int) -> dict:
    return {"version": root_rules.version(), "feedback_mode": feedback_store.mode(conn, viewer_id), "text": root_rules.text()}


def _updates(conn: psycopg.Connection, user_id: int) -> dict:
    try:
        with conn.transaction():
            u = feedback_store.updates_for_briefing(conn, user_id)
    except Exception:  # never let the feedback queue spoil the briefing
        return {}
    return {"feedback_updates": u} if u else {}


def get(conn: psycopg.Connection, viewer_id: int, owner_id: int) -> dict:
    """The briefing, from the cache when the caller reads their own and nothing changed; root rules always added fresh."""
    return {**_get(conn, viewer_id, owner_id), "system_rules": _rules(conn, viewer_id),
            **(_updates(conn, viewer_id) if viewer_id == owner_id else {})}


def _get(conn: psycopg.Connection, viewer_id: int, owner_id: int) -> dict:
    if viewer_id != owner_id:  # family view: never cached (sections depend on the viewer's shares)
        res = build(conn, viewer_id, owner_id)
        return {"now": _now(res["timezone"]), **res}
    # Refresh the auto status first so the fingerprint already includes it
    try:
        with conn.transaction():
            status.refresh_if_stale(conn, owner_id)
    except Exception:
        pass
    tzname = (conn.execute("SELECT timezone FROM users WHERE id = %s", (owner_id,)).fetchone() or {}).get("timezone") or "UTC"
    today = datetime.now(ZoneInfo(tzname)).date()
    key = (today, _fingerprint(conn, owner_id, today - timedelta(days=67)))
    with _lock:
        hit = _cache.get(owner_id)
        if hit and hit[0] == key and _time.monotonic() - hit[1] < CACHE_TTL_S:
            _cache.move_to_end(owner_id)
            return {"now": _now(tzname), "cached": True, **hit[2]}
    res = build(conn, viewer_id, owner_id)
    with _lock:
        _cache[owner_id] = (key, _time.monotonic(), res)
        _cache.move_to_end(owner_id)
        while len(_cache) > CACHE_MAX_USERS:
            _cache.popitem(last=False)
    return {"now": _now(tzname), **res}
