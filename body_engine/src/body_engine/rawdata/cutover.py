"""Apple sync engine, step S5 (docs/apple-sync-engine.md §5.1–5.2; issue #219): the per-user cut-over from Open
Wearables to the app's own sync engine, and the way back.

Who is in which mode (rawdata/engine.mode): `app` only when sync_mode_state.mode = app (set here, after the gates) and
the kill switch apple_sync_primary is on for the user and they are not pinned to ow. The flag says who MAY be switched
(the deployer turns it on for a cohort, e.g. `flags set apple_sync_primary --state cohort --cohort sync_app`); this
module does the switch user by user:

- switch(user): one step. Adds the user to PRIMARY_COHORT when the flag does not reach them yet, checks the gates
  (gates(): an app ≥ CUTOVER_MIN_APP_VERSION uploaded engine batches lately; the shadow comparison is ready
  (rawdata/shadow.py: CUTOVER_CLEAN_DAYS clean days, coverage); the scores the app mode would compute for the last
  CUTOVER_CHECK_DAYS days agree with the stored ones), keeps a snapshot of those stored scores, sets the mode, then
  converts the derived data: the app's workouts lose excluded = 'shadow' and rank first among duplicates, the last
  CUTOVER_REDERIVE_DAYS days of engine data are queued again (hk_dirty_days) and derived in the app mode (daily values
  re-chosen with app above ow, the app's nights replace OW's in sleep_sessions), and the score recompute is queued.
  `force` skips the shadow and score gates (never the app version: an older app cannot feed the engine). A user without
  an OW link (a new account syncing only through the engine) needs only the app version.
- rollback(user): one step, effective at once. Pins the user to ow (engine.mode answers shadow from now on), puts the
  app's workouts back to excluded = 'shadow', removes the app's nights from sleep_sessions and imports OW's nights of
  those dates again, re-chooses the daily values without the app's, queues the score recompute. Nothing is deleted
  from the engine's raw data or from OW, so switching again later works. unpin() lets the flags decide again.
- apply(): reconcile everyone (hourly, idempotent): a switched user the flags no longer want is rolled back the same way
  (flag turned off = rollback for everyone); a user the flags want and who passes the gates is switched; after the first
  recompute in the app mode the post-switch check compares the new scores with the snapshot (post_check).
The app learns the mode from GET /v1/ingest/healthkit (engine.mode): with `app` it stops the OW SDK (keeps its
credentials and anchors), with `shadow` it starts it again (OW resumes from its anchors).

Step S5b, new accounts (flag apple_sync_new_accounts, global: only `--state all` counts, and only while apple_sync_engine
is `all` too, new_accounts()): provisioning (account/provision.py) creates no OW user and calls start_on_engine(): the
account is in the app mode from the start (nothing to compare, no OW history to lose). Such an engine-only account (no
OW link) stays in app whatever apple_sync_primary says (engine.mode) and is not rolled back without force: shadow would
only hide its data. readiness.py lists, per user, what is still missing before the switch.
"""
from __future__ import annotations

import logging
import re
import statistics
import time as _time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..rollout import flags
from . import engine as EN
from . import params as P
from . import shadow as SH

log = logging.getLogger(__name__)

PRIMARY_COHORT = "sync_app"


def _version(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("+")[0])[:3]) or (0,)


