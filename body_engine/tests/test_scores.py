"""Formulas and boundaries of the scoring engine (synthetic data, no database). End-to-end tests for the database / API / assistant are in test_scores_db.py."""
import math
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from body_engine.scores import engine, params as P
from body_engine.scores import recovery as R
from body_engine.scores import sleep as S
from body_engine.scores import strain as L
from body_engine.scores.inputs import Inputs, Night, is_watch_sleep

SYD = ZoneInfo("Australia/Sydney")


def at(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=SYD)


# ------------------------------------------------------------------ sleep score (OW spec)

def test_duration_score_matches_ow_curve():
    assert S.duration_score(7.0) == S.duration_score(8.0) == S.duration_score(9.0) == 100
    # Too little sleep: k=-1.5, midpoint 5h, equals 100 at 7h -> 5h = 100 x (1 + e^-3) / 2 = 52.49
    assert S.duration_score(5.0) == 52
    assert S.duration_score(6.99) == 99
    assert S.duration_score(2.0) == 1 and S.duration_score(0.5) == 0
    # Oversleeping: drops slowly, minimum 50
    assert 50 < S.duration_score(10.0) < 100
    assert S.duration_score(16.0) == 50


def test_stages_score_linear_below_target():
    assert S.stages_score(90, 90) == 100
    assert S.stages_score(45, 90) == 75          # deep sleep 50 points x 0.5 + REM 100 x 0.5
    assert S.stages_score(0, 0) == 0
    assert S.stages_score(200, 30) == 66         # exceeding the target adds nothing: 100 x 0.5 + 33 x 0.5


def test_consistency_score_late_early_and_midnight_wrap():
    hist = [at("2026-10-01", "23:30")] * 5
    assert S.consistency_score([], at("2026-10-06", "23:30")) == 0                 # no history: OW gives 0
    assert S.consistency_score(hist, at("2026-10-06", "23:40")) == 100              # no deduction within 15 minutes
    assert S.consistency_score(hist, at("2026-10-06", "00:00")) == 85               # 30 minutes late: deduct 15/105
    assert S.consistency_score(hist, at("2026-10-06", "00:30")) == 57               # continuous across midnight: 60 minutes late
    assert S.consistency_score(hist, at("2026-10-06", "03:00")) == 0                # 2h or more late: fully deducted
    assert S.consistency_score(hist, at("2026-10-06", "20:00")) == 80               # going to bed early deducts at most 20
    assert S.hours_past_noon(at("2026-10-06", "02:00")) == 14


def test_interruptions_score():
    assert S.interruptions_score(20, [10]) == 100           # no deduction within 20 minutes; 0-1 awakenings give full marks
    assert S.interruptions_score(0, [6, 6]) == 95           # 2 awakenings is 0.75 x 20
    assert S.interruptions_score(55, []) == 60              # over 35 minutes: 80 x (1 - 35/70) + 20
    assert S.interruptions_score(200, [6] * 4) == 0
    assert S.interruptions_score(0, [3, 4, 5, 2]) == 100    # awake periods <= 5 minutes don't count as awakenings


def test_waso_strips_latency_and_lie_in_and_merges_adjacent():
    segs = [(at("2026-10-06", "01:00"), at("2026-10-06", "01:30"), "awake"),   # sleep onset latency, not counted
            (at("2026-10-06", "01:30"), at("2026-10-06", "03:00"), "core"),
            (at("2026-10-06", "03:00"), at("2026-10-06", "03:04"), "awake"),
            (at("2026-10-06", "03:04"), at("2026-10-06", "03:10"), "awake"),   # adjacent to the previous segment -> one 10-minute awakening
            (at("2026-10-06", "03:10"), at("2026-10-06", "07:00"), "deep"),
            (at("2026-10-06", "07:00"), at("2026-10-06", "08:00"), "awake")]   # lingering in bed, not counted
    total, awakenings = S.waso(segs)
    assert total == 10 and awakenings == [10]
    assert S.waso([(at("2026-10-06", "01:00"), at("2026-10-06", "02:00"), "awake")]) == (0.0, [])


