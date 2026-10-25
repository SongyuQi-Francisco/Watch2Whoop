"""Local copy of the Open Wearables raw samples (importers/ow_cache.py): reads through the copy hand out exactly what OW
would, only the recent tail is fetched again, a full recompute ("refresh") replaces it, import-ow fills it, a re-linked
OW user invalidates it, and the scores computed through it are identical to the ones computed straight from OW.

Uses its own test database (body_test_ow_cache, BODY_TEST_DB_NAME), reset on every test; skipped if Postgres is unreachable.
Open Wearables is an in-memory fake.
"""
import dataclasses
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import settings
from body_engine.db import migrate
from body_engine.importers import ow, ow_cache
from body_engine.scores import engine
from test_scores import make_inputs

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_ow_cache")
TEST_SETTINGS = dataclasses.replace(settings, db_name=TEST_DB)
SYD = ZoneInfo("Australia/Sydney")
UTC = timezone.utc
WATCH = {"source": "healthkit", "device_type": "watch", "device": "Watch7,9"}
PHONE = {"source": "healthkit", "device_type": "phone", "device": "iPhone17,1"}
XML = {"source": "apple_health_xml", "device_type": "watch", "device": "Watch7,9"}


@pytest.fixture
def conn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(dataclasses.replace(settings, db_name="postgres").dsn, autocommit=True)
    except (psycopg.OperationalError, RuntimeError) as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    c = psycopg.connect(TEST_SETTINGS.dsn, row_factory=dict_row)
    migrate(c)
    c.commit()
    yield c
    c.close()


class FakeOW:
    """/timeseries ([start, end), types filter, pages of `limit`), /events/sleep and /events/workouts; counts requests."""

    def __init__(self, raw=(), sleep=()):
        self.raw = list(raw)
        self.sleep = list(sleep)
        self.calls: list[tuple[str, dict]] = []

    def add(self, ts: datetime, code: str, value: float, src=WATCH, daily_total=False):
        self.raw.append({"timestamp": ts.isoformat(), "type": code, "value": value, "unit": "x", "source": src,
                         **({"is_daily_total": True} if daily_total else {})})

    def paginate(self, path, **params):
        self.calls.append((path.rsplit("/", 1)[-1], params))
        if path.endswith("/timeseries"):
            lo, hi = datetime.fromisoformat(params["start_time"]), datetime.fromisoformat(params["end_time"])
            types = params.get("types")
            yield from sorted((r for r in self.raw if (types is None or r["type"] in types)
                               and lo <= datetime.fromisoformat(r["timestamp"]) < hi),
                              key=lambda r: datetime.fromisoformat(r["timestamp"]))
        elif path.endswith("/events/sleep"):
            yield from self.sleep

    def series_calls(self):
        return [p for name, p in self.calls if name == "timeseries"]


def user(conn, ow_user="ow-1") -> int:
    uid = conn.execute("""INSERT INTO users (display_name, role, birth_date, timezone, ow_user_id)
                          VALUES ('测试', 'owner', '2001-06-15', 'Australia/Sydney', %s) RETURNING id""", (ow_user,)).fetchone()["id"]
    conn.commit()
    return uid


def flat(by_type: dict) -> list:
    return sorted((code, s.ts.astimezone(UTC), s.value, s.source, s.xml, s.priority)
                  for code, xs in by_type.items() for s in xs)


NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def world() -> FakeOW:
    fake = FakeOW()
    u = datetime(2026, 8, 31, 14, tzinfo=UTC)
    while u < NOW:
        t = u.astimezone(SYD)                     # Sydney offsets in the time stamps, as OW gives them
        u += timedelta(minutes=10)
        fake.add(t, "heart_rate", 50 + t.minute % 40)
        if t.minute == 0:
            fake.add(t, "heart_rate", 51.0, XML)                     # an export.xml copy at the same time
            fake.add(t, "steps", 12.0, PHONE)
            fake.add(t, "steps", 30.0)
            fake.add(t, "respiratory_rate", 15.5)
        if t.hour == 3 and t.minute == 0:
            fake.add(t, "heart_rate_variability_sdnn", 45.0)
            fake.add(t, "oxygen_saturation", 0.97)
            fake.add(t, "body_temperature", 35.6)
            fake.add(t, "steps", 4000.0, daily_total=True)           # whole-day totals are never handed out
            fake.add(t, "active_energy", 3.0)                        # not a cached type
    return fake


