"""Feature flags: ship code dark, open it to the beta cohort, then to a percentage or everyone; or turn it off again.

- A flag's key is declared in registry.py; its row in feature_flags holds the state:
  `all` -> on for everyone; `percent` -> on when the user's stable bucket (sha256 of key + user_id, 0–99.99) is below
  `percent`, and always for members of `cohort`; `cohort` -> on for members of `cohort` (user_cohorts, e.g. beta);
  `off` (or no row yet) -> off.
- `min_consent_version`: the feature stays off for a targeted user until they have agreed to that consent version; while
  they are targeted, the consent dialog asks them for it (profile/consent.required_version). So a new consent text can go
  to the beta cohort first and to everyone when the flag goes to `all`.
- enabled() is called on hot paths, so flag rows and cohort memberships are cached per process for CACHE_TTL_S; set_flag()
  and the cohort functions clear the cache of this process, other processes see a change within CACHE_TTL_S.
- Endpoints of a gated feature use `Depends(require("key"))` (404 when off, so a hidden feature is also unreachable); other
  code calls guard() (raises FlagOff, a LookupError) or enabled().
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime

import psycopg
from fastapi import Depends, HTTPException

from ..i18n import L
from . import audit
from .registry import FLAGS

STATES = ("off", "cohort", "percent", "all")
DEFAULT_COHORT = "beta"
CACHE_TTL_S = 30.0

_clock = time.monotonic
_flag_cache: dict[str, tuple[float, dict[str, dict]]] = {}                 # db name -> (at, {key: row})
_cohort_cache: dict[tuple[str, int], tuple[float, frozenset[str]]] = {}     # (db name, user_id) -> (at, cohorts)


class FlagOff(LookupError):
    pass


def invalidate() -> None:
    _flag_cache.clear()
    _cohort_cache.clear()


def _db(conn: psycopg.Connection) -> str:
    return conn.info.dbname


def _fresh(at: float) -> bool:
    return _clock() - at < CACHE_TTL_S


def _check_key(key: str) -> None:
    if key not in FLAGS:
        raise KeyError(f"unknown feature flag {key!r}: declare it in rollout/registry.py")


def rows(conn: psycopg.Connection) -> dict[str, dict]:
    hit = _flag_cache.get(_db(conn))
    if hit and _fresh(hit[0]):
        return hit[1]
    data = {r["key"]: r for r in conn.execute("SELECT * FROM feature_flags")}
    _flag_cache[_db(conn)] = (_clock(), data)
    return data


def cohorts_of(conn: psycopg.Connection, user_id: int) -> frozenset[str]:
    k = (_db(conn), user_id)
    hit = _cohort_cache.get(k)
    if hit and _fresh(hit[0]):
        return hit[1]
    got = frozenset(r["cohort"] for r in conn.execute("SELECT cohort FROM user_cohorts WHERE user_id = %s", (user_id,)))
    _cohort_cache[k] = (_clock(), got)
    return got


def bucket(salt: str, key: str, user_id: int) -> float:
    """Stable position of a user for one key, uniform in [0, 100). Different keys give independent positions."""
    h = hashlib.sha256(f"{salt}:{key}:{user_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64 * 100


def targeted(conn: psycopg.Connection, user_id: int, key: str) -> bool:
    """Whether the flag's state reaches this user (all -> percent -> cohort -> off), ignoring consent."""
    _check_key(key)
    row = rows(conn).get(key)
    if row is None or row["state"] == "off":
        return False
    if row["state"] == "all":
        return True
    if row["state"] == "percent" and bucket("flag", key, user_id) < row["percent"]:
        return True
    return row["cohort"] in cohorts_of(conn, user_id)


def enabled(conn: psycopg.Connection, user_id: int, key: str) -> bool:
    if not targeted(conn, user_id, key):
        return False
    need = rows(conn)[key]["min_consent_version"]
    if not need:
        return True
    from ..profile import consent
    return consent.agreed_at_least(conn, user_id, need)


def enabled_keys(conn: psycopg.Connection, user_id: int) -> list[str]:
    return sorted(k for k in FLAGS if enabled(conn, user_id, k))


def consent_versions_needed(conn: psycopg.Connection, user_id: int) -> list[str]:
    """min_consent_version of every flag that targets this user (profile/consent.required_version takes the max)."""
    return [rows(conn)[k]["min_consent_version"] for k in FLAGS
            if k in rows(conn) and rows(conn)[k]["min_consent_version"] and targeted(conn, user_id, k)]


def guard(conn: psycopg.Connection, user_id: int, key: str) -> None:
    if not enabled(conn, user_id, key):
        raise FlagOff(L("这个功能还没有对你开放", "This feature is not available to you yet"))


def require(key: str):
    """FastAPI dependency: 404 unless the flag is on for the signed-in user."""
    _check_key(key)
    from ..records.api import current_user, get_conn

    def dep(conn=Depends(get_conn), viewer=Depends(current_user)) -> None:
        if not enabled(conn, viewer["id"], key):
            raise HTTPException(404, L("这个功能还没有对你开放", "This feature is not available to you yet"))
    return dep


