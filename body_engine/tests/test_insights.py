"""WHOOP-style analysis (insights/): formula boundaries, insufficient data, time zones. Pure functions, no database."""
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from body_engine.insights import coach, fitness, reports, sleep_detail, stress
from body_engine.insights import healthspan as HS
from body_engine.insights import healthspan_params as H
from body_engine.insights import healthspan_store as store
from body_engine.scores.engine import _latency_efficiency
from body_engine.scores.inputs import Night

SYD = ZoneInfo("Australia/Sydney")


def at(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=SYD)


# ================================================================== body age: curves and conversion
def test_weighted_mean_recent_30_days_count_double():
    as_of = date(2026, 10, 17)
    vals = {as_of - timedelta(days=k): (10.0 if k < 30 else 4.0) for k in range(180)}
    vals[as_of - timedelta(days=200)] = 1000.0            # outside the window does not count
    v, n = HS.weighted_mean(vals, as_of)
    assert n == 180 and v == pytest.approx((30 * 2 * 10 + 150 * 4) / (30 * 2 + 150))
    assert HS.weighted_mean({}, as_of) == (None, 0)


def test_interp_log_hr_flat_outside_and_log_linear_inside():
    knots = [(0, 1.0), (10, 0.5)]
    assert HS.interp_log_hr(-5, knots) == 1.0 and HS.interp_log_hr(50, knots) == 0.5
    assert HS.interp_log_hr(5, knots) == pytest.approx(math.sqrt(0.5))


def test_anchors_are_zero_years():
    measured_7h = (7 - H.SLEEP_SELF_REPORT[0]) / H.SLEEP_SELF_REPORT[1]
    assert HS.hr_sleep_duration(measured_7h) == pytest.approx(1.0)
    assert HS.hr_regularity(H.SRI_ANCHOR) == pytest.approx(1.0)
    assert HS.hr_steps(H.STEPS_ANCHOR_UNDER60, 25) == pytest.approx(1.0)
    assert HS.hr_steps(H.STEPS_ANCHOR_60PLUS, 70) == pytest.approx(1.0)
    assert HS.hr_mvpa(150) == pytest.approx(1.0)
    assert HS.hr_strength(0) == pytest.approx(1.0)
    assert HS.hr_rhr(65) == pytest.approx(1.0)
    assert HS.hr_bmi(25) == pytest.approx(1.0)
    assert HS.hr_whtr(0.5) == pytest.approx(1.0)
    assert HS.hr_vo2(46.5, 46.5) == 1.0


def test_curve_directions_and_shapes():
    # Sleep U shape: both too little and too much raise risk, and each extra hour of oversleeping is steeper
    assert HS.hr_sleep_duration(4.0) > 1 and HS.hr_sleep_duration(10.0) > 1
    assert HS.self_report_hours(5) == pytest.approx(6.2, abs=0.01) and HS.self_report_hours(7) == pytest.approx(7.33, abs=0.01)
    # Steps: for the same 6000 steps, the under-60 anchor is higher -> higher risk ratio
    assert HS.hr_steps(6000, 25) > 1 and HS.hr_steps(6000, 25) > HS.hr_steps(6000, 70)
    assert HS.hr_steps(20000, 25) == HS.hr_steps(8911, 25)                       # plateau after Q3
    # Strength training J shape: lowest at 40 minutes, flat after 140 with no penalty
    assert HS.hr_strength(40) < HS.hr_strength(140) < 1 and HS.hr_strength(300) == HS.hr_strength(140)
    # Resting heart rate: flat below 45, x1.16 for every 10 bpm higher
    assert HS.hr_rhr(40) == HS.hr_rhr(45) and HS.hr_rhr(75) == pytest.approx(1.16)
    # BMI U shape
    assert HS.hr_bmi(18) > 1 and HS.hr_bmi(32) > HS.hr_bmi(28) > 1 and HS.hr_bmi(23) < 1
    assert HS.hr_whtr(0.6) == pytest.approx(1.24) and HS.hr_whtr(0.4) == 1.0