def test_sleep_score_overall_weights():
    hist = [at("2026-10-01", "23:00")] * 14
    perfect = S.sleep_score(480, 90, 90, at("2026-10-06", "23:00"), hist, 0, [])
    assert perfect == {"score": 100, "duration": 100, "stages": 100, "consistency": 100, "interruptions": 100}
    # regularity 0, everything else full -> 80
    assert S.sleep_score(480, 90, 90, at("2026-10-06", "04:00"), hist, 0, [])["score"] == 80
    with pytest.raises(ValueError):
        S.sleep_score(0, 0, 0, at("2026-10-06", "04:00"), hist, 0, [])


# ------------------------------------------------------------------ sleep need / debt / performance

def test_sleep_debt_weighted_average_with_floor():
    assert S.sleep_debt([None] * 14) == 0
    assert S.sleep_debt([420] * 14) == pytest.approx(60)
    assert S.sleep_debt([600] * 14) == 0                                   # oversleeping never becomes debt
    assert S.sleep_debt([300, 480]) == pytest.approx(180 / 1.75)           # last night has weight 1, the night before 0.75
    assert S.sleep_debt([300, None, 480]) == pytest.approx(180 / (1 + 0.75 ** 2))
    assert S.sleep_debt([0] * 20) == 480                                   # only 14 nights are considered


def test_sleep_need_and_performance():
    assert S.sleep_need(0, None)["need"] == 480
    assert S.sleep_need(60, 14)["need"] == 480 + 60 + 21                    # strain 14 -> (14 - 7) x 3
    assert S.sleep_need(500, 3)["need"] == 480 + 120                        # sleep debt adds at most 2h; strain <= 7 adds nothing
    assert S.performance(240, 480) == 0.5
    assert S.performance(600, 480) == 1.0


# ------------------------------------------------------------------ recovery

def test_night_hrv_is_median_of_ln_inside_window():
    hrv = [(at("2026-10-06", "00:30"), 100.0), (at("2026-10-06", "02:00"), 30.0),
           (at("2026-10-06", "04:00"), 40.0), (at("2026-10-06", "06:00"), 90.0), (at("2026-10-06", "12:00"), 5.0)]
    v, n = R.night_hrv_ln(hrv, at("2026-10-06", "01:00"), at("2026-10-06", "08:00"))
    assert n == 3 and v == pytest.approx(math.log(40))
    assert R.night_hrv_ln(hrv, at("2026-10-07", "01:00"), at("2026-10-07", "08:00")) == (None, 0)


def test_lowest_five_minute_mean():
    start, end = at("2026-10-06", "01:00"), at("2026-10-06", "07:00")
    dense = [(start + timedelta(minutes=i), 60.0) for i in range(360)]
    for i in range(200, 205):                         # drops to 45 between 03:20 and 03:25
        dense[i] = (dense[i][0], 45.0)
    dense[100] = (dense[100][0], 30.0)                # a single artifact is diluted by the 5-minute mean
    assert R.lowest_window_mean(dense, start, end) == pytest.approx(45.0)
    # Sparse (the watch records about one sample per 4-5 minutes during sleep): each sample represents the span until the next one
    sparse = [(start + timedelta(minutes=5 * i), 55.0 - (i == 10) * 5) for i in range(70)]
    assert R.lowest_window_mean(sparse, start, end) == pytest.approx(50.0)
    # Only one sample: fall back to the lowest single sample when coverage is under half
    assert R.lowest_window_mean([(start, 52.0)], start, start + timedelta(minutes=1)) == 52.0
    assert R.lowest_window_mean([], start, end) is None


def test_baseline_needs_14_nights():
    assert R.baseline([3.8] * 13) is None
    mean, sd = R.baseline([3.8] * 7 + [4.0] * 7)
    assert mean == pytest.approx(3.9) and sd > 0


