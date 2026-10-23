"""Validation study helpers (body_engine.validation, issue #118): synthetic person, perturbation, metrics. No database."""
from datetime import date

import pytest

from body_engine.scores import params as P
from body_engine.scores import strain as L
from body_engine.validation import study, synthetic


@pytest.fixture(scope="module")
def person():
    return synthetic.make_person(days=60, seed=3)


@pytest.fixture(scope="module")
def rows(person):
    return study.score(person.inputs, person.first, person.last)


def test_synthetic_person_is_deterministic(person):
    again = synthetic.make_person(days=60, seed=3)
    assert again.inputs.hrv == person.inputs.hrv and again.readiness == person.readiness
    assert len(person.inputs.nights) == 60 and person.first == synthetic.START
    assert all(1 <= sum(n.start <= t <= n.end for t, _ in person.inputs.hrv) <= 3 for n in person.inputs.nights)


def test_scores_track_the_latent_state(person, rows):
    v = study.validity(rows, person.readiness)
    assert v["recovery_vs_truth"] > 0.5 and v["rhr_vs_truth"] < -0.5
    assert v["hrv_vs_rhr_same_night"] < 0
    assert sum(r["recovery"] is not None for r in rows) == 60 - P.MIN_BASELINE_NIGHTS


def test_patched_restores_params_and_captured_defaults():
    k, defaults = P.STRAIN_K, L.strain.__defaults__
    with study.patched({"STRAIN_K": k * 2}):
        assert P.STRAIN_K == k * 2
        assert L.strain(100.0) == pytest.approx(P.STRAIN_MAX * (1 - 2.718281828 ** (-100 / (k * 2))), rel=1e-6)
    assert P.STRAIN_K == k and L.strain.__defaults__ == defaults


def test_sleep_weights_stay_normalised():
    new = study._new_values("sleep:DURATION_IMPACT", 1.2)
    assert sum(new.values()) == pytest.approx(1.0) and new["DURATION_IMPACT"] == pytest.approx(0.48)


def test_sensitivity_zero_change_and_direction(person, rows):
    k0 = P.STRAIN_K
    zero = study.sensitivity(person.inputs, person.first, person.last, [("STRAIN_K", "scalar", 0.0)], base=rows)
    assert all(r["recovery"] == 0 and r["strain"] == 0 and r["zone_change"] == 0 for r in zero)
    k = study.sensitivity(person.inputs, person.first, person.last, [("STRAIN_K", "scalar", 0.2)], base=rows)
    assert [r["change"] for r in k] == ["-20%", "+20%"] and all(r["strain"] > 0 for r in k)
    assert P.STRAIN_K == k0


def test_agreement_identical_and_shifted(rows):
    same = study.agreement(rows, rows)
    assert same["corr"] == 1.0 and same["zone_agree"] == 1.0 and same["loa"] == (0.0, 0.0)
    shifted = [{**r, "recovery": None if r["recovery"] is None else min(100, r["recovery"] + 10)} for r in rows]
    a = study.agreement(rows, shifted)
    assert a["both"] == same["both"] and 0 < a["mean_diff"] <= 10


def test_markdown_has_no_dates(person, rows):
    text = study.markdown("t", rows, person.readiness)
    assert "Stability" in text and str(date(2026, 1, 5)) not in text


# ---------------------------------------------------------------- traits, regression, beats, population (#118 next step)
def test_default_traits_give_the_documented_person(person):
    t = synthetic.Traits()
    assert (t.hrv_ms, t.hrv_per_r, t.rhr_bpm, t.load_effect, t.readings) == \
        (synthetic.HRV_MS, synthetic.HRV_PER_R, synthetic.RHR_BPM, synthetic.LOAD_EFFECT, synthetic.READINGS)
    again = synthetic.make_person(days=60, seed=3, traits=t)
    assert again.inputs.hrv == person.inputs.hrv and again.inputs.hr == person.inputs.hr and again.readiness == person.readiness


def test_traits_change_the_person(person):
    calm = synthetic.make_person(days=60, seed=3, traits=synthetic.Traits(load_effect=0.0, illness=False))
    assert calm.readiness != person.readiness and not calm.illness
    many = synthetic.make_person(days=20, seed=3, traits=synthetic.Traits(readings=(4,)))
    assert len(many.inputs.hrv) == 4 * 20


def test_load_regression_shapes(rows):
    fit = study.load_regression(rows)
    assert set(fit) == set(study.LOAD_OUTCOMES)
    rec = fit["recovery"]
    assert rec["n"] > 30 and {"estimate", "ci95", "p", "q"} <= set(rec["strain"]) and rec["strain"]["q"] >= rec["strain"]["p"]
    short = study.load_regression(rows[:20])
    assert all(v.get("insufficient") for v in short.values())
    wd = study.load_regression(rows, weekday=True)
    assert "strain" in wd["recovery"] or wd["recovery"].get("collinear")


def test_beats_filter_recovers_the_clean_truth():
    import random

    from body_engine.rawdata import hrv as H
    from body_engine.validation import beats

    rng = random.Random(5)
    clean = beats.clean_series(rng, 900.0, 30.0, 20.0, 12.0)
    t_rmssd, t_sdnn = beats.truth(clean)
    res = H.analyze(clean)
    assert res.rmssd == pytest.approx(t_rmssd, abs=0.01) and res.sdnn == pytest.approx(t_sdnn, abs=0.01)
    assert beats.corrupt(rng, clean, 0.0) == clean
    bad = beats.corrupt(random.Random(6), clean, 0.05, kinds=("missed", "extra"))
    assert bad != clean
    assert abs(H.analyze(bad).rmssd - t_rmssd) / t_rmssd < 0.1          # gross artifacts are filtered
    off = H.analyze(bad, params=H.override(MAX_DEVIATION=10.0))
    assert abs(off.rmssd - t_rmssd) / t_rmssd > 0.5                      # ...and would wreck RMSSD without the filter
    assert beats.quantise([0.123, 0.987]) == pytest.approx([0.12, 0.99])
    rows = beats.run(n=10, levels=beats.LEVELS[1:2], rates=(0.0,), filters=beats.FILTERS[:1])
    assert rows[0]["rmssd_err_median"] == 0.0 and rows[0]["rejected"] == 0.0


def test_population_runs_and_summarises():
    from body_engine.validation import population

    people = population.run(n=5, days=60, seed=2)
    assert len(people) == 5 and all(0 <= p["green"] <= 1 and p["k_if_calibrated"] for p in people)
    s = population.summary(people)
    assert set(s["spearman"]["green"]) == set(population.TRAITS) and len(s["spread"]["recovery_mean"]) == 5
    assert population._spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == 1.0


def test_cli_modes_smoke(capsys):
    from body_engine.validation.__main__ import main

    main(["beats", "--n", "5"])
    main(["population", "--n", "3", "--days", "60"])
    main(["regression", "--seeds", "1", "--days", "60"])
    text = capsys.readouterr().out
    assert "artifact filter" in text and "Synthetic population" in text and "regression version" in text