def test_vo2_norm_percentile_and_cap():
    assert HS.vo2_norm(25, "male") == 46.5
    assert HS.vo2_norm(30, "male") == pytest.approx((46.5 + 39.7) / 2)
    assert HS.vo2_norm(18, "female") == 36.6 and HS.vo2_norm(95, "female") == 15.4    # no extrapolation at either end
    assert HS.vo2_norm(30, "other") is None
    assert HS.vo2_percentile(46.5, 25, "male") == pytest.approx(50)
    assert HS.vo2_percentile(10, 25, "male") == 5 and HS.vo2_percentile(70, 25, "male") == 95
    assert HS.hr_vo2(46.5 + 3.5, 46.5) == pytest.approx(0.87)
    assert HS.hr_vo2(80, 46.5) == pytest.approx(0.87 ** 3)                           # counts at most 3 MET


def test_years_conversion_caps_and_shrinkage():
    assert HS.years_from_hr(2 ** 0.25) == pytest.approx(2.0)          # HR 2^(1/4) -> 8 / 4 years
    assert HS.years_from_hr(2.0) == 4.0 and HS.years_from_hr(0.5) == -4.0
    assert HS.combine([{"years": 4.0}] * 2) == pytest.approx(4.8)    # 0.6 × 8
    assert HS.combine([{"years": 4.0}] * 8) == 10.0 and HS.combine([{"years": -4.0}] * 8) == -10.0


def test_pace_of_aging_26_weeks_smoothed():
    as_of = date(2026, 10, 18)
    yrs = lambda d: (d - as_of).days / 365.2425
    weeks = [as_of - timedelta(weeks=k) for k in range(40)][::-1]
    flat = [(d, 3.0) for d in weeks]                                    # Δ unchanged -> grows together with the actual age
    assert HS.pace(flat, as_of) == {"pace": 1.0, "pace_raw": 1.0, "window_weeks": 26}
    rising = [(d, 3.0 + yrs(d)) for d in weeks]                         # Δ +1 per year -> the body ages 2 years for every 1 year
    assert HS.pace(rising, as_of)["pace"] == pytest.approx(2.0, abs=0.01)
    falling = [(d, 3.0 - 0.5 * yrs(d)) for d in weeks]
    assert HS.pace(falling, as_of)["pace"] == pytest.approx(0.5, abs=0.01)
    steep = [(d, 3.0 - 10 * yrs(d)) for d in weeks]
    r = HS.pace(steep, as_of)
    assert r["pace"] == -1.0 and r["pace_raw"] == pytest.approx(-9.0, abs=0.05)   # clamped, raw kept
    # Only the last 26 weeks are used: earlier drastic changes have no effect
    jump = [(d, 10.0 if d < as_of - timedelta(weeks=30) else 3.0) for d in weeks]
    assert HS.pace(jump, as_of)["pace"] == 1.0
    # The 4-week moving average damps single-week jumps (without smoothing the slope would be much larger)
    spike = [(d, 3.0 + (2.0 if d == as_of else 0.0)) for d in weeks]
    assert HS.smooth(spike)[-1][1] == pytest.approx(3.5)
    assert HS.pace(spike, as_of)["pace_raw"] < 1 + 2.0 / (26 / 52) / 2
    # Fewer than 26 weeks early on: use all existing weeks (>= 8); under 8 weeks gives no result
    assert HS.pace(rising[-10:], as_of)["window_weeks"] == 10
    assert HS.pace(rising[-7:], as_of) == {"pace": None, "pace_raw": None, "window_weeks": None}
    assert HS.pace([(d, None) for d in weeks], as_of)["pace"] is None
    assert HS.pace(rising, as_of - timedelta(weeks=20))["window_weeks"] == 20   # only data before as_of is used


def test_tips():
    assert HS.tip("steps", 2) == "每天多走 2000 步" and HS.tip("steps", -1) == "步数够了，保持"
    assert HS.tip("sleep_duration", 1, 5.5) == H.TIPS["sleep_short"]
    assert HS.tip("sleep_duration", 1, 10) == H.TIPS["sleep_long"]
    assert HS.tip("sleep_duration", 0.1, 6.4) == H.TIPS["sleep_ok"]
    assert all(len(t) <= 20 for t in [H.TIPS["sleep_short"], H.TIPS["sleep_long"], H.TIPS["sleep_ok"]]
               + [x for k, v in H.TIPS.items() if isinstance(v, tuple) for x in v])


