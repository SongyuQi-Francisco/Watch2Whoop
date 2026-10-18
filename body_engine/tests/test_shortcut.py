"""iOS Shortcut import: two archive formats, cols-v1 null alignment, idempotent shortcut_samples writes, per-hour sample overriding, daily metrics / sleep / inferred workouts.
The database part uses a separate test database body_test_shortcut (configurable via BODY_TEST_DB_NAME_SHORTCUT), recreated on every run; skipped if it cannot connect."""
import gzip
import os
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import shortcut as sc

TZ = timezone(timedelta(hours=11))
WATCH = "My Apple Watch"
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_SHORTCUT", "body_test_shortcut")


def t(hh: int, mm: int = 0, ss: int = 0, day: int = 16) -> str:
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=TZ).isoformat()


def cols_file(rows: list[tuple]) -> str:
    """rows: (type, start, end, value, unit, source); when unit / source is None it is skipped outright, like the Shortcut does."""
    out = ["#format=cols-v1"]
    for i, name in enumerate(sc.COLS):
        out.append(f"@@{name}")
        out += [r[i] for r in rows if r[i] is not None]
    return "\n".join(out)


def workout_day() -> list[tuple]:
    """10-16: sleeps 1:00-7:00; trains 18:00-18:40 (exercise minutes + heart rate around 130); hourly steps and active energy."""
    rows = [("Sleep", t(1), t(4), "Core", None, None), ("Sleep", t(4), t(4, 20), "Awake", None, None),
            ("Sleep", t(4, 20), t(7), "Deep", None, None),
            ("Steps", t(9, 5), t(9, 55), "1200", "count", None), ("Steps", t(18, 1), t(18, 59), "3000", "count", None),
            ("Active Calories", t(18, 0), t(18, 59), "1255.2", "kJ", None)]
    rows += [("Heart Rate", t(18, m), t(18, m), str(120 + m), "count/min", WATCH) for m in range(0, 40, 2)]
    rows += [("Heart Rate", t(3, 0), t(3, 0), "52", "count/min", WATCH)]
    rows += [("Exercise Time", t(18, m), t(18, m + 1), "1", "min", WATCH) for m in range(0, 40)]
    rows += [("Heart Rate Variability", t(3, 30), t(3, 31), "48.5", "ms", WATCH)]
    return rows


# ---------- parsing ----------

def test_align_fills_missing_units_and_sources_by_type():
    types = ["Sleep", "Sleep", "Steps", "Heart Rate", "Heart Rate"]
    assert sc._align(types, ["count", "count/min", "count/min"], [sc.NO_UNIT], True) == \
        ["", "", "count", "count/min", "count/min"]
    assert sc._align(types, ["表", "表"], [sc.NO_SOURCE], False) == ["", "", "", "表", "表"]
    # When prefer is wrong, look for the unique fit: 4 missing can only be Sleep(2) + Heart Rate(2)
    assert sc._align(types, ["x"], [{"Nope"}], False) == ["", "", "x", "", ""]
    assert sc._align(["A", "B", "C"], ["x", "y"], [{"Nope"}], False) is None   # 1 missing: A / B / C all work, not unique
    assert sc._align(["A", "B", "B", "B"], ["u"], [{"Nope"}], True) == ["u", "", "", ""]
    assert sc._align(types, ["a", "b", "c"], [sc.NO_UNIT], True) is None  # inconsistent units within one type -> no match


def test_source_candidates_cover_grouped_exercise_time():
    # When exercise minutes are also grouped by hour (no source), they can still be aligned as a "cumulative type missing as a whole" even if they collide with the types of the other 7
    types = ["Sleep"] * 2 + ["Exercise Time"] * 3 + ["Resting Heart Rate"] * 3 + ["Steps"]
    col = ["表"] * 3
    assert sc._align(types, col, sc._source_candidates(types), False) == [""] * 5 + ["表"] * 3 + [""]