def test_zscore_floor_and_clip():
    assert R.zscore(4.0, 3.9, 0.0, 0.05) == pytest.approx(2.0)     # standard deviation too small: use the floor
    assert R.zscore(10, 0, 1, 0.1) == P.Z_CLIP
    assert R.zscore(-10, 0, 1, 0.1) == -P.Z_CLIP


def test_recovery_score_logistic_and_reweighting():
    neutral, s = R.recovery_score({"hrv": 0, "rhr": 0, "sleep_performance": 0, "resp_rate": 0})
    assert s == 0 and neutral == round(100 / (1 + math.exp(-P.RECOVERY_OFFSET)))
    high, _ = R.recovery_score({"hrv": 2, "rhr": 1})
    low, _ = R.recovery_score({"hrv": -2, "rhr": -1})
    assert low < neutral < high and 0 <= low and high <= 100
    # Missing items redistribute their weight proportionally to the others: the result is unchanged when all z are equal
    assert R.recovery_score({"hrv": 1})[0] == R.recovery_score({"hrv": 1, "rhr": 1, "resp_rate": 1})[0]
    with pytest.raises(ValueError):
        R.recovery_score({"rhr": 1})


def test_illness_temperature_spo2_flags():
    base_ln = math.log(50)
    assert R.illness_warning(56, 50, math.log(40), base_ln)          # +6 bpm and HRV 0.8 x
    assert not R.illness_warning(55, 50, math.log(40), base_ln)      # exactly +5 doesn't count
    assert not R.illness_warning(56, 50, math.log(45), base_ln)      # HRV 0.9 x doesn't count
    assert not R.illness_warning(None, 50, math.log(40), base_ln)
    assert R.wrist_temp_flag(35.5, [34.8] * 4) is None               # baseline under 5 nights
    assert R.wrist_temp_flag(35.5, [34.8] * 5) == pytest.approx(0.7)
    assert R.wrist_temp_flag(35.3, [34.8] * 5) is None
    assert R.spo2_flag([97, 96, 95]) is None
    assert R.spo2_flag([93, 93, 96])["median"] == 93
    assert R.spo2_flag([97, 89, 88, 97, 97])["below_90"] == 2
    assert R.spo2_flag([]) is None


# ------------------------------------------------------------------ strain

def test_hrmax_rule():
    assert L.hrmax_formula(25) == pytest.approx(190.5)
    assert L.hrmax(25, 160) == pytest.approx(190.5)        # use the formula when the observed value is lower
    assert L.hrmax(25, 198) == 198                         # use the observed value when it is higher
    assert L.age_on(date(2001, 6, 15), date(2026, 6, 14)) == 24
    assert L.age_on(date(2001, 6, 15), date(2026, 6, 15)) == 25


def test_hr_histogram_percentile_sliding():
    h = L.HrHistogram()
    assert h.percentile(99.5) is None
    day1 = Counter({60: 990, 180: 10})
    h.add(day1)
    assert h.percentile(99.5) == 180 and h.percentile(50) == 60
    h.add(day1, -1)
    h.add(Counter({70: 5}))
    assert h.percentile(99.5) == 70 and h.n == 5


def test_zone_weights_edges():
    assert L.zone_weight(99.9, 200) == 0
    assert L.zone_weight(100, 200) == 1
    assert L.zone_weight(139.9, 200) == 2
    assert L.zone_weight(140, 200) == 3
    assert L.zone_weight(180, 200) == 5
    assert L.zone_weight(250, 200) == 5


def test_sample_minutes_and_trimp():
    t0 = at("2026-10-06", "18:00")
    times = [t0, t0 + timedelta(seconds=5), t0 + timedelta(minutes=30)]
    assert L.sample_minutes(times) == pytest.approx([5 / 60, 5.0, 2.5])   # gap capped at 5 minutes; the last sample counts as half
    # One sample per minute for 60 minutes: first 30 minutes at 130 bpm (65% -> zone 2), last 30 minutes at 170 bpm (85% -> zone 4)
    samples = [(t0 + timedelta(minutes=i), 130.0 if i < 30 else 170.0) for i in range(60)]
    minutes = L.sample_minutes([t for t, _ in samples])
    trimp, zones = L.edwards_trimp(samples, minutes, 200)
    assert zones == pytest.approx([0, 30, 0, 31.5, 0])      # the last sample counts as half the cap (2.5 minutes)
    assert trimp == pytest.approx(30 * 2 + 31.5 * 4)


