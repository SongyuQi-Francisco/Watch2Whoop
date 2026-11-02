"""2.0.0 S9: the server stops computing for app-mode users, per package (docs/ios-native-2.0.md section 4 S9, decision 4).

A package (params.COMPUTE_PACKAGES) is **primary** for a user when
- the package is implemented on this server, its flag and the global kill switch `native_compute_primary` are on for the
  user (compute.kind_state),
- a grant of this module covers the user (table app_compute_primary: the user, one of their cohorts, or everyone), made
  with `body-engine app-compute primary`, which checks the spot-check pass rate first (gate(); --force is audited),
- and every upload kind of the package is trusted (last spot check passed, enough days, the server's algorithm version,
  not forced back by an admin: compute.trusted).
Then the app shows its own values and the server stores its uploads (derived.upload). What the server stops for that user
and package (server_primary(): the same conditions without a client, plus "the phone uploaded within APP_ACTIVE_DAYS"):
- derive: hk-derive-run / sync-refresh-run derive no daily values or sleep for a queued day the phone uploaded (the
  workouts themselves are still imported: the phone's upload only enriches the server's row). A day the phone has not
  uploaded within DIRTY_GRACE_MINUTES is derived by the server again (fallback, recorded).
- workouts: no heart-rate enrichment (training/importer.enrich_pending) and no heart-rate recovery (cardio/hrr.compute in
  compute-scores) of the app's workouts.
- stress, coach (body age): the insights job after compute-scores (insights/jobs.run) writes no stress_daily /
  healthspan_weekly rows.
- rules: the hourly evaluate-rules and the refresh's rules run write no reminders / rule_runs.
- series, reports: views only, computed when asked; nothing scheduled.
The app-serving endpoints read what is stored, i.e. the phone's rows; where the phone has not uploaded anything yet the
endpoint computes on demand with the Python twin, without writing, and records a fallback (fallback()). AI / MCP tool
calls compute on demand as before (decision 4), circles read the stored copy.
Nothing changes for a user for whom no package is primary.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from ..algo import versions
from ..rollout import audit, flags
from . import compute, derived_kinds, params as P

log = logging.getLogger(__name__)
SCOPES = ("user", "cohort", "all")


class GateFailed(Exception):
    """The pass-rate gate did not pass (and no --force): nothing changed. args[0] = the gate."""


# ---------------------------------------------------------------- grants
def granted(conn: psycopg.Connection, uid: int, pkg: str) -> bool:
    """A grant of app_compute_primary covers this user for this package."""
    return conn.execute(
        """SELECT 1 FROM app_compute_primary WHERE package = %s AND (scope = 'all' OR (scope = 'user' AND user_id = %s)
           OR (scope = 'cohort' AND cohort = ANY(%s))) LIMIT 1""",
        (pkg, uid, sorted(flags.cohorts_of(conn, uid)))).fetchone() is not None


def grants(conn: psycopg.Connection, pkg: str | None = None) -> list[dict]:
    return conn.execute("""SELECT package, scope, user_id, cohort, forced, gate, granted_by, granted_at
                           FROM app_compute_primary WHERE %(p)s::text IS NULL OR package = %(p)s
                           ORDER BY package, scope, user_id NULLS FIRST, cohort""", {"p": pkg}).fetchall()


# ---------------------------------------------------------------- the server's side (no client)
def server_primary(conn: psycopg.Connection, uid: int, pkg: str) -> bool:
    """The scheduled jobs leave this package to the user's phone (see the module doc)."""
    spec = P.COMPUTE_PACKAGES[pkg]
    if not compute.package_implemented(pkg):
        return False
    if not (flags.enabled(conn, uid, spec["flag"]) and flags.enabled(conn, uid, P.COMPUTE_PRIMARY_FLAG)
            and granted(conn, uid, pkg)):
        return False
    since = datetime.now(timezone.utc) - timedelta(days=P.APP_ACTIVE_DAYS)
    for k in spec["kinds"]:
        row = compute.kind_row(conn, uid, k)
        if not compute.trusted(row, versions.current(derived_kinds.get(k).ALGO_KEY)):
            return False
        if row["last_upload_at"] is None or row["last_upload_at"] < since:
            return False   # the phone went quiet: the server computes the package again
    return True


def primary_packages(conn: psycopg.Connection, uid: int) -> set[str]:
    return {p for p in P.COMPUTE_PACKAGES if server_primary(conn, uid, p)}


def record_days(conn: psycopg.Connection, uid: int, kind: str, days) -> None:
    """The phone uploaded these days of a kind (derived.upload, accepted uploads; no commit)."""
    days = sorted(days)
    if days:
        conn.execute("""INSERT INTO app_compute_days (user_id, kind, day) SELECT %s, %s, unnest(%s::date[])
                        ON CONFLICT (user_id, kind, day) DO UPDATE SET uploaded_at = now()""", (uid, kind, days))