def test_parse_cols_v1(tmp_path):
    f = tmp_path / "health_2026-10-16_200000.txt"
    f.write_text(cols_file(workout_day()) + "\n")
    up = sc.parse_file(f)
    assert up.format == "cols-v1" and not up.notes
    by = {}
    for s in up.samples:
        by.setdefault(s.type, []).append(s)
    assert {s.agg for s in by["Heart Rate"]} == {"raw"} and by["Heart Rate"][0].source == WATCH
    assert {s.agg for s in by["Steps"]} == {"hour"} and by["Steps"][0].source == ""
    assert by["Sleep"][0].value_text == "Core" and by["Sleep"][0].unit is None
    assert by["Exercise Time"][0].agg == "raw"
    rows = sc.read_archive(tmp_path)
    assert ["Heart Rate", t(18, 0), t(18, 0), "120", "count/min", WATCH] in rows


def test_parse_legacy_rows_and_gzip(tmp_path):
    lines = [f"Heart Rate|{t(9)}|{t(9, 58)}|843|count/min|", f"Heart Rate|{t(10, 1)}|{t(10, 1)}|67|count/min|",
             f"Steps|{t(9)}|{t(9, 50)}|500|count|", f"Sleep|{t(1)}|{t(2)}|Core||",
             f"Oxygen Saturation|{t(3)}|{t(3)}|0.97|%|{WATCH}"]
    (tmp_path / "health_2026-10-16_100000.txt.gz").write_bytes(gzip.compress("\n".join(lines).encode()))
    up = sc.parse_file(tmp_path / "health_2026-10-16_100000.txt.gz")
    assert up.file == "health_2026-10-16_100000.txt" and up.format == "rows-v0"
    assert {s.agg for s in up.samples if s.type == "Heart Rate"} == {"hour"} and "hr_hourly_sum" in up.notes
    assert len(up.samples) == 5


def test_cols_source_misaligned_falls_back_to_shape_rule(tmp_path):
    rows = workout_day()
    text = cols_file(rows).split("\n")
    i = text.index("@@source")
    text = text[:i + 2]                       # only one line left in the source segment: no match
    (tmp_path / "health_2026-10-16_200000.txt").write_text("\n".join(text))
    up = sc.parse_file(tmp_path / "health_2026-10-16_200000.txt")
    assert "source_misaligned" in up.notes
    assert {s.agg for s in up.samples if s.type == "Heart Rate"} == {"raw"}   # start = end, plausible value


def test_infer_workouts():
    up_rows = workout_day()
    samples = []
    for p in up_rows:
        s = sc._to_sample(list(p[:4]) + [p[4] or "", p[5] or ""])
        s.agg = "hour" if p[0] in ("Steps", "Active Calories") else "raw"
        samples.append({"type": s.type, "start_at": s.start, "end_at": s.end, "value": s.value, "unit": s.unit,
                        "source": s.source, "agg": s.agg, "value_text": s.value_text})
    w = sc.infer_workouts(samples)
    assert len(w) == 1
    _, start, end, typ, dur, kcal, avg, mx, src = w[0]
    assert (start, end, typ, dur) == (t(18), t(18, 40), "Other", "2400")
    assert float(kcal) == pytest.approx(300 * 40 / 59, abs=0.5)     # 1255.2 kJ = 300 kcal, spread by overlap
    assert float(avg) == pytest.approx(139) and mx == "158" and src == sc.WORKOUT_SOURCE


# ---------- DB writes ----------

@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
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
        uid = c.execute("INSERT INTO users (display_name, timezone) VALUES ('测试', 'Australia/Sydney') RETURNING id"
                        ).fetchone()["id"]
        c.uid = uid
        yield c


