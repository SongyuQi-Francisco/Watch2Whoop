"""Personal profile: filled in by the user and editable any time (the admin only supplies an email when onboarding; the display name is a placeholder from the email prefix).

Fields: display_name (how to address the user), birth_date, sex (male / female / empty), height_cm, timezone, goal_weight_kg,
language (zh / en; empty = follow the device language; the Today page language and the AI reply language).
- All but goal weight are in the users table; goal weight is written to the status card goal_weight (source user; "distance to goal" uses it); clearing = deleting that item.
- Missing fields degrade gracefully everywhere: no birthday -> HRmax uses only observed values, body age is not computed; no sex -> VO2max is not compared with norms; no height -> no BMI.
- missing: items prompted during guided fill-in (display name always exists, so it does not count).
"""
from datetime import date
from zoneinfo import ZoneInfo, available_timezones

import psycopg

from ..assistant import status as status_card

SEXES = {"male", "female"}
SEX_ALIASES = {"m": "male", "男": "male", "f": "female", "女": "female"}
LANGUAGES = {"zh", "en"}
FIELDS = ("display_name", "birth_date", "sex", "height_cm", "timezone", "goal_weight_kg", "language")


def _sex(v):
    s = (v or "").strip().lower()
    s = SEX_ALIASES.get(s, s)
    return s if s in SEXES else None


def get(conn: psycopg.Connection, user_id: int) -> dict:
    u = conn.execute("SELECT display_name, birth_date, sex, height_cm, timezone, language FROM users WHERE id = %s",
                     (user_id,)).fetchone()
    goal = conn.execute("SELECT value FROM assistant_status WHERE user_id = %s AND key = %s",
                        (user_id, status_card.GOAL_KEY)).fetchone()
    try:
        g = float(goal["value"]) if goal else None
    except ValueError:
        g = None
    out = {"display_name": u["display_name"], "birth_date": u["birth_date"], "sex": _sex(u["sex"]),
           "height_cm": u["height_cm"], "timezone": u["timezone"], "goal_weight_kg": g, "language": u["language"]}
    out["missing"] = [k for k in ("birth_date", "sex", "height_cm") if out[k] is None]
    return out


def _validate(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k not in FIELDS:
            raise ValueError(f"不能改 {k}")
        if k == "display_name":
            v = (v or "").strip()
            if not 1 <= len(v) <= 30:
                raise ValueError("称呼要 1–30 个字")
        elif k == "birth_date" and v is not None:
            v = v if isinstance(v, date) else date.fromisoformat(str(v))
            today = date.today()
            if not date(1920, 1, 1) <= v <= date(today.year - 10, today.month, min(today.day, 28)):
                raise ValueError("生日不对（要在 1920 年以后、至少 10 岁）")
        elif k == "sex" and v is not None:
            v = _sex(v)
            if v is None:
                raise ValueError("性别填 male / female，或者留空")
        elif k == "height_cm" and v is not None:
            v = float(v)
            if not 100 <= v <= 230:
                raise ValueError("身高要在 100–230 cm 之间")
            v = round(v, 1)
        elif k == "timezone":
            if v not in available_timezones():
                raise ValueError("时区不对（例：Australia/Sydney、Asia/Shanghai）")
            ZoneInfo(v)
        elif k == "language" and v is not None:
            if v not in LANGUAGES:
                raise ValueError("语言填 zh / en，或者留空（跟随系统）")
        elif k == "goal_weight_kg" and v is not None:
            v = float(v)
            if not 30 <= v <= 250:
                raise ValueError("目标体重要在 30–250 kg 之间")
            v = round(v, 1)
        out[k] = v
    return out


def update(conn: psycopg.Connection, user_id: int, patch: dict) -> dict:
    p = _validate(patch)
    cols = {k: v for k, v in p.items() if k != "goal_weight_kg"}
    with conn.transaction():
        if cols:
            conn.execute(f"UPDATE users SET {', '.join(f'{k} = %s' for k in cols)} WHERE id = %s",
                         (*cols.values(), user_id))
        if "goal_weight_kg" in p:
            g = p["goal_weight_kg"]
            if g is None:
                status_card.set_items(conn, user_id, [], [status_card.GOAL_KEY], source="user")
            else:
                status_card.set_items(conn, user_id, [{
                    "key": status_card.GOAL_KEY, "label": "目标体重", "value": f"{g:g}", "unit": "kg",
                    "tone": "neutral", "trend": None, "good_direction": None, "group": "目标", "spark": None,
                    "note": None}], [], source="user")
    return get(conn, user_id)
