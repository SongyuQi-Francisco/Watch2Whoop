from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from body_engine.importers import ow
from body_engine.importers.common import sessionize
from body_engine.importers.ow import (Sample, daily_rows, dedupe_cumulative, drop_xml_duplicates, parse_sample,
                                      sleep_segments)

SYD = ZoneInfo("Australia/Sydney")


def at(hhmm: str, day: str = "2026-10-13") -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=SYD)


def watch(hhmm: str, v: float, day: str = "2026-10-13") -> Sample:
    return Sample(at(hhmm, day), v, "watch:Watch", 0)


def phone(hhmm: str, v: float, day: str = "2026-10-13") -> Sample:
    return Sample(at(hhmm, day), v, "phone:iPhone", 1)


def test_non_overlapping_sources_are_added():
    # Watch and phone recorded different periods (e.g. the watch was charging while the phone went along on a walk) -> count both, not just the largest single source
    days = dedupe_cumulative([watch("09:00", 100), watch("09:05", 50), phone("15:00", 80)], SYD)
    assert days == {date(2026, 10, 13): 230}


def test_overlap_counts_only_higher_priority_source():
    # Both devices recorded the same walk: only the watch counts; the phone sample is fully covered
    days = dedupe_cumulative([watch("09:00", 100), watch("09:04", 60), phone("09:01", 90)], SYD)
    assert days == {date(2026, 10, 13): 160}


def test_partial_overlap_is_prorated():
    # One watch sample at 09:00 (inferred to last 10 minutes until 09:10); the phone starts at 09:05 with the next sample at 09:15 -> the first half is covered
    days = dedupe_cumulative([watch("09:00", 100), phone("09:05", 40), phone("09:15", 10)], SYD)
    assert days[date(2026, 10, 13)] == 100 + 40 * 0.5 + 10


def test_days_split_in_user_timezone():
    days = dedupe_cumulative([watch("23:55", 10), watch("00:05", 20, day="2026-10-14")], SYD)
    assert days == {date(2026, 10, 13): 10, date(2026, 10, 14): 20}


def raw(code, value, unit, device_type="watch", source="apple_health_xml", ts="2026-10-13T09:00:00+11:00"):
    return {"timestamp": ts, "type": code, "value": value, "unit": unit,
            "source": {"provider": "apple", "source": source, "device": "Watch", "device_type": device_type}}


def test_units_xml_kept_sdk_converted():
    # OW's XML import does not convert units: labelled meters, but it is actually km from export.xml
    assert parse_sample(raw("distance_walking_running", 0.5, "meters"))[2].value == 0.5
    assert parse_sample(raw("distance_walking_running", 500, "meters", source="healthkit"))[2].value == 0.5
    assert round(parse_sample(raw("walking_speed", 1.25, "m_per_s", source="healthkit"))[2].value, 6) == 4.5
    assert parse_sample(raw("oxygen_saturation", 0.96, "percent"))[2].value == 96.0
    # Energy in export.xml is kJ (the Australian default); OW copies the value and labels it kcal
    assert round(parse_sample(raw("active_energy", 418.4, "kcal"))[2].value, 6) == 100.0
    assert parse_sample(raw("active_energy", 100, "kcal"), xml_energy_unit="kcal")[2].value == 100.0
    assert parse_sample(raw("active_energy", 100, "kcal", source="healthkit"))[2].value == 100.0
    assert parse_sample(raw("walking_asymmetry_percentage", 2.0, "percent", source="healthkit"))[2].value == 0.02


def test_priority_and_xml_flag():
    # Priority = device type x 2 + XML: live watch 0 < XML watch 1 < live phone 2 < XML phone 3 < other 4
    _, _, s = parse_sample(raw("steps", 10, "count", device_type="phone", source="healthkit"))
    assert (s.priority, s.xml, s.source) == (2, False, "phone:Watch:healthkit")
    _, _, s = parse_sample(raw("steps", 10, "count"))
    assert (s.priority, s.xml) == (1, True)
    _, _, s = parse_sample({**raw("steps", 10, "count"), "source": None})
    assert (s.priority, s.xml) == (4, False)


def test_daily_rows_avg_and_sum():
    rows = daily_rows({
        "heart_rate": ("bpm", [watch("09:00", 60), watch("10:00", 80)]),
        "steps": ("count", [watch("09:00", 100), phone("09:01", 90)]),
    }, SYD)
    by = {r["metric"]: r for r in rows}
    assert (by["heart_rate"]["value"], by["heart_rate"]["min_value"], by["heart_rate"]["max_value"]) == (70, 60, 80)
    assert (by["heart_rate"]["unit"], by["heart_rate"]["agg"], by["heart_rate"]["n"]) == ("count/min", "avg", 2)
    # Watch inferred until 09:10; of the phone's 09:01-09:11 only the last minute is uncovered -> 100 + 90 x 0.1
    assert (by["step_count"]["value"], by["step_count"]["agg"], by["step_count"]["n"]) == (109, "sum", 2)


