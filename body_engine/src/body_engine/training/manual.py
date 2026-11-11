"""Manual cardio / ball-game workouts: the user logs a session by hand (date or start time, type, minutes, light / moderate / hard).

A manual workout is one `workouts` row with source 'manual' (the same table the watch, Shortcut and Hevy rows live in), so the
calendar, the day view, the weekly report and the friends' feed see it without special cases, and the phone's duplicate check
(src/31_workouts.js `duplicates`, which gets every non-app row of the server as `others`) already treats it like any other source.
- Idempotent: external_id = the client's `client_id`, else "<start UTC>|<type>"; the same request twice is one row (an update).
- Watch wins: 'manual' is not in P.SOURCE_RANK (rank 9 = last), so a watch workout of the same session stays and the manual
  row points to it with dup_of (counted nowhere). With no start time given, the same-day workout of the same type is found
  here and linked with flags.manual_dup (the kept copy of refresh_duplicates).
- No heart rate: the picked level is the intensity as it is (P.MANUAL_INTENSITY, history.classify); trimp stays null so nothing
  downstream mistakes a guess for a measurement.
- Only manual rows can be deleted here, and only the user's own.
Authorization lives in api.py (self only, token scope write)."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..i18n import L, is_en
from . import importer, params as P


def _tz(conn: psycopg.Connection, user_id: int) -> ZoneInfo:
    r = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    return ZoneInfo((r or {}).get("timezone") or "Australia/Sydney")


def type_options() -> list[dict]:
    """The picker: [{type, name, name_en}] in display order."""
    return [{"type": t, "name": P.TYPE_NAMES[t], "name_en": P.TYPE_NAMES_EN.get(t, t)} for t in P.MANUAL_TYPES]


def intensity_options() -> list[dict]:
    return [{"key": k, "label": P.INTENSITY_LABELS_EN[v] if is_en() else P.INTENSITY_LABELS[v]} for k, v in P.MANUAL_INTENSITY.items()]


def _check(type_: str, intensity: str, duration_min: float, avg_hr, active_kcal) -> None:
    if type_ not in P.MANUAL_TYPES:
        raise ValueError(L("训练类型不在可选列表里", "Unknown workout type") + f": {type_}; " + ", ".join(P.MANUAL_TYPES))
    if intensity not in P.MANUAL_INTENSITY:
        raise ValueError(L("强度只能是 light / moderate / hard", "Intensity must be light, moderate or hard"))
    if not P.MANUAL_MIN_DURATION <= duration_min <= P.MANUAL_MAX_DURATION:
        raise ValueError(L(f"时长要在 {P.MANUAL_MIN_DURATION}–{P.MANUAL_MAX_DURATION} 分钟之间",
                           f"Duration must be {P.MANUAL_MIN_DURATION}-{P.MANUAL_MAX_DURATION} minutes"))
    if avg_hr is not None and not 30 <= avg_hr <= 230:
        raise ValueError(L("平均心率不合理", "Average heart rate is out of range"))
    if active_kcal is not None and not 0 <= active_kcal <= 10000:
        raise ValueError(L("活动能量不合理", "Active energy is out of range"))


def _window(conn, user_id: int, tz: ZoneInfo, day: date | None, start: datetime | None, duration_min: float):
    """-> (start_at, end_at, untimed). `start` without a zone is read in the user's time zone."""
    now = datetime.now(tz)
    if start is not None:
        start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
        untimed = False
    elif day is not None:
        start = datetime.combine(day, time(P.MANUAL_UNTIMED_HOUR), tzinfo=tz)
        untimed = True
    else:
        raise ValueError(L("要给 date 或 start", "Give a date or a start time"))
    if start.date() > now.date():
        raise ValueError(L("不能补记未来的训练", "A workout cannot be in the future"))
    if (now.date() - start.date()).days > P.MANUAL_MAX_PAST_DAYS:
        raise ValueError(L(f"最多补记 {P.MANUAL_MAX_PAST_DAYS} 天内的训练", f"Workouts can be logged up to {P.MANUAL_MAX_PAST_DAYS} days back"))
    end = start + timedelta(minutes=duration_min)
    return start, end, untimed


def _external_id(start_at: datetime, type_: str, client_id: str | None) -> str:
    if client_id:
        return client_id
    return f"{start_at.astimezone(timezone.utc):%Y%m%dT%H%MZ}|{type_}"


def _same_day_copy(conn, user_id: int, day: date, type_: str, exclude_id: int | None) -> dict | None:
    """Untimed record: the longest kept non-manual workout of that day with the same type (or either side generic)."""
    rows = conn.execute("""SELECT * FROM workouts WHERE user_id = %s AND local_date = %s AND dup_of IS NULL AND excluded IS NULL
                           AND source <> %s ORDER BY duration_min DESC""", (user_id, day, P.MANUAL_SOURCE)).fetchall()
    for w in rows:
        if w["type"] == type_ or w["type"] in P.GENERIC_TYPES or type_ in P.GENERIC_TYPES:
            return w
    return None


