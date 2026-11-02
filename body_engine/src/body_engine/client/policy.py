"""Forced upgrade policy (R2): which client a request comes from, and whether it is asked to upgrade (notice) or refused
(block, HTTP 426).

Policy = flag `force_upgrade` (rollout/registry.py; off = nothing at all, the kill switch) + rows in rollout_settings
(params.SETTING_*; every change goes through rollout.flags.set_setting, so it is in rollout_audit):
  notice_from / block_from   an ISO date (midnight in the user's own time zone) or an ISO date-time with offset;
                             no notice_from = NOTICE_DAYS before block_from; no block_from = a notice only, never a block
  min per platform           ios-native: "2.0.0" or "2.0.0+57"; web: "all" | "none" (params.DEFAULT_MIN)
  url                        the App Store / TestFlight link (else BODY_UPGRADE_URL); https only

Client (client_of): a Today page cookie session = web; a Bearer request with X-App-Client: ios-native = the native app,
its X-App-Version (+ X-App-Build). Anything else (API keys and scripts without X-App-Client, the 1.0.8 app's own Bearer
uploads, an unknown X-App-Client, a native request without a readable version) is unknown and never asked to upgrade.
MCP tokens only work on /mcp, which never goes through api.current_user, so it is never checked.
"""
from __future__ import annotations

import os
import re
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from fastapi import HTTPException, Request

from ..i18n import L
from ..rollout import flags
from . import params as P

_VERSION = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
_SPEC = re.compile(r"^\d+(\.\d+){0,2}(\+\d+)?$")
_URL = re.compile(r"^https://[^\s<>\"']+$")
MAX_URL = 500

_clock = _time.monotonic
_cache: dict[str, tuple[float, "Policy"]] = {}


@dataclass(frozen=True)
class Policy:
    notice_from: str | None = None
    block_from: str | None = None
    url: str | None = None
    mins: dict[str, str] = field(default_factory=lambda: dict(P.DEFAULT_MIN))


@dataclass(frozen=True)
class Client:
    platform: str                 # ios-native | web
    version: str | None = None
    build: str | None = None


# ---------------------------------------------------------------- versions and dates
def vtuple(v: str | None) -> tuple[int, int, int] | None:
    m = _VERSION.match((v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None   # type: ignore[return-value]


def meets(version: str | None, build: str | None, spec: str) -> bool:
    """version (+ build) is at least spec ("2.0.0", or "2.0.0+57": 2.0.0 from build 57 on)."""
    want_v, _, want_b = spec.partition("+")
    have, want = vtuple(version), vtuple(want_v)
    if have is None or want is None:
        return False
    if have != want or not want_b:
        return have >= want
    try:
        return int(build or "") >= int(want_b)
    except ValueError:
        return False


def check_when(value: str) -> str:
    """An ISO date or date-time (with or without offset); raises ValueError otherwise."""
    v = value.strip()
    try:
        if len(v) == 10:
            date.fromisoformat(v)
        else:
            datetime.fromisoformat(v)
    except ValueError:
        raise ValueError(f"not an ISO date / date-time: {value!r}") from None
    return v


def at(value: str | None, tz: ZoneInfo) -> datetime | None:
    """The instant a policy date stands for: a date = midnight in tz; a date-time without offset = in tz."""
    if not value:
        return None
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), time(0), tz)
    d = datetime.fromisoformat(value)
    return d if d.tzinfo else d.replace(tzinfo=tz)


