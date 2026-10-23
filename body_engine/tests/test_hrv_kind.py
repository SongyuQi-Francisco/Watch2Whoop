"""Per-user HRV kind (scores/hrv_kind.py, issue #117): SDNN by default; RMSSD only when switched on and with enough
beat data; never mixed within one person (a partial run keeps the stored kind, only a full recompute switches).

Synthetic data only. Own database body_test_hrv_kind (BODY_TEST_DB_NAME_HRV_KIND), recreated on every run; skipped
without Postgres."""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rawdata import store as rx_store
from body_engine.scores import hrv_kind as HK
from body_engine.scores import params as P

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_HRV_KIND", "body_test_hrv_kind")
TZ = ZoneInfo("Australia/Sydney")
END = date(2026, 10, 20)


@pytest.fixture(scope="module")
def conn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    c = psycopg.connect(Settings(db_name=TEST_DB).dsn, row_factory=dict_row, autocommit=True)
    migrate(c)
    yield c
    c.close()


def user(conn, name: str) -> int:
    return conn.execute("INSERT INTO users (display_name, timezone) VALUES (%s, 'Australia/Sydney') RETURNING id",
                        (name,)).fetchone()["id"]


def beats_at(conn, uid, times: list[datetime]) -> None:
    """One example beat series (RMSSD 40 ms) per time."""
    rr = [1.0 + (0.02 if k % 2 else -0.02) for k in range(40)]
    beats = [sum(rr[:k]) for k in range(len(rr) + 1)]
    rx_store.save_heartbeats(conn, uid, [{"start_at": t, "end_at": t + timedelta(minutes=1), "source": "Example Watch",
                                          "origin": "app", "beats_s": beats} for t in times])


def nightly(first: date, last: date, hour: int = 2) -> list[datetime]:
    return [datetime.combine(first + timedelta(days=i), time(hour), TZ) for i in range((last - first).days + 1)]


def scores(conn, uid, first: date, last: date, kind: str) -> None:
    d = first
    while d <= last:
        conn.execute("INSERT INTO daily_scores (user_id, date, hrv_kind, algo_version) VALUES (%s, %s, %s, 'test')",
                     (uid, d, kind))
        d += timedelta(days=1)


def test_default_is_sdnn_for_everyone(conn):
    """No score_settings row (every existing user after the migration): SDNN, whatever beat data there is."""
    uid = user(conn, "example-default")
    beats_at(conn, uid, nightly(END - timedelta(days=40), END))
    assert not HK.rmssd_enabled(conn, uid) and HK.coverage(conn, uid, TZ, END)["enough"]
    assert HK.choose(conn, uid, TZ, END - timedelta(days=90), END) == ("sdnn", "default")
    assert conn.execute("SELECT count(*) AS n FROM score_settings").fetchone()["n"] == 0


def test_switched_on_needs_enough_beat_data(conn):
    uid = user(conn, "example-enabled")
    HK.set_rmssd_enabled(conn, uid, True, by="test")
    assert HK.choose(conn, uid, TZ, END - timedelta(days=90), END) == ("sdnn", "rmssd_insufficient_beats")
    # 20 of the last 28 nights: one short of RMSSD_MIN_NIGHTS
    beats_at(conn, uid, nightly(END - timedelta(days=19), END))
    cov = HK.coverage(conn, uid, TZ, END)
    assert cov["nights"] == P.RMSSD_MIN_NIGHTS - 1 and not cov["enough"]
    beats_at(conn, uid, nightly(END - timedelta(days=20), END - timedelta(days=20)))
    assert HK.coverage(conn, uid, TZ, END)["enough"]
    assert HK.choose(conn, uid, TZ, END - timedelta(days=90), END) == ("rmssd", "rmssd_enabled")
    # uploads stopped more than RMSSD_MAX_GAP_DAYS before the scoring end: not enough to switch
    later = END + timedelta(days=P.RMSSD_MAX_GAP_DAYS + 1)
    assert not HK.coverage(conn, uid, TZ, later)["enough"]
    assert HK.choose(conn, uid, TZ, later - timedelta(days=90), later)[0] == "sdnn"


def test_night_boundaries(conn):
    """18:00–12:00 local belongs to the night ending that morning; an afternoon reading is not a night."""
    uid = user(conn, "example-nights")
    beats_at(conn, uid, [datetime.combine(END - timedelta(days=1), time(19), TZ),   # night ending END
                         datetime.combine(END - timedelta(days=3), time(14), TZ),   # afternoon: not counted
                         datetime.combine(END - timedelta(days=5), time(11, 30), TZ)])
    cov = HK.coverage(conn, uid, TZ, END)
    assert cov["nights"] == 2 and cov["last_reading"] == END - timedelta(days=1)


def test_never_mixed_partial_runs_keep_the_stored_kind(conn):
    """Switched on with enough data, but the stored history is SDNN: the hourly partial run keeps SDNN; the full
    recompute (start = scoring start, nothing left outside) switches the whole history."""
    uid = user(conn, "example-switch")
    origin = END - timedelta(days=60)
    scores(conn, uid, origin, END, "sdnn")
    beats_at(conn, uid, nightly(END - timedelta(days=30), END))
    HK.set_rmssd_enabled(conn, uid, True)
    assert HK.choose(conn, uid, TZ, END - timedelta(days=2), END) == ("sdnn", "kept_until_full_recompute")
    assert HK.choose(conn, uid, TZ, origin, END) == ("rmssd", "rmssd_enabled")


def test_rmssd_user_stays_on_rmssd_through_an_upload_gap(conn):
    """Once scored with RMSSD, missing uploads do not flip the history back to SDNN: those nights just lack HRV."""
    uid = user(conn, "example-gap")
    origin = END - timedelta(days=60)
    scores(conn, uid, origin, END, "rmssd")
    HK.set_rmssd_enabled(conn, uid, True)
    assert not HK.coverage(conn, uid, TZ, END)["enough"]
    assert HK.choose(conn, uid, TZ, origin, END) == ("rmssd", "rmssd_enabled")


def test_switching_back_off(conn):
    uid = user(conn, "example-off")
    origin = END - timedelta(days=60)
    scores(conn, uid, origin, END, "rmssd")
    HK.set_rmssd_enabled(conn, uid, True)
    HK.set_rmssd_enabled(conn, uid, False, by="test")
    row = conn.execute("SELECT rmssd_enabled, updated_by FROM score_settings WHERE user_id = %s", (uid,)).fetchone()
    assert row == {"rmssd_enabled": False, "updated_by": "test"}
    assert HK.choose(conn, uid, TZ, END - timedelta(days=2), END) == ("rmssd", "kept_until_full_recompute")
    assert HK.choose(conn, uid, TZ, origin, END) == ("sdnn", "default")