def view(conn, w: dict, tz: ZoneInfo) -> dict:
    from . import history
    flags = w["flags"] or {}
    return {"id": w["id"], "source": w["source"], "date": w["local_date"].isoformat(), "type": w["type"], "name": w["name_zh"],
            "start": None if flags.get("untimed") else w["start_at"].astimezone(tz).isoformat(timespec="minutes"),
            "end": None if flags.get("untimed") else w["end_at"].astimezone(tz).isoformat(timespec="minutes"),
            "duration_min": round(w["duration_min"]), "intensity": flags.get("manual_intensity"),
            "intensity_label": history.intensity_label(P.MANUAL_INTENSITY.get(flags.get("manual_intensity"), "light")),
            "avg_hr": None if w["avg_hr"] is None else round(w["avg_hr"]),
            "active_kcal": None if w["active_kcal"] is None else round(w["active_kcal"]),
            "excluded": w["excluded"]}


def add(conn: psycopg.Connection, user_id: int, *, type_: str, duration_min: float, intensity: str, day: date | None = None,
        start: datetime | None = None, avg_hr: float | None = None, active_kcal: float | None = None,
        client_id: str | None = None) -> dict:
    """Log (or update, same key) one manual workout. Returns {created, workout, duplicate_of}: duplicate_of = the watch / other
    copy it was linked to (then this record counts nowhere), else null."""
    if client_id is not None and not (1 <= len(client_id) <= P.MANUAL_CLIENT_ID_MAX):
        raise ValueError(L("client_id 长度不对", "client_id has the wrong length"))
    _check(type_, intensity, duration_min, avg_hr, active_kcal)
    tz = _tz(conn, user_id)
    start_at, end_at, untimed = _window(conn, user_id, tz, day, start, duration_min)
    ext = _external_id(start_at, type_, client_id)
    row = importer._base_row(tz, start_at, end_at)
    row.update(source=P.MANUAL_SOURCE, external_id=ext, origin="manual", is_xml=False, device=None, type=type_,
               name_zh=importer.type_name(type_), active_kcal=active_kcal, total_kcal=None, distance_km=None,
               avg_hr=avg_hr, max_hr=None, zone_offset=None)
    with conn.transaction():
        existed = conn.execute("SELECT id FROM workouts WHERE user_id = %s AND source = %s AND external_id = %s",
                               (user_id, P.MANUAL_SOURCE, ext)).fetchone()
        res = importer.upsert(conn, user_id, [row])
        wid = res["ids"][0]
        flags = {"manual_intensity": intensity, "computed": P.TRAINING_VERSION}   # computed: no heart-rate enrichment (enrich_pending)
        dup_id = None
        if untimed:
            flags["untimed"] = True
            copy = _same_day_copy(conn, user_id, row["local_date"], type_, wid)
            if copy:
                flags["manual_dup"] = True
                dup_id = copy["id"]
        # replace the manual flags wholesale, keep the import-time ones (implausible_duration…)
        conn.execute("""UPDATE workouts SET flags = (flags - 'manual_dup' - 'untimed') || %s, dup_of = %s, updated_at = now()
                        WHERE id = %s AND user_id = %s""", (Jsonb(flags), dup_id, wid, user_id))
        importer.refresh_duplicates(conn, user_id)
        w = conn.execute("SELECT * FROM workouts WHERE id = %s", (wid,)).fetchone()
        dup = conn.execute("SELECT * FROM workouts WHERE id = %s", (w["dup_of"],)).fetchone() if w["dup_of"] else None
    from ..assistant import writelog
    writelog.record(conn, "manual_workout", "create" if existed is None else "update", w["id"], None,
                    {"id": w["id"], "date": w["local_date"].isoformat(), "type": type_, "duration_min": duration_min})
    return {"created": existed is None, "workout": view(conn, w, tz),
            "duplicate_of": None if dup is None else {"id": dup["id"], "source": dup["source"], "name": dup["name_zh"],
                                                      "date": dup["local_date"].isoformat(),
                                                      "start": dup["start_at"].astimezone(tz).isoformat(timespec="minutes"),
                                                      "duration_min": round(dup["duration_min"])}}


def delete(conn: psycopg.Connection, user_id: int, workout_id: int) -> dict:
    """Delete one manual workout of this user. LookupError = not there (or not theirs); PermissionError = not a manual one."""
    w = conn.execute("SELECT * FROM workouts WHERE id = %s AND user_id = %s", (workout_id, user_id)).fetchone()
    if w is None:
        raise LookupError(L("没有这条训练", "No such workout"))
    if w["source"] != P.MANUAL_SOURCE:
        raise PermissionError(L("只能删手动记的训练；手表来的用 delete_workout 隐藏", "Only a manually logged workout can be deleted; hide a watch one instead"))
    tz = _tz(conn, user_id)
    out = view(conn, w, tz)
    with conn.transaction():
        conn.execute("DELETE FROM workouts WHERE id = %s AND user_id = %s", (workout_id, user_id))
        importer.refresh_duplicates(conn, user_id)
    return {"deleted": out}


def register_rollbacks(reg) -> None:
    """dl_app.py: rollback_write on a logged `log_cardio_workout` deletes the record (only a manual one, only this user's)."""
    def undo(conn, user_id, row, client=None):
        try:
            return delete(conn, user_id, int(row["target"]))
        except (LookupError, PermissionError) as e:
            raise ValueError(str(e))
    reg.rollback("manual_workout", undo, only_action="create")
