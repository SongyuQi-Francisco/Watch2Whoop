"""Importer replays: the same upload twice, and an upload split across the user's midnight, give the same tables as one clean import
(retrospective 2026-11, section 10 F; "importers are idempotent" is a project rule, here it is tested table by table).

Three variants of one synthetic day set are imported for three users with the same time zone, and their rows are compared with
the user id, ids and import timestamps taken out:
  once   - everything imported in one go, at 00:10 the next morning (user's time);
  twice  - the same upload imported again (the same file again, and the same rows re-sent as a new file, which is what the phone's
           overlapping 2-day / 7-day uploads do);
  split  - the first half imported at 23:50 (a partial day: `resolve_daily` treats "today" as partial), the second half at 00:10.
Two channels: the iPhone Shortcut (cols-v1 files, importers/shortcut.py) and the app's native upload (POST /v1/ingest/healthkit
`quantities[]`, rawdata/quantities.py). Also: samples around the two daylight-saving changes of a zone land on the right local day, and
a sleep that spans one has its real duration (instants, not wall-clock minutes).

Own test database BODY_TEST_DB_NAME (default body_test_importer_replay), recreated each run; skipped without Postgres.
Synthetic data only; no health_data/, no production database.
"""
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import clock
from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import shortcut as sc
from body_engine.rawdata import hk_types as T

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_importer_replay")
SYD, LA = "Australia/Sydney", "America/Los_Angeles"
Q = "HKQuantityTypeIdentifier"
PHONE, WATCH = "测试 iPhone", "测试 Apple Watch"
# columns that differ between two imports of the same data without meaning anything: ids, import / update stamps, the file a row came from
VOLATILE = {"id", "user_id", "updated_at", "imported_at", "upload", "received_at", "requested_at", "scored_at", "batch_id",
            "range_id", "import_queued_at", "scores_queued_at", "dup_of"}


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def new_user(conn, zone: str, name: str) -> tuple[int, str]:
    uid = conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                          VALUES (%s, 'member', %s, '2026-10-01', '2026-10-22.1') RETURNING id""", (name, zone)).fetchone()["id"]
    return uid, auth.create_token(conn, uid, "t", ["read", "write"])


def snapshot(conn, uid: int, tables: tuple[str, ...]) -> dict[str, list[str]]:
    out = {}
    for t in tables:
        rows = conn.execute(f"SELECT to_jsonb(x) AS j FROM {t} x WHERE user_id = %s", (uid,)).fetchall()
        out[t] = sorted(json.dumps({k: v for k, v in r["j"].items() if k not in VOLATILE}, sort_keys=True, ensure_ascii=False)
                        for r in rows)
    return out


def same(a: dict, b: dict, what: str) -> None:
    assert a.keys() == b.keys()
    for t in a:
        assert a[t] == b[t], f"{what}: table {t} differs\n only in first: {sorted(set(a[t]) - set(b[t]))[:3]}\n only in second: {sorted(set(b[t]) - set(a[t]))[:3]}"


def at(zone: str, day: date, hh: int, mm: int = 0, ss: int = 0, fold: int = 0) -> datetime:
    return clock.local(zone, day.year, day.month, day.day, hh, mm, ss, fold=fold)


# ================================================================== the iPhone Shortcut (cols-v1 files)
SHORTCUT_TABLES = ("shortcut_samples", "daily_metric_sources", "daily_metrics", "sleep_sessions", "workouts")


def cols_file(rows: list[tuple]) -> str:
    """rows: (type, start, end, value, unit, source); a None unit / source is left out of its column, like the Shortcut does."""
    out = ["#format=cols-v1"]
    for i, name in enumerate(sc.COLS):
        out.append(f"@@{name}")
        out += [r[i] for r in rows if r[i] is not None]
    return "\n".join(out) + "\n"


def sample_rows(zone: str, day: date, lo: datetime, hi: datetime) -> list[tuple]:
    """One Sunday of an active person plus the first minutes of the Monday (local days `day`, `day+1`), restricted to samples that START in
    [lo, hi): the sleep that began the evening before and ended 07:00, raw steps and active energy per half hour from 09:00 to 00:08, raw
    heart rate and exercise minutes around a 40-minute workout at 18:00, and a night's sleep that begins at 23:40 (so that it is cut in
    two by the 23:50 / 00:10 split)."""
    nxt = day + timedelta(days=1)
    z = ZoneInfo(zone)
    rows: list[tuple] = []

    def add(kind, start, end, value, unit, source):
        if lo <= start.astimezone(z) < hi:
            rows.append((kind, start.isoformat(), end.isoformat(), value, unit, source))

    d0 = at(zone, day, 0)
    add("Sleep", d0 - timedelta(hours=1, minutes=30), d0 + timedelta(hours=2), "Core", None, None)
    add("Sleep", d0 + timedelta(hours=2), d0 + timedelta(hours=3), "Deep", None, None)
    add("Sleep", d0 + timedelta(hours=3), d0 + timedelta(hours=3, minutes=20), "Awake", None, None)
    add("Sleep", d0 + timedelta(hours=3, minutes=20), d0 + timedelta(hours=7), "REM", None, None)
    for i in range(0, 30 * 31, 30):                                             # 09:00 ... 23:30 on `day`, and 00:00 on `nxt`
        s = at(zone, day, 9) + timedelta(minutes=i)
        add("Steps", s, s + timedelta(minutes=29), "200", "count", PHONE)
        add("Active Calories", s, s + timedelta(minutes=29), "12", "kcal", WATCH)
    add("Steps", at(zone, day, 23, 52), at(zone, day, 23, 58), "40", "count", PHONE)
    add("Steps", at(zone, nxt, 0, 2), at(zone, nxt, 0, 6), "35", "count", PHONE)
    for m in range(0, 40, 2):
        add("Heart Rate", at(zone, day, 18, m), at(zone, day, 18, m), str(120 + m), "count/min", WATCH)
    for m in range(0, 40):
        add("Exercise Time", at(zone, day, 18, m), at(zone, day, 18, m + 1), "1", "min", WATCH)
    add("Heart Rate", at(zone, day, 23, 55), at(zone, day, 23, 55), "62", "count/min", WATCH)
    add("Heart Rate", at(zone, nxt, 0, 5), at(zone, nxt, 0, 5), "58", "count/min", WATCH)
    add("Sleep", at(zone, day, 23, 40), at(zone, day, 23, 50), "Core", None, None)             # falls asleep ...
    add("Sleep", at(zone, day, 23, 50), at(zone, nxt, 0, 8), "Core", None, None)               # ... and sleeps on into Monday
    return rows


def write_upload(folder, name: str, rows: list[tuple]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(cols_file(rows))


@pytest.mark.parametrize("zone", [SYD, LA])
def test_shortcut_same_upload_twice_changes_nothing(zone, conn, tmp_path, frozen_clock):
    day = date(2026, 11, 22)
    rows = sample_rows(zone, day, at(zone, day, 0) - timedelta(days=1), at(zone, day, 23, 59, 59) + timedelta(days=1))
    clock_ = frozen_clock(at(zone, day + timedelta(days=1), 0, 10))
    once, twice = new_user(conn, zone, "once")[0], new_user(conn, zone, "twice")[0]
    write_upload(tmp_path / "once", "health_2026-11-23_001000.txt", rows)
    write_upload(tmp_path / "twice", "health_2026-11-23_001000.txt", rows)
    sc.run(conn, once, tmp_path / "once")
    sc.run(conn, twice, tmp_path / "twice")
    ref = snapshot(conn, once, SHORTCUT_TABLES)
    assert ref["daily_metrics"] and ref["sleep_sessions"] and ref["workouts"], "the synthetic day must exercise every table"
    # 1. the very same file again (all_files: the "already imported" skip is off), and again via the incremental path
    sc.run(conn, twice, tmp_path / "twice", all_files=True)
    sc.run(conn, twice, tmp_path / "twice")
    same(ref, snapshot(conn, twice, SHORTCUT_TABLES), "same file twice")
    # 2. the same rows as a NEW file an hour later (the phone re-sends an overlapping window) — the rows are the same, the day is the same
    clock_.advance(hours=1)
    write_upload(tmp_path / "twice", "health_2026-11-23_011000.txt", rows)
    sc.run(conn, twice, tmp_path / "twice")
    same(ref, snapshot(conn, twice, SHORTCUT_TABLES), "same rows in a second file")


@pytest.mark.parametrize("zone", [SYD, LA])
def test_shortcut_upload_split_across_midnight_equals_one_import(zone, conn, tmp_path, frozen_clock):
    day = date(2026, 11, 22)                       # a Sunday; the week turns over at the user's midnight
    nxt = day + timedelta(days=1)
    everything = sample_rows(zone, day, at(zone, day, 0) - timedelta(days=1), at(zone, nxt, 1))
    first = sample_rows(zone, day, at(zone, day, 0) - timedelta(days=1), at(zone, day, 23, 50))     # what the phone has at 23:50
    second = sample_rows(zone, day, at(zone, day, 23, 50), at(zone, nxt, 1))                          # 23:50 on (the file is uploaded at 00:10)
    assert len(first) + len(second) == len(everything) and first and second
    once, split = new_user(conn, zone, "once")[0], new_user(conn, zone, "split")[0]
    clock_ = frozen_clock(at(zone, day, 23, 50))
    write_upload(tmp_path / "split", "health_2026-11-22_235000.txt", first)
    sc.run(conn, split, tmp_path / "split")
    assert len(snapshot(conn, split, SHORTCUT_TABLES)["sleep_sessions"]) == 1       # the morning's; 10 minutes of the next night are no session
    clock_.move_to(at(zone, nxt, 0, 10))
    write_upload(tmp_path / "split", "health_2026-11-23_001000.txt", second)
    sc.run(conn, split, tmp_path / "split")
    write_upload(tmp_path / "once", "health_2026-11-23_001000.txt", everything)
    sc.run(conn, once, tmp_path / "once")
    same(snapshot(conn, once, SHORTCUT_TABLES), snapshot(conn, split, SHORTCUT_TABLES), "23:50 + 00:10 vs one import at 00:10")
    mins = [r["asleep_min"] for r in conn.execute("SELECT asleep_min FROM sleep_sessions WHERE user_id = %s ORDER BY start_at", (split,))]
    assert len(mins) == 2 and mins[-1] == 28, "the sleep that began before the split is one session of 28 minutes"


# ================================================================== daylight-saving days: which local day a sample belongs to
def hourly_steps(zone: str, day: date) -> list[tuple]:
    """Raw steps, 100 each from one device, one sample at :30 of every REAL hour of the local day `day` (23, 24 or 25 of them), plus the
    neighbours: 23:30 the evening before (7 steps) and 00:30 the morning after (9)."""
    z = ZoneInfo(zone)
    start = at(zone, day, 0).astimezone(clock.UTC)
    hours = int((at(zone, day + timedelta(days=1), 0).astimezone(clock.UTC) - start).total_seconds() // 3600)
    out = []
    for i in range(hours):
        s = start + timedelta(hours=i, minutes=30)          # arithmetic on UTC: `+ timedelta` on a ZoneInfo time is wall-clock and loses `fold`
        out.append(("Steps", s.astimezone(z).isoformat(), (s + timedelta(minutes=10)).astimezone(z).isoformat(), "100", "count", PHONE))
    prev, nxt = day - timedelta(days=1), day + timedelta(days=1)
    out.append(("Steps", at(zone, prev, 23, 30).isoformat(), at(zone, prev, 23, 40).isoformat(), "7", "count", PHONE))
    out.append(("Steps", at(zone, nxt, 0, 30).isoformat(), at(zone, nxt, 0, 40).isoformat(), "9", "count", PHONE))
    return out


# (zone, the day the clock changes, hours in that local day)
DST_DAYS = [(SYD, date(2026, 10, 4), 23), (SYD, date(2027, 4, 4), 25), (LA, date(2026, 11, 1), 25), (LA, date(2027, 3, 14), 23)]


@pytest.mark.parametrize("zone,day,hours", DST_DAYS, ids=[f"{z.split('/')[1]}-{d}" for z, d, _ in DST_DAYS])
def test_shortcut_samples_land_on_the_right_day_across_a_clock_change(zone, day, hours, conn, tmp_path, frozen_clock):
    """One sample in every real hour of a 23- or 25-hour day: the day has all of them (2300 / 2500 steps), the neighbours stay on theirs."""
    start, end = at(zone, day, 0), at(zone, day + timedelta(days=1), 0)
    assert (end.astimezone(clock.UTC) - start.astimezone(clock.UTC)) == timedelta(hours=hours)
    frozen_clock(at(zone, day + timedelta(days=2), 9))
    uid = new_user(conn, zone, f"dst-{day}")[0]
    rows = hourly_steps(zone, day)
    assert len(rows) == hours + 2
    write_upload(tmp_path, "health_x.txt", rows)
    sc.run(conn, uid, tmp_path)
    got = {r["date"]: r["value"] for r in conn.execute(
        "SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'step_count'", (uid,))}
    assert got == {day - timedelta(days=1): 7, day: 100 * hours, day + timedelta(days=1): 9}, got


@pytest.mark.parametrize("session", ["server", "user"])
@pytest.mark.parametrize("zone,day,hours", DST_DAYS, ids=[f"{z.split('/')[1]}-{d}" for z, d, _ in DST_DAYS])
def test_a_sleep_across_a_clock_change_has_its_real_length(zone, day, hours, session, conn, tmp_path, frozen_clock):
    """Bed at 22:30 the evening before, up at 07:30 on the clock-change day: 9 hours on the wall clock, 8 real hours when an hour was skipped
    and 10 when one repeated. The night belongs to the morning it ended on. The samples come back from the database as times in the
    SESSION's zone: Python subtracts two times of the same ZoneInfo on the wall clock, so this holds only if the importer pins them
    to their offsets first (importers.ow._local did, the Shortcut path did not). `session`: the database session runs in the server's zone
    (compose.yaml: TZ Australia/Sydney, what production does) or in the user's own."""
    frozen_clock(at(zone, day + timedelta(days=1), 9))
    if session == "user":
        conn.execute(f"SET TIME ZONE '{zone}'")
    uid = new_user(conn, zone, f"sleep-{day}-{session}")[0]
    bed = at(zone, day - timedelta(days=1), 22, 30)
    wake = at(zone, day, 7, 30)
    real_min = (wake.astimezone(clock.UTC) - bed.astimezone(clock.UTC)).total_seconds() / 60
    assert real_min == 540 + {23: -60, 25: 60}[hours]
    write_upload(tmp_path, "health_x.txt", [("Sleep", bed.isoformat(), wake.isoformat(), "Core", None, None)])
    sc.run(conn, uid, tmp_path)
    s = conn.execute("SELECT wake_date, asleep_min, core_min FROM sleep_sessions WHERE user_id = %s", (uid,)).fetchall()
    assert s == [{"wake_date": day, "asleep_min": real_min, "core_min": real_min}], s


