"""Nutrition goals with version history (table nutrition_goal_versions, migration 290, issue #31).

Why Body Engine and not SparkyFitness holds them: SparkyFitness has goal presets and ONE active weekly goal plan;
/goals/by-date resolves only through the active plan (dates before it fall back to the default goal), its presets have no
added-sugar field, and nothing records who changed a goal or why. So past dates cannot be resolved from it once the plan
changes. Here every change is a new row (never updated); the version in effect on a date is the latest-created one with
the greatest effective_from <= date, and its training set applies on training_weekdays, the rest set on other days.
SparkyFitness /goals/by-date is only the fallback for dates before a user's first version.

Read API for other modules (Today page cards, rules, reports, recipes): resolver() / resolve() for one date's goal,
current() for the version in effect plus upcoming ones, history() for every version. Callers handle authorization.
"""
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from .. import sparky_client
from ..i18n import is_en
from ..rules import params as rule_params
from . import params as P

FIELDS = ("calories", "protein_g", "carbs_g", "fat_g", "sat_fat_max_g", "added_sugar_max_g", "fiber_min_g")
MACROS = ("calories", "protein_g", "carbs_g", "fat_g")
DAY_TYPE_ZH = {"training": "训练日", "rest": "休息日"}
DAY_TYPE_EN = {"training": "Training day", "rest": "Rest day"}   # goal name for the Today page in English (issue #42)
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")   # ISO 1..7


def _today(conn, user_id: int) -> date:
    row = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        raise ValueError(f"没有用户 {user_id}")
    return datetime.now(ZoneInfo(row["timezone"] or "Australia/Sydney")).date()


def _num(v):
    if v is None or v == "":
        return None
    v = float(v)
    return int(v) if v.is_integer() else round(v, 1)


# ---------------------------------------------------------------- read
def versions(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """All versions, newest effective_from first (ties: newest row first)."""
    return conn.execute("""SELECT * FROM nutrition_goal_versions WHERE user_id = %s
                           ORDER BY effective_from DESC, id DESC""", (user_id,)).fetchall()


def version_for(rows: list[dict], day: date) -> dict | None:
    return next((r for r in rows if r["effective_from"] <= day), None)


def day_type(version: dict, day: date) -> str:
    return "training" if day.isoweekday() in (version["training_weekdays"] or []) else "rest"


def _version_view(r: dict, superseded: bool | None = None) -> dict:
    out = {"id": r["id"], "effective_from": r["effective_from"], "name": r["name"],
           "training": {f: r["training"].get(f) for f in FIELDS}, "rest": {f: r["rest"].get(f) for f in FIELDS},
           "training_weekdays": sorted(r["training_weekdays"] or []), "reason": r["reason"], "set_by": r["set_by"],
           "actor_user_id": r["actor_user_id"], "created_at": r["created_at"]}
    if superseded is not None:
        out["superseded"] = superseded   # an older row with the same effective_from: never in effect
    return out


def _goal_name(t: str, version_name: str | None) -> str:
    if is_en():
        return DAY_TYPE_EN[t] + (f" ({version_name})" if version_name else "")
    return DAY_TYPE_ZH[t] + (f"（{version_name}）" if version_name else "")


def _goal(day: date, version: dict | None, sparky: dict | None) -> dict:
    """One date's goal. Keys name / calories / protein_g / carbs_g / fat_g keep the shape of sparky_client.goal_for_date."""
    out = {"date": day.isoformat(), "source": None, "version_id": None, "effective_from": None, "day_type": None,
           "name": None, **{f: None for f in FIELDS}, "added_sugar_cap_default": False}
    if version is not None:
        t = day_type(version, day)
        s = version[t]
        out.update(source="body_engine", version_id=version["id"], effective_from=version["effective_from"],
                   day_type=t, name=_goal_name(t, version["name"]),
                   **{f: s.get(f) for f in FIELDS})
    elif sparky is not None:
        out.update(source="sparkyfitness", name=sparky.get("name"),
                   **{f: sparky.get(f) for f in ("calories", "protein_g", "carbs_g", "fat_g")})
    if out["added_sugar_max_g"] is None:   # no cap of one's own: the default from rules/params.py (WHO 5%)
        out.update(added_sugar_max_g=rule_params.ADDED_SUGAR_CAP_G, added_sugar_cap_default=True)
    return out


def resolver(conn: psycopg.Connection, user_id: int, client=None) -> Callable[[date], dict]:
    """day -> goal in effect that day. Loads the versions once; SparkyFitness is asked only for dates before the first
    version (once per date; unreachable = no calorie / macro goal that day, source null)."""
    rows = versions(conn, user_id)
    cache: dict[date, dict] = {}
    state = {"client": client}

    def sparky_goal(day: date) -> dict | None:
        try:
            if state["client"] is None:
                state["client"] = sparky_client.reader_for_user(conn, user_id)
            return sparky_client.goal_for_date(state["client"], day)
        except Exception:  # noqa: BLE001  not linked / unreachable: no fallback goal (and do not retry for later dates)
            state["client"] = False
            return None

    def resolve_day(day: date) -> dict:
        if day not in cache:
            v = version_for(rows, day)
            cache[day] = _goal(day, v, None if v is not None or state["client"] is False else sparky_goal(day))
        return cache[day]
    return resolve_day


def resolve(conn: psycopg.Connection, user_id: int, day: date, client=None) -> dict:
    return resolver(conn, user_id, client)(day)


def history(conn: psycopg.Connection, user_id: int) -> list[dict]:
    rows = versions(conn, user_id)
    seen: set[date] = set()
    out = []
    for r in rows:
        out.append(_version_view(r, superseded=r["effective_from"] in seen))
        seen.add(r["effective_from"])
    return out


def current(conn: psycopg.Connection, user_id: int, day: date | None = None, client=None) -> dict:
    """The goal for `day` (default today), the version in effect, both of its sets, and versions starting later."""
    day = day or _today(conn, user_id)
    rows = versions(conn, user_id)
    v = version_for(rows, day)
    later = sorted({r["effective_from"] for r in rows if r["effective_from"] > day})
    return {"date": day, "goal": resolver(conn, user_id, client)(day),
            "version": _version_view(v) if v else None,
            "upcoming": [_version_view(version_for(rows, d)) for d in later]}


def view(conn: psycopg.Connection, user_id: int, day: date | None = None, client=None) -> dict:
    """GET /goals/nutrition: current + full history (newest first)."""
    return {**current(conn, user_id, day, client), "history": history(conn, user_id)}


# ---------------------------------------------------------------- write
def _check_set(label: str, s: dict) -> tuple[dict, list[str]]:
    clean, warnings = {}, []
    for f in FIELDS:
        v = s.get(f)
        if v is None:
            clean[f] = None
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"{label}：{f} 要是数字")
        lo, hi = P.NUTRITION_RANGES[f]
        if not lo <= v <= hi:
            raise ValueError(f"{label}：{f} {v:g} 不在 {lo}–{hi} 之间")
        clean[f] = _num(v)
    missing = [f for f in MACROS if clean[f] is None]
    if missing:
        raise ValueError(f"{label}：缺 {'、'.join(missing)}（第一版要给全热量和三大营养素）")
    if clean["sat_fat_max_g"] is not None and clean["sat_fat_max_g"] > clean["fat_g"]:
        raise ValueError(f"{label}：饱和脂肪上限不能高于总脂肪")
    est = 4 * clean["protein_g"] + 4 * clean["carbs_g"] + 9 * clean["fat_g"]
    if abs(est - clean["calories"]) > P.MACRO_KCAL_TOL * clean["calories"]:
        warnings.append(f"{label}：三大营养素算出来约 {est:.0f} kcal，和目标 {clean['calories']:g} kcal 差得多")
    return clean, warnings