def uploaded_since(conn: psycopg.Connection, uid: int, wanted: list[tuple[str, date]], since: datetime) -> bool:
    """Every (kind, day) was uploaded by the phone at or after `since`."""
    if not wanted:
        return True
    got = conn.execute("""SELECT count(*) AS n FROM app_compute_days d
                          JOIN unnest(%s::text[], %s::date[]) AS w(kind, day) ON w.kind = d.kind AND w.day = d.day
                          WHERE d.user_id = %s AND d.uploaded_at >= %s""",
                       ([k for k, _ in wanted], [d for _, d in wanted], uid, since)).fetchone()["n"]
    return got == len(set(wanted))


def derive_plan(conn: psycopg.Connection, uid: int, d: date, types: set[str], queued_at: datetime, today: date) -> str:
    """A queued day of a derive-primary user: "phone" (the phone uploaded its daily values / sleep after the day was
    queued: derive only the workouts), "wait" (queued less than DIRTY_GRACE_MINUTES ago: workouts only, keep it queued) or
    "server" (the phone did not upload it in time: derive everything, as before; recorded as a fallback)."""
    from ..rawdata import derive
    wanted: list[tuple[str, date]] = []
    if derive.derived_types(types):
        wanted.append(("daily", d))
    if derive.SLEEP in types:
        wanted += [("sleep", w) for w in (d, d + timedelta(days=1)) if w <= today]
    if uploaded_since(conn, uid, wanted, queued_at):
        return "phone"
    if queued_at > datetime.now(timezone.utc) - timedelta(minutes=P.DIRTY_GRACE_MINUTES):
        return "wait"
    return "server"


def fallback(conn: psycopg.Connection, uid: int, pkg: str, path: str, day: date | None) -> None:
    """The server computed `pkg` for a primary user on demand because the phone had not uploaded it: log + count (ids and
    dates only). Never raises; no commit (the caller's request / job commits)."""
    log.warning("app-mode user %s: %s computed on the server by %s for %s (not uploaded by the phone)", uid, pkg, path, day)
    try:
        with conn.transaction():
            conn.execute("""INSERT INTO app_compute_fallbacks (user_id, package, path, day) VALUES (%s, %s, %s, %s)
                            ON CONFLICT (user_id, package, path, day)
                            DO UPDATE SET count = app_compute_fallbacks.count + 1, last_at = now()""",
                         (uid, pkg, path, day or date.today()))
    except Exception as e:  # noqa: BLE001  bookkeeping only
        log.warning("could not record the fallback: %s", e)


# ---------------------------------------------------------------- the flip (admin)
def _target(scope: str, user_id: int | None, cohort: str | None) -> str:
    return {"user": f"user:{user_id}", "cohort": f"cohort:{cohort}", "all": "all"}[scope]


def _scope(user_id: int | None, cohort: str | None, everyone: bool) -> str:
    if sum((user_id is not None, cohort is not None, bool(everyone))) != 1:
        raise ValueError("exactly one of --user-id N, --cohort C, --all")
    return "user" if user_id is not None else "cohort" if cohort is not None else "all"


def target_users(conn: psycopg.Connection, scope: str, user_id: int | None, cohort: str | None) -> list[int] | None:
    """The users a grant covers (None = everyone)."""
    if scope == "user":
        if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
            raise ValueError(f"no user {user_id}")
        return [user_id]
    if scope == "cohort":
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM user_cohorts WHERE cohort = %s ORDER BY user_id",
                                                   (cohort,))]
    return None


def gate(conn: psycopg.Connection, pkg: str, users: list[int] | None, days: int = P.PRIMARY_CHECK_DAYS) -> dict:
    """Spot-check pass rate of the package's upload kinds over these users (None = everyone) in the last `days` days:
    counts and rates only. A views-only package (series, reports) has nothing the server checks: ok, `views_only`."""
    kinds = P.COMPUTE_PACKAGES[pkg]["kinds"]
    out = {"package": pkg, "days": days, "min_checks": P.PRIMARY_MIN_CHECKS, "min_pass_rate": P.PRIMARY_MIN_PASS_RATE,
           "views_only": not kinds, "kinds": {}}
    for k in kinds:
        r = conn.execute("""SELECT count(*) AS n, count(*) FILTER (WHERE ok) AS ok, count(DISTINCT user_id) AS users
                            FROM app_compute_checks WHERE kind = %s AND checked_at >= now() - make_interval(days => %s)
                            AND (%s::int[] IS NULL OR user_id = ANY(%s))""", (k, days, users, users)).fetchone()
        rate = r["ok"] / r["n"] if r["n"] else None
        out["kinds"][k] = {"checks": r["n"], "passed": r["ok"], "users": r["users"],
                           "pass_rate": None if rate is None else round(rate, 4),
                           "ok": r["n"] >= P.PRIMARY_MIN_CHECKS and rate is not None and rate >= P.PRIMARY_MIN_PASS_RATE}
    out["ok"] = all(v["ok"] for v in out["kinds"].values())
    return out