# ---------------------------------------------------------------- admin
def _plain(row: dict | None) -> dict | None:
    return None if row is None else {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


def sync(conn: psycopg.Connection) -> int:
    """Create missing rows (state off) for declared keys; refresh their descriptions. Returns rows created."""
    n = 0
    for key, desc in FLAGS.items():
        r = conn.execute("""INSERT INTO feature_flags (key, description) VALUES (%s, %s)
                            ON CONFLICT (key) DO UPDATE SET description = EXCLUDED.description
                            RETURNING (xmax = 0) AS created""", (key, desc)).fetchone()
        n += bool(r["created"])
    invalidate()
    return n


def list_flags(conn: psycopg.Connection) -> list[dict]:
    sync(conn)
    out = []
    for r in conn.execute("SELECT * FROM feature_flags ORDER BY key"):
        users = conn.execute("SELECT count(*) AS n FROM user_cohorts WHERE cohort = %s", (r["cohort"],)).fetchone()["n"]
        out.append({**r, "declared": r["key"] in FLAGS, "cohort_size": users})
    return out


def set_flag(conn: psycopg.Connection, key: str, state: str, *, cohort: str | None = None, percent: int | None = None,
             min_consent: str | None = None, clear_min_consent: bool = False, actor: str | None = None) -> dict:
    _check_key(key)
    if state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    if percent is not None and not 0 <= percent <= 100:
        raise ValueError("percent must be 0–100")
    if state == "percent" and percent is None:
        raise ValueError("--state percent needs --percent N")
    if min_consent:
        from ..profile import consent
        if consent.vkey(min_consent) > consent.vkey(consent.version()):
            raise ValueError(f"min consent {min_consent} is newer than the consent text ({consent.version()})")
    sync(conn)
    before = conn.execute("SELECT * FROM feature_flags WHERE key = %s", (key,)).fetchone()
    after = conn.execute(
        """UPDATE feature_flags SET state = %s, cohort = COALESCE(%s, cohort), percent = COALESCE(%s, percent),
                  min_consent_version = CASE WHEN %s THEN NULL ELSE COALESCE(%s, min_consent_version) END,
                  updated_at = now(), updated_by = %s
           WHERE key = %s RETURNING *""",
        (state, cohort, percent, clear_min_consent, min_consent, audit.actor(actor), key)).fetchone()
    audit.log(conn, audit.actor(actor), "flag.set", key, _plain(before), _plain(after))
    invalidate()
    return after


def get_setting(conn: psycopg.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM rollout_settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: psycopg.Connection, key: str, value: str | None, actor: str | None = None) -> None:
    """value None = remove (back to the default)."""
    before = get_setting(conn, key)
    if value is None:
        conn.execute("DELETE FROM rollout_settings WHERE key = %s", (key,))
    else:
        conn.execute("""INSERT INTO rollout_settings (key, value, updated_by) VALUES (%s, %s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(),
                        updated_by = EXCLUDED.updated_by""", (key, value, audit.actor(actor)))
    audit.log(conn, audit.actor(actor), "setting.set", key,
              None if before is None else {"value": before}, None if value is None else {"value": value})


# ---------------------------------------------------------------- cohorts
def cohort_add(conn: psycopg.Connection, user_id: int, cohort: str = DEFAULT_COHORT, actor: str | None = None) -> bool:
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise ValueError(f"no user {user_id}")
    added = conn.execute("""INSERT INTO user_cohorts (user_id, cohort, added_by) VALUES (%s, %s, %s)
                            ON CONFLICT DO NOTHING RETURNING user_id""", (user_id, cohort, audit.actor(actor))).fetchone()
    if added:
        audit.log(conn, audit.actor(actor), "cohort.add", f"{cohort}:{user_id}", None, {"user_id": user_id, "cohort": cohort})
    invalidate()
    return added is not None


def cohort_remove(conn: psycopg.Connection, user_id: int, cohort: str = DEFAULT_COHORT, actor: str | None = None) -> bool:
    gone = conn.execute("DELETE FROM user_cohorts WHERE user_id = %s AND cohort = %s RETURNING user_id",
                        (user_id, cohort)).fetchone()
    if gone:
        audit.log(conn, audit.actor(actor), "cohort.remove", f"{cohort}:{user_id}", {"user_id": user_id, "cohort": cohort}, None)
    invalidate()
    return gone is not None


def cohort_list(conn: psycopg.Connection, cohort: str | None = None) -> list[dict]:
    return conn.execute("""SELECT c.cohort, c.user_id, u.display_name, c.added_at, c.added_by FROM user_cohorts c
                           JOIN users u ON u.id = c.user_id WHERE %s::text IS NULL OR c.cohort = %s
                           ORDER BY c.cohort, c.user_id""", (cohort, cohort)).fetchall()
