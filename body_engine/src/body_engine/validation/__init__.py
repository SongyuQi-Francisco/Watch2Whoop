"""Scoring validation study (issue #118, docs/scoring-validation.md): stability, construct validity, sensitivity analysis
and HRV-kind agreement. Pure functions over scores.engine.score_days; the synthetic person lives in synthetic.py.

    uv run python -m body_engine.validation synthetic [--days 180] [--seed 7]
    uv run python -m body_engine.validation user --user-id N [--kinds sdnn,rmssd] [--no-ow]
    uv run python -m body_engine.validation healthspan --user-id N [--json]   (body age v1.1 vs v1.2, healthspan_compare.py)

The user mode reads the database only and prints aggregate statistics (no dates, no daily values); keep its output out of git anyway.
"""
