#!/usr/bin/env python3
"""Timing of `body-engine hourly-batch` with synthetic users (docs/hourly-batch.md).

Seeds N synthetic users (made-up data, no personal data) into a throwaway database body_test_bench on the Postgres of
BODY_DB_HOST/PORT, then runs compute-scores + evaluate-rules for all of them with 1..W worker processes. Open Wearables is
replaced by an in-memory fake that can sleep per 1000-sample page (--ow-latency-ms) to stand in for the HTTP round trips.
Never touches any other database.

  cd body_engine && uv run python ../tools/bench_hourly_batch.py --users 300 --days 120 --workers 1,4 --ow-latency-ms 0,20
"""
import argparse
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "body_engine" / "tests"))
from body_engine import batch  # noqa: E402
from body_engine.config import Settings  # noqa: E402
from body_engine.db import migrate  # noqa: E402
from test_scores import make_inputs  # noqa: E402
from test_scores_db import FakeOW  # noqa: E402

DB = "body_test_bench"
STATE = {"days": 120, "latency_ms": 0.0}


class SlowFakeOW(FakeOW):
    """FakeOW that sleeps per 1000-sample page like an HTTP round trip."""
    latency_ms = 0.0

    def paginate(self, path, **params):
        rows = list(super().paginate(path, **params))
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000 * max(1, math.ceil(len(rows) / 1000)))
        yield from rows


def _init_worker(days: int, latency_ms: float) -> None:
    batch._client = _fake(days, latency_ms)


def _fake(days: int, latency_ms: float) -> SlowFakeOW:
    c = SlowFakeOW(make_inputs(days, first=date.today() - timedelta(days=days - 1)))
    c.latency_ms = latency_ms
    return c


def seed(n_users: int, days: int) -> None:
    admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    admin.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    admin.execute(f'CREATE DATABASE "{DB}"')
    admin.close()
    inp = make_inputs(days, first=date.today() - timedelta(days=days - 1))
    with psycopg.connect(Settings(db_name=DB).dsn, row_factory=dict_row) as conn:
        migrate(conn)
        for i in range(n_users):
            uid = conn.execute("""INSERT INTO users (display_name, role, birth_date, timezone, ow_user_id)
                                  VALUES (%s, 'owner', '2001-06-15', 'Australia/Sydney', %s) RETURNING id""",
                               (f"bench{i}", f"ow-{i}")).fetchone()["id"]
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min,
                    deep_min, rem_min, awake_min, is_main, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,'ow')""",
                                [(uid, n.wake_date, n.start, n.end, n.asleep_min, n.asleep_min - n.deep_min - n.rem_min,
                                  n.deep_min, n.rem_min, n.awake_min) for n in inp.nights])
                rows = []
                for d, v in inp.daily_rhr.items():
                    rows += [(uid, d, "resting_heart_rate", v, "count/min", "avg", "ow"),
                             (uid, d, "step_count", 6000 + i % 50 * 100, "count", "sum", "ow"),
                             (uid, d, "active_energy_burned", 500 + i % 30, "kcal", "sum", "ow"),
                             (uid, d, "apple_exercise_time", 45, "min", "sum", "ow")]
                cur.executemany("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s)""", rows)
        conn.execute("INSERT INTO sync_state (user_id, source, last_synced_at, cursor) "
                     "SELECT id, 'ow', now(), now()::text FROM users")


def timed(workers: int, latency_ms: float, days: int, skip: bool) -> tuple:
    dsn = Settings(db_name=DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        users = batch.list_users(conn)
    t0 = time.time()
    lines: list[str] = []
    steps = ("compute-scores", "evaluate-rules")
    if workers <= 1:
        batch._client = _fake(days, latency_ms)
        res = batch.run(users, steps, workers=1, skip=skip, dsn=dsn, emit=lines.append)
    else:
        res = batch.run(users, steps, workers=workers, skip=skip, dsn=dsn, emit=lines.append,
                        initializer=_init_worker, initargs=(days, latency_ms))
    return time.time() - t0, res, lines


def typical_hour(workers: int, latency_ms: float, days: int, changed_pct: int) -> tuple:
    """A normal hour: everything was computed before, changed_pct % of the users got new data since."""
    dsn = Settings(db_name=DB).dsn
    timed(workers, latency_ms, days, skip=False)                      # state of the previous hour: everything computed
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM users ORDER BY id")]
        changed = ids[::max(1, round(100 / changed_pct))]
        for uid in changed:
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                            VALUES (%s, current_date, 'step_count', 1234, 'count', 'sum', 'ow')
                            ON CONFLICT (user_id, date, metric) DO UPDATE SET value = daily_metrics.value + 1""", (uid,))
        conn.commit()
    secs, res, _ = timed(workers, latency_ms, days, skip=True)
    return secs, res, len(changed)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=300)
    ap.add_argument("--days", type=int, default=120, help="days of history per user")
    ap.add_argument("--workers", default="1,4")
    ap.add_argument("--ow-latency-ms", default="0,20")
    ap.add_argument("--changed-pct", type=int, default=10, help="share of users with new data in the 'typical hour' run")
    a = ap.parse_args()
    t0 = time.time()
    seed(a.users, a.days)
    print(f"seeded {a.users} users x {a.days} days in {time.time() - t0:.0f} s")
    for lat in map(float, a.ow_latency_ms.split(",")):
        for w in map(int, a.workers.split(",")):
            secs, res, lines = timed(w, lat, a.days, skip=False)
            print(f"workers={w} ow-latency={lat:g}ms/page: {secs:.0f} s for {res['users']} users "
                  f"({secs / res['users']:.2f} s/user), {len(res['errors'])} errors")
    secs, res, _ = timed(1, 0, a.days, skip=True)
    print(f"all unchanged (skip on): {secs:.1f} s, {res['skipped_steps']} steps skipped")
    for lat in map(float, a.ow_latency_ms.split(",")):
        for w in map(int, a.workers.split(",")):
            secs, res, n = typical_hour(w, lat, a.days, a.changed_pct)
            print(f"typical hour, {n} of {res['users']} users changed, workers={w} ow-latency={lat:g}ms/page: {secs:.0f} s "
                  f"({res['skipped_steps']} steps skipped, {len(res['errors'])} errors)")


if __name__ == "__main__":
    main()
