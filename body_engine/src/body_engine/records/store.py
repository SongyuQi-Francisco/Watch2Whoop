"""Read/write functions for health records. The caller handles authorization (reads need can_view, writes only for oneself); this module reads/writes purely by user_id."""
import math
from datetime import date

import psycopg
from psycopg import sql

SUPPLEMENT_STATUSES = ("active", "as_needed", "paused", "stopped", "not_needed")
SUPPLEMENT_FIELDS = ("name", "dose", "timing", "ingredients", "status", "started_on", "stopped_on",
                     "rationale", "advice", "journal_key")
LAB_FIELDS = ("collected_on", "test_key", "test_name", "category", "value", "value_text", "unit",
              "ref_low", "ref_high", "flag", "lab", "note", "source", "fasting")
EVENT_FIELDS = ("occurred_at", "time_precision", "category", "body_part", "severity", "title", "note", "resolved_on")
JOURNAL_ITEM_FIELDS = ("label_zh", "kind", "unit", "category", "description", "sort", "active")
EXERCISE_FIELDS = ("name_zh", "name_en", "aliases", "status", "per_side", "cues", "reason", "app_exercise_id")


def _insert(conn, table: str, user_id: int, fields: dict, allowed: tuple, import_key: str | None = None,
            returning: str = "*") -> dict | None:
    """Insert a row; with an import_key, skip on conflict (return None) and never overwrite existing data."""
    cols = {k: v for k, v in fields.items() if k in allowed}
    cols["user_id"] = user_id
    if import_key is not None:
        cols["import_key"] = import_key
    q = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)))
    if import_key is not None:
        q += sql.SQL(" ON CONFLICT (user_id, import_key) DO NOTHING")
    q += sql.SQL(" RETURNING " + returning)
    return conn.execute(q, list(cols.values())).fetchone()


def _update(conn, table: str, where: dict, fields: dict, allowed: tuple, touch: bool = True) -> dict | None:
    cols = {k: v for k, v in fields.items() if k in allowed}
    if not cols:
        raise ValueError("没有要修改的字段")
    sets = [sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder()) for k in cols]
    if touch:
        sets.append(sql.SQL("updated_at = now()"))
    q = sql.SQL("UPDATE {} SET {} WHERE {} RETURNING *").format(
        sql.Identifier(table), sql.SQL(", ").join(sets),
        sql.SQL(" AND ").join(sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder()) for k in where))
    return conn.execute(q, [*cols.values(), *where.values()]).fetchone()


# ---------- Supplements ----------

def list_supplements(conn: psycopg.Connection, user_id: int, statuses: list[str] | None = None) -> list[dict]:
    q = "SELECT * FROM supplements WHERE user_id = %s"
    params: list = [user_id]
    if statuses:
        q += " AND status = ANY(%s)"
        params.append(statuses)
    q += """ ORDER BY array_position(ARRAY['active','as_needed','paused','stopped','not_needed'], status), id"""
    return conn.execute(q, params).fetchall()


def add_supplement(conn, user_id: int, fields: dict, import_key: str | None = None) -> dict | None:
    if fields.get("journal_key"):
        ensure_supplement_journal_item(conn, user_id, fields["journal_key"], fields["name"])
    return _insert(conn, "supplements", user_id, fields, SUPPLEMENT_FIELDS, import_key)


def update_supplement(conn, user_id: int, supplement_id: int, fields: dict) -> dict | None:
    if fields.get("journal_key"):
        name = fields.get("name") or (conn.execute("SELECT name FROM supplements WHERE id = %s AND user_id = %s",
                                                   (supplement_id, user_id)).fetchone() or {}).get("name", "")
        ensure_supplement_journal_item(conn, user_id, fields["journal_key"], name)
    return _update(conn, "supplements", {"id": supplement_id, "user_id": user_id}, fields, SUPPLEMENT_FIELDS)


def ensure_supplement_journal_item(conn, user_id: int, key: str, name: str) -> None:
    conn.execute("""INSERT INTO journal_items (user_id, key, label_zh, kind, category, sort)
                    VALUES (%s, %s, %s, 'bool', 'supplement', 10) ON CONFLICT DO NOTHING""",
                 (user_id, key, f"吃了{name}"))