def set_goals(conn: psycopg.Connection, user_id: int, *, reason: str, set_by: str, actor_user_id: int | None,
              training: dict | None = None, rest: dict | None = None, training_weekdays: list[int] | None = None,
              effective_from: date | None = None, name: str | None = None, import_key: str | None = None) -> dict:
    """Add a version. Fields left null (or a whole set left null) carry over from the version in effect on
    effective_from (default today); training_weekdays null carries over too. The first version needs both sets' calories
    and macros (a set left out copies the other one). Earlier versions are never changed."""
    today = _today(conn, user_id)
    day = effective_from or today
    if import_key is None and (day < today - timedelta(days=P.GOAL_BACKDATE_DAYS)
                               or day > today + timedelta(days=P.GOAL_AHEAD_DAYS)):
        raise ValueError(f"生效日期要在 {P.GOAL_BACKDATE_DAYS} 天前到 {P.GOAL_AHEAD_DAYS} 天后之间")
    reason = (reason or "").strip()
    if not reason or len(reason) > 300:
        raise ValueError("要写为什么改（reason，1–300 字）")
    name = (name or "").strip() or None
    if name and len(name) > 40:
        raise ValueError("name 最多 40 字")
    if training is None and rest is None and training_weekdays is None:
        raise ValueError("没有要改的内容")
    base = version_for(versions(conn, user_id), day)
    if base is None:
        if training is None and rest is None:
            raise ValueError("还没有饮食目标：先给训练日 / 休息日的热量和三大营养素")
        training, rest = training or rest, rest or training
        merged = {"training": training, "rest": rest}
    else:
        merged = {t: {f: (new or {}).get(f) if (new or {}).get(f) is not None else base[t].get(f) for f in FIELDS}
                  for t, new in (("training", training), ("rest", rest))}
    sets, warnings = {}, []
    for t in ("training", "rest"):
        sets[t], w = _check_set(DAY_TYPE_ZH[t], merged[t])
        warnings += w
    weekdays = training_weekdays if training_weekdays is not None else (base["training_weekdays"] if base else [])
    if any(not isinstance(d, int) or not 1 <= d <= 7 for d in weekdays):
        raise ValueError("training_weekdays 是 1（周一）到 7（周日）")
    weekdays = sorted(set(weekdays))
    if not weekdays and sets["training"] != sets["rest"]:
        warnings.append("没有训练日（training_weekdays 为空），每天都按休息日目标")
    row = conn.execute(
        """INSERT INTO nutrition_goal_versions (user_id, effective_from, training, rest, training_weekdays, name, reason,
                                                set_by, actor_user_id, import_key)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING *""",
        (user_id, day, Jsonb(sets["training"]), Jsonb(sets["rest"]), weekdays, name or (base or {}).get("name"),
         reason, set_by, actor_user_id, import_key)).fetchone()
    if row is None:   # same import_key already imported
        return {"version": None, "skipped": "already imported", "warnings": []}
    return {"version": _version_view(row), "warnings": warnings,
            "goal_today": resolve(conn, user_id, today) if day <= today else None}