def test_sleep_segments_map_stages():
    segs = sleep_segments([{"source": {"device_name": "Apple Watch"}, "sleep_stage_intervals": [
        {"stage": "in_bed", "start_time": "2026-10-13T01:00:00+11:00", "end_time": "2026-10-13T09:00:00+11:00"},
        {"stage": "light", "start_time": "2026-10-13T02:00:00+11:00", "end_time": "2026-10-13T03:00:00+11:00"},
        {"stage": "sleeping", "start_time": "2026-10-13T03:00:00+11:00", "end_time": "2026-10-13T04:00:00+11:00"},
    ]}], SYD)
    assert [(s.stage, s.device) for s in segs] == [("core", "Apple Watch"), ("unspecified", "Apple Watch")]



# --- Regression: sleep on the wrong day, XML and app double counting found on 2026-10-17 ------------------------------------

def night(start, end, stage="light", source=None):
    """One OW sleep session; source=None means imported from XML (OW does not record a source)."""
    src = ({"provider": "apple", "source": "My Apple Watch", "device_type": "watch", "device_name": "Apple Watch"}
           if source == "app" else {"provider": "unknown", "source": None, "device_type": None})
    return {"source": src, "sleep_stage_intervals": [{"stage": stage, "start_time": start, "end_time": end}]}


def test_app_sleep_in_utc_gets_local_wake_date():
    # Stage times synced by the app are UTC: 10-13 09:08-14:04 (Sydney) = 10-12 22:08Z-10-13 03:04Z, should belong to 10-13
    nights = sessionize(sleep_segments([night("2026-10-12T22:08:05Z", "2026-10-13T03:04:32Z", source="app")], SYD))
    assert [(n.wake_date, n.asleep_min) for n in nights] == [(date(2026, 10, 13), 296)]
    assert nights[0].end.utcoffset() == timedelta(hours=11)


def test_xml_sleep_with_wrong_dst_offset_gets_local_wake_date():
    # Winter data from the XML import was written as +11:00: 02:45-03:01 (Sydney +10), wake time - 4h lands on the previous day
    nights = sessionize(sleep_segments([night("2023-08-07T03:45:42+11:00", "2023-08-07T04:01:22+11:00")], SYD))
    assert [n.wake_date for n in nights] == [date(2023, 8, 6)]


def test_duplicate_xml_and_app_sleep_counted_once():
    xml = night("2026-10-13T09:08:05+11:00", "2026-10-13T14:04:32+11:00")
    app = night("2026-10-12T22:08:05Z", "2026-10-13T03:04:32Z", source="app")
    for sessions in ([xml, app], [app, xml]):
        nights = sessionize(sleep_segments(sessions, SYD))
        assert [(n.wake_date, n.asleep_min, n.device) for n in nights] == [(date(2026, 10, 13), 296, "Apple Watch")]
    # non-overlapping XML sessions are kept as usual
    other = night("2026-10-11T06:26:32+11:00", "2026-10-11T13:56:39+11:00")
    assert len(sessionize(sleep_segments([app, other], SYD))) == 2



def test_sleep_across_dst_start_keeps_real_duration():
    # 2026-10-04 Sydney DST starts 02:00 -> 03:00: 00:34 (+10) to 03:12 (+11) is actually 1 h 37 min, not 2 h 37 min
    nights = sessionize(sleep_segments([night("2026-10-03T14:34:41Z", "2026-10-03T16:12:10Z", source="app")], SYD))
    assert [(n.wake_date, n.asleep_min) for n in nights] == [(date(2026, 10, 3), 97)]

def test_duplicate_xml_and_app_steps_counted_once():
    samples = []
    for src in ("apple_health_xml", "My Apple Watch"):
        for ts, v in (("09:00", 100), ("09:05", 50)):
            samples.append(parse_sample(raw("steps", v, "count", source=src, ts=f"2026-10-12T{ts}:00+11:00"))[2])
    # periods only in the XML (the app did not backfill) are still counted
    samples.append(parse_sample(raw("steps", 30, "count", device_type="phone", ts="2026-10-12T15:00:00+11:00"))[2])
    kept = drop_xml_duplicates(samples)
    assert len(kept) == 3
    assert dedupe_cumulative(kept, SYD) == {date(2026, 10, 12): 180}
    # Duplicates with mismatched timestamps (only partly backfilled) are deduplicated by priority: the live watch covers the XML watch
    shifted = [Sample(at("09:00", "2026-10-12"), 100, "watch:a", 0),
               Sample(at("09:00:30", "2026-10-12"), 100, "watch:b", 1, xml=True)]
    # The XML sample is inferred as 09:00:30-09:10:30, only the last 30 seconds are uncovered -> 100 x 0.5 / 10
    assert round(dedupe_cumulative(shifted, SYD)[date(2026, 10, 12)], 6) == 100 + 100 * 0.5 / 10