def test_import_idempotent_and_daily(conn, tmp_path):
    (tmp_path / "health_2026-10-16_200000.txt").write_text(cols_file(workout_day()))
    r1 = sc.run(conn, conn.uid, tmp_path)
    assert r1["files"] == 1 and r1["workouts_inferred"] == 1 and r1["sleep"]["inserted"] == 1
    n1 = conn.execute("SELECT count(*) n FROM shortcut_samples WHERE user_id = %s", (conn.uid,)).fetchone()["n"]
    assert sc.run(conn, conn.uid, tmp_path)["files"] == 0                     # already imported ones are skipped
    sc.run(conn, conn.uid, tmp_path, all_files=True)                          # re-import everything: result unchanged
    assert conn.execute("SELECT count(*) n FROM shortcut_samples WHERE user_id = %s", (conn.uid,)).fetchone()["n"] == n1
    d = {r["metric"]: r for r in conn.execute(
        "SELECT metric, value, agg, n, min_value, max_value FROM daily_metrics WHERE user_id = %s AND date = %s",
        (conn.uid, date(2026, 10, 16)))}
    assert d["step_count"]["value"] == 4200
    assert d["active_energy_burned"]["value"] == pytest.approx(300)
    assert d["apple_exercise_time"]["value"] == 40
    assert d["heart_rate"]["min_value"] == 52 and d["heart_rate"]["max_value"] == 158
    s = conn.execute("SELECT wake_date, asleep_min, deep_min, awake_min FROM sleep_sessions WHERE user_id = %s",
                     (conn.uid,)).fetchall()
    assert s == [{"wake_date": date(2026, 10, 16), "asleep_min": 340, "deep_min": 160, "awake_min": 20}]


def test_newer_hour_sample_replaces_older_one(conn, tmp_path):
    (tmp_path / "health_2026-10-16_183000.txt").write_text(cols_file([("Steps", t(18, 5), t(18, 29), "800", "count", None)]))
    sc.run(conn, conn.uid, tmp_path)
    # The later-uploaded version: the earliest reading in this hour is 18:01 (the start changed), total 3000
    (tmp_path / "health_2026-10-16_200000.txt").write_text(cols_file([("Steps", t(18, 1), t(18, 59), "3000", "count", None)]))
    sc.run(conn, conn.uid, tmp_path)
    rows = conn.execute("SELECT start_at, value FROM shortcut_samples WHERE user_id = %s AND type = 'Steps'",
                        (conn.uid,)).fetchall()
    assert [r["value"] for r in rows] == [3000]
    assert conn.execute("SELECT value FROM daily_metrics WHERE user_id = %s AND metric = 'step_count'",
                        (conn.uid,)).fetchone()["value"] == 3000
    # Re-importing all the old files doesn't add the old 800 back (an earlier upload doesn't overwrite)
    sc.run(conn, conn.uid, tmp_path, all_files=True)
    assert conn.execute("SELECT count(*) n FROM shortcut_samples WHERE user_id = %s AND type = 'Steps'",
                        (conn.uid,)).fetchone()["n"] == 1


def test_scores_read_raw_hr_from_table(conn, tmp_path):
    from body_engine.scores import inputs
    (tmp_path / "health_2026-10-16_200000.txt").write_text(cols_file(workout_day()))
    sc.run(conn, conn.uid, tmp_path)
    m = inputs._Merger()
    segs = inputs.load_shortcut_db(conn, conn.uid, inputs.ZoneInfo("Australia/Sydney"), date(2026, 10, 16),
                                   date(2026, 10, 16), m)
    inp = inputs.Inputs(inputs.ZoneInfo("Australia/Sydney"), None)
    m.fill(inp)
    assert len(inp.hr) == 21 and len(inp.hrv) == 1 and len(segs) == 3


# ---------- cumulative types: raw samples (Shortcut 1.1) vs hour-grouped (1.0), time zone, mixing ----------

PHONE = "我的 iPhone"
SYD = sc.ZoneInfo("Australia/Sydney")


def smp(type_, start, end, value, source="", agg="raw", unit="count"):
    return {"type": type_, "start_at": datetime.fromisoformat(start), "end_at": datetime.fromisoformat(end),
            "value": value, "value_text": None, "unit": unit, "source": source, "agg": agg}


def by_metric(rows):
    return {(r["date"], r["metric"]): r for r in rows}


