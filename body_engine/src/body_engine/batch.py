"""body-engine hourly-batch: the hourly / nightly job for all users in one process.

The old way started one container per user and step (import-ow, compute-scores, evaluate-rules, about 2 s of start-up
each); with 300 users that is more than an hour. Here one process loops over the users (optionally several worker
processes, each with its own database connection), runs the same code the single commands run, and isolates users and
steps from each other: an error is recorded and the next step / user carries on.

Only what changed is processed. A user whose inputs did not change since the last successful run is skipped without any
computation: a content fingerprint of the source tables (+ the user's local date) is stored per user and step
(batch_state); every writer (OW import, Shortcut upload, the app sync engine, manual logs, labs...) ends up in those
tables, so there is no queue to forget to fill. import-ow is probed with one cheap request (is there any OW sample newer
than the sync cursor?) and skipped when there is none. Unchanged steps are still recomputed as a safety net: from 4 hours
on in the user's turn (staggered by user id), at the latest after --max-age-hours (default 6), because some inputs
(SparkyFitness, late samples older than the cursor) are not in the fingerprint. A skipped step stamps batch_state.checked_at,
which the doctor counts as fresh together with daily_scores.computed_at. --full (nightly) and --no-skip run everything.

Output: one line per step in the old log format (`✓ compute-scores user 3（2026-10-22 起）（1 秒）...`), then a last line
`BATCH_RESULT {json}`. The exit code is 1 when any step failed."""
import hashlib
import json
import multiprocessing
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from . import db
from .profile import consent

STEPS = ("import-ow", "compute-scores", "evaluate-rules", "refresh-status")
DEFAULT_STEPS = ("import-ow", "compute-scores", "evaluate-rules")
LOOKBACK_DAYS = 3                 # the hourly job rewrites the last 3 days
DEFAULT_MAX_AGE_HOURS = 6.0       # an unchanged step is recomputed at the latest after this long
REFRESH_AFTER_HOURS = 4.0         # ... and from this age on in the user's turn (one hour in REFRESH_TURNS), to spread the work
REFRESH_TURNS = 4
RESULT_PREFIX = "BATCH_RESULT "

# The fingerprint covers the inputs by content, not by updated_at (import-ow rewrites the last days every hour and
# bumps updated_at even when nothing changed): an order-independent sum of row hashes of the daily values, sleep and
# workouts, the sync cursor of Open Wearables (advances when new raw samples arrived), and the upload / import counters.
FINGERPRINT_SQL = """
SELECT
  (SELECT concat_ws('|', count(*), sum(hashtext(date::text || metric || value::text || source))) FROM daily_metrics WHERE user_id = %(u)s) AS daily,
  (SELECT concat_ws('|', count(*), sum(hashtext(start_at::text || end_at::text || coalesce(asleep_min, 0) || coalesce(deep_min, 0)
                                               || coalesce(rem_min, 0) || coalesce(awake_min, 0)))) FROM sleep_sessions WHERE user_id = %(u)s) AS sleep,
  (SELECT concat_ws('|', count(*), sum(hashtext(start_at::text || end_at::text || type || coalesce(active_kcal, 0)
                                               || coalesce(avg_hr, 0) || coalesce(excluded, '')))) FROM workouts WHERE user_id = %(u)s) AS workouts,
  (SELECT string_agg(source || '=' || coalesce(cursor, ''), ',' ORDER BY source) FROM sync_state WHERE user_id = %(u)s) AS cursor,
  (SELECT concat_ws('|', count(*), max(imported_at)) FROM shortcut_uploads WHERE user_id = %(u)s) AS uploads,
  (SELECT concat_ws('|', count(*), max(updated_at)) FROM history_imports WHERE user_id = %(u)s AND status = 'done') AS imports"""


# Extra inputs of evaluate-rules / refresh-status: the user's own logs of the last two weeks.
EXTRAS_SQL = """
SELECT
  (SELECT concat_ws('|', count(*), max(updated_at)) FROM food_entries WHERE user_id = %(u)s AND date >= %(since)s) AS food,
  (SELECT concat_ws('|', count(*), max(updated_at)) FROM body_measurements WHERE user_id = %(u)s AND date >= %(since)s) AS weight,
  (SELECT concat_ws('|', count(*), max(updated_at)) FROM logged_workouts WHERE user_id = %(u)s AND date >= %(since)s) AS logged,
  (SELECT concat_ws('|', count(*), sum(hashtext(date::text || key || value::text))) FROM journal_entries
     WHERE user_id = %(u)s AND date >= %(since)s) AS journal,
  (SELECT concat_ws('|', count(*), max(updated_at)) FROM training_day_overrides WHERE user_id = %(u)s) AS overrides"""