TYPES = {"heart_rate", "heart_rate_variability_sdnn", "respiratory_rate", "oxygen_saturation", "body_temperature", "steps"}
LO, HI = datetime(2026, 9, 3, tzinfo=SYD), datetime(2026, 10, 26, tzinfo=SYD)


def test_use_hands_out_what_open_wearables_has_and_fetches_only_the_tail(conn):
    uid, fake = user(conn), world()
    direct = flat(ow_cache.fetch(fake, "ow-1", TYPES, LO, HI))
    assert direct and not any(code == "active_energy" for code, *_ in direct)
    first = ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "use", now=NOW)
    conn.commit()
    assert flat(first) == direct
    fake.calls.clear()
    again = ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "use", now=NOW)
    conn.commit()
    assert flat(again) == direct
    # the second read asked OW only for the last LIVE_TAIL
    starts = {datetime.fromisoformat(p["start_time"]) for p in fake.series_calls()}
    assert starts == {NOW - ow_cache.LIVE_TAIL}
    # a narrower window inside the copy
    sub_lo, sub_hi = datetime(2026, 9, 10, 13, 5, tzinfo=SYD), datetime(2026, 9, 20, 7, tzinfo=SYD)
    assert flat(ow_cache.samples(conn, fake, uid, "ow-1", {"heart_rate"}, sub_lo, sub_hi, "use", now=NOW)) == \
        flat(ow_cache.fetch(fake, "ow-1", {"heart_rate"}, sub_lo, sub_hi))


def test_late_and_old_changes(conn):
    uid, fake = user(conn), world()
    ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "use", now=NOW)
    conn.commit()
    fake.add(NOW - timedelta(hours=20), "heart_rate", 99.0)           # uploaded late, within the tail: seen at once
    fake.add(datetime(2026, 9, 15, 4, 1, tzinfo=SYD), "heart_rate", 98.0)   # deep in the history: needs a refresh
    got = flat(ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "use", now=NOW))
    conn.commit()
    assert any(v == 99.0 for _, _, v, *_ in got) and not any(v == 98.0 for _, _, v, *_ in got)
    got = flat(ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "refresh", now=NOW))
    conn.commit()
    assert got == flat(ow_cache.fetch(fake, "ow-1", TYPES, LO, HI))
    assert flat(ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "use", now=NOW)) == got


def test_a_wider_read_and_a_relinked_user_fetch_again(conn):
    uid, fake = user(conn), world()
    ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "use", now=NOW)
    conn.commit()
    earlier = datetime(2026, 9, 1, tzinfo=SYD)
    fake.calls.clear()
    got = ow_cache.samples(conn, fake, uid, "ow-1", TYPES, earlier, HI, "use", now=NOW)
    conn.commit()
    assert flat(got) == flat(ow_cache.fetch(fake, "ow-1", TYPES, earlier, HI))
    assert {datetime.fromisoformat(p["start_time"]) for p in fake.series_calls()} >= {earlier}
    # another OW user behind the same Body Engine user: nothing of the old copy is used
    other = FakeOW()
    other.add(datetime(2026, 9, 20, tzinfo=SYD), "heart_rate", 70.0)
    got = ow_cache.samples(conn, other, uid, "ow-2", TYPES, LO, HI, "use", now=NOW)
    conn.commit()
    assert flat(got) == flat(ow_cache.fetch(other, "ow-2", TYPES, LO, HI))
    assert conn.execute("SELECT count(DISTINCT ow_user_id) AS n FROM ow_sample_cache_range WHERE user_id = %s",
                        (uid,)).fetchone()["n"] == 1


