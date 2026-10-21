"""Per-user diet constraints (issue #76): what this user should limit or avoid, with the basis and the date it applies from.

Readers: the rule engine (rules/nutrition.frequency_caps), recipe ranking and exclusion (assistant/recipes.py), the
grocery list notes, the status card, meal logging warnings and the AI (get_diet_constraints, get_briefing). A user
without rows has no diet constraint: nothing is limited, penalised or excluded for them.

Storage: table diet_constraints (migration 450), versioned: a change closes the row in effect and adds a new one, so the
rows of one (kind, target) are its history. Vocabulary and reference values: constraint_params.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import constraint_params as P


def _today(conn: psycopg.Connection, user_id: int) -> date:
    row = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    return datetime.now(ZoneInfo((row or {}).get("timezone") or "Australia/Sydney")).date()


def norm(s: str | None) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


# ---------------------------------------------------------------- reading
@dataclass
class Constraints:
    """The constraints in effect for one user on one day, ready for the code that applies them."""
    rows: list[dict] = field(default_factory=list)

    def _one(self, kind: str) -> dict | None:
        return next((r for r in self.rows if r["kind"] == kind), None)

    @property
    def empty(self) -> bool:
        return not self.rows

    @property
    def red_meat_g(self) -> float | None:
        r = self._one("red_meat_g_per_week")
        return r and r["value"]

    @property
    def red_meat_meals(self) -> float | None:
        r = self._one("red_meat_meals_per_week")
        return r and r["value"]

    @property
    def low_purine(self) -> bool:
        return self._one("low_purine") is not None

    @property
    def low_sat_fat(self) -> bool:
        return self._one("low_saturated_fat") is not None

    @property
    def avoid_alcohol(self) -> bool:
        return self._one("avoid_alcohol") is not None

    @property
    def food_limits(self) -> list[dict]:
        return [{"target": r["target"], "names": names(r), "max": r["value"], "basis": r.get("basis")}
                for r in self.rows if r["kind"] == "food_times_per_week"]

    def food_limit_hit(self, food: str) -> dict | None:
        """The food_times_per_week constraint this food name counts for, if any."""
        s = norm(food)
        return next((f for f in self.food_limits if any(n and n in s for n in f["names"])), None)


def names(row: dict) -> list[str]:
    t = row.get("target") or ""
    return [norm(x) for x in (t, *P.FOOD_ALIASES.get(t, ()), *(row.get("match") or [])) if x]


def rows_in_effect(conn: psycopg.Connection, user_id: int, day: date) -> list[dict]:
    return conn.execute(
        """SELECT * FROM diet_constraints WHERE user_id = %s AND effective_from <= %s
           AND (effective_to IS NULL OR effective_to > %s) ORDER BY kind, target""", (user_id, day, day)).fetchall()


def for_user(conn: psycopg.Connection, user_id: int, day: date | None = None) -> Constraints:
    return Constraints(rows_in_effect(conn, user_id, day or _today(conn, user_id)))


def view(r: dict) -> dict:
    k = P.KINDS.get(r["kind"], {})
    zh, en = k.get("label", (r["kind"], r["kind"]))
    unit = k.get("unit")
    label = L(zh, en) + (f"：{r['target']}" if r.get("target") else "")
    value = None if r["value"] is None else (int(r["value"]) if float(r["value"]).is_integer() else r["value"])
    return {"id": r["id"], "kind": r["kind"], "label": label, "target": r.get("target") or None,
            "match": list(r.get("match") or []) or None, "value": value, "unit": L(*unit) if unit else None,
            "text": L(*k["short"]).format(v=value, t=r.get("target") or "") if k.get("short") and value is not None else label,
            "basis": r.get("basis"), "effective_from": r["effective_from"].isoformat(),
            "effective_to": r["effective_to"].isoformat() if r.get("effective_to") else None, "source": r["source"]}


def listing(conn: psycopg.Connection, user_id: int, history: bool = False) -> dict:
    """In effect today (+ every version when history=True) and the vocabulary with reference values."""
    today = _today(conn, user_id)
    out = {"date": today.isoformat(), "constraints": [view(r) for r in rows_in_effect(conn, user_id, today)],
           "kinds": [{"kind": k, "label": L(*v["label"]), "unit": L(*v["unit"]) if v["unit"] else None,
                      "needs_value": v["range"] is not None, "needs_target": bool(v.get("target")),
                      "reference": v["reference"]} for k, v in P.KINDS.items()]}
    if history:
        out["history"] = [view(r) for r in conn.execute(
            "SELECT * FROM diet_constraints WHERE user_id = %s ORDER BY kind, target, effective_from, id", (user_id,))]
    return out


# ---------------------------------------------------------------- writing (versioned)
def _check(kind: str, value, target: str | None) -> tuple[float | None, str]:
    k = P.KINDS.get(kind)
    if k is None:
        raise ValueError(f"没有这种约束 {kind!r}；可选：{'、'.join(P.KINDS)}")
    target = (target or "").strip()[:40]
    if k.get("target") and not target:
        raise ValueError(f"{kind} 要写是哪种食物（target）")
    if not k.get("target"):
        target = ""
    if k["range"] is None:
        return None, target
    if value is None:
        raise ValueError(f"{kind} 要给一个数值（{k['unit'][0]}）")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{kind} 的数值要是数字")
    lo, hi = k["range"]
    if not lo <= v <= hi:
        raise ValueError(f"{kind} 超出范围 {lo}–{hi}")
    return v, target


def set_constraint(conn: psycopg.Connection, user_id: int, kind: str, value=None, target: str | None = None,
                   match: list[str] | None = None, basis: str | None = None, effective_from: date | None = None,
                   source: str = "assistant") -> dict:
    """Add or change one constraint from effective_from (default today). The row in effect is closed, not overwritten."""
    v, target = _check(kind, value, target)
    match = [m.strip()[:40] for m in (match or []) if m and m.strip()][:10]
    basis = (basis or "").strip()[:300] or None
    day = effective_from or _today(conn, user_id)
    cur = conn.execute("""SELECT * FROM diet_constraints WHERE user_id = %s AND kind = %s AND target = %s
                          AND effective_to IS NULL""", (user_id, kind, target)).fetchone()
    if cur is not None:
        if cur["value"] == v and list(cur["match"] or []) == match and (basis is None or cur["basis"] == basis):
            return {"changed": False, "constraint": view(cur)}
        if day < cur["effective_from"]:
            raise ValueError(f"生效日期不能早于现在这条的生效日期 {cur['effective_from']}")
        conn.execute("UPDATE diet_constraints SET effective_to = %s WHERE id = %s", (day, cur["id"]))
    row = conn.execute(
        """INSERT INTO diet_constraints (user_id, kind, target, match, value, basis, effective_from, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (user_id, kind, target, match, v, basis if basis is not None else (cur or {}).get("basis"), day, source)).fetchone()
    return {"changed": True, "before": view(cur) if cur else None, "constraint": view(row)}


