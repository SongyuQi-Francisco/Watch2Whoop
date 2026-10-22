"""Data status (「数据状态」, issue #163): per health data type, what the server actually received.

HealthKit never tells an app whether read access was granted (only whether the prompt was shown), so the Today page and
the app answer "is my data being read?" from the data that reached the server: the latest day (and time, where the table
keeps one), the days and records of the last WINDOW_DAYS days, the sources of the last SOURCES_DAYS days, and a status:

- ok      data within STALE_AFTER_DAYS of today (sync/params.py)
- stale   data, but older than that ("很久没收到")
- never   nothing ever stored for this user ("从没收到")

Read only, own data only (sync/api.py). Tables: daily_metrics / daily_metric_sources (OW, the Shortcut, the Apple Health
export), sleep_sessions, workouts (not duplicates, not excluded), heartbeat_series / ecg_recordings (the app's direct
upload or the export), water_intake (only drinks from Apple Health). `channels` says when each way in last delivered.
`strength` (issue #210): the strength-training records of the last few weeks by source (watch / SparkyFitness / Hevy), the
same records body age counts, so the user can check them (insights/healthspan_store.strength_sources).
With the sync center (flag sync_center, `extended`): the types the app reads itself (rawdata/hk_types.py: stand time,
exercise minutes, daylight ...) are listed too, wrist temperature counts as read by the app, and those types carry the
time of the newest stored sample (hk_samples), so the app can compare it with the newest one on the phone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import params

STATUSES = ("ok", "stale", "never")


@dataclass(frozen=True)
class Kind:
    key: str
    zh: str
    en: str
    channel: str                       # ow = the app's background sync (Open Wearables SDK); app = read by the app itself
    metrics: tuple[str, ...] = ()      # daily_metrics names (types stored per day)
    notes: tuple[str, ...] = ()        # codes the page / app explain: sdk_unsupported, app_direct, consent, on_demand, needs_device, outdoor


KINDS: tuple[Kind, ...] = (
    Kind("heart_rate", "心率", "Heart rate", "ow", ("heart_rate",)),
    Kind("hrv", "HRV", "HRV", "ow", ("heart_rate_variability_sdnn", "heart_rate_variability_rmssd")),
    Kind("resting_heart_rate", "静息心率", "Resting heart rate", "ow", ("resting_heart_rate",)),
    Kind("sleep", "睡眠", "Sleep", "ow"),
    Kind("steps", "步数", "Steps", "ow", ("step_count",)),
    Kind("active_energy", "活动能量", "Active energy", "ow", ("active_energy_burned",)),
    Kind("workouts", "体能训练", "Workouts", "ow"),
    Kind("weight", "体重", "Weight", "ow", ("body_mass",), ("needs_device",)),
    Kind("blood_oxygen", "血氧", "Blood oxygen", "ow", ("oxygen_saturation",)),
    Kind("respiratory_rate", "呼吸频率", "Respiratory rate", "ow", ("respiratory_rate",)),
    Kind("wrist_temperature", "手腕温度", "Wrist temperature", "ow", ("apple_sleeping_wrist_temperature",), ("sdk_unsupported",)),
    Kind("vo2max", "VO₂max", "VO₂ max", "ow", ("vo2_max",), ("outdoor",)),
    Kind("heartbeats", "心跳间期", "Beat-to-beat HRV", "app", (), ("app_direct", "consent")),
    Kind("ecg", "心电图", "ECG", "app", (), ("app_direct", "on_demand", "consent")),
    Kind("water", "饮水", "Water", "app", (), ("app_direct",)),
)
assert [k.key for k in KINDS] == list(params.STALE_AFTER_DAYS), "sync/params.STALE_AFTER_DAYS must list every kind"


def _app_kind(key: str, zh: str, en: str) -> Kind:
    from ..rawdata import hk_types
    return Kind(key, zh, en, "app", hk_types.metrics_of_kind(key), ("app_direct",))


# The sync center's list: wrist temperature is read by the app now, then the other types the app reads itself
EXTENDED_REPLACE = {"wrist_temperature": _app_kind("wrist_temperature", "手腕温度", "Wrist temperature")}
EXTRA_KINDS: tuple[Kind, ...] = (
    _app_kind("stand_time", "站立时间", "Stand time"),
    _app_kind("exercise_time", "锻炼分钟", "Exercise minutes"),
    _app_kind("daylight", "日照时间", "Time in daylight"),
    _app_kind("physical_effort", "体能消耗", "Physical effort"),
    _app_kind("walking_heart_rate", "步行平均心率", "Walking heart rate"),
    _app_kind("environmental_audio", "环境音量", "Environmental sound"),
    _app_kind("headphone_audio", "耳机音量", "Headphone audio"),
    _app_kind("stair_speed", "上下楼速度", "Stair speed"),
    _app_kind("walking_steadiness", "步行稳定性", "Walking steadiness"),
)
assert [k.key for k in EXTRA_KINDS] == list(params.EXTRA_STALE_AFTER_DAYS), \
    "sync/params.EXTRA_STALE_AFTER_DAYS must list every extra kind"


def kinds(extended: bool = False) -> tuple[Kind, ...]:
    if not extended:
        return KINDS
    return tuple(EXTENDED_REPLACE.get(k.key, k) for k in KINDS) + EXTRA_KINDS


def _stale_after(key: str) -> int | None:
    return params.STALE_AFTER_DAYS[key] if key in params.STALE_AFTER_DAYS else params.EXTRA_STALE_AFTER_DAYS[key]


def classify(last: date | None, today: date, stale_after: int | None) -> str:
    """ok / stale / never for the latest day with data."""
    if last is None:
        return "never"
    if stale_after is not None and (today - last).days > stale_after:
        return "stale"
    return "ok"


def _iso(v: datetime | None, tz: ZoneInfo) -> str | None:
    return v.astimezone(tz).isoformat(timespec="seconds") if v else None


def _event_rows(conn: psycopg.Connection, user_id: int, tz: str, since: date, src_since: date) -> dict[str, dict]:
    """Kinds stored as one row per event: {key: {last_date, last_at, days, count, sources}}."""
    p = {"u": user_id, "tz": tz, "since": since, "src": src_since}
    # (key, table, date expression, time expression, source expression, extra condition)
    specs = [
        ("sleep", "sleep_sessions", "wake_date", "end_at", "source", "TRUE"),
        ("workouts", "workouts", "local_date", "end_at", "source", "dup_of IS NULL AND excluded IS NULL"),
        ("heartbeats", "heartbeat_series", "(start_at AT TIME ZONE %(tz)s)::date", "end_at", "origin", "TRUE"),
        ("ecg", "ecg_recordings", "(start_at AT TIME ZONE %(tz)s)::date", "start_at", "origin", "TRUE"),
        ("water", "water_intake", "local_date", "at", "source", "source = 'apple_health'"),
    ]
    sql = " UNION ALL ".join(
        f"""SELECT '{key}' AS key, max({d}) AS last_date, max({t}) AS last_at,
                   count(DISTINCT {d}) FILTER (WHERE {d} >= %(since)s) AS days,
                   count(*) FILTER (WHERE {d} >= %(since)s) AS count,
                   array_remove(array_agg(DISTINCT {s}) FILTER (WHERE {d} >= %(src)s), NULL) AS sources
            FROM {table} WHERE user_id = %(u)s AND {cond}"""
        for key, table, d, t, s, cond in specs)
    return {r["key"]: r for r in conn.execute(sql, p).fetchall()}


def _metric_rows(conn: psycopg.Connection, user_id: int, since: date, src_since: date,
                 kind_list: tuple[Kind, ...] = KINDS) -> dict[str, dict]:
    """Kinds stored per day in daily_metrics: {key: {last_date, days, count, sources}} (count = samples of those days)."""
    metric_kind = {m: k.key for k in kind_list for m in k.metrics}
    p = {"u": user_id, "m": list(metric_kind), "since": since, "src": src_since}
    out: dict[str, dict] = {}
    for r in conn.execute("""SELECT metric, max(date) AS last_date,
                                    array_agg(DISTINCT date) FILTER (WHERE date >= %(since)s) AS dates,
                                    coalesce(sum(coalesce(n, 1)) FILTER (WHERE date >= %(since)s), 0) AS count
                             FROM daily_metrics WHERE user_id = %(u)s AND metric = ANY(%(m)s) GROUP BY metric""", p):
        o = out.setdefault(metric_kind[r["metric"]], {"last_date": None, "dates": set(), "count": 0, "sources": set()})
        if r["last_date"] and (o["last_date"] is None or r["last_date"] > o["last_date"]):
            o["last_date"] = r["last_date"]
        o["dates"] |= set(r["dates"] or ())
        o["count"] += int(r["count"])
    for r in conn.execute("""SELECT DISTINCT metric, source FROM daily_metric_sources
                             WHERE user_id = %(u)s AND metric = ANY(%(m)s) AND date >= %(src)s""", p):
        out.setdefault(metric_kind[r["metric"]], {"last_date": None, "dates": set(), "count": 0, "sources": set()})["sources"].add(r["source"])
    return {k: {"last_date": o["last_date"], "last_at": None, "days": len(o["dates"]), "count": o["count"],
                "sources": sorted(o["sources"])} for k, o in out.items()}


def _channels(conn: psycopg.Connection, user_id: int, tz: ZoneInfo) -> dict:
    """When each way in last delivered: Open Wearables (newest sample pulled), the app's direct upload, the Shortcut."""
    r = conn.execute(r"""SELECT (SELECT CASE WHEN cursor ~ '^\d{4}-\d{2}-\d{2}' THEN cursor::timestamptz END
                                 FROM sync_state WHERE user_id = %(u)s AND source = 'ow') AS ow,
                                (SELECT max(received_at) FROM rawdata_uploads WHERE user_id = %(u)s) AS app,
                                (SELECT max(imported_at) FROM shortcut_uploads WHERE user_id = %(u)s) AS shortcut""",
                     {"u": user_id}).fetchone()
    return {"ow_last_sample_at": _iso(r["ow"], tz), "app_upload_at": _iso(r["app"], tz), "shortcut_upload_at": _iso(r["shortcut"], tz)}