@pytest.mark.parametrize("session", ["server", "user"])
@pytest.mark.parametrize("zone,day,hours", DST_DAYS, ids=[f"{z.split('/')[1]}-{d}" for z, d, _ in DST_DAYS])
def test_a_short_wake_across_a_clock_change_does_not_split_the_night(zone, day, hours, session, conn, tmp_path, frozen_clock):
    """Two sleep segments with a 30-minute (real) gap between them, the gap straddling the clock change: one night. Counted on the wall
    clock the gap would read 90 minutes after a skipped hour (> the 60 that cuts a night in two) and 30 after a repeated one."""
    frozen_clock(at(zone, day + timedelta(days=1), 9))
    if session == "user":
        conn.execute(f"SET TIME ZONE '{zone}'")
    uid = new_user(conn, zone, f"gap-{day}-{session}")[0]
    # the change is at 02:00 local: the first part ends at 01:50 local-before-the-change, the second one begins 30 real minutes later
    midnight_utc = at(zone, day, 0).astimezone(clock.UTC)
    change_utc = next(midnight_utc + timedelta(minutes=m) for m in range(0, 26 * 60)
                      if (midnight_utc + timedelta(minutes=m)).astimezone(ZoneInfo(zone)).utcoffset()
                      != midnight_utc.astimezone(ZoneInfo(zone)).utcoffset())
    first_end = (change_utc - timedelta(minutes=10)).astimezone(ZoneInfo(zone))
    second_start = (change_utc + timedelta(minutes=20)).astimezone(ZoneInfo(zone))
    rows = [("Sleep", (first_end - timedelta(hours=3)).isoformat(), first_end.isoformat(), "Core", None, None),
            ("Sleep", second_start.isoformat(), (second_start + timedelta(hours=3)).astimezone(ZoneInfo(zone)).isoformat(), "Core", None, None)]
    write_upload(tmp_path, "health_x.txt", rows)
    sc.run(conn, uid, tmp_path)
    s = conn.execute("SELECT asleep_min, start_at, end_at FROM sleep_sessions WHERE user_id = %s", (uid,)).fetchall()
    assert len(s) == 1 and s[0]["asleep_min"] == 360, s


