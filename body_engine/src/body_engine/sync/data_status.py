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
Weight counts the person's own weigh-ins too (body_measurements, the Today page / the AI's log_weight, source `records`):
it is on the server, and someone who weighs in there is not missing anything.

Why a type is not ok (`reason`, `action`, `alarm`; explain(), thresholds in params.py), so "old because nothing new was
recorded" is told apart from "old because the phone did not deliver" (the owner's report 2026-10-31: the sync said it was
up to date while weight / wrist temperature / water showed as stale):
- reason not_delivered: nothing came through the phone's sync for DELIVERY_FRESH; action from the phone's last report:
  enable_background_refresh / unlock_phone / sign_in / check_network / open_app;
- reason not_recorded: the sync delivered, the phone had nothing newer; action what makes it appear: wear_watch,
  wear_watch_sleep, use_scale, outdoor_workout, log_in_health, log_workout, wear_headphones;
- reason no_permission: never anything of a type a worn watch records daily while other data arrives; action authorize;
- reason on_demand (ECG: action record_ecg), logged_elsewhere (water logged in the app, not in Health), unsupported
  (the sync cannot read it), flag_off;
- alarm: the reason is a sync problem (params.ALARM_REASONS); `alarm_count` counts them. `delivery`: the phone's channel
  as one state (ok / behind / never), its last delivery and the action for it (background refresh off shows even while ok).