# ---------- Lab results ----------

def list_labs(conn, user_id: int, start: date | None = None, end: date | None = None,
              test_keys: list[str] | None = None) -> list[dict]:
    q = "SELECT * FROM lab_results WHERE user_id = %s"
    params: list = [user_id]
    if start:
        q += " AND collected_on >= %s"
        params.append(start)
    if end:
        q += " AND collected_on <= %s"
        params.append(end)
    if test_keys:
        q += " AND (test_key = ANY(%s) OR test_name = ANY(%s))"
        params += [test_keys, test_keys]
    return conn.execute(q + " ORDER BY collected_on, category, test_key, id", params).fetchall()


def compare_to_range(r: dict) -> str:
    """Compare with the reference range: high / low / normal; with no value or no reference range, use the report's flag; with neither, unknown."""
    v, lo, hi = r.get("value"), r.get("ref_low"), r.get("ref_high")
    if v is not None and (lo is not None or hi is not None):
        if hi is not None and v > hi:
            return "high"
        if lo is not None and v < lo:
            return "low"
        return "normal"
    return {"H": "high", "L": "low", "abnormal": "abnormal", "normal": "normal"}.get(r.get("flag"), "unknown")


def lab_report(conn, user_id: int, test_keys: list[str] | None = None) -> list[dict]:
    """One entry per lab test: latest result, comparison with the reference range, past values and changes."""
    rows = list_labs(conn, user_id, test_keys=test_keys)
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r["test_key"] or r["test_name"], []).append(r)
    out = []
    for key, hist in by_key.items():
        last = hist[-1]
        status = compare_to_range(last)
        item = {
            "test_key": key, "test_name": last["test_name"], "category": last["category"],
            "latest": {"collected_on": last["collected_on"], "value": last["value"], "value_text": last["value_text"],
                       "unit": last["unit"], "ref_low": last["ref_low"], "ref_high": last["ref_high"],
                       "flag": last["flag"], "note": last["note"], "source": last["source"], "fasting": last["fasting"]},
            "status": status,
            "history": [{"collected_on": h["collected_on"], "value": h["value"], "value_text": h["value_text"],
                         "unit": h["unit"], "status": compare_to_range(h), "source": h["source"]} for h in hist],
        }
        nums = [h for h in hist if h["value"] is not None]
        if len(nums) >= 2:
            prev, cur = nums[-2], nums[-1]
            item["change_from_previous"] = {"from": prev["collected_on"], "delta": round(cur["value"] - prev["value"], 4),
                                            "pct": round((cur["value"] - prev["value"]) / prev["value"] * 100, 1)
                                            if prev["value"] else None}
        if last["value"] is not None and last["ref_high"] is not None and status == "high":
            item["above_upper_pct"] = round((last["value"] - last["ref_high"]) / last["ref_high"] * 100, 1)
        out.append(item)
    order = {"high": 0, "low": 0, "abnormal": 1, "unknown": 2, "normal": 3}
    return sorted(out, key=lambda i: (order[i["status"]], i["category"] or "", i["test_key"]))


def add_lab(conn, user_id: int, fields: dict, import_key: str | None = None) -> dict | None:
    return _insert(conn, "lab_results", user_id, fields, LAB_FIELDS, import_key)


def update_lab(conn, user_id: int, lab_id: int, fields: dict) -> dict | None:
    return _update(conn, "lab_results", {"id": lab_id, "user_id": user_id}, fields, LAB_FIELDS)


# ---------- Body events ----------
# An occurred_at without a timezone is interpreted in the user's own timezone (same as queries.add_event).
_LOCAL_TS = """CASE WHEN {v}::text ~ '([+-]\\d{{2}}:?\\d{{2}}|Z)$' THEN {v}::timestamptz
                    ELSE ({v}::timestamp AT TIME ZONE (SELECT timezone FROM users WHERE id = {uid})) END"""