def test_avg_metric_not_double_counted():
    rows = daily_rows({"oxygen_saturation": ("percent", drop_xml_duplicates([
        parse_sample(raw("oxygen_saturation", 0.96, "percent"))[2],
        parse_sample(raw("oxygen_saturation", 96, "percent", source="healthkit"))[2]]))}, SYD)
    assert [(r["value"], r["n"]) for r in rows] == [(96.0, 1)]

# --- run(): with a fake OW client and database, verify "nothing is written on the export day" and the cursor -----------------------------

class FakeClient:
    def __init__(self, samples):
        self.samples = samples

    def get(self, path, **params):
        return {"data": self.samples[:1]}

    def paginate(self, path, **params):
        if path.endswith("/timeseries"):
            lo = datetime.fromisoformat(params["start_time"])
            hi = datetime.fromisoformat(params["end_time"])
            return iter([s for s in self.samples if lo <= datetime.fromisoformat(s["timestamp"]) < hi])
        return iter([])


class FakeConn:
    def __init__(self):
        self.cursor_state = None

    def execute(self, sql, params=None):
        conn = self

        class R:
            def fetchone(self):
                if "FROM users" in sql:
                    return {"ow_user_id": "ow-1", "timezone": "Australia/Sydney"}
                if "FROM sync_state" in sql:
                    return {"cursor": conn.cursor_state} if conn.cursor_state else None

        if "INSERT INTO sync_state" in sql:
            self.cursor_state = params[2]
        return R()

    @contextmanager
    def transaction(self):
        yield


def no_workouts(monkeypatch):
    """Workout import has its own tests (test_training.py); here only daily metrics and the cursor are checked."""
    from body_engine.training import importer as workouts
    monkeypatch.setattr(workouts, "import_ow", lambda *a, **k: {"fetched": 0})


def run_fake(monkeypatch, samples, **kw):
    no_workouts(monkeypatch)
    written = []
    monkeypatch.setattr(ow, "upsert_daily", lambda conn, uid, src, rows: written.extend(rows) or len(rows))
    monkeypatch.setattr(ow, "replace_sleep", lambda conn, uid, src, nights: {"inserted": 0, "skipped": 0})
    conn = FakeConn()
    result = ow.run(conn, FakeClient(samples), 1, **kw)
    return result, written, conn


def test_run_skips_xml_only_export_day(monkeypatch):
    samples = [raw("steps", 100, "count", ts="2026-10-12T10:00:00+11:00"),
               raw("steps", 7, "count", ts="2026-10-13T05:00:00+11:00")]   # the morning of the export day, incomplete
    result, written, conn = run_fake(monkeypatch, samples, start=date(2026, 10, 12), end=date(2026, 10, 13))
    assert result["skipped_export_day"] == date(2026, 10, 13)
    assert [(r["date"], r["value"]) for r in written] == [(date(2026, 10, 12), 100)]
    assert conn.cursor_state == "2026-10-13T05:00:00+11:00"


def test_run_keeps_export_day_when_live_data_exists(monkeypatch):
    samples = [raw("steps", 7, "count", ts="2026-10-13T05:00:00+11:00"),
               raw("steps", 50, "count", device_type="phone", source="healthkit", ts="2026-10-13T12:00:00+11:00")]
    result, written, _ = run_fake(monkeypatch, samples, start=date(2026, 10, 13), end=date(2026, 10, 13))
    assert result["skipped_export_day"] is None
    assert [(r["date"], r["value"]) for r in written] == [(date(2026, 10, 13), 57)]


def test_run_incremental_starts_lookback_before_cursor(monkeypatch):
    no_workouts(monkeypatch)
    monkeypatch.setattr(ow, "upsert_daily", lambda *a: 0)
    monkeypatch.setattr(ow, "replace_sleep", lambda *a: {"inserted": 0, "skipped": 0})
    conn = FakeConn()
    conn.cursor_state = "2026-10-13T05:00:00+11:00"
    result = ow.run(conn, FakeClient([]), 1, end=date(2026, 10, 13))
    assert result["start"] == date(2026, 10, 11)
    assert conn.cursor_state == "2026-10-13T05:00:00+11:00"   # no new data, the cursor stays