def fingerprint(conn: psycopg.Connection, user_id: int, today: date, extras: bool = False) -> str:
    row = conn.execute(FINGERPRINT_SQL, {"u": user_id}).fetchone()
    parts = [str(v) for v in row.values()]
    if extras:
        parts += [str(v) for v in conn.execute(EXTRAS_SQL, {"u": user_id, "since": today - timedelta(days=14)}).fetchone().values()]
    return hashlib.sha256("\n".join([today.isoformat(), *parts]).encode()).hexdigest()[:32]


def _now_hour() -> int:
    return int(time.time() // 3600)


def _fresh(conn: psycopg.Connection, user_id: int, step: str, fp: str, max_age_hours: float) -> bool:
    """True = nothing changed and the last run is recent enough, so the step can be skipped."""
    r = conn.execute("""SELECT fingerprint = %s AS same, extract(epoch FROM now() - ran_at) / 3600 AS age
                        FROM batch_state WHERE user_id = %s AND step = %s""", (fp, user_id, step)).fetchone()
    if not (r and r["same"]):
        return False
    age = float(r["age"])
    if age >= max_age_hours:
        return False
    # refresh on the user's turn: a quarter of the users in each hour, so the periodic refresh is spread out
    return not (age >= REFRESH_AFTER_HOURS and (_now_hour() + user_id) % REFRESH_TURNS == 0)


def remember(conn: psycopg.Connection, user_id: int, step: str, fp: str) -> None:
    """The step ran with these inputs (also called by sync/refresh.py after its recompute, so this job skips the user)."""
    _remember(conn, user_id, step, fp)
    conn.commit()


def _remember(conn: psycopg.Connection, user_id: int, step: str, fp: str) -> None:
    conn.execute("""INSERT INTO batch_state (user_id, step, fingerprint, ran_at, checked_at) VALUES (%s, %s, %s, now(), now())
                    ON CONFLICT (user_id, step) DO UPDATE SET fingerprint = EXCLUDED.fingerprint, ran_at = now(), checked_at = now()""",
                 (user_id, step, fp))


def _checked(conn: psycopg.Connection, user_id: int, step: str) -> None:
    """The step was skipped because nothing changed: the data is verified current (the doctor reads checked_at)."""
    conn.execute("UPDATE batch_state SET checked_at = now() WHERE user_id = %s AND step = %s", (user_id, step))
    conn.commit()


def list_users(conn: psycopg.Connection, only: list[int] | None = None) -> list[dict]:
    rows = conn.execute("""
        SELECT u.id, u.display_name AS name, coalesce(u.timezone, 'Australia/Sydney') AS tz, u.ow_user_id IS NOT NULL AS ow,
               EXISTS (SELECT 1 FROM daily_metrics m WHERE m.user_id = u.id)
               OR EXISTS (SELECT 1 FROM sleep_sessions s WHERE s.user_id = u.id) AS has_data
        FROM users u ORDER BY u.id""").fetchall()
    return [r for r in rows if only is None or r["id"] in only]


def make_ow_client():
    """Hook for tests; one client per process."""
    from .ow_client import OWClient
    return OWClient()


# ---------- the steps (the same calls the single commands make) ----------

def _step_import_ow(conn, client, u):
    from .importers import ow
    return json.dumps(ow.run(conn, client, u["id"]), default=str)


def _step_compute_scores(conn, client, u, start):
    from .importers import shortcut
    from .insights import jobs as insights_jobs
    from .scores import engine as scores_engine
    from .nativesync import appscores
    # OW raw samples through the local copy (importers/ow_cache.py); the full nightly recompute re-reads and replaces it
    mode = "refresh" if start is None else "use"
    plan = appscores.plan(conn, u["id"])
    if plan["mode"] == "app":   # the user's app computes the scores (#340 N2): only the days it left stale
        if not plan["days"]:
            return json.dumps({"app_scoring": True, "rows": 0}, ensure_ascii=False)
        res = scores_engine.run(conn, client, u["id"], min(plan["days"]), None, shortcut.user_archive_dir(u["id"]),
                                ow_cache="use", only=set(plan["days"]))
        res["app_scoring"] = True
        res["insights"] = insights_jobs.run(conn, client, u["id"], ow_cache="use")
        return json.dumps(res, default=str, ensure_ascii=False)
    res = scores_engine.run(conn, client, u["id"], start, None, shortcut.user_archive_dir(u["id"]), ow_cache=mode)
    res["insights"] = insights_jobs.run(conn, client, u["id"], ow_cache=mode)
    return json.dumps(res, default=str, ensure_ascii=False)


def _step_evaluate_rules(conn, client, u):
    from .nativesync import primary
    from .rules import engine as rules_engine
    if primary.server_primary(conn, u["id"], "rules"):   # 2.0.0 S9: the user's phone evaluates the rules and uploads them
        return "app 计算（rules 已转正），服务器不写提醒"
    res = rules_engine.run(conn, u["id"])
    return (f"{res['date']} 新增 {res['inserted']}、更新 {res['updated']}、不变 {res['unchanged']}、删除 {res['deleted']}"
            + (f"；数据来源失败 {sorted(res['sources_failed'])}" if res["sources_failed"] else ""))


def _step_refresh_status(conn, client, u):
    from .assistant import status as assistant_status
    return json.dumps(assistant_status.refresh_auto(conn, u["id"]), ensure_ascii=False, default=str)


def _ow_quiet(conn: psycopg.Connection, client, u: dict) -> bool:
    """One small request: does Open Wearables have any raw sample newer than our sync cursor? No = nothing to import.
    Marks the sync as just done (the doctor watches sync_state.last_synced_at). Any doubt (no cursor, no client, an error) = False."""
    if client is None:
        return False
    row = conn.execute("SELECT cursor FROM sync_state WHERE user_id = %s AND source = 'ow'", (u["id"],)).fetchone()
    ow_user = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (u["id"],)).fetchone()["ow_user_id"]
    if not (row and row["cursor"] and ow_user):
        return False
    try:
        since = datetime.fromisoformat(row["cursor"]) + timedelta(seconds=1)
        page = client.get(f"/users/{ow_user}/timeseries", start_time=since.isoformat(),
                          end_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), resolution="raw", limit=1)
        if page["data"]:
            return False
    except Exception:  # noqa: BLE001
        return False
    conn.execute("UPDATE sync_state SET last_synced_at = now() WHERE user_id = %s AND source = 'ow'", (u["id"],))
    conn.commit()
    return True


