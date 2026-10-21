"""User corrections of watch / Shortcut workouts (issue #30): type, name, hide; and type rules for generic workouts.

- workout_overrides is keyed by (source, external_id), so importer.upsert re-applies it on every import and never overwrites it,
  even when a workout is deleted and imported again. The workouts row itself also carries the corrected type / name_zh, so the
  training calendar, weekly report, healthspan and load all count the corrected type without knowing about overrides.
- hidden = excluded 'user_hidden': gone from every view and sum, still used for duplicate detection (its copies stay hidden).
- workout_type_rules: "generic workouts on this weekday around this time are basketball" (update_workout apply_to_similar);
  applied at import to generic workouts without an override, and straight away to existing ones.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg

from . import params as P

HIDDEN = "user_hidden"


def _tz(conn, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


def load(conn: psycopg.Connection, user_id: int) -> dict:
    ovs = {(r["source"], r["external_id"]): r for r in conn.execute(
        "SELECT * FROM workout_overrides WHERE user_id = %s", (user_id,))}
    rules = conn.execute("SELECT * FROM workout_type_rules WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()
    return {"overrides": ovs, "rules": rules, "tz": _tz(conn, user_id)}


def _hour(dt: datetime, tz: ZoneInfo) -> tuple[int, float]:
    loc = dt.astimezone(tz)
    return loc.weekday(), loc.hour + loc.minute / 60


def rule_for(rules: list[dict], start_at: datetime, tz: ZoneInfo) -> dict | None:
    wd, h = _hour(start_at, tz)
    return next((r for r in rules if r["weekday"] == wd and abs(r["start_hour"] - h) <= P.RULE_HOUR_WINDOW), None)


def apply(row: dict, ctx: dict) -> dict:
    """Apply the user's override, else a type rule, to one parsed import row (in place)."""
    ov = ctx["overrides"].get((row["source"], row["external_id"]))
    if ov:
        if ov["type"]:
            row["type"] = ov["type"]
            row["name_zh"] = ov["name_zh"] or P.TYPE_NAMES.get(ov["type"], ov["type"])
        if ov["name_zh"]:
            row["name_zh"] = ov["name_zh"]
        if ov["hidden"]:
            row["excluded"] = HIDDEN
    elif row["type"] in P.GENERIC_TYPES and (r := rule_for(ctx["rules"], row["start_at"], ctx["tz"])):
        row["type"] = r["to_type"]
        row["name_zh"] = r["name_zh"] or P.TYPE_NAMES.get(r["to_type"], r["to_type"])
    return row


def _workout(conn, user_id: int, workout_id: int) -> dict:
    w = conn.execute("SELECT * FROM workouts WHERE id = %s AND user_id = %s", (workout_id, user_id)).fetchone()
    if w is None:
        raise ValueError(f"没有这次训练：{workout_id}（workout_id 在 get_training_day 里）")
    return w


def _state(conn, user_id: int, w: dict) -> dict:
    ov = conn.execute("SELECT type, name_zh, hidden FROM workout_overrides WHERE user_id = %s AND source = %s "
                      "AND external_id = %s", (user_id, w["source"], w["external_id"])).fetchone()
    return {"type": w["type"], "name_zh": w["name_zh"], "excluded": w["excluded"], "override": ov}


def _save_override(conn, user_id: int, w: dict, ov: dict | None) -> None:
    if ov is None:
        conn.execute("DELETE FROM workout_overrides WHERE user_id = %s AND source = %s AND external_id = %s",
                     (user_id, w["source"], w["external_id"]))
        return
    conn.execute("""INSERT INTO workout_overrides (user_id, source, external_id, type, name_zh, hidden)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (user_id, source, external_id) DO UPDATE SET type = EXCLUDED.type,
                      name_zh = EXCLUDED.name_zh, hidden = EXCLUDED.hidden, updated_at = now()""",
                 (user_id, w["source"], w["external_id"], ov.get("type"), ov.get("name_zh"), bool(ov.get("hidden"))))


def _set_row(conn, wid: int, type_: str, name: str, excluded: str | None) -> None:
    conn.execute("UPDATE workouts SET type = %s, name_zh = %s, excluded = %s, updated_at = now() WHERE id = %s",
                 (type_, name, excluded, wid))


def _refresh(conn, user_id: int) -> None:
    from .importer import refresh_duplicates
    refresh_duplicates(conn, user_id)


def check_type(t: str) -> str:
    t = (t or "").strip().lower()
    if t not in P.TYPE_NAMES or t in P.GENERIC_TYPES:
        raise ValueError(f"type 不认识：{t}；可用：{', '.join(k for k in P.TYPE_NAMES if k not in P.GENERIC_TYPES)}")
    return t


