"""Sync overview (「同步状态」, issue: one-tap sync onboarding): one answer to "is my phone syncing, and if not, why and what
do I tap?", for the Today page's 「开启数据同步」 card, the status line under the title and the panel on the Today / Me pages
(web/static/sync-status.js). GET /v1/users/me/sync/overview (own data only, sync/api.py).

It combines what the server already knows, read only:
- the phone's Open Wearables link (broken.py: credentials gone bad, the server not reaching OW, the app not opened);
- the newest data of each way in (OW, the app's direct upload, the Shortcut) and of the main types (data_status.py);
- today's scores (daily_scores) and, when the recovery ring is empty, why (web/recovery_empty.py);
- the on-demand refresh and the history import (refresh.py, history.py: 「同步中…」);
- what the app last reported about the phone (table sync_device_state, app 1.0.8+, put_device()): Background App Refresh,
  Low Power Mode, Health locked, the last error, Health types iOS never asked about. Older apps send nothing; then the
  phone-side causes collapse into one "the background sync is behind" with the same buttons.

`issue` is the first match of (codes in ISSUES; `actions` are the buttons, the page has a branch for each one, ACTIONS):
  consent         the consent form is not confirmed: nothing is processed
  not_connected   nothing ever arrived through a sync (skipped the setup, or the first sync never ran)
  credentials     the phone's OW credentials went bad (broken.py "broken"; given_up when nothing else arrives either), or
                  the app reported an authentication error
  server          the server has not reached OW lately (our side)
  app_closed      data stopped and the app was not opened for a while (only outside the app: inside it the app is open)
  background_off  the app reported Background App Refresh off for it (shown even while the data is fresh: it will stop)
  low_power       behind and the app reported Low Power Mode
  locked          behind and the app reported Health unreadable (the phone locked when iOS woke the app)
  lagging         behind (SYNC_LAG_AFTER) without a phone report
  type_missing    heart rate arrives but one of OVERVIEW_PARTIAL does not (the Health permission of that type is off;
                  for sleep also: sleep tracking off on the watch)
`state`: consent / off / syncing / ok / issue. The sync engine of #219 plugs in at _engine(): today every user is on the
Open Wearables path; in the engine's app mode the OW checks are skipped and its uploads are the phone's channel.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L, is_en
from . import broken, data_status, history, params as P, refresh

ISSUES = ("consent", "not_connected", "credentials", "server", "app_closed", "background_off", "low_power", "locked",
          "lagging", "type_missing")
# connect = the one-tap setup; reconnect = the sync-repair sheet; retry = the server pulls again and rescores (refresh.py);
# sync_now = the app uploads now; open_app = open the app (outside it); authorize = the type's Health switch;
# open_settings = the app's iOS settings; sleep_help = sleep tracking on the watch; consent = the consent form
ACTIONS = ("connect", "reconnect", "retry", "sync_now", "open_app", "authorize", "open_settings", "sleep_help", "consent")
DEVICE_ERRORS = ("auth", "network", "locked", "health_denied", "server", "other")
TONE = {"consent": "mid", "not_connected": "plain", "credentials": "bad", "server": "mid", "app_closed": "bad",
        "background_off": "mid", "low_power": "mid", "locked": "mid", "lagging": "mid", "type_missing": "mid"}


def _label(code: str, types: list[dict] | None = None) -> str:
    if code == "type_missing":
        names = (", " if is_en() else "、").join(t["label"] for t in types or [])
        return L(f"没收到{names}", f"Missing: {names}")
    return {
        "consent": L("需要先确认同意书", "Review the consent form first"),
        "not_connected": L("还没开启数据同步", "Data sync is off"),
        "credentials": L("同步连接失效", "Sync connection expired"),
        "server": L("服务器同步延迟", "Server sync delayed"),
        "app_closed": L("太久没打开 app", "App not opened for a while"),
        "background_off": L("后台刷新被关", "Background refresh is off"),
        "low_power": L("低电量模式暂停了同步", "Low Power Mode paused sync"),
        "locked": L("手机锁着，读不到健康数据", "Phone locked, Health unreadable"),
        "lagging": L("后台同步慢了", "Background sync is behind"),
    }[code]


def _issue(code: str, actions: list[str], types: list[dict] | None = None) -> dict:
    out = {"code": code, "label": _label(code, types), "tone": TONE[code], "actions": actions}
    if types:
        out["types"] = [t["key"] for t in types]
    return out


def _engine(conn: psycopg.Connection, user_id: int) -> str:
    """Which path feeds this user's scores: ow / shadow (OW, the engine uploads alongside) / app (the engine). #219 S5 makes
    rawdata.engine.mode() return app; the engine's own status (per type, per batch) can be added to the answer here."""
    from ..rawdata import engine as rx_engine
    try:
        return rx_engine.mode(conn, user_id)
    except Exception:  # noqa: BLE001  an unknown flag on an old database: the OW path
        conn.rollback()
        return "ow"