def test_strain_curve():
    assert L.strain(0) == 0
    assert L.strain(P.STRAIN_K) == pytest.approx(21 * (1 - math.exp(-1)))
    assert 10 <= L.strain(145) <= 14                     # Calibration: the median TRIMP of the user's strength training days is about 145
    assert L.strain(1e6) == pytest.approx(21)
    assert L.strain(100) < L.strain(200) < 21


def test_training_load_ewma():
    assert L.training_load([None, None])[0]["ctl"] is None
    out = L.training_load([100.0] * 30)
    assert out[-1]["ctl"] == pytest.approx(100) and out[-1]["tsb"] == pytest.approx(0)
    out = L.training_load([0.0] * 14 + [300.0])
    assert out[-1]["atl"] > out[-1]["ctl"] > 0 and out[-1]["tsb"] < 0     # Sudden load increase: ATL rises faster than CTL
    held = L.training_load([50.0, None, 80.0])
    assert held[1]["missing"] and held[1]["ctl"] == held[0]["ctl"]          # days without data carry the previous day forward


# ------------------------------------------------------------------ day-by-day orchestration (a synthetic person)

def make_inputs(nights: int = 20, sick_night: int | None = None, first: date = date(2026, 9, 1)) -> Inputs:
    """Sleeps 23:00-07:00 every night from 2026-09-01; 2 HRV samples per night, one heart rate sample every 4 minutes; trains 1 hour at 18:00 every day."""
    inp = Inputs(SYD, date(2001, 6, 15))
    for i in range(nights):
        wake = first + timedelta(days=i)
        start = datetime.combine(wake - timedelta(days=1), datetime.min.time(), SYD).replace(hour=23)
        end = start + timedelta(hours=8)
        sick = i == sick_night
        inp.nights.append(Night(wake, start, end, 470, 80, 100, 10, "ow"))
        inp.segments += [(start, start + timedelta(hours=3), "core"), (start + timedelta(hours=3), start + timedelta(hours=3, minutes=10), "awake"),
                         (start + timedelta(hours=3, minutes=10), end, "deep")]
        base_hrv = 50 + (i % 5)          # slight variation, so the baseline standard deviation is not 0
        inp.hrv += [(start + timedelta(hours=2), (35.0 if sick else base_hrv)), (start + timedelta(hours=5), (35.0 if sick else base_hrv + 2))]
        rest = 58.0 if sick else 48.0 + (i % 3)
        inp.hr += [(start + timedelta(minutes=4 * k), rest + (k % 4)) for k in range(120)]
        inp.resp += [(start + timedelta(hours=1), 16.0 + (i % 2) * 0.5)]
        day = datetime.combine(wake, datetime.min.time(), SYD)
        inp.hr += [(day + timedelta(hours=h), 75.0) for h in range(8, 22)]                     # one sample per hour in the daytime
        inp.hr += [(day + timedelta(hours=18, seconds=5 * k), 150.0) for k in range(720)]      # trains 1 hour at 18:00
        inp.daily_rhr[wake] = 55.0
    inp.hr.sort()
    inp.hrv.sort()
    return inp