def set_primary(conn: psycopg.Connection, pkg: str, *, user_id: int | None = None, cohort: str | None = None,
                everyone: bool = False, force: bool = False, actor: str | None = None,
                days: int = P.PRIMARY_CHECK_DAYS) -> dict:
    """Move `pkg` from shadow to primary for a user / cohort / everyone. Raises GateFailed (nothing changed) when the
    pass-rate gate fails and force is off; with force the grant is marked forced and audited as such. Commits."""
    if pkg not in P.COMPUTE_PACKAGES:
        raise ValueError(f"unknown package {pkg!r} (one of {', '.join(P.COMPUTE_PACKAGES)})")
    scope = _scope(user_id, cohort, everyone)
    g = gate(conn, pkg, target_users(conn, scope, user_id, cohort), days)
    if not g["ok"] and not force:
        raise GateFailed(g)
    forced = not g["ok"]
    who = audit.actor(actor)
    before = conn.execute("""SELECT package, scope, user_id, cohort, forced, granted_by FROM app_compute_primary
                             WHERE package = %s AND scope = %s AND coalesce(user_id, 0) = coalesce(%s, 0)
                             AND coalesce(cohort, '') = coalesce(%s, '')""", (pkg, scope, user_id, cohort)).fetchone()
    conn.execute(
        """INSERT INTO app_compute_primary (package, scope, user_id, cohort, forced, gate, granted_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (package, scope, coalesce(user_id, 0), coalesce(cohort, ''))
           DO UPDATE SET forced = EXCLUDED.forced, gate = EXCLUDED.gate, granted_by = EXCLUDED.granted_by, granted_at = now()""",
        (pkg, scope, user_id, cohort, forced, Jsonb(g), who))
    after = {"package": pkg, "scope": scope, "user_id": user_id, "cohort": cohort, "forced": forced, "gate": g}
    audit.log(conn, who, "compute.primary.force" if forced else "compute.primary", f"{pkg}:{_target(scope, user_id, cohort)}",
              None if before is None else dict(before), after)
    conn.commit()
    return after


def set_shadow(conn: psycopg.Connection, pkg: str, *, user_id: int | None = None, cohort: str | None = None,
               everyone: bool = False, actor: str | None = None) -> dict:
    """Remove the grant of exactly this target (back to shadow, unless another grant still covers someone). Commits."""
    if pkg not in P.COMPUTE_PACKAGES:
        raise ValueError(f"unknown package {pkg!r} (one of {', '.join(P.COMPUTE_PACKAGES)})")
    scope = _scope(user_id, cohort, everyone)
    gone = conn.execute("""DELETE FROM app_compute_primary WHERE package = %s AND scope = %s
                           AND coalesce(user_id, 0) = coalesce(%s, 0) AND coalesce(cohort, '') = coalesce(%s, '')
                           RETURNING package, scope, user_id, cohort, forced, granted_by""",
                        (pkg, scope, user_id, cohort)).fetchone()
    who = audit.actor(actor)
    if gone is not None:
        audit.log(conn, who, "compute.shadow", f"{pkg}:{_target(scope, user_id, cohort)}", dict(gone), None)
    conn.commit()
    return {"package": pkg, "scope": scope, "user_id": user_id, "cohort": cohort, "removed": gone is not None}


def status(conn: psycopg.Connection, user_id: int | None = None, days: int = P.PRIMARY_CHECK_DAYS) -> dict:
    """Grants, the everyone-gate per package, and for one user: per package granted / primary for the jobs, the kinds'
    state rows and the fallbacks of the last `days` days (counts only)."""
    out: dict = {"grants": [{**g, "granted_at": g["granted_at"].isoformat()} for g in grants(conn)],
                 "gates": {p: gate(conn, p, None if user_id is None else [user_id], days) for p in P.COMPUTE_PACKAGES}}
    if user_id is not None:
        out["user"] = {
            "user_id": user_id,
            "packages": {p: {"granted": granted(conn, user_id, p), "server_primary": server_primary(conn, user_id, p)}
                         for p in P.COMPUTE_PACKAGES},
            "fallbacks": [{**r, "day": r["day"].isoformat()} for r in conn.execute(
                """SELECT package, path, day, count FROM app_compute_fallbacks WHERE user_id = %s
                   AND day >= current_date - %s ORDER BY day DESC, package, path""", (user_id, days))]}
    else:
        out["fallbacks"] = conn.execute(
            """SELECT package, path, count(DISTINCT user_id) AS users, sum(count) AS count FROM app_compute_fallbacks
               WHERE day >= current_date - %s GROUP BY package, path ORDER BY package, path""", (days,)).fetchall()
    return out