# ---------------------------------------------------------------- backfill from SparkyFitness weekly goal plans
def _preset_set(p: dict) -> dict:
    return {"calories": _num(p.get("calories")), "protein_g": _num(p.get("protein")), "carbs_g": _num(p.get("carbs")),
            "fat_g": _num(p.get("fat")), "sat_fat_max_g": _num(p.get("saturated_fat")), "added_sugar_max_g": None,
            "fiber_min_g": _num(p.get("dietary_fiber"))}


def plan_to_version(plan: dict, presets: dict[str, dict]) -> tuple[dict | None, str | None]:
    """One SparkyFitness weekly goal plan -> (version fields, None) or (None, why it is skipped).
    Two presets: the one with more calories is the training set; one preset: both sets the same."""
    by_day = [plan.get(f"{d}_preset_id") for d in WEEKDAYS]
    ids = list(dict.fromkeys(i for i in by_day if i))
    if not ids:
        return None, "计划里没有目标"
    if any(i not in presets for i in ids):
        return None, "找不到计划用的目标预设"
    if len(ids) > 2:
        return None, f"用了 {len(ids)} 套目标，只能对应训练日 / 休息日两套"
    sets = {i: _preset_set(presets[i]) for i in ids}
    if any(s[f] is None for s in sets.values() for f in MACROS):
        return None, "目标预设缺热量或三大营养素"
    train_id = max(ids, key=lambda i: sets[i]["calories"])
    rest_id = min(ids, key=lambda i: sets[i]["calories"]) if len(ids) == 2 else train_id
    weekdays = [k + 1 for k, i in enumerate(by_day) if i == train_id] if len(ids) == 2 else []
    return {"effective_from": date.fromisoformat(str(plan["start_date"])[:10]), "training": sets[train_id],
            "rest": sets[rest_id], "training_weekdays": weekdays, "name": (plan.get("plan_name") or "")[:40] or None,
            "import_key": f"sparky-plan:{plan['id']}"}, None


def import_sparky_plans(conn: psycopg.Connection, user_id: int, apply: bool = False, client=None) -> dict:
    """Backfill versions from every SparkyFitness weekly goal plan (active and inactive), oldest first. Idempotent
    (import_key per plan). apply=False only reports what would be written."""
    client = client or sparky_client.client_for_user(conn, user_id)
    presets = {p["id"]: p for p in client.get("/goal-presets") or []}
    plans = sorted(client.get("/weekly-goal-plans") or [], key=lambda p: str(p.get("start_date")))
    done = {r["import_key"] for r in conn.execute(
        "SELECT import_key FROM nutrition_goal_versions WHERE user_id = %s AND import_key IS NOT NULL", (user_id,))}
    out = {"apply": apply, "created": [], "existing": [], "skipped": []}
    for plan in plans:
        v, why = plan_to_version(plan, presets)
        label = {"plan": plan.get("plan_name"), "start_date": str(plan.get("start_date"))[:10]}
        if v is None:
            out["skipped"].append({**label, "reason": why})
            continue
        if v["import_key"] in done:
            out["existing"].append(label)
            continue
        row = {**label, "training": v["training"], "rest": v["rest"], "training_weekdays": v["training_weekdays"]}
        if apply:
            res = set_goals(conn, user_id, reason=f"从 SparkyFitness 每周目标计划「{plan.get('plan_name')}」导入",
                            set_by="import", actor_user_id=None, **v)
            row["version_id"] = (res["version"] or {}).get("id")
        out["created"].append(row)
    return out