# ================================================================== body age: snapshot (synthetic data)
def make_data(days: int = 180, as_of: date = date(2026, 10, 17), **over) -> SimpleNamespace:
    ds = [as_of - timedelta(days=k) for k in range(days)]
    regular = {d: sleep_detail.asleep_minutes([(at((d - timedelta(days=1)).isoformat(), "23:30"),
                                                at(d.isoformat(), "07:00"))], d, SYD) for d in ds}
    base = dict(tz=SYD, birth=date(2001, 6, 15), sex="male", height=180.0, notes=[],
                sleep={d: 7.0 for d in ds}, sleep_days=regular, steps={d: 8078.0 for d in ds},
                mvpa={d: 150 / 7 for d in ds}, strength=defaultdict(float), strength_from=as_of - timedelta(days=days - 1),
                vo2={as_of - timedelta(days=10): 46.5, as_of - timedelta(days=50): 46.5},
                rhr={d: 65.0 for d in ds}, weight={as_of - timedelta(days=5): 85.0}, waist={})
    base.update(over)
    return SimpleNamespace(**base)


def test_snapshot_typical_person_close_to_chrono_age():
    snap = store.snapshot(make_data(), date(2026, 10, 17))
    assert snap["insufficient"] == []
    assert len(snap["contributors"]) == 8
    by = {c["key"]: c for c in snap["contributors"]}
    assert abs(by["steps"]["years"]) < 0.01 and abs(by["mvpa"]["years"]) < 0.01 and by["strength"]["years"] == 0
    assert by["sleep_regularity"]["value"] == 100.0 and by["sleep_regularity"]["years"] < 0     # identical every day
    assert by["body_comp"]["measure"] == "bmi" and by["vo2max"]["percentile"] == pytest.approx(50, abs=1)
    assert snap["body_age"] == pytest.approx(snap["chrono_age"] + snap["delta_years"], abs=0.06)
    assert set(by["sleep_duration"]) >= {"key", "label", "category", "value", "unit", "years", "direction", "tip", "data_days"}


def test_snapshot_insufficient_items_are_listed_with_reasons():
    as_of = date(2026, 10, 17)
    data = make_data(sleep={as_of - timedelta(days=k): 7.0 for k in range(10)}, sex=None, vo2={}, weight={},
                     steps={}, strength_from=None)
    snap = store.snapshot(data, as_of)
    miss = {m["key"]: m["reason"] for m in snap["insufficient"]}
    assert set(miss) == {"sleep_duration", "steps", "strength", "vo2max", "body_comp"}
    assert "10 晚" in miss["sleep_duration"] and "性别" in miss["vo2max"]
    assert snap["body_age"] is not None          # the other 3 items are computed as usual


def test_snapshot_waist_preferred_and_vo2_readings_rule():
    as_of = date(2026, 10, 17)
    snap = store.snapshot(make_data(waist={as_of - timedelta(days=3): 101.0}), as_of)
    bc = next(c for c in snap["contributors"] if c["key"] == "body_comp")
    assert bc["measure"] == "waist_to_height" and bc["value"] == pytest.approx(101 / 180, abs=0.001)
    old = store.snapshot(make_data(vo2={as_of - timedelta(days=150): 50.0, as_of - timedelta(days=170): 50.0}), as_of)
    assert any(m["key"] == "vo2max" and "120" in m["reason"] for m in old["insufficient"])


def test_snapshot_worse_habits_older():
    as_of = date(2026, 10, 17)
    good = store.snapshot(make_data(steps={as_of - timedelta(days=k): 12000.0 for k in range(180)},
                                    rhr={as_of - timedelta(days=k): 50.0 for k in range(180)}), as_of)
    bad = store.snapshot(make_data(steps={as_of - timedelta(days=k): 3000.0 for k in range(180)},
                                   rhr={as_of - timedelta(days=k): 80.0 for k in range(180)}), as_of)
    assert bad["body_age"] > good["body_age"]
    steps_bad = next(c for c in bad["contributors"] if c["key"] == "steps")
    assert steps_bad["years"] == 4.0 and steps_bad["direction"] == "older"


def test_week_ends_sunday_and_partial_current_week():
    weeks = store.week_ends(date(2026, 10, 1), date(2026, 10, 17))   # 10-01 is a Thursday, 10-17 a Saturday
    assert weeks[0] == (date(2026, 9, 28), date(2026, 10, 4))
    assert weeks[-1] == (date(2026, 10, 12), date(2026, 10, 17))
    assert all(ws.weekday() == 0 for ws, _ in weeks)


