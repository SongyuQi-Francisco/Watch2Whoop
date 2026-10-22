"""notify(): the one entry point every feature calls (issue #175).

    notify(conn, user_id, kind, title, body, url=None, data=None) -> {"id", "push"}

1. writes an in-app notification (table notifications) inside the caller's transaction;
2. pushes to the user's registered iPhones through APNs, unless the push is off: rollout flag `notifications` off for
   the user, the category switched off in the settings, quiet hours, no device, or no APNs key configured;
3. merges: the same kind to the same person within params.MERGE_MINUTES while unread becomes one notification on the
   phone (apns-collapse-id), its text a count ("3 个人给你点了 ❤️"); only the first one makes a sound.

title / body are a string (Chinese) or {"zh": …, "en": …}; the push uses the recipient's language. url is a path inside
the Today page (/app/#tab=friends …); data is small caller data kept on the server (data["actor_id"] counts people when
merging), never sent to Apple. The push itself goes out from a background thread after the call returns, so a slow
APNs never slows the caller; results (invalid tokens removed) are written with the worker's own connection.
"""
from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
from datetime import datetime, time
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..rollout import flags
from . import apns, params as P

log = logging.getLogger(__name__)
_UNSET = object()
_client: object = _UNSET     # apns.Client, None (not configured) or _UNSET (read the configuration on first use)
_sync = False                # tests: send inline on the caller's connection


def set_client(client: apns.Client | None, sync: bool = True) -> None:
    """Tests (and admin tools): use this client; None = APNs not configured. sync=True sends inline."""
    global _client, _sync
    _client, _sync = client, sync


def client() -> apns.Client | None:
    global _client
    if _client is _UNSET:
        cfg = apns.load_config()
        _client = apns.HttpClient(cfg) if cfg else None
        log.info("APNs %s", f"configured for {cfg.bundle_id}" if cfg else "not configured: in-app notifications only")
    return _client  # type: ignore[return-value]


def configured() -> bool:
    return client() is not None


# ------------------------------------------------------------------------------------------------ settings
def category_of(kind: str) -> str:
    """circle.like, circle_feed_like and circle all belong to the category circle."""
    for c in P.CATEGORIES:
        if kind == c or kind.startswith((c + ".", c + "_")):
            return c
    return kind.split(".", 1)[0]


def settings(conn: psycopg.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT categories, quiet_enabled, quiet_start, quiet_end FROM notification_settings WHERE user_id = %s",
                       (user_id,)).fetchone()
    cats = (row or {}).get("categories") or {}
    return {
        "categories": {k: bool(cats.get(k, c["default"])) for k, c in P.CATEGORIES.items()},
        "quiet": {"enabled": row["quiet_enabled"] if row else P.QUIET_ENABLED,
                  "start": (row["quiet_start"] if row else P.QUIET_START).strftime("%H:%M"),
                  "end": (row["quiet_end"] if row else P.QUIET_END).strftime("%H:%M")},
    }


def save_settings(conn: psycopg.Connection, user_id: int, categories: dict | None = None, quiet: dict | None = None) -> dict:
    cur = settings(conn, user_id)
    for k, v in (categories or {}).items():
        if k not in P.CATEGORIES:
            raise ValueError(f"category: {k}")
        cur["categories"][k] = bool(v)
    q = {**cur["quiet"], **(quiet or {})}
    start, end = (time.fromisoformat(q[k]) for k in ("start", "end"))
    conn.execute("""INSERT INTO notification_settings (user_id, categories, quiet_enabled, quiet_start, quiet_end)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET categories = EXCLUDED.categories, quiet_enabled = EXCLUDED.quiet_enabled,
                      quiet_start = EXCLUDED.quiet_start, quiet_end = EXCLUDED.quiet_end, updated_at = now()""",
                 (user_id, Jsonb(cur["categories"]), bool(q["enabled"]), start, end))
    return settings(conn, user_id)


def in_quiet_hours(local: time, enabled: bool, start: time, end: time) -> bool:
    if not enabled or start == end:
        return False
    return start <= local < end if start < end else local >= start or local < end