def list_events(conn, user_id: int, start: date, end: date, category: str | None = None) -> list[dict]:
    q = """SELECT e.id, e.occurred_at, (e.occurred_at AT TIME ZONE u.timezone)::date AS local_date, e.time_precision,
                  e.category, e.body_part, e.severity, e.title, e.note, e.resolved_on
           FROM events e JOIN users u ON u.id = e.user_id
           WHERE e.user_id = %s AND (e.occurred_at AT TIME ZONE u.timezone)::date BETWEEN %s AND %s"""
    params: list = [user_id, start, end]
    if category:
        q += " AND e.category = %s"
        params.append(category)
    return conn.execute(q + " ORDER BY e.occurred_at", params).fetchall()


def add_event(conn, user_id: int, fields: dict, import_key: str | None = None) -> dict | None:
    f = {k: v for k, v in fields.items() if k in EVENT_FIELDS and v is not None}
    occurred_at = str(f.pop("occurred_at"))
    cols = ["user_id", *f, "import_key"]
    q = (sql.SQL("INSERT INTO events (occurred_at, {}) VALUES (").format(sql.SQL(", ").join(map(sql.Identifier, cols)))
         + sql.SQL(_LOCAL_TS.format(v="%s", uid="%s"))  # this part contains a regex {2}, so it cannot go through .format again
         + sql.SQL(", {})").format(sql.SQL(", ").join(sql.Placeholder() * len(cols))))
    if import_key is not None:
        q += sql.SQL(" ON CONFLICT (user_id, import_key) DO NOTHING")
    q += sql.SQL(" RETURNING *")
    return conn.execute(q, [occurred_at, occurred_at, occurred_at, user_id, user_id, *f.values(), import_key]).fetchone()


def update_event(conn, user_id: int, event_id: int, fields: dict) -> dict | None:
    f = dict(fields)
    if "occurred_at" in f:
        if f["occurred_at"] is None:
            raise ValueError("occurred_at 不能为空")
        ts = str(f.pop("occurred_at"))
        row = conn.execute("SELECT " + _LOCAL_TS.format(v="%s", uid="%s") + " AS t", (ts, ts, ts, user_id)).fetchone()
        f["occurred_at"] = row["t"]
    if "title" in f and not f["title"]:
        raise ValueError("title 不能为空")
    return _update(conn, "events", {"id": event_id, "user_id": user_id}, f, EVENT_FIELDS)


# ---------- Journal ----------

def list_journal_items(conn, user_id: int, active_only: bool = False) -> list[dict]:
    q = "SELECT key, label_zh, kind, unit, category, description, sort, active FROM journal_items WHERE user_id = %s"
    if active_only:
        q += " AND active"
    return conn.execute(q + " ORDER BY sort, key", (user_id,)).fetchall()


def upsert_journal_item(conn, user_id: int, key: str, fields: dict, overwrite: bool = True) -> dict | None:
    cols = {k: v for k, v in fields.items() if k in JOURNAL_ITEM_FIELDS and v is not None}
    if "label_zh" not in cols:
        existing = conn.execute("SELECT 1 FROM journal_items WHERE user_id = %s AND key = %s", (user_id, key)).fetchone()
        if existing is None:
            raise ValueError("新日记项要有 label_zh")
        return _update(conn, "journal_items", {"user_id": user_id, "key": key}, cols, JOURNAL_ITEM_FIELDS, touch=False)
    names = ["user_id", "key", *cols]
    conflict = (sql.SQL("DO UPDATE SET {}").format(sql.SQL(", ").join(
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(k)) for k in cols)) if overwrite else sql.SQL("DO NOTHING"))
    q = sql.SQL("INSERT INTO journal_items ({}) VALUES ({}) ON CONFLICT (user_id, key) {} RETURNING *").format(
        sql.SQL(", ").join(map(sql.Identifier, names)), sql.SQL(", ").join(sql.Placeholder() * len(names)), conflict)
    return conn.execute(q, [user_id, key, *cols.values()]).fetchone()


