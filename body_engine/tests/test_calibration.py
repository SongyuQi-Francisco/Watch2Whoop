"""Calibration decision rules (body_engine.validation.calibration, docs/scoring-calibration.md, issue #118). Synthetic data only;
no database, no real person's values."""
import math
import statistics
from datetime import date, timedelta

import pytest

from body_engine.scores import params as P
from body_engine.validation import calibration as K
from body_engine.validation import calibration_params as C
from body_engine.validation import calibration_synth as KS
from body_engine.validation import study, synthetic
from body_engine.validation.__main__ import main


@pytest.fixture(scope="module")
def noisy_sdnn():
    return KS.make_person(300, 3, rmssd_noise=0.15, sdnn_noise=0.45)


@pytest.fixture(scope="module")
def no_difference():
    return KS.make_person(300, 4, rmssd_noise=0.25, sdnn_noise=0.25)


# ---------------------------------------------------------------- export
def test_export_is_one_user_and_read_only():
    queries = K.export_queries(7)
    assert set(queries) == {"beats.csv", "sdnn.csv", "nights.csv", "scores.csv", "daily_rhr.csv", "hrr.csv"}
    for sql in queries.values():
        assert sql.lstrip().startswith("SELECT")
        for part in sql.split("UNION ALL"):
            assert "user_id = 7" in part
        words = sql.upper().split()
        assert not {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"} & set(words)


def test_export_sql_wraps_every_copy_in_a_read_only_transaction(capsys):
    main(["calibration", "export-sql", "--user-id", "7"])
    out = capsys.readouterr().out
    assert out.count("BEGIN READ ONLY;") == out.count("COPY (") == out.count("ROLLBACK;") == 6


def test_a_reading_from_two_sources_counts_once():
    rows = [{"src": "ow", "ts": "2030-01-01 01:00:00+00:00", "value": "40.0"},
            {"src": "hk", "ts": "2030-01-01 01:00:00.123456+00:00", "value": "40.25"},
            {"src": "shortcut", "ts": "2030-01-01 01:00:00+00:00", "value": "40.2"},
            {"src": "ow", "ts": "2030-01-01 02:00:00+00:00", "value": "50.0"}]
    merged = K._merge_sdnn(rows)
    assert [v for _, v in merged] == [40.25, 50.0]


# ---------------------------------------------------------------- replay
def _person_from_engine(days=120, seed=5):
    """A synthetic person scored by the real engine; the replay has to reproduce that."""
    s = synthetic.make_person(days, seed)
    rows = study.score(s.inputs, s.first, s.last)
    by = {r["date"]: r for r in rows}
    mains = {}
    for n in s.inputs.nights:
        if n.wake_date not in mains or n.asleep_min > mains[n.wake_date].asleep_min:
            mains[n.wake_date] = n
    p = K.Person(stored_kind="sdnn", sdnn=list(s.inputs.hrv), daily_rhr=dict(s.inputs.daily_rhr))
    for d, n in mains.items():
        r = by[d]
        p.days[d] = K.Day(d, n.start, n.end, rhr=r["rhr_night"], rhr_from_daily="rhr_from_daily" in r["flags"], resp=r["resp_rate_night"],
                          perf=r["sleep_performance"], actual_min=r["sleep_actual_min"], trimp=r["trimp"], strain=r["strain"],
                          debt_min=r["sleep_debt_min"], stored_recovery=r["recovery"], stored_hrv_ln=r["hrv_night_ln"])
    for r in rows:
        if r["trimp"] is not None and r["strain"] is not None:
            p.train_days[r["date"]] = (r["trimp"], r["strain"])
    return p


def test_replay_reproduces_the_real_engine():
    p = _person_from_engine()
    series = K.sdnn_series(p)
    res = K.rule_a(p, series, K.replay(p, series))
    assert res["ok"] and res["days"] > 60 and res["within_1"] >= C.REPLAY_WITHIN_1 and res["only_stored"] == res["only_replay"] == 0


def test_replayed_performance_matches_the_stored_one():
    p = _person_from_engine()
    perf, strain = K.recomputed_perf(p)
    assert all(abs(perf[d] - x.perf) < 0.005 for d, x in p.days.items() if x.perf is not None and d in perf)
    assert all(abs(strain[d] - p.train_days[d][1]) < 0.011 for d in strain)


def test_rule_a_failure_blocks_every_decision():
    p = KS.make_person(120, 2)
    for x in p.days.values():
        if x.stored_recovery is not None:
            x.stored_recovery = min(100, x.stored_recovery + 10)
    res = K.run(p, n_boot=20, thresholds=(0.20,), synthetic_check_n=5)
    assert not res["rule_a"]["ok"]
    assert {res["d2_rmssd_vs_sdnn"]["verdict"], res["d3"]["20%"]["verdict"], res["d4_logistic"]["verdict"]} == {"no decision (rule A failed)"}


def test_the_analysis_leaves_the_parameters_alone():
    before = (P.RECOVERY_SLOPE, P.RECOVERY_OFFSET, dict(P.RECOVERY_WEIGHTS), P.STRAIN_K, P.HRV_KIND, K.H.MAX_DEVIATION)
    K.run(KS.make_person(120, 2), n_boot=20, thresholds=(0.20,), synthetic_check_n=5)
    assert before == (P.RECOVERY_SLOPE, P.RECOVERY_OFFSET, dict(P.RECOVERY_WEIGHTS), P.STRAIN_K, P.HRV_KIND, K.H.MAX_DEVIATION)


# ---------------------------------------------------------------- statistics
def test_pearson_from_sums_is_pearson():
    xs, ys = [1.0, 2.0, 4.0, 3.0, 7.0, 5.0], [2.0, 1.0, 5.0, 2.5, 9.0, 4.0]
    s = [sum(c) for c in zip(*(K._pair(x, y) for x, y in zip(xs, ys)))]
    assert K._r_from(s) == pytest.approx(statistics.correlation(xs, ys))


def test_icc_matches_the_anova_formula_with_unequal_groups():
    groups = [[1.0, 1.2], [2.0, 2.1, 2.3], [3.0, 2.8], [0.5, 0.7, 0.4, 0.6], [1.5, 1.4], [2.5, 2.7]]
    s = [sum(c) for c in zip(*(K._group(g) for g in groups))]
    n, k = sum(map(len, groups)), len(groups)
    grand = statistics.fmean(x for g in groups for x in g)
    msb = sum(len(g) * (statistics.fmean(g) - grand) ** 2 for g in groups) / (k - 1)
    msw = sum((x - statistics.fmean(g)) ** 2 for g in groups for x in g) / (n - k)
    n0 = (n - sum(len(g) ** 2 for g in groups) / n) / (k - 1)
    assert K._icc_from(s) == pytest.approx((msb - msw) / (msb + (n0 - 1) * msw))


def test_identical_series_have_a_zero_interval(no_difference):
    p = no_difference
    a = K.rmssd_series(p, "a")
    paired = sorted(a.nightly)
    cal = K._calendar(paired)
    rhr = {d: p.days[d].rhr for d in paired}
    st = K.primary_stats(a, set(paired), rhr, cal)
    ci = K.bootstrap_diffs(st, st, len(cal), n=50)
    assert all(v == (0.0, 0.0) for v in ci.values())


def test_the_bootstrap_is_repeatable(no_difference):
    p = no_difference
    a, b = K.sdnn_series(p), K.rmssd_series(p, "b")
    paired = sorted(set(a.nightly) & set(b.nightly))
    cal = K._calendar(paired)
    rhr = {d: p.days[d].rhr for d in paired}
    sa, sb = K.primary_stats(a, set(paired), rhr, cal), K.primary_stats(b, set(paired), rhr, cal)
    assert K.bootstrap_diffs(sa, sb, len(cal), n=50, seed=9) == K.bootstrap_diffs(sa, sb, len(cal), n=50, seed=9)


# ---------------------------------------------------------------- D2, D3: known answers
def test_d2_finds_truly_less_noisy_readings(noisy_sdnn):
    p = noisy_sdnn
    res = K.compare(p, K.sdnn_series(p), K.rmssd_series(p, "RMSSD"), n_boot=300, era=K.app_era(p))
    assert res["verdict"] == "eligible" and res["gates"]["E_improvement"]["ok"] and res["diff"]["P3"] > 0


def test_d2_keeps_the_reference_when_there_is_no_difference(no_difference):
    p = no_difference
    res = K.compare(p, K.sdnn_series(p), K.rmssd_series(p, "RMSSD"), n_boot=300, era=K.app_era(p))
    assert res["verdict"] == "keep"


def test_too_few_nights_decide_nothing():
    p = KS.make_person(60, 2)
    assert K.compare(p, K.sdnn_series(p), K.rmssd_series(p, "RMSSD"), n_boot=20)["verdict"] == "insufficient data"


def test_d3_clean_data_has_no_practical_effect():
    p = KS.make_person(200, 6)
    cur, cand = K.rmssd_series(p, "25%"), K.rmssd_series(p, "20%", max_deviation=0.20)
    extra = K.filter_extras(cur, cand, len(p.readings))
    res = K.compare(p, cur, cand, n_boot=100, era=K.app_era(p), extra=extra)
    assert res["verdict"].startswith("keep") and not res["gates"]["F_practical"]["ok"] and extra["reading_loss"]["ok"]


def test_d3_tighter_filter_removes_mild_premature_beats():
    p = KS.make_person(200, 6, ectopic_rate=0.03)
    cur, cand = K.rmssd_series(p, "25%"), K.rmssd_series(p, "20%", max_deviation=0.20)
    shifts = [cand.per_reading[t] - cur.per_reading[t] for t in set(cur.per_reading) & set(cand.per_reading)]
    assert statistics.median(shifts) < -0.05   # these beats inflated RMSSD


def test_over_filtering_is_caught():
    p = KS.make_person(120, 6)
    cur = K.rmssd_series(p, "25%")
    squeezed = K.Series("x", cur.nightly, cur.readings, {t: v - 0.2 for t, v in cur.per_reading.items()})
    assert not K.filter_extras(cur, squeezed, len(p.readings))["no_overfilter"]["ok"]


def test_lost_readings_are_caught():
    p = KS.make_person(120, 6)
    cur = K.rmssd_series(p, "25%")
    few = dict(list(cur.per_reading.items())[: int(len(cur.per_reading) * 0.9)])
    assert not K.filter_extras(cur, K.Series("x", cur.nightly, cur.readings, few), len(p.readings))["reading_loss"]["ok"]


def test_a_broad_churn_needs_two_improved_metrics(noisy_sdnn, monkeypatch):
    p = noisy_sdnn
    series = K.sdnn_series(p), K.rmssd_series(p, "RMSSD")
    wide, above = (-0.1, 0.1), (0.01, 0.5)
    monkeypatch.setattr(K, "bootstrap_diffs", lambda *a, **k: {"P1": wide, "P2": above, "P3": wide})
    one = K.compare(p, *series, era=K.app_era(p))
    assert one["impact"]["zone_change"] > C.CHURN_ZONE and one["gates"]["E_improvement"]["metrics"] == ["P2"]
    assert not one["gates"]["G_churn"]["ok"] and one["verdict"] == "keep"
    monkeypatch.setattr(K, "bootstrap_diffs", lambda *a, **k: {"P1": wide, "P2": above, "P3": above})
    two = K.compare(p, *series, era=K.app_era(p))
    assert two["gates"]["G_churn"]["ok"] and two["verdict"] == "eligible"


def test_harm_on_any_metric_blocks_a_candidate(noisy_sdnn, monkeypatch):
    p = noisy_sdnn
    series = K.sdnn_series(p), K.rmssd_series(p, "RMSSD")
    monkeypatch.setattr(K, "bootstrap_diffs", lambda *a, **k: {"P1": (-0.3, -0.01), "P2": (0.01, 0.5), "P3": (0.01, 0.5)})
    res = K.compare(p, *series, era=K.app_era(p))
    assert not res["gates"]["D_no_harm"]["by_metric"]["P1"] and res["verdict"] == "keep"


def test_named_threshold_is_the_only_one_that_can_be_eligible():
    res = K.run(KS.make_person(120, 2), n_boot=20, thresholds=(0.15, 0.20, 0.30), synthetic_check_n=5)
    assert res["d3"]["15%"]["verdict"].startswith("trend only") and res["d3"]["30%"]["verdict"].startswith("trend only")
    assert res["d3"]["local"]["verdict"].startswith("exploratory")
    assert not res["d3"]["20%"]["verdict"].startswith(("trend", "exploratory"))


# ---------------------------------------------------------------- D4
def _rows(zs, first=date(2030, 1, 1)):
    return [{"date": first + timedelta(days=i), "weighted_z": z, "z": {"hrv": z}, "hrv_base": (0.0, 1.0),
             "recovery": K._map_score(z, P.RECOVERY_SLOPE, P.RECOVERY_OFFSET)} for i, z in enumerate(zs)]


def test_tercile_mapping_puts_the_terciles_on_the_colour_boundaries():
    zs = [(i - 50) / 25 for i in range(101)]
    slope, offset = K.tercile_mapping(zs)
    q1, q2 = statistics.quantiles(zs, n=3)
    assert K._map_score(q1, slope, offset) == K.RED and K._map_score(q2, slope, offset) == K.GREEN


def test_scale_check_needs_all_three_colours():
    assert not K.scale_check(_rows([1.5] * 90))["ok"]
    balanced = [((i * 37) % 101 - 50) / 40 for i in range(120)]
    assert K.scale_check(_rows(balanced))["ok"]


def test_a_mean_drift_fails_the_scale_check():
    balanced = [((i * 37) % 101 - 50) / 40 + 0.5 for i in range(120)]
    assert not K.scale_check(_rows(balanced))["ok"]


def test_rolling_mapping_never_looks_ahead():
    zs = [((i * 37) % 101 - 50) / 40 for i in range(120)]
    a = K.rolling_scores(_rows(zs))
    changed = zs[:100] + [3.0] * 20
    b = K.rolling_scores(_rows(changed))
    assert [r["recovery"] for r in a[:100]] == [r["recovery"] for r in b[:100]]


def test_logistic_keeps_the_fixed_mapping_when_the_scale_is_fine():
    p = KS.make_person(200, 2)
    series = K.rmssd_series(p, "x")
    res = K.decide_logistic(series, K.replay(p, series))
    assert res["verdict"] == "keep"
    assert set(res["flip"]) == {f"slope x{f}" for f in C.SLOPE_FACTORS} and 0 <= res["flip"]["slope x1.0"]["flip"] <= 1


def test_noise_flip_rate_is_zero_when_readings_agree():
    day = date(2030, 1, 1)
    series = K.Series("x", {day: 0.0}, {day: [0.0, 0.0, 0.0]})
    rows = [{"date": day, "recovery": 60, "z": {"hrv": 0.0}, "hrv_base": (0.0, 0.5)}]
    assert K.flip_rate(series, rows) == {"nights": 1, "flip": 0.0}


def test_noise_flip_rate_counts_a_night_decided_by_one_reading():
    day = date(2030, 1, 1)
    series = K.Series("x", {day: 0.5}, {day: [1.5, -1.5]})   # the median is 0; either reading alone changes the colour
    rows = [{"date": day, "recovery": 57, "z": {"hrv": 0.0}, "hrv_base": (0.0, 0.5)}]
    assert K.flip_rate(series, rows) == {"nights": 1, "flip": 1.0}


# ---------------------------------------------------------------- D5
def test_pc1_weights_are_non_negative_and_sum_to_one():
    rows = []
    for i in range(80):
        s = math.sin(i / 3)
        rows.append({"z": {"hrv": s + 0.1 * math.cos(i), "rhr": 0.5 * s, "sleep_performance": 0.2 * math.cos(i * 2), "resp_rate": 0.1 * s}})
    w = K.pc1_weights(rows)
    assert all(v >= 0 for v in w.values()) and sum(w.values()) == pytest.approx(1, abs=0.01) and w["hrv"] == max(w.values())


def test_equal_weights_are_only_a_bracket():
    p = KS.make_person(200, 2)
    series = K.rmssd_series(p, "x")
    res = K.decide_weights(p, K.replay(p, series), n_boot=50)
    assert "equal" in res["candidates"] and "pc1_development" in res["candidates"]
    assert res["verdict"] in ("keep", "eligible")
    if res["verdict"] == "eligible":
        assert res["candidates"]["pc1_development"]["eligible"]


# ---------------------------------------------------------------- D6, D7, D8
def test_k_comes_from_the_reference_session_when_it_fails_the_scale_checks():
    p = KS.make_person(120, 2)
    series = K.rmssd_series(p, "x")
    rows = K.replay(p, series)
    ok = K.decide_k(p, series, rows)
    assert ok["checks_ok"] and ok["verdict"].startswith("keep")
    assert ok["reference_k"] == pytest.approx(180 / math.log(3), abs=0.1)
    assert ok["reference_session_strain_now"] == pytest.approx(21 * (1 - math.exp(-180 / P.STRAIN_K)), abs=0.01)
    p.train_days = {d: (1000.0, 21.0) for d in p.train_days}   # every day at the top of the scale
    assert K.decide_k(p, series, rows)["verdict"].startswith("eligible")


def test_sleep_need_is_report_only():
    res = K.report_sleep_need(KS.make_person(120, 2))
    assert res["verdict"] == "report only" and res["current_baseline_min"] == P.SLEEP_NEED_BASELINE_MIN


def test_mixed_sources_shrug_at_normal_hrv_but_catch_low_hrv():
    normal = KS.make_person(120, 2, app_from=0.0, rmssd_level=60.0)
    low = KS.make_person(120, 2, app_from=0.0, rmssd_level=8.0)
    for p, want in ((normal, True), (low, False)):
        exact = K.rmssd_series(p, "x")
        res = K.mixed_sources(p, exact, K.replay(p, exact))
        assert res["ok"] is want, res
        assert res["mean_shift"] > 0   # rounding only ever pushes RMSSD up


def test_app_era_holds_only_nights_with_a_newest_source_reading():
    p = KS.make_person(100, 2, app_from=0.7)
    era = K.app_era(p)
    assert min(era) >= KS.START + timedelta(days=70) and len(era) == 30


# ---------------------------------------------------------------- end to end, files and report
def _write_export(tmp_path, p):
    """A Person written the way the exported files look, to exercise load_dir."""
    (tmp_path / "beats.csv").write_text("start_at,end_at,source,origin,beats_ms,gaps,apple_sdnn,rmssd,n_beats,n_valid\n"
                                        + "".join(f'{r.start.isoformat()},{r.start.isoformat()},w,{r.origin},"{r.beats_ms}",,{r.apple_sdnn},{r.rmssd or ""},{len(r.beats_ms)},{len(r.beats_ms)}\n'
                                                  for r in p.readings))
    (tmp_path / "sdnn.csv").write_text("src,ts,value\n" + "".join(f"ow,{t.isoformat()},{v}\n" for t, v in p.sdnn))
    (tmp_path / "nights.csv").write_text("wake_date,start_at,end_at,asleep_min,core_min,deep_min,rem_min,device\n"
                                         + "".join(f"{d},{x.night_start.isoformat()},{x.night_end.isoformat()},{x.actual_min},100,50,50,Watch\n" for d, x in p.days.items()))
    (tmp_path / "scores.csv").write_text("date,sleep_performance,sleep_actual_min,sleep_debt_min,hrv_kind,hrv_night_ln,rhr_night,resp_rate_night,recovery,trimp,strain,rhr_from_daily,illness\n"
                                         + "".join(f"{d},{x.perf},{x.actual_min},{x.debt_min},rmssd,{'' if x.stored_hrv_ln is None else x.stored_hrv_ln},{x.rhr},{x.resp},{'' if x.stored_recovery is None else x.stored_recovery},{x.trimp},{x.strain},f,f\n"
                                                   for d, x in p.days.items()))
    (tmp_path / "daily_rhr.csv").write_text("date,value\n" + "".join(f"{d},{v}\n" for d, v in p.daily_rhr.items()))
    (tmp_path / "hrr.csv").write_text("local_date,hrr60\n" + "".join(f"{d},{v}\n" for d, v in p.hrr.items()))


def test_run_end_to_end_from_files(tmp_path, capsys):
    p = KS.make_person(150, 2)
    _write_export(tmp_path, p)
    loaded = K.load_dir(tmp_path)
    assert len(loaded.days) == 150 and len(loaded.readings) == len(p.readings) and loaded.stored_kind == "rmssd"
    main(["calibration", "run", "--data", str(tmp_path), "--bootstrap", "20", "--json", str(tmp_path / "out.json")])
    out = capsys.readouterr().out
    assert "Rule A**: ok" in out and "D2 RMSSD vs SDNN" in out and "D8 mixed sources" in out
    assert (tmp_path / "out.json").exists()


def test_rules_report_on_synthetic_persons(capsys):
    main(["calibration", "rules", "--seeds", "1", "--days", "100", "--bootstrap", "10"])
    assert "| D3, no artifacts |" in capsys.readouterr().out