def test_parse_cols_v1_raw_cumulative_keeps_source(tmp_path):
    rows = [("Sleep", t(1), t(4), "Core", None, None),
            ("Steps", t(9, 5), t(9, 9), "300", "count", WATCH), ("Steps", t(9, 6), t(9, 10), "310", "count", PHONE),
            ("Steps", t(9, 20), t(9, 30), "500", "count", WATCH),
            ("Active Calories", t(9, 5), t(9, 6), "8.4", "kJ", WATCH)]
    f = tmp_path / "health_2026-10-16_200000.txt"
    f.write_text(cols_file(rows))
    up = sc.parse_file(f)
    assert not up.notes
    steps = [s for s in up.samples if s.type == "Steps"]
    assert [(s.source, s.agg) for s in steps] == [(WATCH, "raw"), (PHONE, "raw"), (WATCH, "raw")]
    assert all(s.agg == "raw" for s in up.samples if s.type == "Active Calories")


def test_sourceless_raw_cumulative_is_not_mistaken_for_hourly(tmp_path):
    lines = [f"Steps|{t(9, 5)}|{t(9, 9)}|300|count|", f"Steps|{t(9, 20)}|{t(9, 30)}|500|count|",
             f"Steps|{t(10, 1)}|{t(10, 50)}|900|count|"]
    (tmp_path / "health_2026-10-16_100000.txt").write_text("\n".join(lines))
    up = sc.parse_file(tmp_path / "health_2026-10-16_100000.txt")
    assert {s.agg for s in up.samples} == {"raw"} and "sum_source_missing" in up.notes


def test_raw_cumulative_takes_largest_single_device():
    s = [smp("Steps", "2026-10-18T09:00+11:00", "2026-10-18T09:10+11:00", 1000, WATCH),
         smp("Steps", "2026-10-18T09:01+11:00", "2026-10-18T09:11+11:00", 950, PHONE),
         smp("Steps", "2026-10-18T12:00+11:00", "2026-10-18T12:10+11:00", 1500, WATCH),
         smp("Steps", "2026-10-18T20:00+11:00", "2026-10-18T20:10+11:00", 2000, PHONE),   # watch charging
         smp("Active Calories", "2026-10-18T09:00+11:00", "2026-10-18T09:10+11:00", 418.4, WATCH, unit="kJ")]
    d = by_metric(sc.build_daily(s, SYD))
    steps = d[(date(2026, 10, 18), "step_count")]
    assert steps["value"] == 2950 and "flags" not in steps      # PHONE 2950 > WATCH 2500, never 5450
    assert d[(date(2026, 10, 18), "active_energy_burned")]["value"] == pytest.approx(100)   # kJ -> kcal


def test_hourly_cumulative_is_flagged():
    s = [smp("Steps", "2026-10-18T09:05+11:00", "2026-10-18T09:55+11:00", 5000, agg="hour"),
         smp("Steps", "2026-10-18T10:05+11:00", "2026-10-18T10:55+11:00", 5327, agg="hour")]
    r = by_metric(sc.build_daily(s, SYD))[(date(2026, 10, 18), "step_count")]
    assert r["value"] == 10327 and "hourly_sum" in r["flags"]


def test_cumulative_days_cut_in_user_time_zone():
    # 2026-10-18 12:30 UTC = 23:30 Sydney (AEDT +11); 13:30 UTC = 10-19 00:30
    s = [smp("Steps", "2026-10-18T12:30+00:00", "2026-10-18T12:40+00:00", 100, WATCH),
         smp("Steps", "2026-10-18T13:30+00:00", "2026-10-18T13:40+00:00", 200, WATCH)]
    d = by_metric(sc.build_daily(s, SYD))
    assert d[(date(2026, 10, 18), "step_count")]["value"] == 100
    assert d[(date(2026, 10, 19), "step_count")]["value"] == 200
    # winter (AEST +10): 2026-06-30 14:30 UTC is already 07-01 00:30
    d = by_metric(sc.build_daily([smp("Steps", "2026-06-30T14:30+00:00", "2026-06-30T14:40+00:00", 50, WATCH)], SYD))
    assert (date(2026, 7, 1), "step_count") in d