# ================================================================== the app's native upload (POST /v1/ingest/healthkit quantities[])
NATIVE_TABLES = ("hk_samples", "daily_metric_sources", "daily_metrics")


def group(hk: str, items: list[dict]) -> dict:
    return {"type": Q + hk, "unit": T.BY_HK[Q + hk].unit, "items": items}


WIRE = "utc"     # how the app writes instants in the JSON: "utc" (…+00:00, what an ISO 8601 formatter gives by default) or "local" (the phone's own offset)


def qsample(uid_: str, start: datetime, value: float, source="Apple Watch", minutes=5) -> dict:
    end = (start.astimezone(clock.UTC) + timedelta(minutes=minutes)).astimezone(start.tzinfo)
    shown = (lambda d: d.astimezone(clock.UTC)) if WIRE == "utc" else (lambda d: d)
    return {"uuid": uid_, "start": shown(start).isoformat(), "end": shown(end).isoformat(), "value": value, "source": source}


def native_samples(zone: str, day: date) -> tuple[list[dict], list[dict]]:
    """(stand minutes — summed, wrist temperature — averaged) around the user's midnight between `day` and `day+1`, fixed UUIDs."""
    nxt = day + timedelta(days=1)
    stand = [qsample(str(uuid.UUID(int=0x5000 + i)), s, v) for i, (s, v) in enumerate((
        (at(zone, day, 9, 5), 4), (at(zone, day, 14, 5), 6), (at(zone, day, 23, 40), 2),
        (at(zone, day, 23, 55), 1), (at(zone, nxt, 0, 3), 1), (at(zone, nxt, 0, 7), 3)))]
    temp = [qsample(str(uuid.UUID(int=0x6000 + i)), s, v, minutes=240) for i, (s, v) in enumerate((
        (at(zone, day, 2, 0), 35.4), (at(zone, day, 23, 30), 35.9), (at(zone, nxt, 0, 5), 36.1)))]
    return stand, temp