# ================================================================== sleep regularity / details
def test_sri_regular_irregular_and_insufficient():
    nights = lambda days, bed, wake: {d: sleep_detail.asleep_minutes(
        [(at((d - timedelta(days=1)).isoformat(), bed), at(d.isoformat(), wake))], d, SYD) for d in days}
    days = [date(2026, 10, 1) + timedelta(days=k) for k in range(8)]
    assert sleep_detail.sri(nights(days, "23:00", "07:00")) == (100.0, 7)
    flip = {}
    for i, d in enumerate(days):    # sleeps at night one day and in the daytime the next (12 hours apart), exactly opposite
        flip.update(nights([d], "23:00", "07:00") if i % 2 == 0 else {d: sleep_detail.asleep_minutes(
            [(at(d.isoformat(), "11:00"), at(d.isoformat(), "11:59")), (at((d - timedelta(days=1)).isoformat(), "12:00"),
                                                                       at((d - timedelta(days=1)).isoformat(), "19:00"))], d, SYD)})
    v, pairs = sleep_detail.sri(flip)
    assert pairs == 7 and v < 0
    gap = nights(days[:2], "23:00", "07:00") | nights(days[5:], "23:00", "07:00")   # watch not worn in between: no pairing across the gap
    assert sleep_detail.sri(gap) == (None, 3)
    assert sleep_detail.consistency_pct(-20) == 0 and sleep_detail.consistency_pct(77.4) == 77


def test_asleep_minutes_block_is_noon_to_noon_local():
    d = date(2026, 10, 5)   # Sydney DST starts at 2am on 10-04: the block has only 23 hours
    mins = sleep_detail.asleep_minutes([(at("2026-10-04", "13:00"), at("2026-10-04", "14:00"))], d, SYD)
    assert sum(mins) == 60 and mins[60] == 1    # starts 60 minutes after 12:00


def test_latency_and_efficiency_from_segments():
    night = Night(date(2026, 10, 6), at("2026-10-06", "01:00"), at("2026-10-06", "08:00"), 400, 60, 90, 20, "ow")
    segs = [(at("2026-10-06", "00:40"), at("2026-10-06", "01:00"), "awake"),
            (at("2026-10-06", "01:00"), at("2026-10-06", "08:00"), "core")]
    r = _latency_efficiency(segs, night)
    assert r["latency_min"] == 20 and r["efficiency"] == pytest.approx(400 / 440, abs=0.001)
    r = _latency_efficiency(segs[1:], night)
    assert r["latency_min"] is None and r["efficiency"] == pytest.approx(400 / 420, abs=0.001)


# ================================================================== stress
def test_robust_baseline():
    assert stress.robust_baseline([60.0] * 49) is None
    med, spread = stress.robust_baseline([60.0] * 100)
    assert med == 60 and spread == stress.SPREAD_FLOOR
    med, spread = stress.robust_baseline([50.0 + (i % 21) for i in range(210)])
    assert med == 60 and spread == pytest.approx(1.4826 * 5, abs=0.01)


def test_calm_samples_exclude_sleep_workouts_and_walking():
    t = lambda hm: at("2026-10-06", hm)
    hr = [(t("03:00"), 50.0), (t("10:00"), 70.0), (t("12:00"), 120.0), (t("15:00"), 95.0), (t("16:00"), 66.0)]
    sleep = [(t("01:00"), t("08:00"))]
    workouts = [(t("11:30"), t("12:30"))]
    steps = [(t("14:57"), 30.0), (t("15:58"), 5.0)]
    assert stress.calm_samples(hr, sleep, workouts, steps) == [(t("10:00"), 70.0), (t("16:00"), 66.0)]


