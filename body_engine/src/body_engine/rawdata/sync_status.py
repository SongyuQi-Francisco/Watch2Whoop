"""Sync status of one user's app uploads (issue #219; the Today page's 「同步状态」 panel and the app's sync center):
what reached the server, what the server refused and why, and how far the pipeline (derive -> scores) got.

- record_failure(): every refused upload to /v1/ingest/* whose user is known (rawdata/api.py), as codes only (no body):
  reason auth (a revoked / wrong-scope token, a disabled or pending account), format (too large, bad gzip, validation,
  contract), refused (409: consent_required, engine_disabled, type_paused), limit (429), server (500). Repeats of the
  same reason and code within REPEAT_WINDOW update one row (n, at), so a phone retrying in a loop is one line with a
  count; rows older than KEEP are dropped when the user's next failure is written.
- collect(): the panel's data (see docs/apple-sync-engine-app-contract.md §6 for the fields). Read only, cheap (index
  lookups per type, no count over the samples), own data only.
An upload with an unknown token cannot be attributed to anyone and is not recorded (the app knows it got 401).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from . import engine as EN
from . import hk_types as T

REASONS = ("auth", "format", "refused", "limit", "server")
REPEAT_WINDOW = timedelta(minutes=10)
KEEP = timedelta(days=30)
RECENT = timedelta(hours=24)
WORKOUT = EN.WORKOUT


def reason_of(status: int) -> str:
    return {401: "auth", 403: "auth", 409: "refused", 429: "limit"}.get(status, "server" if status >= 500 else "format")


def record_failure(conn: psycopg.Connection, user_id: int, status: int, code: str | None, batch_id=None,
                   phase: str | None = None, app_version: str | None = None) -> None:
    """Write one refused upload (commits; call after the request's own transaction was rolled back). Never raises."""
    reason = reason_of(status)
    try:
        conn.rollback()
        row = conn.execute("""SELECT id FROM ingest_failures WHERE user_id = %s AND reason = %s AND code IS NOT DISTINCT FROM %s
                              AND at > now() - %s ORDER BY at DESC LIMIT 1""",
                           (user_id, reason, code, REPEAT_WINDOW)).fetchone()
        if row:
            conn.execute("""UPDATE ingest_failures SET n = n + 1, at = now(), status = %s,
                              batch_id = coalesce(%s, batch_id), phase = coalesce(%s, phase),
                              app_version = coalesce(%s, app_version) WHERE id = %s""",
                         (status, batch_id, phase, (app_version or "")[:40] or None, row["id"]))
        else:
            conn.execute("""INSERT INTO ingest_failures (user_id, reason, status, code, batch_id, phase, app_version)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                         (user_id, reason, status, (code or "")[:60] or None, batch_id, phase, (app_version or "")[:40] or None))
            conn.execute("DELETE FROM ingest_failures WHERE user_id = %s AND at < now() - %s", (user_id, KEEP))
        conn.commit()
    except Exception:  # noqa: BLE001  bookkeeping must never change the answer
        conn.rollback()


def _iso(x, tz):
    return x.astimezone(tz).isoformat(timespec="seconds") if x else None


def _latest_days(conn: psycopg.Connection, user_id: int, hk: list) -> dict:
    """HealthKit type -> the newest local day the server has of it through any way in (daily_metrics of every source:
    Open Wearables, the Apple Health export, the Shortcut, the engine; sleep_sessions; workouts). The app's phone vs
    server comparison uses it for samples older than what the engine reads (its first sync reads 14 days, the history
    backfill is optional): those are on the server already when an export or Open Wearables brought them, and are not
    "not synced" (the owner's report 2026-10-31)."""
    keys = {t.key: t.hk for t in hk if t.kind == "quantity"}
    out = {keys[r["metric"]]: r["d"] for r in conn.execute(
        """SELECT metric, max(date) AS d FROM daily_metrics WHERE user_id = %s AND metric = ANY(%s) GROUP BY metric""",
        (user_id, list(keys)))}
    r = conn.execute("""SELECT (SELECT max(wake_date) FROM sleep_sessions WHERE user_id = %(u)s) AS sleep,
                               (SELECT max(local_date) FROM workouts WHERE user_id = %(u)s AND dup_of IS NULL
                                       AND excluded IS NULL) AS workout""", {"u": user_id}).fetchone()
    for t in hk:
        d = r["sleep"] if t.status == "sleep" else r["workout"] if t.kind == "workout" else None
        if d is not None:
            out[t.hk] = d
    return out


def collect(conn: psycopg.Connection, user_id: int) -> dict:
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    now = datetime.now(timezone.utc)
    last = conn.execute("""SELECT received_at, phase, app_version, device FROM rawdata_uploads WHERE user_id = %s
                           ORDER BY received_at DESC LIMIT 1""", (user_id,)).fetchone()
    day = conn.execute("""SELECT count(*) AS batches, coalesce(sum(bytes), 0) AS bytes FROM rawdata_uploads
                          WHERE user_id = %s AND received_at > %s""", (user_id, now - RECENT)).fetchone()
    engine = EN.enabled(conn, user_id)
    hk = [t for t in T.ENGINE_TYPES if t.kind in ("quantity", "category", "workout")]
    raw_types = [t.hk for t in hk if t.raw or t.kind != "quantity"]
    stat_types = [t.hk for t in hk if t.kind == "quantity" and t.stats]
    newest = {r["t"]: (r["s"], r["r"]) for r in conn.execute(
        """SELECT t, x.s, x.r FROM unnest(%s::text[]) t, LATERAL (
               SELECT start_at AS s, received_at AS r FROM hk_samples WHERE user_id = %s AND type = t
               ORDER BY start_at DESC LIMIT 1) x""", (raw_types, user_id))}
    buckets = {r["t"]: (r["s"], r["r"]) for r in conn.execute(
        """SELECT t, x.s, x.r FROM unnest(%s::text[]) t, LATERAL (
               SELECT bucket_start AS s, updated_at AS r FROM hk_stats WHERE user_id = %s AND type = t
               ORDER BY bucket_start DESC LIMIT 1) x""", (stat_types, user_id))}
    days = _latest_days(conn, user_id, hk)
    types = []
    for t in hk:
        s, b = newest.get(t.hk), buckets.get(t.hk)
        latest = max((x for x in (s and s[0], b and b[0]) if x), default=None)
        received = max((x for x in (s and s[1], b and b[1]) if x), default=None)
        newest_day = days.get(t.hk)
        if latest is None and newest_day is None:
            continue
        types.append({"type": t.hk, "key": t.key, "kind": t.kind, "status": t.status or None,
                      "latest_sample_at": _iso(latest, tz), "received_at": _iso(received, tz),
                      "latest_day": newest_day.isoformat() if newest_day else None})
    fails = conn.execute("""SELECT reason, code, status, n, first_at, at, phase, app_version FROM ingest_failures
                            WHERE user_id = %s ORDER BY at DESC LIMIT 20""", (user_id,)).fetchall()
    counts = {r: 0 for r in REASONS}
    for f in fails:
        if f["at"] > now - RECENT:
            counts[f["reason"]] += f["n"]
    last_fail = fails[0] if fails else None
    ow = conn.execute("""SELECT max(last_synced_at) AS t FROM sync_state WHERE user_id = %s AND source = 'ow'""",
                      (user_id,)).fetchone()
    queue = conn.execute("""SELECT count(*) AS days, min(queued_at) AS oldest FROM hk_dirty_days WHERE user_id = %s""",
                         (user_id,)).fetchone()
    ref = conn.execute("SELECT status, scored_at, scores_queued_at, error FROM sync_refresh WHERE user_id = %s",
                       (user_id,)).fetchone() or {}
    run = conn.execute("""SELECT trigger, result, error, started_at, duration_ms, batches, samples FROM sync_runs
                          WHERE user_id = %s ORDER BY started_at DESC LIMIT 1""", (user_id,)).fetchone()
    scored, up = ref.get("scored_at"), last["received_at"] if last else None
    ok_since_fail = bool(up and last_fail and up > last_fail["at"])
    return {
        "mode": EN.mode(conn, user_id), "engine_enabled": engine,
        "last_upload": None if not last else {
            "at": _iso(last["received_at"], tz), "phase": last["phase"], "app_version": last["app_version"],
            "device": last["device"], "batches_24h": day["batches"], "bytes_24h": int(day["bytes"])},
        "ow_last_sync_at": _iso(ow["t"] if ow else None, tz),
        "types": types,
        "failures": {"counts_24h": counts, "recovered": ok_since_fail or (last_fail is None),
                     "last": None if not last_fail else {
                         "reason": last_fail["reason"], "code": last_fail["code"], "status": last_fail["status"],
                         "n": last_fail["n"], "first_at": _iso(last_fail["first_at"], tz), "at": _iso(last_fail["at"], tz),
                         "phase": last_fail["phase"], "app_version": last_fail["app_version"]}},
        "pipeline": {"pending_days": queue["days"], "oldest_pending_at": _iso(queue["oldest"], tz),
                     "refresh_status": ref.get("status"), "scored_at": _iso(scored, tz),
                     "scores_queued_at": _iso(ref.get("scores_queued_at"), tz), "error": ref.get("error"),
                     "upload_to_score_s": round((scored - up).total_seconds()) if scored and up and scored >= up else None,
                     "up_to_date": bool(scored and up and scored >= up and not queue["days"])},
        "last_run": None if not run else {**{k: run[k] for k in ("trigger", "result", "error", "duration_ms", "batches",
                                                                 "samples")},
                                          "started_at": _iso(run["started_at"], tz)},
    }
