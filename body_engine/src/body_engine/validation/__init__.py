"""Scoring validation study (issue #118, docs/scoring-validation.md): stability, construct validity, sensitivity analysis
and HRV-kind agreement. Pure functions over scores.engine.score_days; the synthetic person lives in synthetic.py, synthetic
beat series in beats.py, a synthetic population in population.py.

    uv run python -m body_engine.validation synthetic [--days 180] [--seed 7] [--noise-sweep]
    uv run python -m body_engine.validation regression [--seeds 50]   (construct validity, OLS + Newey-West + BH, synthetic)
    uv run python -m body_engine.validation beats                     (HRV artifact filter on synthetic beat series, beats.py)
    uv run python -m body_engine.validation population [--n 40]       (synthetic population: zones and strain K, population.py)
    uv run python -m body_engine.validation user --user-id N [--kinds sdnn,rmssd] [--no-ow]
    uv run python -m body_engine.validation healthspan --user-id N [--json]   (body age v1.1 vs v1.2, healthspan_compare.py)

The user mode reads the database only and prints aggregate statistics (no dates, no daily values); keep its output out of git anyway.
"""