def journal_entries(conn, user_id: int, start: date, end: date, keys: list[str] | None = None) -> list[dict]:
    q = """SELECT e.date, e.key, i.label_zh, i.kind, i.unit, e.value, e.source, e.note
           FROM journal_entries e JOIN journal_items i USING (user_id, key)
           WHERE e.user_id = %s AND e.date BETWEEN %s AND %s"""
    params: list = [user_id, start, end]
    if keys:
        q += " AND e.key = ANY(%s)"
        params.append(keys)
    return conn.execute(q + " ORDER BY e.date, i.sort, e.key", params).fetchall()


def log_journal(conn, user_id: int, day: date, key: str, value: float, note: str | None = None,
                source: str = "manual", overwrite: bool = True) -> dict | None:
    """Record an item for a day. Bool items can only be 0 / 1. With overwrite=False, an existing record is left alone (used by migration)."""
    item = conn.execute("SELECT kind FROM journal_items WHERE user_id = %s AND key = %s", (user_id, key)).fetchone()
    if item is None:
        raise ValueError(f"没有这个日记项：{key}（先用日记项列表确认 key）")
    if item["kind"] == "bool" and value not in (0, 1):
        raise ValueError(f"{key} 是是 / 否项，value 只能是 1（是）或 0（否）")
    if not math.isfinite(value):
        raise ValueError("value 必须是有限的数")
    conflict = ("DO UPDATE SET value = EXCLUDED.value, note = EXCLUDED.note, source = EXCLUDED.source, updated_at = now()"
                if overwrite else "DO NOTHING")
    return conn.execute(
        f"""INSERT INTO journal_entries (user_id, date, key, value, source, note) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, date, key) {conflict} RETURNING date, key, value, source, note""",
        (user_id, day, key, value, source, note)).fetchone()


def delete_journal_entry(conn, user_id: int, day: date, key: str) -> bool:
    return conn.execute("DELETE FROM journal_entries WHERE user_id = %s AND date = %s AND key = %s",
                        (user_id, day, key)).rowcount > 0


# ---------- Exercise extra info ----------

def list_exercises(conn, user_id: int, statuses: list[str] | None = None) -> list[dict]:
    q = "SELECT * FROM exercise_meta WHERE user_id = %s"
    params: list = [user_id]
    if statuses:
        q += " AND status = ANY(%s)"
        params.append(statuses)
    q += " ORDER BY array_position(ARRAY['banned','caution','active'], status), exercise_ref"
    return conn.execute(q, params).fetchall()


def upsert_exercise(conn, user_id: int, ref: str, fields: dict, overwrite: bool = True) -> dict | None:
    cols = {k: v for k, v in fields.items() if k in EXERCISE_FIELDS}
    names = ["user_id", "exercise_ref", *cols]
    if overwrite and cols:
        conflict = sql.SQL("DO UPDATE SET {}, updated_at = now()").format(sql.SQL(", ").join(
            sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(k)) for k in cols))
    else:
        conflict = sql.SQL("DO NOTHING")
    q = sql.SQL("INSERT INTO exercise_meta ({}) VALUES ({}) ON CONFLICT (user_id, exercise_ref) {} RETURNING *").format(
        sql.SQL(", ").join(map(sql.Identifier, names)), sql.SQL(", ").join(sql.Placeholder() * len(names)), conflict)
    return conn.execute(q, [user_id, ref, *cols.values()]).fetchone()


def update_exercise(conn, user_id: int, ref: str, fields: dict) -> dict | None:
    return _update(conn, "exercise_meta", {"user_id": user_id, "exercise_ref": ref}, fields, EXERCISE_FIELDS)


def exercise_rules(conn, user_id: int) -> dict:
    """For the assistant: banned and caution exercises, with key points for each."""
    rows = list_exercises(conn, user_id)
    pick = ("exercise_ref", "name_zh", "name_en", "aliases", "per_side", "cues", "reason", "app_exercise_id")
    return {
        "banned": [{k: r[k] for k in pick} for r in rows if r["status"] == "banned"],
        "caution": [{k: r[k] for k in pick} for r in rows if r["status"] == "caution"],
        "active": [{k: r[k] for k in pick} for r in rows if r["status"] == "active"],
    }