def _channels(conn: psycopg.Connection, user_id: int) -> dict:
    return conn.execute("""SELECT (SELECT max(received_at) FROM rawdata_uploads WHERE user_id = %(u)s) AS app,
                                  (SELECT max(imported_at) FROM shortcut_uploads WHERE user_id = %(u)s) AS shortcut,
                                  (SELECT created_at FROM users WHERE id = %(u)s) AS created""", {"u": user_id}).fetchone()


def device(conn: psycopg.Connection, user_id: int, now: datetime | None = None) -> dict | None:
    """The app's last phone report while it is fresh (SYNC_DEVICE_FRESH), else None."""
    r = conn.execute("SELECT * FROM sync_device_state WHERE user_id = %s", (user_id,)).fetchone()
    now = now or datetime.now(timezone.utc)
    if r is None or now - r["reported_at"] > P.SYNC_DEVICE_FRESH:
        return None
    r = dict(r)
    r.pop("user_id", None)
    return r


def put_device(conn: psycopg.Connection, user_id: int, rep: dict) -> dict:
    """Store the app's phone report (replaces the previous one). Commits."""
    cols = ("app_version", "background_refresh", "low_power", "health_readable", "last_attempt_at", "last_upload_at",
            "last_error", "unasked_types")
    vals = {c: rep.get(c) for c in cols}
    vals["unasked_types"] = list(vals["unasked_types"] or [])
    conn.execute(f"""INSERT INTO sync_device_state (user_id, reported_at, {", ".join(cols)})
                     VALUES (%(u)s, now(), {", ".join(f"%({c})s" for c in cols)})
                     ON CONFLICT (user_id) DO UPDATE SET reported_at = now(),
                       {", ".join(f"{c} = EXCLUDED.{c}" for c in cols)}""", {"u": user_id, **vals})
    conn.commit()
    return device(conn, user_id) or {}


def _types(conn: psycopg.Connection, user_id: int, tz_name: str, today: date) -> list[dict]:
    """The panel's types with their newest data (data_status.py's queries, only OVERVIEW_TYPES)."""
    from datetime import timedelta
    tz = ZoneInfo(tz_name)
    kinds = tuple(k for k in data_status.KINDS if k.key in P.OVERVIEW_TYPES)
    since = today - timedelta(days=P.WINDOW_DAYS - 1)
    rows = {**data_status._metric_rows(conn, user_id, since, since, kinds),
            **data_status._event_rows(conn, user_id, tz_name, since, since)}
    out = []
    for k in kinds:
        r = rows.get(k.key) or {}
        last = r.get("last_date")
        at = r.get("last_at")
        out.append({"key": k.key, "label": L(k.zh, k.en),
                    "status": data_status.classify(last, today, P.STALE_AFTER_DAYS[k.key]),
                    "last_date": last.isoformat() if last else None,
                    "last_at": at.astimezone(tz).isoformat(timespec="seconds") if at else None,
                    "days_since": max(0, (today - last).days) if last else None,
                    "days": int(r.get("days") or 0)})
    return out


def _scores(conn: psycopg.Connection, user_id: int, today: date) -> dict:
    from ..web import recovery_empty
    row = conn.execute("""SELECT recovery, sleep_score, strain, flags FROM daily_scores WHERE user_id = %s AND date = %s""",
                       (user_id, today)).fetchone()
    out = {"date": today.isoformat(), "recovery": row is not None and row["recovery"] is not None,
           "sleep": row is not None and row["sleep_score"] is not None,
           "strain": row is not None and row["strain"] is not None}
    out["ready"] = out["recovery"]
    out["empty"] = recovery_empty.explain(conn, user_id, today, dict(row) if row else None, today)
    return out