def update_workout(conn: psycopg.Connection, user_id: int, workout_id: int, type_: str | None, name: str | None,
                   apply_to_similar: bool = False) -> dict:
    from ..assistant import writelog
    w = _workout(conn, user_id, workout_id)
    if not type_ and not (name or "").strip():
        raise ValueError("type 和 name 至少给一个")
    name = (name or "").strip() or None
    if name and len(name) > 40:
        raise ValueError("name 最多 40 字")
    before = _state(conn, user_id, w)
    ov = dict(before["override"] or {})
    new_type = check_type(type_) if type_ else w["type"]
    if type_:
        ov["type"] = new_type
    if name:
        ov["name_zh"] = name
    elif type_:
        ov["name_zh"] = None  # the name follows the new type
    new_name = ov.get("name_zh") or P.TYPE_NAMES.get(new_type, new_type)
    _save_override(conn, user_id, w, ov)
    _set_row(conn, w["id"], new_type, new_name, w["excluded"])
    rule = None
    if apply_to_similar:
        if before["type"] not in P.GENERIC_TYPES and (before["override"] or {}).get("type") is None:
            raise ValueError("apply_to_similar 只用于手表 / 快捷指令没认出类型（其他运动）的训练")
        rule = _add_rule(conn, user_id, w, new_type, name)
    _refresh(conn, user_id)
    writelog.record(conn, "workout", "update", w["id"], before,
                    {"type": new_type, "name_zh": new_name, "rule": rule})
    return {"workout_id": w["id"], "date": str(w["local_date"]), "type": new_type, "name": new_name,
            "was": before["name_zh"], "rule": rule}


def _add_rule(conn, user_id: int, w: dict, to_type: str, name: str | None) -> dict:
    tz = _tz(conn, user_id)
    wd, h = _hour(w["start_at"], tz)
    h = round(h * 4) / 4
    r = conn.execute("""INSERT INTO workout_type_rules (user_id, weekday, start_hour, to_type, name_zh) VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (user_id, weekday, start_hour) DO UPDATE SET to_type = EXCLUDED.to_type,
                          name_zh = EXCLUDED.name_zh RETURNING id""", (user_id, wd, h, to_type, name)).fetchone()
    # existing generic workouts without an override that match: change them now (old values kept for rollback)
    changed = []
    ovs = load(conn, user_id)["overrides"]
    for x in conn.execute("SELECT id, source, external_id, type, name_zh, start_at FROM workouts WHERE user_id = %s "
                          "AND type = ANY(%s) AND id <> %s", (user_id, list(P.GENERIC_TYPES), w["id"])).fetchall():
        if (x["source"], x["external_id"]) in ovs:
            continue
        xwd, xh = _hour(x["start_at"], tz)
        if xwd == wd and abs(xh - h) <= P.RULE_HOUR_WINDOW:
            changed.append({"id": x["id"], "type": x["type"], "name_zh": x["name_zh"]})
            conn.execute("UPDATE workouts SET type = %s, name_zh = %s, updated_at = now() WHERE id = %s",
                         (to_type, name or P.TYPE_NAMES.get(to_type, to_type), x["id"]))
    return {"id": r["id"], "weekday": "一二三四五六日"[wd], "around": f"{int(h):02d}:{int(h % 1 * 60):02d}",
            "changed": changed}


def hide_workout(conn: psycopg.Connection, user_id: int, workout_id: int) -> dict:
    """Hide a wrong / duplicate workout for good (re-imports keep it hidden). Undo: rollback_write."""
    from ..assistant import writelog
    w = _workout(conn, user_id, workout_id)
    if w["excluded"] == HIDDEN:
        raise ValueError("这次训练已经删掉（隐藏）了")
    before = _state(conn, user_id, w)
    _save_override(conn, user_id, w, {**(before["override"] or {}), "hidden": True})
    _set_row(conn, w["id"], w["type"], w["name_zh"], HIDDEN)
    _refresh(conn, user_id)
    writelog.record(conn, "workout", "delete", w["id"], before, None)
    return {"deleted": {"workout_id": w["id"], "date": str(w["local_date"]), "name": w["name_zh"],
                        "start": f"{w['start_at'].astimezone(_tz(conn, user_id)):%H:%M}",
                        "duration_min": round(w["duration_min"])}}


def restore(conn: psycopg.Connection, user_id: int, workout_id: int, before: dict, after: dict | None) -> dict:
    """Rollback of update_workout / delete_workout: the previous override, type, name and visibility."""
    w = _workout(conn, user_id, workout_id)
    _save_override(conn, user_id, w, before.get("override"))
    _set_row(conn, w["id"], before["type"], before["name_zh"], before.get("excluded"))
    rule = (after or {}).get("rule")
    if rule:
        conn.execute("DELETE FROM workout_type_rules WHERE id = %s AND user_id = %s", (rule["id"], user_id))
        for x in rule["changed"]:
            conn.execute("UPDATE workouts SET type = %s, name_zh = %s, updated_at = now() WHERE id = %s AND user_id = %s",
                         (x["type"], x["name_zh"], x["id"], user_id))
    _refresh(conn, user_id)
    return {"workout_id": w["id"], "type": before["type"], "name": before["name_zh"],
            "hidden": before.get("excluded") == HIDDEN}
