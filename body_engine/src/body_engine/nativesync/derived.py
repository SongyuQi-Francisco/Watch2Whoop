"""Rows the iOS app computed for one kind (PUT /v1/users/me/derived/app; docs/ios-native-2.0.md sections 3.2 and 3.4).

Upload: {"kind", "algo_version", "bundle_version", "days": [{"date", "rows": [...]}]}, at most DERIVED_MAX_DAYS days,
newest batch first, only settled days (the phone uploads after everything it read from HealthKit is on the server).
The kind's module (derived_kinds/<kind>.py) validates the rows, recomputes a day in Python and stores them; this file
is the generic part, the same for every kind:
- refused (409 kind_off) while the kind is `off` for this user and client (compute.kind_state);
- answered `stale_algo` (nothing kept) when `algo_version` is not the server's version of the kind's algorithm: the app
  fetches the current bundle and recomputes;
- spot check: for the first upload of a bundle version, after a failed check, and otherwise at most every
  CHECK_EVERY_HOURS, the server recomputes the newest uploaded day within CHECK_WITHIN_DAYS (before today) with the
  Python twin and compares field by field with the kind's TOLERANCE. Kept in app_compute_checks: pass / fail and the names
  of the fields beyond the tolerance, never values (section 3.4). A failure makes the kind untrusted until a check passes;
- stored as the user's rows only while the kind is `primary` and the check (if any) passed. In shadow the server keeps
  computing its own rows; the upload only feeds the spot check and the state.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..algo import versions
from ..i18n import L
from . import compute, derived_kinds, params as P, store

log = logging.getLogger(__name__)


class KindOff(Exception):
    """The kind is off for this user / client: nothing is taken."""


# ---------------------------------------------------------------- comparison
def _tol(spec, a: float, s: float) -> float:
    if isinstance(spec, (tuple, list)) and len(spec) == 2 and spec[0] == "rel":
        return float(spec[1]) * max(abs(a), abs(s))
    return float(spec)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fields_over(app_rows: list[dict], server_rows: list[dict], tolerance: dict, row_key=None) -> list[str]:
    """Names of the fields whose app and server values differ beyond the tolerance (numbers) or at all (anything else);
    "rows" when the two sides do not have the same rows. Sorted, no values."""
    if row_key is None:
        if len(app_rows) != len(server_rows):
            return ["rows"]
        pairs = list(zip(app_rows, server_rows))
    else:
        a_by = {row_key(r): r for r in app_rows}
        s_by = {row_key(r): r for r in server_rows}
        if set(a_by) != set(s_by):
            return ["rows"]
        pairs = [(a_by[k], s_by[k]) for k in a_by]
    over: set[str] = set()
    for a, s in pairs:
        for f in set(a) | set(s):
            av, sv = a.get(f), s.get(f)
            if av is None and sv is None:
                continue
            if f in tolerance and _num(av) and _num(sv):
                if not (math.isfinite(av) and math.isfinite(sv)) or abs(av - sv) > _tol(tolerance[f], av, sv) + 1e-12:
                    over.add(f)
            elif av != sv:
                over.add(f)
    return sorted(over)


# ---------------------------------------------------------------- spot check
def _check_due(row: dict | None, bundle_version: str | None) -> bool:
    if row is None or row["last_check_at"] is None or row["bundle_version"] != bundle_version or not row["trusted"]:
        return True
    return row["last_check_at"] < datetime.now(row["last_check_at"].tzinfo) - timedelta(hours=P.CHECK_EVERY_HOURS)


def _check_day(days: dict[date, list], today: date) -> date | None:
    recent = sorted((d for d in days if today - timedelta(days=P.CHECK_WITHIN_DAYS) <= d < today), reverse=True)
    with_rows = [d for d in recent if days[d]]
    return (with_rows or recent or [None])[0]


def spot_check(conn: psycopg.Connection, uid: int, mod, day: date, days: dict[date, list[dict]], algo_version: str,
               bundle_version: str | None) -> dict:
    """Recompute `day` with the kind's Python twin, compare, keep the outcome (no commit)."""
    server = mod.recompute(conn, uid, day, days)
    over = [] if server is None else fields_over(days[day], server, mod.TOLERANCE, getattr(mod, "row_key", None))
    ok = not over
    conn.execute("""INSERT INTO app_compute_checks (user_id, kind, day, algo_version, bundle_version, ok, compared, fields_over)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                 (uid, mod.KIND, day, algo_version, bundle_version, ok, server is not None, over))
    if not ok:
        log.warning("app %s rows of user %s (bundle %s) differ on %s: %s", mod.KIND, uid, bundle_version, day, over)
    return {"date": day.isoformat(), "ok": ok, "compared": server is not None, "fields_over": over}


# ---------------------------------------------------------------- upload
def validate(mod, items: list[dict], today: date) -> dict[date, list[dict]]:
    if len(items) > P.DERIVED_MAX_DAYS:
        raise ValueError(L(f"一次最多 {P.DERIVED_MAX_DAYS} 天", f"At most {P.DERIVED_MAX_DAYS} days per upload"))
    out: dict[date, list[dict]] = {}
    for item in items:
        d = item["date"]
        if d > today:
            raise ValueError(L(f"{d} 是未来的日期", f"{d} is in the future"))
        if d in out:
            raise ValueError(L(f"{d} 出现了两次", f"{d} is in the upload twice"))
        rows = item.get("rows") or []
        if len(rows) > P.DERIVED_MAX_ROWS_PER_DAY:
            raise ValueError(L(f"{d} 的行太多", f"Too many rows on {d}"))
        if any(not isinstance(r, dict) for r in rows):
            raise ValueError(L("每一行应该是对象", "Every row must be an object"))
        out[d] = list(mod.validate(d, rows))
    return out


def upload(conn: psycopg.Connection, uid: int, kind: str, algo_version: str, bundle_version: str | None,
           items: list[dict], app_version: str | None, app_build: str | None, force_check: bool = False) -> dict:
    """Take the phone's rows of one kind (see the module doc). Commits. Raises KeyError (unknown kind), KindOff,
    ValueError (malformed rows)."""
    mod = derived_kinds.get(kind)
    if mod is None:
        raise KeyError(kind)
    state = compute.kind_state(conn, uid, kind, app_version, app_build)
    if state == "off":
        raise KindOff(kind)
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
    today = datetime.now(tz).date()
    days = validate(mod, items, today)
    server_version = versions.current(mod.ALGO_KEY)
    if algo_version != server_version:
        return {"kind": kind, "status": "stale_algo", "state": state, "stored": 0, "check": None,
                "server_algo_version": server_version}
    row = compute.kind_row(conn, uid, kind)
    check = None
    day = _check_day(days, today) if days and (force_check or _check_due(row, bundle_version)) else None
    if day is not None:
        check = spot_check(conn, uid, mod, day, days, algo_version, bundle_version)
    ok_now = check is None or check["ok"]
    stored = 0
    if state == "primary" and ok_now and days:
        stored = mod.write(conn, uid, days, bundle_version)
    conn.execute(
        """INSERT INTO app_compute_state AS s (user_id, kind, algo_version, bundle_version, last_upload_at, uploads,
                                               days_uploaded, trusted, last_check_at, last_check_ok)
           VALUES (%(u)s, %(k)s, %(a)s, %(b)s, now(), 1, %(n)s, %(ok)s, %(ca)s, %(cok)s)
           ON CONFLICT (user_id, kind) DO UPDATE SET algo_version = EXCLUDED.algo_version,
             bundle_version = EXCLUDED.bundle_version, last_upload_at = now(), uploads = s.uploads + 1,
             days_uploaded = s.days_uploaded + EXCLUDED.days_uploaded,
             trusted = CASE WHEN %(checked)s THEN EXCLUDED.trusted ELSE s.trusted END,
             last_check_at = coalesce(EXCLUDED.last_check_at, s.last_check_at),
             last_check_ok = coalesce(EXCLUDED.last_check_ok, s.last_check_ok), updated_at = now()""",
        {"u": uid, "k": kind, "a": algo_version, "b": bundle_version, "n": len(days), "ok": ok_now,
         "ca": datetime.now(tz) if check else None, "cok": check["ok"] if check else None, "checked": check is not None})
    conn.commit()
    return {"kind": kind, "status": "ok" if ok_now else "mismatch", "state": state, "stored": stored, "check": check,
            "server_algo_version": server_version,
            "state_after": compute.kind_state(conn, uid, kind, app_version, app_build),
            "data_version": store.data_version(conn, uid)}


def set_server_forced(conn: psycopg.Connection, uid: int, kind: str, forced: bool) -> None:
    """Admin: the server keeps this kind for the user whatever the app uploads (forced), or the app may again. Commits."""
    conn.execute("""INSERT INTO app_compute_state (user_id, kind, server_forced) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, kind) DO UPDATE SET server_forced = EXCLUDED.server_forced, updated_at = now()""",
                 (uid, kind, forced))
    conn.commit()
