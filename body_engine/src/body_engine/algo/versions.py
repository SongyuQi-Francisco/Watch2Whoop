"""Algorithm versions: each key of the JS bundle's `OBS.ALGO_VERSIONS` and the Python constant that defines it
(docs/ios-native-2.0.md sections 2.2 and 3.3).

The Python is the reference; the bundle carries the same string. `tests/test_algo_bundle.py` compares every key the bundle
has with the constant named here, so a Python change without a rebuilt bundle (or the other way round) fails.

Planned keys (the 2.0.0 packages) are listed with the constant their package will add, in a file that package owns. Until
that constant exists the key has no server version (`current()` is None): the server then treats the package as not
implemented, `GET /v1/sync/compute` reports it `off` and uploads of its kinds are refused. A package adds its constant and
its `src/3N_<pkg>.js` in the same PR and never edits this file unless it moves the constant.
"""
from __future__ import annotations

import importlib

# key -> "module:CONSTANT"
VERSIONS: dict[str, str] = {
    # shipped (N1 / N2b / N4)
    "scores": "openbodyscore.params:ALGO_VERSION",
    "healthspan": "openbodyscore.healthspan_params_v12:ALGO_VERSION",
    "hrv": "openbodyscore.hrv:ALGO",
    "stress": "openbodyscore.stress:STRESS_VERSION",
    "trends": "body_engine.trends.params:TREND_VERSION",
    # planned (2.0.0 packages, docs/ios-native-2.0.md section 4)
    "derive": "body_engine.rawdata.derive:DERIVE_VERSION",              # M1, src/30_derive.js
    "workouts": "body_engine.training.importer:WORKOUTS_VERSION",       # M1c, src/31_workouts.js
    "stress_cards": "body_engine.insights.stress:STRESS_CARDS_VERSION",  # M2, src/32_stress.js
    "series": "body_engine.trends.series:SERIES_VERSION",               # M3, src/33_series.js
    "coach": "body_engine.insights.coach:COACH_VERSION",                # M4, src/34_coach.js
    "reports": "body_engine.insights.reports:REPORTS_VERSION",          # M5, src/35_reports.js
    "rules": "body_engine.rules.params:RULES_VERSION",                  # M6, src/36_rules.js
    "explain": "body_engine.explain.personal:EXPLAIN_VERSION",          # M6, src/37_explain.js
}


def path(key: str) -> str:
    """The "module:CONSTANT" of a key (KeyError for an unknown key)."""
    return VERSIONS[key]


def current(key: str) -> str | None:
    """The server's version string of one algorithm, or None while its package has not added the constant yet."""
    mod, _, const = VERSIONS[key].partition(":")
    try:
        m = importlib.import_module(mod)
    except ImportError:
        return None
    v = getattr(m, const, None)
    return v if isinstance(v, str) and v else None


def server_versions() -> dict[str, str]:
    """{key: version} of every key whose constant exists (planned keys without one are left out)."""
    return {k: v for k in VERSIONS if (v := current(k)) is not None}