def post(client, token, stand, temp, batch_id=None) -> dict:
    body = {"batch_id": batch_id or str(uuid.uuid4()), "device": "iPhone", "app_version": "2.1.0+1",
            "quantities": [g for g in (group("AppleStandTime", stand) if stand else None,
                                       group("AppleSleepingWristTemperature", temp) if temp else None) if g]}
    r = client.post("/v1/ingest/healthkit", json=body, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


def refresh_row(conn, uid) -> dict | None:
    return conn.execute("SELECT status, scores_from FROM sync_refresh WHERE user_id = %s", (uid,)).fetchone()


@pytest.fixture(params=["utc", "local"])
def wire(request, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "WIRE", request.param)
    return request.param


@pytest.mark.parametrize("zone", [SYD, LA])
def test_native_upload_twice_changes_nothing(zone, wire, client, conn, frozen_clock):
    day = date(2026, 11, 22)
    frozen_clock(at(zone, day + timedelta(days=1), 0, 10))
    stand, temp = native_samples(zone, day)
    (a, ta), (b, tb) = new_user(conn, zone, "n-once"), new_user(conn, zone, "n-twice")
    post(client, ta, stand, temp)
    post(client, tb, stand, temp)
    ref = snapshot(conn, a, NATIVE_TABLES)
    assert ref["daily_metrics"] and len(ref["hk_samples"]) == 9
    again = post(client, tb, stand, temp)                                  # the same samples in a new batch: skipped by their UUIDs
    assert (again["metrics"], again["skipped"]) == (0, 9)
    same(ref, snapshot(conn, b, NATIVE_TABLES), "same samples, new batch")
    same(ref, snapshot(conn, b, NATIVE_TABLES), "(again)")
    bid = str(uuid.uuid4())
    post(client, tb, stand, temp, batch_id=bid)
    assert post(client, tb, stand, temp, batch_id=bid)["duplicate_batch"] is True      # the same batch id: answered from the log
    same(ref, snapshot(conn, b, NATIVE_TABLES), "same batch id")
    assert refresh_row(conn, a) == refresh_row(conn, b)


@pytest.mark.parametrize("zone", [SYD, LA])
def test_native_upload_split_across_midnight_equals_one_upload(zone, wire, client, conn, frozen_clock):
    day = date(2026, 11, 22)
    nxt = day + timedelta(days=1)
    stand, temp = native_samples(zone, day)
    cut = at(zone, day, 23, 50)
    early = lambda xs: [x for x in xs if datetime.fromisoformat(x["start"]) < cut]      # noqa: E731
    late = lambda xs: [x for x in xs if datetime.fromisoformat(x["start"]) >= cut]      # noqa: E731
    assert early(stand) and late(stand) and early(temp) and late(temp)
    (once, t1), (split, t2) = new_user(conn, zone, "n-once2"), new_user(conn, zone, "n-split")
    clock_ = frozen_clock(at(zone, day, 23, 50))
    post(client, t2, early(stand), early(temp))
    clock_.move_to(at(zone, nxt, 0, 10))
    post(client, t2, late(stand), late(temp))
    post(client, t1, stand, temp)
    same(snapshot(conn, once, NATIVE_TABLES), snapshot(conn, split, NATIVE_TABLES), "23:50 + 00:10 vs one upload at 00:10")
    got = {(r["date"], r["metric"]): r["value"] for r in conn.execute(
        "SELECT date, metric, value FROM daily_metrics WHERE user_id = %s", (split,))}
    assert got[(day, "apple_stand_time")] == 13 and got[(nxt, "apple_stand_time")] == 4      # 4+6+2+1 | 1+3: cut at the user's midnight
    assert got[(day, "apple_sleeping_wrist_temperature")] == pytest.approx((35.4 + 35.9) / 2, abs=0.01)
    assert got[(nxt, "apple_sleeping_wrist_temperature")] == pytest.approx(36.1)
    assert refresh_row(conn, once)["scores_from"] == refresh_row(conn, split)["scores_from"] == day


@pytest.mark.parametrize("zone,day", [(SYD, date(2026, 10, 4)), (SYD, date(2027, 4, 4)), (LA, date(2026, 11, 1)), (LA, date(2027, 3, 14))],
                         ids=["SYD-spring-forward", "SYD-fall-back", "LA-fall-back", "LA-spring-forward"])
def test_native_samples_on_a_clock_change_day_belong_to_that_day(zone, day, wire, client, conn, frozen_clock):
    """The server stores each sample's local day (hk_samples.local_date) from its own instant: 23:40 on a 23- or 25-hour day is still that
    day, 00:03 the next morning is the next one, whatever the UTC date says."""
    frozen_clock(at(zone, day + timedelta(days=1), 9))
    uid, tok = new_user(conn, zone, f"n-dst-{day}")
    stand, temp = native_samples(zone, day)
    post(client, tok, stand, temp)
    got = {(r["date"], r["metric"]): r["value"] for r in conn.execute(
        "SELECT date, metric, value FROM daily_metrics WHERE user_id = %s", (uid,))}
    assert got[(day, "apple_stand_time")] == 13 and got[(day + timedelta(days=1), "apple_stand_time")] == 4
    days = {r["local_date"] for r in conn.execute("SELECT local_date FROM hk_samples WHERE user_id = %s", (uid,))}
    assert days == {day, day + timedelta(days=1)}


# ================================================================== which day a night / a workout belongs to
WAKE_CASES = [(3, 59, -1), (4, 0, 0), (4, 1, 0), (12, 0, 0), (23, 59, 0), (0, 1, -1)]    # wake at hh:mm on `day` -> the sleep belongs to day + offset


@pytest.mark.parametrize("zone,day", [(SYD, date(2026, 11, 22)), (SYD, date(2026, 10, 4)), (SYD, date(2027, 4, 4)), (LA, date(2026, 11, 1)), (LA, date(2027, 3, 14)),
                                      ("Asia/Shanghai", date(2026, 11, 22))])
def test_a_night_belongs_to_the_day_of_wake_minus_four_hours(zone, day, conn, tmp_path, frozen_clock):
    """A night that ends before 04:00 counts for the day before (a late-night person's day is not over), from 04:00 on for the day of waking;
    the same on a 23- or 25-hour day, in every zone. (Importers: common.WAKE_DAY_SHIFT; the wall clock decides, not the UTC date.)"""
    frozen_clock(at(zone, day + timedelta(days=2), 9))
    # one user per case keeps the nights from merging into each other
    for (hh, mm, shift) in WAKE_CASES:
        u = new_user(conn, zone, f"wake-{zone}-{day}-{hh}-{mm}")[0]
        wake = at(zone, day, hh, mm)
        bed = (wake.astimezone(clock.UTC) - timedelta(hours=1)).astimezone(ZoneInfo(zone))
        folder = tmp_path / f"{hh}-{mm}"
        write_upload(folder, "health_x.txt", [("Sleep", bed.isoformat(), wake.isoformat(), "Core", None, None)])
        sc.run(conn, u, folder)
        got = [r["wake_date"] for r in conn.execute("SELECT wake_date FROM sleep_sessions WHERE user_id = %s", (u,))]
        assert got == [day + timedelta(days=shift)], (zone, day, hh, mm, got)


@pytest.mark.parametrize("zone,day", [(SYD, date(2026, 11, 22)), (SYD, date(2026, 10, 4)), (SYD, date(2027, 4, 4)), (LA, date(2026, 11, 22)),
                                      (LA, date(2027, 3, 14)), (LA, date(2026, 11, 1))])
def test_a_workout_belongs_to_the_local_day_it_started_on(zone, day, conn, frozen_clock):
    """local_date = the start day in the user's zone: a run from 23:40 to 00:20 counts for the evening it began, one that starts at 00:05
    for the new day, whatever UTC says; sending the same rows again adds nothing (external_id = start instant + type)."""
    from body_engine.training import history as th
    from body_engine.training import shortcut_workouts as sw
    frozen_clock(at(zone, day + timedelta(days=1), 12))
    uid = new_user(conn, zone, f"wo-{zone}-{day}")[0]
    late, early = at(zone, day, 23, 40), at(zone, day + timedelta(days=1), 0, 5)
    rows = [["Workout", late.isoformat(), (late.astimezone(clock.UTC) + timedelta(minutes=40)).astimezone(ZoneInfo(zone)).isoformat(),
             "Running", "2400", "300", "140", "170", "测试"],
            ["Workout", early.isoformat(), (early.astimezone(clock.UTC) + timedelta(minutes=30)).astimezone(ZoneInfo(zone)).isoformat(),
             "Traditional Strength Training", "1800", "150", "120", "150", "测试"]]
    first = sw.run(conn, uid, rows)
    assert first["inserted"] == 2
    again = sw.run(conn, uid, rows)
    assert again["inserted"] == 0
    assert conn.execute("SELECT count(*) AS n FROM workouts WHERE user_id = %s", (uid,)).fetchone()["n"] == 2
    got = {r["external_id"].split("|")[-1]: r["local_date"] for r in conn.execute("SELECT external_id, local_date FROM workouts WHERE user_id = %s", (uid,))}
    assert sorted(got.values()) == [day, day + timedelta(days=1)], got
    cal = th.calendar(conn, uid, uid, start=day, end=day + timedelta(days=1))
    by_day = {d["date"]: [s["type"] for s in d["sessions"]] for d in cal["days"]}
    assert len(by_day[day]) == 1 and len(by_day[day + timedelta(days=1)]) == 1, by_day