def _iso(t: datetime | None, tz: ZoneInfo) -> str | None:
    return t.astimezone(tz).isoformat(timespec="seconds") if t else None


def collect(conn: psycopg.Connection, user_id: int, now: datetime | None = None, in_app: bool = False) -> dict:
    """The whole overview of one user. in_app: asked from inside the Watch2Whoop app (the app is open right now)."""
    from ..profile import consent
    now = now or datetime.now(timezone.utc)
    tz_name = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()
    engine = _engine(conn, user_id)
    link = broken.check(conn, user_id, now, visiting=in_app)
    ch = _channels(conn, user_id)
    ow_last = link["last_data_at"] if engine != "app" else None
    last = max((t for t in (ow_last, ch["app"], ch["shortcut"]) if t is not None), default=None)
    dev = device(conn, user_id, now)
    ref = refresh.status(conn, user_id)
    hist = history.status(conn, user_id)
    types = _types(conn, user_id, tz_name, today)
    scores = _scores(conn, user_id, today)
    syncing = bool(ref["busy"] or hist["active"])

    issue = None
    behind = last is not None and now - last > P.SYNC_LAG_AFTER
    if consent.needs_consent(conn, user_id):
        issue = _issue("consent", ["consent"])
    elif last is None:
        issue = _issue("not_connected", ["connect"])
    elif (engine != "app" and (link["reason"] == "broken" or (link["reason"] == "given_up" and behind))) \
            or (dev and dev.get("last_error") == "auth" and behind):
        issue = _issue("credentials", ["reconnect"])
    elif engine != "app" and link["reason"] == "server_stale":
        issue = _issue("server", ["retry"])
    elif behind and link["reason"] == "not_seen" and not in_app:
        issue = _issue("app_closed", ["open_app"])
    elif dev and dev.get("background_refresh") in ("denied", "restricted"):
        issue = _issue("background_off", ["open_settings", "sync_now"])
    elif behind and dev and dev.get("low_power"):
        issue = _issue("low_power", ["sync_now"])
    elif behind and dev and (dev.get("health_readable") is False or dev.get("last_error") == "locked"):
        issue = _issue("locked", ["sync_now"])
    elif behind:
        issue = _issue("lagging", ["sync_now", "retry"])
    else:
        hr = next(t for t in types if t["key"] == "heart_rate")
        missing = [t for t in types if t["key"] in P.OVERVIEW_PARTIAL and t["status"] != "ok"]
        settled = hr["status"] == "ok" and hr["days"] >= 3 and not syncing \
            and (ch["created"] is None or now - ch["created"] > P.SYNC_FIRST_GRACE)
        if missing and settled:
            acts = (["sleep_help"] if any(t["key"] == "sleep" for t in missing) else []) + ["authorize"]
            issue = _issue("type_missing", acts, missing)

    if issue and issue["code"] == "consent":
        state = "consent"
    elif issue and issue["code"] == "not_connected":
        state = "syncing" if syncing else "off"
    elif issue:
        state = "issue"
    else:
        state = "syncing" if syncing else "ok"
    label = issue["label"] if state in ("issue", "consent") else {
        "off": L("未开启同步", "Sync off"), "syncing": L("同步中", "Syncing"), "ok": L("同步正常", "Syncing normally")}[state]
    return {
        "state": state, "label": label, "issue": issue, "engine": engine,
        "setup_needed": last is None and not consent.needs_consent(conn, user_id),
        "last_sync_at": _iso(last, tz),
        "minutes_ago": int((now - last).total_seconds() // 60) if last else None,
        "channels": {"ow": _iso(ow_last, tz), "app": _iso(ch["app"], tz), "shortcut": _iso(ch["shortcut"], tz)},
        "types": types, "scores": scores,
        "refresh": {"busy": ref["busy"], "status": ref["status"], "scored_at": _iso(ref["scored_at"], tz),
                    "linked": ref["linked"], "error": bool(ref["error"]) and ref["status"] == "error"},
        "history": {"active": hist["active"], "percent": hist["percent"]},
        "device": {k: (_iso(v, tz) if isinstance(v, datetime) else v) for k, v in dev.items()} if dev else None,
    }