def test_score_days_full_pipeline():
    inp = make_inputs(20)
    rows = engine.score_days(inp, date(2026, 9, 1), date(2026, 9, 21), today=date(2026, 10, 16))
    by = {r["date"]: r for r in rows}
    assert all(r["algo_version"] == P.ALGO_VERSION and r["hrv_kind"] == "sdnn" for r in rows)
    first, d15, d20 = by[date(2026, 9, 1)], by[date(2026, 9, 15)], by[date(2026, 9, 20)]
    # Sleep: 470 minutes per night, deep 80 / REM 100, one 10-minute awakening, regular
    assert first["sleep_actual_min"] == 470 and first["details"]["sleep"]["awakenings"] == 1
    assert d20["sleep_score"] >= 90 and d20["details"]["sleep"]["consistency"] == 100
    assert d20["sleep_debt_min"] == 10 and d20["sleep_need_min"] > 480
    # Recovery: a score is only given with a full 14-night baseline
    assert first["recovery"] is None and "baseline_insufficient" in first["flags"]
    assert by[date(2026, 9, 14)]["recovery"] is None
    assert d15["recovery"] is not None and 0 <= d15["recovery"] <= 100
    assert d15["rhr_night"] < 52 and d15["hrv_night_ln"] == pytest.approx((math.log(54) + math.log(56)) / 2, abs=1e-4)
    # Strain: trains 1 hour at 150 bpm every day (about 78% HRmax -> zone 3)
    assert d15["trimp"] == pytest.approx(180, rel=0.05) and 10 <= d15["strain"] <= 16
    assert d15["hr_max"] == pytest.approx(190.5)                     # Age 25: 208 - 0.7 x 25, the observed value didn't exceed it
    assert d15["ctl"] is not None and "ctl_warmup" in d15["flags"]
    # 9-21: no sleep and no heart rate after the last night
    last = by[date(2026, 9, 21)]
    assert last["sleep_score"] is None and last["strain"] is None
    assert set(last["flags"]) >= {"no_sleep", "no_raw_hr", "load_missing"}


def test_score_days_illness_and_fallbacks():
    inp = make_inputs(20, sick_night=18)
    rows = {r["date"]: r for r in engine.score_days(inp, date(2026, 9, 1), date(2026, 9, 20), today=date(2026, 10, 16))}
    sick = rows[date(2026, 9, 19)]
    assert "illness_warning" in sick["flags"] and sick["recovery"] < rows[date(2026, 9, 18)]["recovery"]
    # No raw heart rate during sleep -> fall back to Apple's all-day resting heart rate and compare against its own baseline (no false illness alarm)
    night = inp.nights[19]
    inp.hr = [s for s in inp.hr if not night.start <= s[0] <= night.end]
    r = {x["date"]: x for x in engine.score_days(inp, date(2026, 9, 1), date(2026, 9, 20), today=date(2026, 10, 16))}[date(2026, 9, 20)]
    assert r["rhr_night"] == 55.0 and "rhr_from_daily" in r["flags"] and "illness_warning" not in r["flags"]


def test_score_days_shortcut_only_day_and_coverage():
    inp = make_inputs(3)
    inp.shortcut_dates = {date(2026, 9, 4)}
    inp.hr += [(at("2026-09-05", "01:00"), 60.0)]          # only one heart rate sample on 9-05
    inp.hr.sort()
    rows = {r["date"]: r for r in engine.score_days(inp, date(2026, 9, 1), date(2026, 9, 5), today=date(2026, 9, 5))}
    assert "快捷指令" in rows[date(2026, 9, 4)]["flags"]["no_raw_hr"]
    assert rows[date(2026, 9, 4)]["strain"] is None
    today = rows[date(2026, 9, 5)]
    assert "day_in_progress" in today["flags"] and "hr_coverage_low" not in today["flags"]
    # Past days with under 12 hours of heart rate: no workout recorded -> left empty, not entered into CTL; workout recorded -> scored as usual and flagged "possibly low"
    inp.hr += [(at("2026-09-06", "01:00"), 60.0)]
    inp.hr += [(at("2026-09-07", "18:00") + timedelta(seconds=5 * k), 150.0) for k in range(720)]
    inp.hr.sort()
    rows = {r["date"]: r for r in engine.score_days(inp, date(2026, 9, 1), date(2026, 9, 7), today=date(2026, 10, 1))}
    empty, trained = rows[date(2026, 9, 6)], rows[date(2026, 9, 7)]
    assert empty["strain"] is None and "1 小时" in empty["flags"]["no_raw_hr"] and "load_missing" in empty["flags"]
    assert empty["details"]["load"]["trimp_recorded"] == 0
    assert trained["strain"] > 10 and "hr_coverage_low" in trained["flags"] and "load_missing" not in trained["flags"]


