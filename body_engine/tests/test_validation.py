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