def test_score_day_hours_bands_and_hrv():
    d = date(2026, 10, 6)
    calm = [(at("2026-10-06", f"{h:02d}:{m:02d}"), v) for h, v in ((9, 60.0), (13, 75.0), (20, 90.0))
            for m in (0, 10, 20)]
    calm += [(at("2026-10-06", "22:00"), 100.0), (at("2026-10-06", "22:10"), 100.0)]         # only 2 samples: no score
    res = stress.score_day(calm, [], d, SYD, (60.0, 10.0), None)
    by = {x["hour"]: x for x in res["hours"]}
    assert set(by) == {9, 13, 20}
    assert by[9]["score"] == 0.5 and by[9]["level"] == "low"          # same as baseline
    assert by[13]["score"] == 2.0 and by[13]["level"] == "high"       # +1.5 dispersion units
    assert by[20]["score"] == 3.0                                       # capped
    assert res["peak_hour"] == 20 and (res["low_h"], res["medium_h"], res["high_h"]) == (1, 0, 2)
    # daytime HRV below the night baseline -> higher stress
    hrv = [(at("2026-10-06", "09:05"), math.exp(3.5))]
    with_hrv = stress.score_day(calm, hrv, d, SYD, (60.0, 10.0), (4.0, 0.25))
    assert next(x for x in with_hrv["hours"] if x["hour"] == 9)["score"] == pytest.approx(0.5 + 0.3 * 2.0)
    assert stress.score_day([], [], d, SYD, (60, 10), None)["score"] is None


def test_score_day_uses_local_hour_across_utc():
    d = date(2026, 10, 6)
    utc = [(datetime(2026, 10, 5, 23, m, tzinfo=timezone.utc), 60.0) for m in (0, 10, 20)]   # = Sydney 10-06 10:00
    res = stress.score_day(utc, [], d, SYD, (60.0, 10.0), None)
    assert [x["hour"] for x in res["hours"]] == [10]
    assert stress.score_day(utc, [], date(2026, 10, 5), SYD, (60.0, 10.0), None)["hours"] == []


def test_stress_band():
    assert [stress.band(x) for x in (0, 0.99, 1.0, 1.99, 2.0, 3.0)] == ["low", "low", "medium", "medium", "high", "high"]
    assert stress.band(None) is None


# ================================================================== coach / health monitor
def test_strain_target_zones_and_adjustments():
    assert coach.recovery_zone(67) == "green" and coach.recovery_zone(66) == "yellow"
    assert coach.recovery_zone(34) == "yellow" and coach.recovery_zone(33) == "red" and coach.recovery_zone(None) is None
    t = coach.strain_target(80, 5, False)
    assert (t["target_low"], t["target_high"]) == (14, 18)
    t = coach.strain_target(50, -40, False)
    assert (t["target_low"], t["target_high"]) == (8, 12) and len(t["reasons"]) == 2
    t = coach.strain_target(20, -40, False)
    assert (t["target_low"], t["target_high"]) == (0, 8)
    assert (coach.strain_target(90, 0, True)["target_high"]) == 8
    assert coach.strain_target(None, None, False)["target_low"] == 10


def test_sleep_need_tonight_same_formula_as_scores():
    r = coach.sleep_need_tonight([420] * 14, 14.0)
    assert r["debt"] == pytest.approx(60) and r["need"] == pytest.approx(480 + 60 + 21)
    assert coach.sleep_need_tonight([None] * 14, None)["need"] == 480


def test_monitor_range():
    assert coach.monitor_range([50.0] * 13, "rhr") is None
    lo, hi, mid = coach.monitor_range([50.0] * 20, "rhr")
    assert (lo, hi, mid) == (47.0, 53.0, 50.0)                    # with standard deviation 0, use the minimum half-bandwidth 3
    lo, hi, mid = coach.monitor_range([40.0, 60.0] * 10, "hrv")
    assert lo < mid < hi and mid == pytest.approx(math.sqrt(2400), rel=0.01)   # symmetric on the ln scale


# ================================================================== reports / fitness
def test_clock_mean_is_circular():
    assert reports._clock_mean(["2026-10-01T23:30+10:00", "2026-10-02T00:30+10:00"]) == "00:00"
    assert reports._clock_mean(["2026-10-01T10:00+10:00", "2026-10-02T14:00+10:00"]) == "12:00"
    assert reports._clock_mean([None]) is None


def test_delta_nested():
    assert reports._delta({"a": 3, "b": {"c": 1.5}, "s": "x", "f": True}, {"a": 1, "b": {"c": 1.0}}) == {"a": 2, "b": {"c": 0.5}}
    assert reports._delta(5, None) is None


def test_fitness_level_boundaries():
    assert fitness.level(80) == ("excellent", "优秀") and fitness.level(79.9) == ("good", "良好")
    assert fitness.level(39) == ("fair", "一般") and fitness.level(5) == ("poor", "较差")
    assert fitness.level(None) == (None, None)