def test_naps_count_toward_next_main_sleep():
    inp = make_inputs(3)
    nap_start = at("2026-09-02", "15:00")
    inp.nights.append(Night(date(2026, 9, 2), nap_start, nap_start + timedelta(minutes=40), 40, 0, 0, 0, "ow"))
    rows = {r["date"]: r for r in engine.score_days(inp, date(2026, 9, 1), date(2026, 9, 3), today=date(2026, 10, 1))}
    assert rows[date(2026, 9, 2)]["sleep_actual_min"] == 470            # the nap comes after the main sleep
    assert rows[date(2026, 9, 3)]["sleep_actual_min"] == 510            # counted into the next night


def test_wrist_temperature_dated_from_the_sleep_schedule():
    """FB-18: Apple dates the sleeping wrist temperature reading from the start of the sleep schedule, hours before the
    watch's first sleep stage. Nights sleep 23:00–07:00 here; the late night of 9-04 starts at 04:00."""
    inp = make_inputs(4)
    late = inp.nights[3]
    inp.nights[3] = Night(late.wake_date, at("2026-09-04", "04:00"), at("2026-09-04", "10:00"), 350, 60, 80, 10, "ow")
    inp.temp = [(at("2026-08-31", "22:00"), 35.0),    # 9-01: 1 h before sleep, inside the old ±2 h window
                (at("2026-09-02", "00:30"), 35.1),    # 9-02: during the night
                (at("2026-09-03", "23:00"), 35.4)]    # 9-04: 5 h before the late night, after 9-03 ended (07:00 + 2 h)
    rows = {r["date"]: r for r in engine.score_days(inp, date(2026, 9, 1), date(2026, 9, 4), today=date(2026, 10, 1))}
    wt = {d: (r["details"].get("recovery") or {}).get("wrist_temp") for d, r in rows.items()}
    assert wt == {date(2026, 9, 1): 35.0, date(2026, 9, 2): 35.1, date(2026, 9, 3): None, date(2026, 9, 4): 35.4}
    # one reading never serves two nights: 08:30 on 9-03 is 9-03's (ended 07:00), not the early-starting 9-04's (from 15:00)
    inp.nights[3] = Night(late.wake_date, at("2026-09-03", "15:00"), at("2026-09-03", "22:00"), 350, 60, 80, 10, "ow")
    inp.temp = [(at("2026-09-03", "08:30"), 35.4)]
    rows = {r["date"]: r for r in engine.score_days(inp, date(2026, 9, 3), date(2026, 9, 4), today=date(2026, 10, 1))}
    assert rows[date(2026, 9, 3)]["details"]["recovery"]["wrist_temp"] == 35.4
    assert rows[date(2026, 9, 4)]["details"]["recovery"]["wrist_temp"] is None
    # nor more than 8 h before the night
    inp.nights[3] = Night(late.wake_date, at("2026-09-04", "08:00"), at("2026-09-04", "14:00"), 350, 60, 80, 10, "ow")
    inp.temp = [(at("2026-09-03", "23:30"), 35.4)]
    rows = {r["date"]: r for r in engine.score_days(inp, date(2026, 9, 4), date(2026, 9, 4), today=date(2026, 10, 1))}
    assert (rows[date(2026, 9, 4)]["details"].get("recovery") or {}).get("wrist_temp") is None


def test_watch_sleep_detection():
    assert is_watch_sleep({"device": None, "core_min": 100, "deep_min": 0, "rem_min": 0})
    assert is_watch_sleep({"device": "Apple Watch", "core_min": 0, "deep_min": 0, "rem_min": 0})
    assert not is_watch_sleep({"device": "iPhone", "core_min": 0, "deep_min": 0, "rem_min": 0})