def state(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM sync_mode_state WHERE user_id = %s", (user_id,)).fetchone()


def desired(conn: psycopg.Connection, user_id: int) -> str:
    """The mode the flags and the pin ask for (the applied mode can lag until apply / switch ran)."""
    if not EN.enabled(conn, user_id):
        return "ow"
    st = state(conn, user_id)
    if st and st["pin"] == "ow":
        return "shadow"
    if st and st["mode"] == "app" and engine_only(conn, user_id):
        return "app"   # no OW path to go back to (engine.mode)
    return "app" if flags.enabled(conn, user_id, EN.PRIMARY_FLAG) else "shadow"


def engine_only(conn: psycopg.Connection, user_id: int) -> bool:
    """No OW link: the engine is this account's only sync path."""
    r = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    return r is not None and not r["ow_user_id"]


# ---------------------------------------------------------------- new accounts (S5b)
def _flag_state(conn: psycopg.Connection, key: str) -> str:
    row = flags.rows(conn).get(key)
    return row["state"] if row else "off"


def new_accounts(conn: psycopg.Connection) -> dict:
    """Whether new accounts start on the engine: {on, flag, engine, reason}. The flag is global (like open_signup: only
    `all` counts) and needs apple_sync_engine at `all` too, else a new account without OW would sync nowhere."""
    flag, engine = _flag_state(conn, EN.NEW_ACCOUNTS_FLAG), _flag_state(conn, EN.FLAG)
    reason = None if flag == "all" and engine == "all" else ("flag_off" if flag != "all" else "engine_not_all")
    if flag == "all" and reason:
        log.warning("%s is on but %s is %s: new accounts still get an OW user", EN.NEW_ACCOUNTS_FLAG, EN.FLAG, engine)
    return {"on": reason is None, "flag": flag, "engine": engine, "reason": reason}


def new_accounts_on_engine(conn: psycopg.Connection) -> bool:
    return new_accounts(conn)["on"]


def start_on_engine(conn: psycopg.Connection, user_id: int, actor: str | None = None) -> dict:
    """A new account without an OW link starts in the app mode (provisioning, flag apple_sync_new_accounts). No gates:
    nothing to compare, no OW history to lose; the app version is the app's business at setup. Idempotent; refuses a
    user who has an OW link (that one goes through switch()). Does not commit."""
    if not engine_only(conn, user_id):
        return {"started": False, "reason": "has_ow"}
    st = state(conn, user_id)
    if st and st["mode"] == "app" and st["pin"] is None:
        return {"started": False, "reason": "already_app"}
    flags.cohort_add(conn, user_id, PRIMARY_COHORT, actor=actor or "provision")
    now = datetime.now(timezone.utc)
    _set_state(conn, user_id, mode="app", pin=None, switched_at=now,
               checks={"at": now, "result": "new_account", "gates": {"shadow": {"ok": True, "reason": "no_ow"}}},
               post_check={"at": now, "skipped": "new_account"}, note=f"new account on the engine ({actor or 'provision'})")
    return {"started": True, "mode": EN.mode(conn, user_id)}


def _tz(conn, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


def _set_state(conn, user_id: int, **fields) -> None:
    conn.execute("INSERT INTO sync_mode_state (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    for k in ("checks", "pre_scores", "post_check"):
        if k in fields and fields[k] is not None:
            fields[k] = Jsonb(_jsonable(fields[k]))
    sets = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(f"UPDATE sync_mode_state SET {sets}, updated_at = now() WHERE user_id = %s", (*fields.values(), user_id))


def _jsonable(x):
    return SH._jsonable(x)


# ---------------------------------------------------------------- gates
def app_version_gate(conn: psycopg.Connection, user_id: int) -> dict:
    r = conn.execute("""SELECT app_version, received_at FROM rawdata_uploads WHERE user_id = %s AND phase IS NOT NULL
                        AND received_at > now() - %s ORDER BY received_at DESC LIMIT 1""",
                     (user_id, P.CUTOVER_APP_SEEN)).fetchone()
    seen = r["app_version"] if r else None
    return {"ok": r is not None and _version(seen) >= _version(P.CUTOVER_MIN_APP_VERSION), "app_version": seen,
            "min": P.CUTOVER_MIN_APP_VERSION, "seen_at": r["received_at"] if r else None}


def score_check(conn: psycopg.Connection, client, user_id: int, start: date, end: date,
                before: dict[date, dict] | None = None, after: dict[date, dict] | None = None) -> dict:
    """Stored scores (`before`, default: daily_scores now) vs the app mode's (`after`, default: recomputed without
    writing, shadow.app_scores) over [start, end]: mean / max |Δ| of recovery and sleep score (points) and strain."""
    before = SH.stored_scores(conn, user_id, start, end) if before is None else before
    after = SH.app_scores(conn, client, user_id, start, end) if after is None else after
    out: dict = {"start": start, "end": end, "days": 0}
    ok = True
    for key, mean_max, max_max in (("recovery", P.CUTOVER_SCORE_MEAN, P.CUTOVER_SCORE_MAX),
                                   ("sleep_score", P.CUTOVER_SCORE_MEAN, P.CUTOVER_SCORE_MAX),
                                   ("strain", P.CUTOVER_STRAIN_MEAN, P.CUTOVER_STRAIN_MAX)):
        ds = [abs(after[d][key] - b[key]) for d, b in before.items()
              if d in after and b.get(key) is not None and after[d].get(key) is not None]
        lost = sum(1 for d, b in before.items() if b.get(key) is not None and (after.get(d) or {}).get(key) is None)
        out["days"] = max(out["days"], len(ds))
        m = round(statistics.fmean(ds), 2) if ds else None
        mx = round(max(ds), 2) if ds else None
        good = lost == 0 and (m is None or (m <= mean_max and mx <= max_max))
        out[key] = {"n": len(ds), "mean_abs": m, "max_abs": mx, "lost": lost, "ok": good}
        ok = ok and good
    out["ok"] = ok
    return out


def gates(conn: psycopg.Connection, client, user_id: int, check_scores: bool = True) -> dict:
    """A user without an OW link (a new account that only ever synced through the engine) has nothing to compare and
    no OW history to lose: only the app version counts."""
    tz = _tz(conn, user_id)
    out = {"app_version": app_version_gate(conn, user_id)}
    if not conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()["ow_user_id"]:
        out["shadow"] = {"ok": True, "reason": "no_ow"}
        out["ok"] = out["app_version"]["ok"]
        return out
    s = SH.summary(conn, user_id)
    out["shadow"] = {"ok": bool(s and s["ready"]), "summary": s}
    if check_scores:
        end = datetime.now(tz).date() - timedelta(days=1)
        try:
            out["scores"] = score_check(conn, client, user_id, end - timedelta(days=P.CUTOVER_CHECK_DAYS - 1), end)
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            out["scores"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    out["ok"] = all(g["ok"] for g in out.values() if isinstance(g, dict))
    return out


# ---------------------------------------------------------------- data conversion
def _requeue(conn, user_id: int, days: int) -> date | None:
    """Queue the last `days` days of engine data for derivation again (they are derived in the current mode)."""
    tz = _tz(conn, user_id)
    since = datetime.now(tz).date() - timedelta(days=days)
    pairs = {(r["type"], r["local_date"]) for r in conn.execute(
        "SELECT DISTINCT type, local_date FROM hk_samples WHERE user_id = %s AND local_date >= %s", (user_id, since))}
    pairs |= {(r["type"], r["d"]) for r in conn.execute(
        """SELECT DISTINCT type, (bucket_start AT TIME ZONE %s)::date AS d FROM hk_stats WHERE user_id = %s
           AND bucket_start >= %s""", (str(tz), user_id, datetime.combine(since, datetime.min.time(), tz)))}
    EN.mark_dirty(conn, user_id, pairs)
    return min((d for _, d in pairs), default=None)


def _derive_now(conn, user_id: int) -> dict:
    from . import derive
    return derive.run_user(conn, user_id, deadline=_time.monotonic() + P.TIME_BUDGET_S)


def _snapshot(conn, user_id: int) -> dict:
    tz = _tz(conn, user_id)
    end = datetime.now(tz).date()
    start = end - timedelta(days=P.CUTOVER_CHECK_DAYS)
    return {d.isoformat(): v for d, v in SH.stored_scores(conn, user_id, start, end).items()}


def _to_app(conn, user_id: int) -> dict:
    from ..sync import refresh
    from ..training import importer as TI
    n = conn.execute("""UPDATE workouts SET excluded = NULL, updated_at = now() WHERE user_id = %s AND source = 'app'
                        AND excluded = 'shadow' RETURNING id""", (user_id,)).fetchall()
    TI.refresh_duplicates(conn, user_id, "app")
    first = _requeue(conn, user_id, P.CUTOVER_REDERIVE_DAYS)
    conn.commit()
    derived = _derive_now(conn, user_id)
    if first is not None:
        refresh.queue_scores(conn, user_id, first)
    return {"workouts_shown": len(n), "requeued_from": first, "derived": derived}


def _to_shadow(conn, user_id: int, client) -> dict:
    """Undo what the app mode derived (the raw data stays): workouts, nights, chosen daily values; scores queued."""
    from ..importers import common
    from ..sync import refresh
    from ..training import importer as TI
    tz = _tz(conn, user_id)
    today = datetime.now(tz).date()
    hidden = conn.execute("""UPDATE workouts SET excluded = 'shadow', updated_at = now() WHERE user_id = %s
                             AND source = 'app' AND excluded IS NULL RETURNING id""", (user_id,)).fetchall()
    TI.refresh_duplicates(conn, user_id, "shadow")
    nights = sorted({r["wake_date"] for r in conn.execute(
        "DELETE FROM sleep_sessions WHERE user_id = %s AND source = 'app' RETURNING wake_date", (user_id,))})
    days = sorted({r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_metrics WHERE user_id = %s AND source = 'app'", (user_id,))})
    if days:
        common.resolve_daily(conn, user_id, days, today, "shadow")
    conn.commit()
    restored = None
    if nights:   # OW's nights of those dates were replaced by the app's: import them again (OW still runs in the rollback period)
        if client is None or not conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()["ow_user_id"]:
            restored = {"error": "no OW client: run import-ow for these dates"}
        else:
            from ..importers import ow
            try:
                ow.run(conn, client, user_id, nights[0] - timedelta(days=1), nights[-1])
                restored = {"from": nights[0], "to": nights[-1]}
            except Exception as e:  # noqa: BLE001  the hourly import-ow fills them later
                conn.rollback()
                restored = {"error": f"{type(e).__name__}: {e}"[:300]}
    first = min([*nights, *days], default=None)
    if first is not None:
        refresh.queue_scores(conn, user_id, first)
    return {"workouts_hidden": len(hidden), "nights_removed": len(nights), "days_rechosen": len(days),
            "ow_nights": restored, "scores_from": first}


# ---------------------------------------------------------------- the commands
def switch(conn: psycopg.Connection, user_id: int, client=None, force: bool = False, dry_run: bool = False,
           check_scores: bool = True, actor: str | None = None) -> dict:
    """Switch one user to the app mode (see the module doc). Returns {switched, gates, ...} or {switched: False, reason}."""
    if not EN.enabled(conn, user_id):
        return {"switched": False, "reason": "engine_off"}
    if EN.mode(conn, user_id) == "app":
        return {"switched": False, "reason": "already_app"}
    if not flags.enabled(conn, user_id, EN.PRIMARY_FLAG) and not dry_run:
        flags.cohort_add(conn, user_id, PRIMARY_COHORT, actor=actor or "sync-mode")
        conn.commit()
    if not flags.enabled(conn, user_id, EN.PRIMARY_FLAG) and not dry_run:
        return {"switched": False, "reason": "primary_off",
                "hint": f"body-engine flags set {EN.PRIMARY_FLAG} --state cohort --cohort {PRIMARY_COHORT}"}
    g = gates(conn, client, user_id, check_scores=check_scores and not force)
    passed = g["app_version"]["ok"] and (force or g["ok"])
    if dry_run or not passed:
        if not dry_run:
            _set_state(conn, user_id, checks={"at": datetime.now(timezone.utc), "gates": g, "result": "blocked"})
            conn.commit()
        return {"switched": False, "reason": None if dry_run and passed else "gates", "would_switch": passed, "gates": g}
    _set_state(conn, user_id, mode="app", pin=None, switched_at=datetime.now(timezone.utc),
               checks={"at": datetime.now(timezone.utc), "gates": g, "forced": force, "result": "switched"},
               pre_scores=_snapshot(conn, user_id), post_check=None,
               note=f"switched by {actor or 'sync-mode'}{' (forced)' if force else ''}")
    conn.commit()
    conv = _to_app(conn, user_id)
    return {"switched": True, "mode": EN.mode(conn, user_id), "gates": g, **conv}


def rollback(conn: psycopg.Connection, user_id: int, client=None, actor: str | None = None, force: bool = False) -> dict:
    """Back to OW for this user, at once (see the module doc). An engine-only account (no OW link) is refused unless
    force: it has no OW data to go back to, the shadow mode would only hide what the engine delivers."""
    if engine_only(conn, user_id) and not force:
        return {"rolled_back": False, "reason": "no_ow", "mode": EN.mode(conn, user_id)}
    was = EN.mode(conn, user_id)
    _set_state(conn, user_id, mode="shadow", pin="ow", rolled_back_at=datetime.now(timezone.utc),
               note=f"rolled back by {actor or 'sync-mode'} from {was}")
    flags.cohort_remove(conn, user_id, PRIMARY_COHORT, actor=actor or "sync-mode")
    conn.commit()
    return {"rolled_back": True, "was": was, "mode": EN.mode(conn, user_id), **_to_shadow(conn, user_id, client)}


def unpin(conn: psycopg.Connection, user_id: int) -> dict:
    _set_state(conn, user_id, pin=None)
    conn.commit()
    return {"user_id": user_id, "desired": desired(conn, user_id), "mode": EN.mode(conn, user_id)}


def post_check(conn: psycopg.Connection, user_id: int) -> dict | None:
    """After the first recompute in the app mode: the snapshot taken before the switch vs daily_scores now."""
    st = state(conn, user_id)
    if not st or st["mode"] != "app" or not st["pre_scores"]:
        return None
    before = {date.fromisoformat(d): v for d, v in st["pre_scores"].items()}
    if not before:
        return None
    after = SH.stored_scores(conn, user_id, min(before), max(before))
    res = score_check(conn, None, user_id, min(before), max(before), before=before, after=after)
    res["at"] = datetime.now(timezone.utc)
    _set_state(conn, user_id, post_check=res)
    conn.commit()
    return res


def _recomputed_since_switch(conn, user_id: int, switched_at) -> bool:
    r = conn.execute("SELECT scored_at, status FROM sync_refresh WHERE user_id = %s", (user_id,)).fetchone()
    return bool(r and r["scored_at"] and switched_at and r["scored_at"] > switched_at and r["status"] == "idle")


def apply(conn: psycopg.Connection, user_id: int | None = None, client=None, check_scores: bool = True) -> list[dict]:
    """`body-engine sync-mode apply`: reconcile every user with the engine on (or one). Idempotent."""
    ids = {r["user_id"] for r in conn.execute("SELECT user_id FROM sync_mode_state")}
    ids |= set(SH.users(conn))
    if user_id is not None:
        ids = {user_id}
    out = []
    for uid in sorted(ids):
        try:
            st = state(conn, uid)
            want = desired(conn, uid)
            applied = st["mode"] if st else "shadow"
            if applied == "app" and want != "app":
                _set_state(conn, uid, mode="shadow", rolled_back_at=datetime.now(timezone.utc),
                           note="rolled back: the flags no longer want app")
                conn.commit()
                out.append({"user_id": uid, "action": "rollback", **_to_shadow(conn, uid, client)})
            elif applied != "app" and want == "app":
                res = switch(conn, uid, client, check_scores=check_scores)
                out.append({"user_id": uid, "action": "switch" if res["switched"] else "blocked",
                            **({"reason": res.get("reason")} if not res["switched"] else {})})
            elif applied == "app" and st["post_check"] is None and _recomputed_since_switch(conn, uid, st["switched_at"]):
                out.append({"user_id": uid, "action": "post_check", "ok": (post_check(conn, uid) or {}).get("ok")})
            else:
                out.append({"user_id": uid, "action": "none", "mode": EN.mode(conn, uid)})
        except Exception as e:  # noqa: BLE001  one user's failure must not stop the others
            conn.rollback()
            log.warning("sync-mode apply failed for user %s: %s", uid, e)
            out.append({"user_id": uid, "error": str(e)[:300]})
    return out


def status(conn: psycopg.Connection, user_id: int) -> dict:
    st = state(conn, user_id)
    return {"user_id": user_id, "mode": EN.mode(conn, user_id), "desired": desired(conn, user_id),
            "engine": EN.enabled(conn, user_id), "primary": flags.enabled(conn, user_id, EN.PRIMARY_FLAG),
            "applied": st["mode"] if st else None, "pin": st["pin"] if st else None,
            "switched_at": st["switched_at"] if st else None, "rolled_back_at": st["rolled_back_at"] if st else None,
            "checks": st["checks"] if st else None, "post_check": st["post_check"] if st else None,
            "shadow": SH.summary(conn, user_id), "app_version": app_version_gate(conn, user_id)}