def window(pol: Policy, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """(notice starts, block starts) for someone in tz."""
    block = at(pol.block_from, tz)
    notice = at(pol.notice_from, tz)
    if notice is None and block is not None:
        notice = block - timedelta(days=P.NOTICE_DAYS)
    return notice, block


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 - a bad zone name in a profile must not break the request
        return ZoneInfo("UTC")


# ---------------------------------------------------------------- policy rows
def invalidate() -> None:
    _cache.clear()


def load(conn: psycopg.Connection) -> Policy:
    hit = _cache.get(conn.info.dbname)
    if hit and _clock() - hit[0] < P.CACHE_TTL_S:
        return hit[1]
    rows = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM rollout_settings WHERE key LIKE %s", ("upgrade.%",)).fetchall()}
    mins = {p: rows.get(P.SETTING_MIN + p) or P.DEFAULT_MIN[p] for p in P.PLATFORMS}
    pol = Policy(notice_from=rows.get(P.SETTING_NOTICE_FROM), block_from=rows.get(P.SETTING_BLOCK_FROM),
                 url=rows.get(P.SETTING_URL) or (os.environ.get(P.URL_ENV) or "").strip() or None, mins=mins)
    _cache[conn.info.dbname] = (_clock(), pol)
    return pol


_UNSET = object()


def update(conn: psycopg.Connection, *, notice_from=_UNSET, block_from=_UNSET, url=_UNSET,
           mins: dict[str, str] | None = None, actor: str | None = None) -> Policy:
    """Changes the given fields ("" / "none" / None = remove: back to the default or unset). Checked as a whole before
    anything is written (notice not after block, versions, https link); each change lands in rollout_audit. The caller commits."""
    def blank(v):
        return v is None or str(v).strip().lower() in ("", P.NONE)

    cur = load_fresh(conn)
    new = {"notice_from": cur.notice_from, "block_from": cur.block_from}
    writes: list[tuple[str, str | None]] = []
    for name, key, value in (("notice_from", P.SETTING_NOTICE_FROM, notice_from), ("block_from", P.SETTING_BLOCK_FROM, block_from)):
        if value is _UNSET:
            continue
        v = None if blank(value) else check_when(str(value))
        new[name] = v
        writes.append((key, v))
    if url is not _UNSET:
        v = None if blank(url) else str(url).strip()
        if v is not None and (len(v) > MAX_URL or not _URL.match(v)):
            raise ValueError("the upgrade link must be an https:// address")
        writes.append((P.SETTING_URL, v))
    for platform, value in (mins or {}).items():
        if platform not in P.PLATFORMS:
            raise ValueError(f"unknown platform {platform!r} (one of {', '.join(P.PLATFORMS)})")
        v = str(value).strip().lower()
        if platform == "web" and v not in P.WEB_VALUES:
            raise ValueError("web minimum is all or none")
        if platform != "web" and v != P.NONE and not _SPEC.match(v):
            raise ValueError(f"{platform} minimum must look like 2.0.0 or 2.0.0+57 (or none)")
        writes.append((P.SETTING_MIN + platform, None if v == P.DEFAULT_MIN[platform] else v))
    utc = ZoneInfo("UTC")
    n, b = at(new["notice_from"], utc), at(new["block_from"], utc)
    if n is not None and b is not None and n > b:
        raise ValueError("the notice must start before the block")
    for key, v in writes:
        if v != _setting(conn, key):
            flags.set_setting(conn, key, v, actor)
    invalidate()
    return load_fresh(conn)


def _setting(conn: psycopg.Connection, key: str) -> str | None:
    return flags.get_setting(conn, key)


def load_fresh(conn: psycopg.Connection) -> Policy:
    invalidate()
    return load(conn)


# ---------------------------------------------------------------- per request
def client_of(request: Request, viewer: dict) -> Client | None:
    if viewer.get("auth") == "session":
        return Client("web")
    if (request.headers.get("x-app-client") or "").strip().lower() == "ios-native":
        return Client("ios-native", request.headers.get("x-app-version"), request.headers.get("x-app-build"))
    return None


def below_min(pol: Policy, client: Client) -> bool:
    spec = pol.mins.get(client.platform) or P.NONE
    if spec == P.NONE:
        return False
    if client.platform == "web":
        return spec == "all"
    if vtuple(client.version) is None:   # no readable version: unknown, never blocked
        return False
    return not meets(client.version, client.build, spec)


def target_version(pol: Policy) -> str:
    """The app version people are asked to install (the native minimum without its build)."""
    spec = pol.mins.get("ios-native") or P.DEFAULT_MIN["ios-native"]
    return (P.DEFAULT_MIN["ios-native"] if spec == P.NONE else spec).partition("+")[0]


def texts(state: str, version: str, block_day: str | None) -> dict:
    title = L("请升级 Watch2Whoop", "Please update Watch2Whoop")
    if state == "block":
        msg = L(f"这个版本已停用。升级到 {version} 后继续使用，数据都在，AI 连接不受影响。",
                f"This version is no longer supported. Update to {version} to continue. Your data is safe and AI connections keep working.")
    elif block_day:
        msg = L(f"{block_day} 起旧版本停用，请尽早升级到 {version}。数据都在，AI 连接不受影响。",
                f"Older versions stop working on {block_day}. Please update to {version}. Your data is safe and AI connections keep working.")
    else:
        msg = L(f"请升级到 {version}。数据都在，AI 连接不受影响。",
                f"Please update to {version}. Your data is safe and AI connections keep working.")
    return {"title": title, "message": msg}


def status(conn: psycopg.Connection, viewer: dict, client: Client | None, now: datetime | None = None) -> dict:
    """{"state": "none"} or {"state": "notice" | "block", "must_upgrade", "min", "block_from", "url", "title", "message"}."""
    if client is None or not flags.enabled(conn, viewer["id"], P.FLAG):
        return {"state": "none", "must_upgrade": False}
    pol = load(conn)
    if not below_min(pol, client):
        return {"state": "none", "must_upgrade": False}
    tz = _tz(viewer.get("timezone"))
    notice_at, block_at = window(pol, tz)
    now = now or datetime.now(timezone.utc)
    if block_at is not None and now >= block_at:
        state = "block"
    elif notice_at is not None and now >= notice_at:
        state = "notice"
    else:
        return {"state": "none", "must_upgrade": False}
    block_day = block_at.astimezone(tz).date().isoformat() if block_at else None
    version = target_version(pol)
    return {"state": state, "must_upgrade": state == "block", "platform": client.platform, "min": version,
            "block_from": block_day, "url": pol.url, **texts(state, version, block_day)}


def for_request(conn: psycopg.Connection, request: Request, viewer: dict) -> dict:
    return status(conn, viewer, client_of(request, viewer))


def enforce(conn: psycopg.Connection, request: Request, viewer: dict) -> None:
    """Called by api.current_user for every authenticated request: 426 upgrade_required for an old client after the block."""
    path = request.url.path
    if not path.startswith("/v1/") or path.startswith(P.EXEMPT_PREFIXES):
        return
    client = client_of(request, viewer)
    if client is None:
        return
    st = status(conn, viewer, client)
    if st["state"] == "block":
        raise HTTPException(426, {"code": "upgrade_required", **st})


def describe(conn: psycopg.Connection) -> dict:
    """For `body-engine upgrade status`: flag, policy and the window in UTC."""
    flag = flags.rows(conn).get(P.FLAG) or {"state": "off"}
    pol = load_fresh(conn)
    notice_at, block_at = window(pol, ZoneInfo("UTC"))
    return {"flag": {k: flag.get(k) for k in ("state", "cohort", "percent")},
            "notice_from": pol.notice_from, "block_from": pol.block_from,
            "notice_from_effective": pol.notice_from or (f"{P.NOTICE_DAYS} days before block_from" if pol.block_from else None),
            "window_utc": {"notice": notice_at.isoformat() if notice_at else None, "block": block_at.isoformat() if block_at else None},
            "min": pol.mins, "target_version": target_version(pol),
            "url": pol.url, "url_from": "setting" if _setting(conn, P.SETTING_URL) else ("env" if pol.url else None)}