# ------------------------------------------------------------------------------------------------ notify
def _text(value, name: str, limit: int, required: bool) -> tuple[str, str | None]:
    zh, en = (value.get("zh") or value.get("en"), value.get("en")) if isinstance(value, dict) else (value, None)
    zh = (zh or "").strip()
    if required and not zh:
        raise ValueError(f"{name} is empty")
    return zh[:limit], (en.strip()[:limit] if en else None)


def notify(conn: psycopg.Connection, user_id: int, kind: str, title, body, url: str | None = None,
           data: dict | None = None, *, now: datetime | None = None) -> dict:
    if category_of(kind) not in P.CATEGORIES:
        raise ValueError(f"unknown notification category: {kind} (declare it in notify/params.py CATEGORIES)")
    if url is not None and (not url.startswith("/") or url.startswith("//") or len(url) > P.MAX_URL):
        raise ValueError("url must be a path on this server, e.g. /app/#tab=friends")
    data = dict(data or {})
    if len(json.dumps(data, ensure_ascii=False, default=str)) > P.MAX_DATA_BYTES:
        raise ValueError("data is too large")
    t_zh, t_en = _text(title, "title", P.MAX_TITLE, True)
    b_zh, b_en = _text(body, "body", P.MAX_BODY, False)
    nid = conn.execute(
        """INSERT INTO notifications (user_id, kind, title, body, title_en, body_en, url, data)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, kind, t_zh, b_zh, t_en, b_en, url, Jsonb(data))).fetchone()["id"]
    status, jobs = _plan_push(conn, user_id, nid, kind, now)
    conn.execute("UPDATE notifications SET push_status = %s WHERE id = %s", (status, nid))
    if jobs:
        _dispatch(conn, jobs)
    return {"id": nid, "push": status}


def _plan_push(conn, user_id: int, nid: int, kind: str, now: datetime | None) -> tuple[str, list[dict]]:
    """Why there is no push (status) or the pushes to send (status pushed / merged)."""
    if not flags.enabled(conn, user_id, P.FEATURE):
        return "flag_off", []
    st = settings(conn, user_id)
    if not st["categories"].get(category_of(kind), True):
        return "off", []
    user = conn.execute("SELECT timezone, language FROM users WHERE id = %s", (user_id,)).fetchone() or {}
    tz = ZoneInfo(user.get("timezone") or "Australia/Sydney")
    now = (now or datetime.now(tz)).astimezone(tz)
    q = st["quiet"]
    if in_quiet_hours(now.time(), q["enabled"], time.fromisoformat(q["start"]), time.fromisoformat(q["end"])):
        return "quiet", []
    devices = _devices(conn, user_id)
    if not devices:
        return "no_device", []
    if not configured():
        return "no_key", []
    # Unread notifications of this kind within the merge window (this one included), newest first
    recent = conn.execute(
        """SELECT id, title, body, title_en, body_en, url, data, push_status FROM notifications
           WHERE user_id = %s AND kind = %s AND read_at IS NULL AND (id = %s OR created_at > now() - make_interval(mins => %s))
           ORDER BY id DESC""",
        (user_id, kind, nid, P.MERGE_MINUTES)).fetchall()
    pushed = sum(1 for r in recent if r["push_status"] in ("pushed", "merged"))
    if pushed >= P.MAX_PUSHES_PER_WINDOW:
        return "merged_quiet", []
    merged = len(recent) > 1
    unread = conn.execute("SELECT count(*) AS n FROM notifications WHERE user_id = %s AND read_at IS NULL",
                          (user_id,)).fetchone()["n"]
    jobs = []
    for d in devices:
        lang = user.get("language") or d["lang"] or "zh"
        alert = _merged_alert(kind, recent, lang) if merged else _alert(recent[0], lang)
        urls = {r["url"] for r in recent}
        aps = {"alert": alert, "badge": unread, "thread-id": kind,
               "interruption-level": "passive" if pushed else "active"}
        if not pushed:
            aps["sound"] = "default"
        payload = {"aps": aps, "kind": kind, "nid": nid,
                   "url": (urls.pop() if len(urls) == 1 else None) or "/app/#notifications"}
        headers = {"apns-push-type": "alert", "apns-priority": "5" if pushed else "10",
                   "apns-collapse-id": kind[:64], "apns-expiration": str(int(now.timestamp()) + 86400)}
        jobs.append({"device_id": d["id"], "token": d["token"], "environment": d["environment"],
                     "payload": payload, "headers": headers})
    return ("merged" if merged else "pushed"), jobs


def _alert(row: dict, lang: str) -> dict:
    en = lang == "en"
    title = (row["title_en"] if en else None) or row["title"]
    body = (row["body_en"] if en else None) or row["body"]
    return {"title": title, "body": body} if body else {"title": title}


def _merged_alert(kind: str, recent: list[dict], lang: str) -> dict:
    actors = {str(r["data"].get("actor_id")) for r in recent if (r["data"] or {}).get("actor_id") is not None}
    n = len(actors) if len(actors) >= 2 else len(recent)
    i = 1 if lang == "en" else 0
    tpl = P.MERGED.get(kind) if len(actors) >= 2 else None
    label = P.CATEGORIES[category_of(kind)]["en" if i else "zh"]
    title = (tpl or P.DEFAULT_MERGED)[i].format(n=n, label=label)
    return {"title": title, "body": P.MERGED_BODY[i]}


# ------------------------------------------------------------------------------------------------ devices
def _devices(conn, user_id: int) -> list[dict]:
    """The user's devices; ones registered with a token that was revoked since (signed out elsewhere) are removed."""
    conn.execute("""DELETE FROM push_devices d USING api_tokens t
                    WHERE d.user_id = %s AND d.api_token_id = t.id AND t.revoked_at IS NOT NULL""", (user_id,))
    return conn.execute("SELECT id, token, environment, lang FROM push_devices WHERE user_id = %s ORDER BY id",
                        (user_id,)).fetchall()


def send_jobs(conn, jobs: list[dict]) -> list[dict]:
    """Sends and records the outcome per device: invalid tokens are deleted; a token Apple only knows in the other
    environment (BadDeviceToken) is retried there once and the device's environment corrected."""
    c = client()
    out = []
    for j in jobs:
        if c is None:
            break
        res = c.send(j["token"], j["environment"], j["payload"], j["headers"])
        env = j["environment"]
        if res.status == 400 and res.reason == "BadDeviceToken":
            other = "sandbox" if env == "production" else "production"
            retry = c.send(j["token"], other, j["payload"], j["headers"])
            if retry.ok:
                res, env = retry, other
        if res.invalid_token:
            conn.execute("DELETE FROM push_devices WHERE id = %s", (j["device_id"],))
            log.info("APNs: removed device %s (%s %s)", j["device_id"], res.status, res.reason)
        else:
            conn.execute("""UPDATE push_devices SET last_push_at = now(), last_status = %s, environment = %s WHERE id = %s""",
                         (str(res.status) if res.ok else f"{res.status} {res.reason or ''}".strip(), env, j["device_id"]))
        out.append({"device_id": j["device_id"], "status": res.status, "reason": res.reason, "removed": res.invalid_token})
    return out


# ------------------------------------------------------------------------------------------------ background sending
_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _dispatch(conn, jobs: list[dict]) -> None:
    if _sync:
        send_jobs(conn, jobs)
        return
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="notify-apns", daemon=True)
            _worker.start()
    _queue.put(jobs)


def _worker_conn():
    from .. import api
    if api.pool is not None:
        return api.pool.connection()
    from psycopg.rows import dict_row

    from ..config import settings as cfg
    return psycopg.connect(cfg.dsn, row_factory=dict_row)


def _run() -> None:
    while True:
        jobs = _queue.get()
        try:
            with _worker_conn() as conn:
                send_jobs(conn, jobs)
        except Exception:  # noqa: BLE001 - one failed batch must not stop the worker
            log.exception("notify: sending failed")
        finally:
            _queue.task_done()


def drain(timeout: float = 15.0) -> None:
    """Wait for queued pushes (CLI commands call it before exiting; also registered with atexit)."""
    if _worker is None:
        return
    done = threading.Event()
    threading.Thread(target=lambda: (_queue.join(), done.set()), daemon=True).start()
    done.wait(timeout)


atexit.register(drain)