def test_hour_samples_before_first_raw_still_count_after_ignored():
    # the old Shortcut uploaded hours until 14:xx; the new one's first raw sample is at 13:20 (its window started then)
    s = [smp("Steps", "2026-10-17T09:05+11:00", "2026-10-17T09:55+11:00", 1800, agg="hour"),
         smp("Steps", "2026-10-17T13:02+11:00", "2026-10-17T13:58+11:00", 999, agg="hour"),    # superseded by raw
         smp("Steps", "2026-10-17T14:02+11:00", "2026-10-17T14:58+11:00", 999, agg="hour"),
         smp("Steps", "2026-10-17T13:20+11:00", "2026-10-17T13:30+11:00", 400, WATCH),
         smp("Steps", "2026-10-17T14:20+11:00", "2026-10-17T14:30+11:00", 300, WATCH),
         smp("Steps", "2026-10-17T14:21+11:00", "2026-10-17T14:31+11:00", 280, PHONE),
         smp("Steps", "2026-10-18T08:00+11:00", "2026-10-18T08:10+11:00", 600, WATCH),
         smp("Steps", "2026-10-18T08:00+11:00", "2026-10-18T08:59+11:00", 1100, agg="hour")]  # old upload of the next day
    d = by_metric(sc.build_daily(s, SYD))
    d17, d18 = d[(date(2026, 10, 17), "step_count")], d[(date(2026, 10, 18), "step_count")]
    assert d17["value"] == 1800 + 700 and "hourly_sum" in d17["flags"]     # hour part before 13:00 + watch raw
    assert d18["value"] == 600 and "flags" not in d18


def test_infer_workouts_raw_energy_largest_device():
    rows = [smp("Exercise Time", f"2026-10-18T18:{m:02d}+11:00", f"2026-10-18T18:{m + 1:02d}+11:00", 1, WATCH, unit="min")
            for m in range(30)]
    rows += [smp("Heart Rate", f"2026-10-18T18:{m:02d}+11:00", f"2026-10-18T18:{m:02d}+11:00", 130, WATCH,
                 unit="count/min") for m in range(0, 30, 3)]
    rows += [smp("Active Calories", "2026-10-18T18:00+11:00", "2026-10-18T18:30+11:00", 836.8, WATCH, unit="kJ"),
             smp("Active Calories", "2026-10-18T18:00+11:00", "2026-10-18T18:30+11:00", 418.4, PHONE, unit="kJ")]
    (w,) = sc.infer_workouts(rows)
    assert float(w[5]) == pytest.approx(200)      # watch 200 kcal, not 300


def test_new_shortcut_replaces_hourly_day_and_rebuild_is_idempotent(conn, tmp_path):
    old = [("Steps", t(9, 5, day=17), t(9, 55, day=17), "5000", "count", None),
           ("Steps", t(10, 5, day=17), t(10, 55, day=17), "5327", "count", None)]
    (tmp_path / "health_2026-10-17_120000.txt").write_text(cols_file(old))
    sc.run(conn, conn.uid, tmp_path)
    r = conn.execute("SELECT value, flags FROM daily_metrics WHERE user_id = %s AND metric = 'step_count'",
                     (conn.uid,)).fetchone()
    assert r["value"] == 10327 and "hourly_sum" in r["flags"]
    new = [("Sleep", t(1, day=17), t(4, day=17), "Core", None, None)]
    new += [("Steps", t(h, 5, day=17), t(h, 50, day=17), "2500", "count", WATCH) for h in (9, 10)]
    new += [("Steps", t(h, 6, day=17), t(h, 51, day=17), "2400", "count", PHONE) for h in (9, 10)]
    (tmp_path / "health_2026-10-17_200000.txt").write_text(cols_file(new))
    sc.run(conn, conn.uid, tmp_path)
    q = "SELECT value, source, flags FROM daily_metrics WHERE user_id = %s AND metric = 'step_count'"
    assert conn.execute(q, (conn.uid,)).fetchone() == {"value": 5000, "source": "shortcut", "flags": {}}
    assert sc.rebuild(conn, conn.uid, date(2026, 10, 16), date(2026, 10, 18))["resolved"] >= 1
    sc.rebuild(conn, conn.uid, date(2026, 10, 16), date(2026, 10, 18))
    assert conn.execute(q, (conn.uid,)).fetchone() == {"value": 5000, "source": "shortcut", "flags": {}}