def end_constraint(conn: psycopg.Connection, user_id: int, kind: str, target: str | None = None,
                   effective_from: date | None = None) -> dict:
    """Stop a constraint from effective_from (default today); its rows stay as history."""
    if kind not in P.KINDS:
        raise ValueError(f"没有这种约束 {kind!r}；可选：{'、'.join(P.KINDS)}")
    target = (target or "").strip()[:40] if P.KINDS[kind].get("target") else ""
    day = effective_from or _today(conn, user_id)
    cur = conn.execute("""SELECT * FROM diet_constraints WHERE user_id = %s AND kind = %s AND target = %s
                          AND effective_to IS NULL""", (user_id, kind, target)).fetchone()
    if cur is None:
        return {"changed": False, "reason": "没有生效中的这条约束"}
    conn.execute("UPDATE diet_constraints SET effective_to = %s WHERE id = %s",
                 (max(day, cur["effective_from"]), cur["id"]))
    return {"changed": True, "ended": view({**cur, "effective_to": max(day, cur["effective_from"])})}


# ---------------------------------------------------------------- carrying existing data over (once)
_LE = r"(?:≤|<=|＜=|不超过|最多|上限)"
_RED_G = re.compile(r"红肉[^；;。\n，,]{0,8}?" + _LE + r"\s*(\d{1,4}(?:\.\d+)?)\s*(?:g|克)\s*/?\s*(?:每)?\s*周")
_RED_MEALS = re.compile(r"红肉[^；;。\n]{0,12}?" + _LE + r"\s*(\d{1,2})\s*顿\s*/?\s*(?:每)?\s*周")
_FOOD_TIMES = re.compile(r"([一-鿿A-Za-z]{1,10})\s*" + _LE + r"\s*(\d{1,2})\s*次\s*/?\s*(?:每)?\s*周")
_FLAGS = (("low_purine", re.compile(r"低嘌呤")), ("low_saturated_fat", re.compile(r"低饱和脂肪")),
          ("avoid_alcohol", re.compile(r"避免酒精|不喝酒|戒酒|禁酒")))


def parse_profile_text(text: str) -> list[dict]:
    """Structured constraints written in a profile card text (generic patterns; the values come from the text)."""
    out: list[dict] = []
    t = text or ""
    if m := _RED_G.search(t):
        out.append({"kind": "red_meat_g_per_week", "value": float(m.group(1))})
    if m := _RED_MEALS.search(t):
        out.append({"kind": "red_meat_meals_per_week", "value": float(m.group(1))})
    for m in _FOOD_TIMES.finditer(t):
        food = re.sub(r"^(?:和|与|及|、)", "", m.group(1))
        if food and "红肉" not in food:
            out.append({"kind": "food_times_per_week", "target": food, "value": float(m.group(2))})
    out += [{"kind": k} for k, rx in _FLAGS if rx.search(t)]
    return out


def carry_over(conn: psycopg.Connection) -> list[dict]:
    """db.DATA_STEPS: for every user whose own profile card has a health_constraints item, write the structured
    constraints found in it (effective from that item's date). Users without one start with no constraint."""
    done = []
    for r in conn.execute("""SELECT user_id, value, updated_at FROM assistant_profile WHERE key = %s AND value IS NOT NULL""",
                          (P.CARRY_OVER_ITEM,)).fetchall():
        found = parse_profile_text(r["value"])
        since = (r["updated_at"].date() if r.get("updated_at") else _today(conn, r["user_id"]))
        for c in found:
            set_constraint(conn, r["user_id"], c["kind"], c.get("value"), c.get("target"), basis=P.CARRY_OVER_BASIS[0],
                           effective_from=since, source="migration")
        done.append({"user_id": r["user_id"], "constraints": [c["kind"] + (f":{c['target']}" if c.get("target") else "")
                                                             for c in found]})
    return done
