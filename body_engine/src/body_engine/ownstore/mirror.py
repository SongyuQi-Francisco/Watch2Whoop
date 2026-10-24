"""Dual write (issue #252 phase 2, rollout flag own_food_store): after a write to SparkyFitness, the dates it touched are
read back from SparkyFitness and synced into the own tables.

Reading back instead of writing the same data twice: SparkyFitness assigns the ids, converts the portions and snapshots the
exercises, so the copy matches what it actually stored; updates, deletes, rollbacks and merges are all "sync the affected
dates", with no mirror logic per operation. Anything missed (a change made in the SparkyFitness app itself, a failed sync)
is caught up by `body-engine sparky-migrate`.

Flag off: returns at once — no extra request, no table touched, exactly the behaviour before. A failing sync never fails
the write it follows: it runs in a savepoint, is logged and dropped.
"""
import logging
from datetime import date

import psycopg

from . import store

FLAG = "own_food_store"
log = logging.getLogger(__name__)


def enabled(conn: psycopg.Connection, user_id: int) -> bool:
    from ..rollout import flags
    return flags.enabled(conn, user_id, FLAG)


def _days(v) -> set[date]:
    return {d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in v or () if d}


def sync_food_day(conn: psycopg.Connection, user_id: int, client, day: date) -> dict:
    return store.apply_food(conn, user_id, day, day, client.get(f"/food-entries/by-date/{day.isoformat()}") or [])


def sync_workout_day(conn: psycopg.Connection, user_id: int, client, day: date) -> dict:
    return store.apply_workouts(conn, user_id, day, day,
                                client.get("/v2/exercise-entries/by-date", selectedDate=day.isoformat()) or [])


def sync_weight_day(conn: psycopg.Connection, user_id: int, client, day: date) -> dict:
    cur = client.get(f"/measurements/check-in/{day.isoformat()}") or {}
    if str(cur.get("entry_date") or day.isoformat())[:10] != day.isoformat():
        cur = {}   # SparkyFitness returns the latest check-in on or before the date, not only that day's
    return store.apply_measurements(conn, user_id, day, day, [cur] if cur else [])


def after_write(conn: psycopg.Connection, user_id: int, client, *, food=(), workouts=(), weight=()) -> bool:
    """Sync the given dates (food / workouts / weight) of this user from SparkyFitness into the own tables.
    Returns True when a sync ran and succeeded."""
    if not enabled(conn, user_id):
        return False
    jobs = [(sync_food_day, d) for d in sorted(_days(food))] + [(sync_workout_day, d) for d in sorted(_days(workouts))] \
        + [(sync_weight_day, d) for d in sorted(_days(weight))]
    if not jobs:
        return False
    try:
        with conn.transaction():
            for fn, d in jobs:
                fn(conn, user_id, client, d)
        return True
    except Exception as e:  # noqa: BLE001  the SparkyFitness write already happened; the migration catches up later
        log.warning("own_food_store sync failed for user %s (%s): %s: %s", user_id,
                    ", ".join(f"{fn.__name__} {d}" for fn, d in jobs), type(e).__name__, str(e)[:200])
        return False