def process_user(u: dict, steps: tuple, full: bool, skip: bool, max_age_hours: float, dsn: str | None = None,
                 client=None) -> dict:
    """All steps of one user with its own connection. Never raises: returns {"id", "lines", "errors", "skipped", "seconds"}."""
    lines: list[str] = []
    errors: list[str] = []
    skipped = 0
    t_user = time.time()
    uid = u["id"]
    try:
        conn = psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) if dsn else db.connect()
    except Exception as e:  # noqa: BLE001
        msg = f"连不上数据库: {type(e).__name__}: {e}"
        return {"id": uid, "lines": [f"✗ user {uid} {msg}"], "errors": [f"user {uid}: {msg}"], "skipped": 0, "seconds": 0}

    def step(label: str, key: str | None, fn, fp: str | None = None, quiet=None, why: str = "输入没变") -> bool:
        """quiet: an extra cheap check (called only when the stored fingerprint is still fresh); False = run anyway."""
        nonlocal skipped
        if key and fp and skip and _fresh(conn, uid, key, fp, max_age_hours) and (quiet is None or quiet()):
            _checked(conn, uid, key)
            lines.append(f"· {label} {why}，跳过")
            skipped += 1
            return True
        t0 = time.time()
        try:
            out = fn()
            if key and fp:
                _remember(conn, uid, key, fp)
            conn.commit()
            lines.append(f"✓ {label}（{time.time() - t0:.0f} 秒）{out[:600]}")
            return True
        except Exception as e:  # noqa: BLE001 one step failing must not stop the others
            conn.rollback()
            lines.append(f"✗ {label}（{time.time() - t0:.0f} 秒）{type(e).__name__}: {e}")
            lines.append(traceback.format_exc()[-1500:].rstrip())
            errors.append(f"{label}: {[f'{type(e).__name__}: {e}']}")
            return False

    try:
        if msg := consent.gate(conn, uid):
            lines.append(f"· user {uid} {msg}")
            return {"id": uid, "lines": lines, "errors": [], "skipped": 0, "seconds": time.time() - t_user}
        client = client or make_ow_client_safe()
        if "import-ow" in steps:
            from .rawdata import retire
            if retire.ow_import_stopped():   # 2.0.0 R1: OW retired; what was imported stays as it is
                if u["ow"]:
                    lines.append(f"· user {uid} {retire.SKIP_NOTE}，跳过 import-ow")
            elif u["ow"]:
                step(f"import-ow user {uid}", "import-ow", lambda: _step_import_ow(conn, client, u), "ran",
                     quiet=lambda: _ow_quiet(conn, client, u), why="OW 没有比同步游标更新的样本")
            else:
                lines.append(f"· user {uid} 没关联 Open Wearables，跳过 import-ow")
        if not u["has_data"]:
            # an import-ow just above may have brought the first data
            u = {**u, "has_data": bool(conn.execute("""SELECT EXISTS (SELECT 1 FROM daily_metrics WHERE user_id = %s)
                                                         OR EXISTS (SELECT 1 FROM sleep_sessions WHERE user_id = %s) AS d""",
                                                      (uid, uid)).fetchone()["d"])}
        if not u["has_data"]:
            lines.append(f"· user {uid} 还没有任何数据，跳过 compute-scores")
            return {"id": uid, "lines": lines, "errors": errors, "skipped": skipped, "seconds": time.time() - t_user}
        today = datetime.now(ZoneInfo(u["tz"])).date()
        fp = fingerprint(conn, uid, today)
        fp_extras = fingerprint(conn, uid, today, extras=True)
        if "compute-scores" in steps:
            if full:
                step(f"compute-scores user {uid}（完整）", "compute-scores",
                     lambda: _step_compute_scores(conn, client, u, None), fp)
            else:
                start = today - timedelta(days=LOOKBACK_DAYS)
                step(f"compute-scores user {uid}（{start.isoformat()} 起）", "compute-scores",
                     lambda: _step_compute_scores(conn, client, u, start), fp)
        if "evaluate-rules" in steps:
            step(f"evaluate-rules user {uid}", "evaluate-rules", lambda: _step_evaluate_rules(conn, client, u), fp_extras)
        if "refresh-status" in steps:
            step(f"refresh-status user {uid}", "refresh-status", lambda: _step_refresh_status(conn, client, u), fp_extras)
    except Exception as e:  # noqa: BLE001
        lines.append(f"✗ user {uid} {type(e).__name__}: {e}")
        errors.append(f"user {uid}: {type(e).__name__}: {e}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return {"id": uid, "lines": lines, "errors": errors, "skipped": skipped, "seconds": time.time() - t_user}


_client = None


def make_ow_client_safe():
    """One OW client per process; None when the API key is missing (users without OW data do not need it)."""
    global _client
    if _client is None:
        try:
            _client = make_ow_client()
        except Exception:  # noqa: BLE001
            return None
    return _client


def _worker(args):
    return process_user(*args)


def run(users: list[dict], steps: tuple = DEFAULT_STEPS, workers: int = 1, full: bool = False, skip: bool = True,
        max_age_hours: float = DEFAULT_MAX_AGE_HOURS, dsn: str | None = None, emit=print, client=None,
        initializer=None, initargs: tuple = ()) -> dict:
    """Process the users; emit(line) gets every log line as soon as a user finishes. Returns the summary."""
    t0 = time.time()
    errors: list[str] = []
    skipped = 0
    done = 0

    def collect(res):
        nonlocal skipped, done
        for line in res["lines"]:
            emit(line)
        errors.extend(res["errors"])
        skipped += res["skipped"]
        done += 1

    jobs = [(u, steps, full, skip, max_age_hours, dsn) for u in users]
    if workers <= 1 or len(users) <= 1:
        for j in jobs:
            collect(process_user(*j, client=client))
    else:
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=initializer, initargs=initargs) as pool:
            futures = {pool.submit(_worker, j): j[0] for j in jobs}
            for f in as_completed(futures):
                u = futures[f]
                try:
                    collect(f.result())
                except Exception as e:  # noqa: BLE001 a worker process died (e.g. killed for memory)
                    emit(f"✗ user {u['id']} 工作进程异常退出: {type(e).__name__}: {e}")
                    errors.append(f"user {u['id']}: 工作进程异常退出 {type(e).__name__}")
                    done += 1
    return {"users": len(users), "done": done, "skipped_steps": skipped, "errors": errors,
            "seconds": round(time.time() - t0, 1), "workers": workers, "full": full}


def main(args) -> int:
    with db.connect() as conn:
        users = list_users(conn, [args.user_id] if args.user_id else None)
    steps = tuple(s for s in STEPS if s in set(args.steps.split(",")))
    print("用户：" + ", ".join(f"{u['id']} {u['name']}" for u in users), flush=True)
    res = run(users, steps, workers=args.workers, full=args.full, skip=not (args.full or args.no_skip),
              max_age_hours=args.max_age_hours, emit=lambda line: print(line, flush=True))
    if not args.user_id:   # the native app's change log: keep the newest row per key (nativesync/store.py)
        try:
            from .nativesync import store as sync_store
            with db.connect() as conn:
                res["sync_changes_compacted"] = sync_store.compact(conn)
        except Exception as e:  # noqa: BLE001  housekeeping only
            res["errors"].append(f"sync-compact: {type(e).__name__}: {e}")
    print(f"{len(users)} 个用户，{res['skipped_steps']} 步没变跳过，{len(res['errors'])} 项失败（{res['seconds']:.0f} 秒，{args.workers} 个进程）")
    print(RESULT_PREFIX + json.dumps(res, ensure_ascii=False), flush=True)
    return 1 if res["errors"] else 0