def test_import_ow_fills_the_copy(conn, monkeypatch):
    uid, fake = user(conn), world()
    monkeypatch.setattr(ow_cache, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
    from body_engine.training import importer as wk
    monkeypatch.setattr(wk, "import_ow", lambda *a, **k: {})
    ow.run(conn, fake, uid, start=date(2026, 9, 3), end=date(2026, 10, 25))
    conn.commit()
    ranges = {r["type"]: r for r in conn.execute("SELECT * FROM ow_sample_cache_range WHERE user_id = %s", (uid,))}
    assert set(ranges) == TYPES
    assert all(r["covered_from"] == LO and r["covered_to"] == NOW for r in ranges.values())
    direct = flat(ow_cache.fetch(fake, "ow-1", TYPES, LO, HI))
    fake.calls.clear()
    got = ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "use", now=NOW)
    assert flat(got) == direct
    assert {datetime.fromisoformat(p["start_time"]) for p in fake.series_calls()} == {NOW - ow_cache.LIVE_TAIL}


def test_a_broken_copy_falls_back_to_open_wearables(conn):
    uid, fake = user(conn), world()
    conn.execute("DROP TABLE ow_sample_cache_range")
    conn.commit()
    got = ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "use", now=NOW)
    assert flat(got) == flat(ow_cache.fetch(fake, "ow-1", TYPES, LO, HI))
    conn.commit()
    with pytest.raises(ValueError):
        ow_cache.samples(conn, fake, uid, "ow-1", TYPES, LO, HI, "sometimes")


class CountingOW:
    """test_scores_db's fake of the two endpoints the scoring engine uses, counting /timeseries requests."""

    def __init__(self, inp):
        self.raw, self.calls = [], 0
        for key, code in (("hr", "heart_rate"), ("hrv", "heart_rate_variability_sdnn"), ("resp", "respiratory_rate")):
            for ts, v in getattr(inp, key):
                self.raw.append({"timestamp": ts.isoformat(), "type": code, "value": v, "unit": "bpm", "source": WATCH})
        self.sleep = [{"source": {"device_name": "Watch"}, "sleep_stage_intervals": [
            {"stage": {"core": "light"}.get(st, st), "start_time": a.isoformat(), "end_time": b.isoformat()}
            for a, b, st in inp.segments]}]

    def paginate(self, path, **params):
        if path.endswith("/timeseries"):
            self.calls += 1
            lo, hi = datetime.fromisoformat(params["start_time"]), datetime.fromisoformat(params["end_time"])
            yield from (r for r in self.raw if r["type"] in params["types"] and lo <= datetime.fromisoformat(r["timestamp"]) < hi)
        elif path.endswith("/events/sleep"):
            yield from self.sleep


def test_scores_through_the_copy_are_identical(conn):
    inp = make_inputs(20)
    uid = user(conn)
    for n in inp.nights:
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min,
                          rem_min, awake_min, is_main, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,'ow')""",
                     (uid, n.wake_date, n.start, n.end, n.asleep_min, n.asleep_min - n.deep_min - n.rem_min,
                      n.deep_min, n.rem_min, n.awake_min))
    for d, v in inp.daily_rhr.items():
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                        VALUES (%s, %s, 'resting_heart_rate', %s, 'count/min', 'avg', 'ow')""", (uid, d, v))
    conn.commit()

    def scores():
        cols = ", ".join(c for c in engine.COLUMNS)
        return [dict(r) for r in conn.execute(f"SELECT date, {cols} FROM daily_scores WHERE user_id = %s ORDER BY date", (uid,))]

    client = CountingOW(inp)
    engine.run(conn, client, uid, end=date(2026, 9, 21))
    conn.commit()
    direct = scores()
    assert any(r["recovery"] is not None for r in direct)
    engine.run(conn, client, uid, end=date(2026, 9, 21), ow_cache="refresh")
    conn.commit()
    assert scores() == direct
    client.calls = 0
    engine.run(conn, client, uid, end=date(2026, 9, 21), ow_cache="use")
    conn.commit()
    got = scores()
    diff = [(a["date"], k, a[k], b[k]) for a, b in zip(direct, got) for k in a if a[k] != b[k]]
    assert diff == []
    assert client.calls <= 1, "only the recent tail is asked for"