All new fields; older apps ignore them.
`channel` follows the sync mode (rawdata/engine.mode, #219 S5b): in the mode app the types the OW SDK used to deliver
come through the app's own sync engine, so they say `app` (and wrist temperature loses `sdk_unsupported`); `mode` is in
the answer too.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import params

STATUSES = ("ok", "stale", "never")
REASONS = ("not_delivered", "not_recorded", "no_permission", "on_demand", "logged_elsewhere", "unsupported", "flag_off")
ACTIONS = ("enable_background_refresh", "unlock_phone", "sign_in", "check_network", "open_app", "authorize", "wear_watch",
           "wear_watch_sleep", "use_scale", "outdoor_workout", "record_ecg", "log_in_health", "log_workout",
           "wear_headphones")
DELIVERY = ("ok", "behind", "never")


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


def kinds(extended: bool = False, mode: str = "ow") -> tuple[Kind, ...]:
    """The listed kinds; mode = the user's sync mode (rawdata/engine.MODES): in `app` nothing comes through OW."""
    out = KINDS if not extended else tuple(EXTENDED_REPLACE.get(k.key, k) for k in KINDS) + EXTRA_KINDS
    if mode == "app":
        out = tuple(k if k.channel != "ow" else
                    Kind(k.key, k.zh, k.en, "app", k.metrics, tuple(n for n in k.notes if n != "sdk_unsupported"))
                    for k in out)
    return out


def _stale_after(key: str) -> int | None:
    return params.STALE_AFTER_DAYS[key] if key in params.STALE_AFTER_DAYS else params.EXTRA_STALE_AFTER_DAYS[key]


def classify(last: date | None, today: date, stale_after: int | None) -> str:
    """ok / stale / never for the latest day with data."""
    if last is None:
        return "never"
    if stale_after is not None and (today - last).days > stale_after:
        return "stale"
    return "ok"


def device_action(dev: dict | None) -> str:
    """The phone-side fix for a sync that stopped delivering, from the app's last report (sync_device_state)."""
    dev = dev or {}
    if dev.get("last_error") == "auth":
        return "sign_in"
    if dev.get("background_refresh") in ("denied", "restricted"):
        return "enable_background_refresh"
    if dev.get("health_readable") is False or dev.get("last_error") in ("locked", "health_denied"):
        return "unlock_phone"
    if dev.get("last_error") == "network":
        return "check_network"
    return "open_app"


def explain(kind: Kind, status: str, delivery: str, dev: dict | None = None, elsewhere: bool = False) -> dict:
    """{reason, action, alarm} of one type (None / False when it is ok). delivery: the phone channel's state (DELIVERY);
    dev: the app's last phone report; elsewhere: the person logs this type where the sync does not read it."""
    def out(reason: str, action: str | None) -> dict:
        return {"reason": reason, "action": action, "alarm": reason in params.ALARM_REASONS}
    if status == "ok":
        return {"reason": None, "action": None, "alarm": False}
    if "flag_off" in kind.notes:
        return out("flag_off", None)
    if "sdk_unsupported" in kind.notes:
        return out("unsupported", None)
    if "on_demand" in kind.notes:
        return out("on_demand", "record_ecg")
    if elsewhere:
        return out("logged_elsewhere", None)
    if delivery != "ok":
        return out("not_delivered", device_action(dev))
    if status == "never" and kind.key in params.GAP_CORE_KINDS:
        return out("no_permission", "authorize")
    if kind.key in params.RECORD_ACTION:
        return out("not_recorded", params.RECORD_ACTION[kind.key])
    return out("not_recorded", "wear_watch_sleep" if kind.key in params.OVERNIGHT_KINDS else "wear_watch")


def _delivery(conn: psycopg.Connection, user_id: int, mode: str, tz: ZoneInfo, now: datetime) -> tuple[dict, dict | None]:
    """The phone channel as one state: ok (delivered within DELIVERY_FRESH), behind, never; and the app's phone report.
    In the engine's app mode only its own uploads count (Open Wearables no longer feeds this user)."""
    from . import overview
    r = conn.execute(r"""SELECT (SELECT CASE WHEN cursor ~ '^\d{4}-\d{2}-\d{2}' THEN cursor::timestamptz END
                                FROM sync_state WHERE user_id = %(u)s AND source = 'ow') AS ow,
                                (SELECT max(received_at) FROM rawdata_uploads WHERE user_id = %(u)s) AS app,
                                (SELECT max(imported_at) FROM shortcut_uploads WHERE user_id = %(u)s) AS shortcut""",
                     {"u": user_id}).fetchone()
    last = max((t for t in (None if mode == "app" else r["ow"], r["app"], r["shortcut"]) if t is not None), default=None)
    dev = overview.device(conn, user_id, now)
    state = "never" if last is None else ("ok" if now - last <= params.DELIVERY_FRESH else "behind")
    bg_off = bool(dev and dev.get("background_refresh") in ("denied", "restricted"))
    action = device_action(dev) if state != "ok" else ("enable_background_refresh" if bg_off else None)
    return {"state": state, "last_at": _iso(last, tz), "action": action}, dev


def _water_elsewhere(conn: psycopg.Connection, user_id: int, since: date) -> bool:
    """Drinks logged outside Apple Health (the app's buttons, the AI) since `since`: the data status counts only Health."""
    return conn.execute("""SELECT EXISTS (SELECT 1 FROM water_intake WHERE user_id = %s AND source <> 'apple_health'
                                          AND local_date >= %s) AS x""", (user_id, since)).fetchone()["x"]


def _iso(v: datetime | None, tz: ZoneInfo) -> str | None:
    return v.astimezone(tz).isoformat(timespec="seconds") if v else None


# Kinds stored as one row per event: (key, table, date expression, time expression, source expression, extra condition)
EVENT_SPECS = (
    ("sleep", "sleep_sessions", "wake_date", "end_at", "source", "TRUE"),
    ("workouts", "workouts", "local_date", "end_at", "source", "dup_of IS NULL AND excluded IS NULL"),
    ("heartbeats", "heartbeat_series", "(start_at AT TIME ZONE %(tz)s)::date", "end_at", "origin", "TRUE"),
    ("ecg", "ecg_recordings", "(start_at AT TIME ZONE %(tz)s)::date", "start_at", "origin", "TRUE"),
    ("water", "water_intake", "local_date", "at", "source", "source = 'apple_health'"),
)


def _event_rows(conn: psycopg.Connection, user_id: int, tz: str, since: date, src_since: date) -> dict[str, dict]:
    """Kinds stored as one row per event: {key: {last_date, last_at, days, count, sources}}."""
    p = {"u": user_id, "tz": tz, "since": since, "src": src_since}
    specs = EVENT_SPECS
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
    if "body_mass" in metric_kind:   # the person's own weigh-ins (Today page / AI log_weight) are weight on the server too
        r = conn.execute("""SELECT max(date) AS last_date, array_agg(date) FILTER (WHERE date >= %(since)s) AS dates,
                                   count(*) FILTER (WHERE date >= %(since)s) AS count, bool_or(date >= %(src)s) AS recent
                            FROM body_measurements WHERE user_id = %(u)s AND weight_kg IS NOT NULL""", p).fetchone()
        if r["last_date"]:
            o = out.setdefault(metric_kind["body_mass"], {"last_date": None, "dates": set(), "count": 0, "sources": set()})
            o["last_date"] = max(o["last_date"] or r["last_date"], r["last_date"])
            o["dates"] |= set(r["dates"] or ())
            o["count"] += int(r["count"])
            if r["recent"]:
                o["sources"].add("records")
    from ..importers import common
    app_mode = common.AppMode(conn, user_id)   # the sync engine's values count only in its app mode (as resolve_daily)
    for r in conn.execute("""SELECT DISTINCT metric, source FROM daily_metric_sources
                             WHERE user_id = %(u)s AND metric = ANY(%(m)s) AND date >= %(src)s""", p):
        if not common.takes_part(r, app_mode):
            continue
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
    from ..rawdata import engine as rx_engine
    from ..rollout import flags
    tz_name = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
    tz = ZoneInfo(tz_name)
    today = today or datetime.now(tz).date()
    if extended is None:
        extended = flags.enabled(conn, user_id, "sync_center")
    mode = rx_engine.mode(conn, user_id)
    kind_list = kinds(extended, mode)
    since = today - timedelta(days=params.WINDOW_DAYS - 1)
    src_since = today - timedelta(days=params.SOURCES_DAYS - 1)
    rows = {**_metric_rows(conn, user_id, since, src_since, kind_list),
            **_event_rows(conn, user_id, tz_name, since, src_since)}
    app_at = _app_last_at(conn, user_id, kind_list) if extended else {}
    water_on = water.enabled(conn, user_id)
    delivery, dev = _delivery(conn, user_id, mode, tz, datetime.now(timezone.utc))
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
        elsewhere = k.key == "water" and status != "ok" and stale_after is not None \
            and _water_elsewhere(conn, user_id, today - timedelta(days=stale_after))
        why = explain(Kind(k.key, k.zh, k.en, k.channel, k.metrics, tuple(notes)), status, delivery["state"], dev, elsewhere)
        types.append({
            "key": k.key, "label": L(k.zh, k.en), "channel": k.channel, "status": status,
            "last_date": last.isoformat() if last else None,
            "last_at": _iso(r.get("last_at"), tz),
            "days_since": max(0, (today - last).days) if last else None,
            "days": int(r.get("days") or 0), "count": int(r.get("count") or 0),
            "sources": list(r.get("sources") or []),
            "stale_after_days": stale_after, "notes": notes, **why,
        })
    return {"today": today.isoformat(), "window_days": params.WINDOW_DAYS, "types": types, "summary": summary,
            "channels": _channels(conn, user_id, tz), "strength": _strength(conn, user_id, today, sparky),
            "extended": extended, "mode": mode, "delivery": delivery,
            "alarm_count": sum(1 for t in types if t["alarm"])}