def _strength(conn: psycopg.Connection, user_id: int, today: date, sparky=None) -> dict:
    from ..insights import healthspan_params as H, healthspan_store   # insights imports sync modules indirectly
    weeks = H.STRENGTH_COVERAGE_WEEKS
    return {"weeks": weeks, **healthspan_store.strength_sources(conn, user_id, today - timedelta(days=7 * weeks - 1), today,
                                                                sparky, timeout=params.SPARKY_TIMEOUT_S)}


def _app_last_at(conn: psycopg.Connection, user_id: int, kind_list: tuple[Kind, ...]) -> dict[str, datetime]:
    """Kind -> start of the newest sample the app uploaded (hk_samples), for the kinds the app reads itself."""
    from ..rawdata import hk_types, quantities
    out: dict[str, datetime] = {}
    for hk, t in quantities.latest_by_type(conn, user_id).items():
        rt = hk_types.BY_HK.get(hk)
        if rt and (rt.kind not in out or t > out[rt.kind]):
            out[rt.kind] = t
    return {k.key: out[k.key] for k in kind_list if k.channel == "app" and k.key in out}


def collect(conn: psycopg.Connection, user_id: int, today: date | None = None, sparky=None,
            extended: bool | None = None) -> dict:
    """{today, window_days, types: [...], summary: {ok, stale, never}, channels, strength, extended}.
    extended: the sync center's longer list; None = the user's sync_center flag."""
    from .. import water
    from ..rollout import flags
    tz_name = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
    tz = ZoneInfo(tz_name)
    today = today or datetime.now(tz).date()
    if extended is None:
        extended = flags.enabled(conn, user_id, "sync_center")
    kind_list = kinds(extended)
    since = today - timedelta(days=params.WINDOW_DAYS - 1)
    src_since = today - timedelta(days=params.SOURCES_DAYS - 1)
    rows = {**_metric_rows(conn, user_id, since, src_since, kind_list),
            **_event_rows(conn, user_id, tz_name, since, src_since)}
    app_at = _app_last_at(conn, user_id, kind_list) if extended else {}
    water_on = water.enabled(conn, user_id)
    types, summary = [], dict.fromkeys(STATUSES, 0)
    for k in kind_list:
        r = dict(rows.get(k.key) or {})
        last: date | None = r.get("last_date")
        at = app_at.get(k.key)
        if at is not None and r.get("last_at") is None and last == at.astimezone(tz).date():
            r["last_at"] = at   # the newest day's sample came from the app: its time is known
        stale_after = _stale_after(k.key)
        status = classify(last, today, stale_after)
        summary[status] += 1
        notes = list(k.notes) + (["flag_off"] if k.key == "water" and not water_on else [])
        types.append({
            "key": k.key, "label": L(k.zh, k.en), "channel": k.channel, "status": status,
            "last_date": last.isoformat() if last else None,
            "last_at": _iso(r.get("last_at"), tz),
            "days_since": max(0, (today - last).days) if last else None,
            "days": int(r.get("days") or 0), "count": int(r.get("count") or 0),
            "sources": list(r.get("sources") or []),
            "stale_after_days": stale_after, "notes": notes,
        })
    return {"today": today.isoformat(), "window_days": params.WINDOW_DAYS, "types": types, "summary": summary,
            "channels": _channels(conn, user_id, tz), "strength": _strength(conn, user_id, today, sparky),
            "extended": extended}
