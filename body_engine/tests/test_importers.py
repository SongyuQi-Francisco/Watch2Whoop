from datetime import date, datetime, timedelta, timezone

from body_engine.importers.common import Segment, normalize, sessionize, snake

TZ = timezone(timedelta(hours=10))


def seg(start: str, end: str, stage: str) -> Segment:
    day = "2026-08-01"
    return Segment(datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=TZ),
                   datetime.fromisoformat(f"{day}T{end}").replace(tzinfo=TZ), stage)


def test_snake():
    assert snake("HeartRateVariabilitySDNN") == "heart_rate_variability_sdnn"
    assert snake("VO2Max") == "vo2_max"
    assert snake("HKQuantityTypeIdentifierStepCount") == "step_count"
    assert snake("AppleSleepingWristTemperature") == "apple_sleeping_wrist_temperature"


def test_normalize():
    value, unit = normalize("active_energy_burned", 418.4, "kJ")
    assert (round(value, 6), unit) == (100.0, "kcal")
    assert normalize("oxygen_saturation", 0.96, "%") == (96.0, "%")
    assert normalize("oxygen_saturation", 96.0, "%") == (96.0, "%")


def test_long_awake_does_not_merge_nap_into_main_sleep():
    sessions = sessionize([
        seg("07:00", "09:00", "core"), seg("09:00", "13:00", "rem"),
        seg("13:00", "17:00", "awake"),                     # 4 hours awake recorded by the watch
        seg("17:00", "18:30", "core"),
    ])
    assert [s.asleep_min for s in sessions] == [360, 90]
    assert sessions[0].minutes["awake"] == 0                 # awake time outside any sleep segment is not counted


def test_awake_inside_session_is_counted_and_short_gap_merges():
    sessions = sessionize([
        seg("02:00", "03:00", "deep"), seg("03:00", "03:10", "awake"),
        seg("03:10", "05:00", "core"), seg("05:40", "08:00", "rem"),   # 40-minute gap, no split
    ])
    assert len(sessions) == 1
    s = sessions[0]
    assert (s.asleep_min, round(s.minutes["awake"])) == (310, 10)
    assert s.wake_date == date(2026, 8, 1)


def test_wake_date_is_end_minus_4h_and_tiny_sessions_dropped():
    sessions = sessionize([seg("01:00", "03:30", "core"), seg("12:00", "12:10", "core")])
    assert len(sessions) == 1
    assert sessions[0].wake_date == date(2026, 7, 31)        # 03:30 - 4h lands on the previous day
