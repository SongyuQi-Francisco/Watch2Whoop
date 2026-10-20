"""「我的饮食档案」(issue #32): why the targets are what they are and how to eat, maintained by the user's AI.

Storage: part of the AI's profile card (assistant_profile, migration 285). Each section is a profile card item with a diet_*
key; its structured content is in `data` and `value` holds a short text rendering (so get_memory, get_briefing and the 「我」
page keep working). Editing an item as text (update_profile_card / PUT /memory/profile/{key}) clears `data`; the page then
shows the text. Version log: table diet_profile_versions (date, what changed, why).

Not stored here: the goals themselves (diet_goals.py over goals/nutrition.py: versioned goals, SparkyFitness before the first version) and the
energy averages (computed from daily_metrics on read). Authorization: profile sections and versions need scope memory, the
goals block nutrition, the energy block metrics (each block is null + listed in `hidden` when the viewer lacks it).
"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from .. import auth
from ..goals import nutrition
from . import diet_goals, diet_profile_params as P

# section -> (profile card key, label, sort). Sorts sit between diet_preferences (60) and training_preferences (70).
SECTIONS: dict[str, tuple[str, str, int]] = {
    "targets": ("diet_targets", "饮食目标备注", 61),
    "rationale": ("diet_rationale", "目标依据", 62),
    "constraints": ("diet_constraints", "饮食约束", 63),
    "day_plan": ("diet_day_plan", "一天怎么吃", 64),
    "adjust_rules": ("diet_adjust_rules", "调整规则", 65),
}
KEY_TO_SECTION = {k: s for s, (k, _, _) in SECTIONS.items()}
VERSION_RE = re.compile(r"^V(\d{1,3})\.(\d{1,3})$")


# ---------------------------------------------------------------- validation and text rendering
def _txt(v, what: str, required: bool = True) -> str | None:
    v = (str(v).strip() if v is not None else "")
    if not v:
        if required:
            raise ValueError(f"{what} 不能为空")
        return None
    if len(v) > P.MAX_TEXT:
        raise ValueError(f"{what} 太长（{len(v)} 字），每条不超过 {P.MAX_TEXT} 字")
    return v


def _num(v, what: str, lo: float, hi: float) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} 要是数字")
    if not lo <= x <= hi:
        raise ValueError(f"{what} 超出范围 {lo:g}–{hi:g}")
    return int(x) if x.is_integer() else round(x, 1)


def _items(v, what: str) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError(f"{what} 要是数组")
    if len(v) > P.MAX_ITEMS:
        raise ValueError(f"{what} 最多 {P.MAX_ITEMS} 条")
    return v


def _clean(section: str, d) -> dict:
    """Validate and normalize one section as the AI gives it; returns the stored `data`."""
    if section in ("constraints", "adjust_rules"):
        a, b = ("rule", "basis") if section == "constraints" else ("when", "action")
        items = [{a: _txt(x.get(a), a), b: _txt(x.get(b), b)}
                 for x in _items(d, section) if isinstance(x, dict)]
        if not items:
            raise ValueError(f"{section} 至少一条")
        return {"items": items}
    if not isinstance(d, dict):
        raise ValueError(f"{section} 要是对象")
    if section == "targets":
        out = {"steps": _num(d.get("steps"), "steps", 0, 50000), "water_ml": _num(d.get("water_ml"), "water_ml", 0, 8000),
               "note": _txt(d.get("note"), "note", required=False)}
    elif section == "rationale":
        cps = []
        for c in _items(d.get("checkpoints"), "checkpoints"):
            try:
                day = date.fromisoformat(str(c.get("date")))
            except ValueError:
                raise ValueError("checkpoints.date 要是 YYYY-MM-DD")
            cps.append({"date": day.isoformat(), "weight_kg": _num(c.get("weight_kg"), "weight_kg", 20, 400),
                        "note": _txt(c.get("note"), "note", required=False)})
        out = {"goal_weight_kg": _num(d.get("goal_weight_kg"), "goal_weight_kg", 20, 400),
               "checkpoints": sorted(cps, key=lambda c: c["date"]), "note": _txt(d.get("note"), "note", required=False)}
    elif section == "day_plan":
        meals = [{"meal": _txt(m.get("meal"), "meal"), "foods": _txt(m.get("foods"), "foods"),
                  "kcal": _num(m.get("kcal"), "kcal", 0, 5000), "protein_g": _num(m.get("protein_g"), "protein_g", 0, 400)}
                 for m in _items(d.get("meals"), "meals") if isinstance(m, dict)]
        staples = [s for s in (_txt(x, "staples", required=False) for x in _items(d.get("staples"), "staples")) if s]
        out = {"meals": meals, "staples": staples}
    else:
        raise ValueError(f"没有这一块：{section}")
    if not any(v not in (None, [], "") for v in out.values()):
        raise ValueError(f"{section} 是空的")
    return out


def _g(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def render(section: str, d: dict) -> str:
    """Short text of one section for the profile card value (what get_memory / get_briefing show)."""
    if section == "targets":
        parts = [f"步数 {_g(d['steps'])}" if d.get("steps") is not None else "",
                 f"喝水 {_g(d['water_ml'])} ml" if d.get("water_ml") is not None else "", d.get("note") or ""]
        return "；".join(p for p in parts if p)
    if section == "rationale":
        parts = [f"目标体重 {_g(d['goal_weight_kg'])} kg" if d.get("goal_weight_kg") is not None else ""]
        if d.get("checkpoints"):
            parts.append("检查点 " + "、".join(f"{c['date']} {_g(c['weight_kg'])} kg" if c.get("weight_kg") is not None
                                               else c["date"] for c in d["checkpoints"]))
        parts.append(d.get("note") or "")
        return "；".join(p for p in parts if p)
    if section == "constraints":
        return "；".join(f"{x['rule']}（依据：{x['basis']}）" if x.get("basis") else x["rule"] for x in d["items"])
    if section == "adjust_rules":
        return "；".join(f"{x['when']} → {x['action']}" for x in d["items"])
    if section == "day_plan":
        lines = [f"{m['meal']}：{m['foods']}" + (f"（约 {_g(m['kcal'])} kcal）" if m.get("kcal") is not None else "")
                 for m in d.get("meals", [])]
        if d.get("staples"):
            lines.append("常备：" + "、".join(d["staples"]))
        return "\n".join(lines)
    return ""


# ---------------------------------------------------------------- versions
def _bump(latest: str | None) -> str:
    m = VERSION_RE.match(latest or "")
    return f"V{m[1]}.{int(m[2]) + 1}" if m else "V1.0"


def latest_version(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute("""SELECT version, changed_on, changes, reason, sections, source, created_at FROM diet_profile_versions
                           WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT 1""", (user_id,)).fetchone()


def _local_today(conn, user_id: int) -> date:
    tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
    return datetime.now(ZoneInfo(tz)).date()


# ---------------------------------------------------------------- write
def update(conn: psycopg.Connection, user_id: int, sections: dict, changes: str | None = None, reason: str | None = None,
           version: str | None = None, source: str = "assistant") -> dict:
    """Replace the given sections (each whole) and, when `changes` is given, append a version entry.
    sections: {targets | rationale | constraints | day_plan | adjust_rules: data}; None values are left alone.
    Returns {updated: [section…], version: {...} | None}."""
    given = {s: d for s, d in (sections or {}).items() if d is not None}
    unknown = set(given) - set(SECTIONS)
    if unknown:
        raise ValueError(f"没有这些块：{', '.join(sorted(unknown))}（可用：{', '.join(SECTIONS)}）")
    changes = _txt(changes, "changes", required=False)
    reason = _txt(reason, "reason", required=False)
    if not given and not changes:
        raise ValueError("什么都没给：至少改一块，或给 changes 记一条版本")
    if version is not None and not VERSION_RE.match(version.strip()):
        raise ValueError("version 写成 V2.3 这样；null = 自动 +0.1")
    if (version or reason) and not changes:
        raise ValueError("给了 version / reason 就要写 changes（改了什么）")
    cleaned = {s: _clean(s, d) for s, d in given.items()}  # validate everything before writing anything
    for s, data in cleaned.items():
        key, label, sort = SECTIONS[s]
        conn.execute(
            """INSERT INTO assistant_profile (user_id, key, label, value, sort, source, data) VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (user_id, key) DO UPDATE SET label = EXCLUDED.label, value = EXCLUDED.value, sort = EXCLUDED.sort,
                 source = EXCLUDED.source, data = EXCLUDED.data, updated_at = now()""",
            (user_id, key, label, render(s, data) or "—", sort, source, Jsonb(data)))
    ver = None
    if changes:
        last = latest_version(conn, user_id)
        v = version.strip() if version else _bump(last and last["version"])
        if conn.execute("SELECT 1 FROM diet_profile_versions WHERE user_id = %s AND version = %s", (user_id, v)).fetchone():
            raise ValueError(f"版本 {v} 已经有了；null = 自动 +0.1")
        ver = conn.execute(
            """INSERT INTO diet_profile_versions (user_id, version, changed_on, changes, reason, sections, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING version, changed_on, changes, reason, sections""",
            (user_id, v, _local_today(conn, user_id), changes, reason, list(cleaned), source)).fetchone()
    return {"updated": list(cleaned), "version": ver}


# ---------------------------------------------------------------- read
def energy(conn: psycopg.Connection, user_id: int, today: date) -> dict:
    """Average daily basal / active / total energy over the last ENERGY_WINDOW_DAYS complete days (daily_metrics)."""
    end = today - timedelta(days=1)
    start = end - timedelta(days=P.ENERGY_WINDOW_DAYS - 1)
    rows = conn.execute(
        """SELECT date, metric, value FROM daily_metrics WHERE user_id = %s AND date BETWEEN %s AND %s
             AND metric IN ('basal_energy_burned', 'active_energy_burned')""", (user_id, start, end)).fetchall()
    by: dict[date, dict] = {}
    for r in rows:
        by.setdefault(r["date"], {})[r["metric"]] = r["value"]
    days = [(d["basal_energy_burned"], d["active_energy_burned"]) for d in by.values()
            if (d.get("basal_energy_burned") or 0) >= P.BASAL_MIN_KCAL and d.get("active_energy_burned") is not None]
    out = {"start": start, "end": end, "days": len(days), "basal_kcal": None, "active_kcal": None, "total_kcal": None}
    if len(days) >= P.ENERGY_MIN_DAYS:
        b = sum(x for x, _ in days) / len(days)
        a = sum(y for _, y in days) / len(days)
        out.update(basal_kcal=round(b), active_kcal=round(a), total_kcal=round(b + a))
    return out


def latest_weight(conn: psycopg.Connection, user_id: int, today: date) -> dict | None:
    return conn.execute(
        """SELECT date, value AS kg FROM daily_metrics WHERE user_id = %s AND metric = 'body_mass'
             AND date BETWEEN %s AND %s ORDER BY date DESC LIMIT 1""", (user_id, today - timedelta(days=30), today)).fetchone()


def plan_math(en: dict | None, goals: dict | None, weight: dict | None) -> dict | None:
    """Daily deficit = 4-week average total expenditure − average daily target; expected weekly change from KCAL_PER_KG."""
    if not en or en.get("total_kcal") is None or not goals or not goals.get("avg_kcal"):
        return None
    deficit = en["total_kcal"] - goals["avg_kcal"]
    loss = deficit * 7 / P.KCAL_PER_KG
    return {"target_kcal": round(goals["avg_kcal"]), "deficit_kcal": round(deficit), "loss_kg_per_week": round(loss, 2),
            "weight_kg": weight and round(weight["kg"], 1),
            "fast": bool(weight) and loss > weight["kg"] * P.FAST_LOSS_SHARE_PER_WEEK}


def _sections(conn, user_id: int, tz: ZoneInfo) -> list[dict]:
    rows = conn.execute(
        """SELECT key, label, value, data, source, updated_at FROM assistant_profile
           WHERE user_id = %s AND key = ANY(%s) ORDER BY sort, key""", (user_id, list(KEY_TO_SECTION))).fetchall()
    return [{"section": KEY_TO_SECTION[r["key"]], "key": r["key"], "label": r["label"], "data": r["data"],
             "text": r["value"], "source": r["source"], "updated_on": r["updated_at"].astimezone(tz).date()} for r in rows]


def summary(conn: psycopg.Connection, user_id: int, viewer_id: int | None = None) -> dict:
    """What the one-line entry needs: diet profile version, the goal version in effect (goals/nutrition.py, only with
    scope nutrition), last update (local date; a new goal version counts), how many sections. No external calls."""
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    row = conn.execute(
        """SELECT count(*) AS n, max(updated_at) AS at FROM assistant_profile WHERE user_id = %s AND key = ANY(%s)""",
        (user_id, list(KEY_TO_SECTION))).fetchone()
    last = latest_version(conn, user_id)
    stamps = [t for t in (row["at"], last and last["created_at"]) if t]
    goal = None
    if viewer_id is None or auth.can_view(conn, viewer_id, user_id, "nutrition"):
        goal = nutrition.version_for(nutrition.versions(conn, user_id), datetime.now(tz).date())
        if goal:
            stamps.append(goal["created_at"])
    return {"version": last and last["version"], "sections": row["n"],
            "goal_version_id": goal and goal["id"], "goal_effective_from": goal and goal["effective_from"],
            "updated_on": max(stamps).astimezone(tz).date() if stamps else None}


def view(conn: psycopg.Connection, viewer_id: int, owner_id: int, client=None, versions: int | None = None) -> dict:
    """Full diet profile for the page / the AI. Caller has checked scope memory; goals and energy are checked here."""
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (owner_id,)).fetchone()["timezone"])
    today = datetime.now(tz).date()
    hidden = []
    goals = en = weight = None
    if auth.can_view(conn, viewer_id, owner_id, "nutrition"):
        goals = diet_goals.current(conn, owner_id, today, client)
    else:
        hidden.append("goals")
    if auth.can_view(conn, viewer_id, owner_id, "metrics"):
        en, weight = energy(conn, owner_id, today), latest_weight(conn, owner_id, today)
    else:
        hidden.append("energy")
    vers = conn.execute(
        """SELECT version, changed_on, changes, reason, sections, source FROM diet_profile_versions WHERE user_id = %s
           ORDER BY created_at DESC, id DESC""" + (" LIMIT %s" if versions else ""),
        (owner_id, versions) if versions else (owner_id,)).fetchall()
    return {**summary(conn, owner_id, viewer_id), "self": viewer_id == owner_id, "goals": goals,
            "goal_history": None if goals is None else diet_goals.history(conn, owner_id)[:versions or None],
            "energy": en, "weight": weight, "plan": plan_math(en, goals, weight),
            "sections": _sections(conn, owner_id, tz), "versions": vers, "hidden": hidden}
