"""Journal analysis, second version (records/regression.py, records/analysis.py regression=True; issue #118 item 4).

Synthetic data with known effects (no real person's values): a made-up person whose next-day recovery depends on the
previous day's strain, the night's sleep, the weekend and one behavior, with autocorrelated noise. The behavior is done
more often on hard training days, so the two-group means (first version) are confounded and the regression is not.

Pure tests need nothing; the database part uses its own database body_test_journal_regression
(BODY_TEST_DB_NAME_JOURNAL_REGRESSION), recreated on every run, and is skipped without Postgres."""
import math
import os
import random
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import analysis, analysis_params as AP, regression as RG

START = date(2026, 1, 5)   # a Monday


def person(n: int, effect: float, seed: int, rho: float = 0.5, noise: float = 6.0, confound: bool = True) -> list[dict]:
    """n behavior days D (example data). recovery(D+1) = 60 + effect·behavior(D) − 1.5·(strain(D) − 10)
    + 4·(sleep(D→D+1) − 7.2) + 3 on weekend behavior days + AR(1) noise. With confound, the behavior happens on 75% of
    days with strain > 10 and 25% of the others, so its raw group difference also carries the strain effect."""
    rng = random.Random(seed)
    e, out = 0.0, []
    for i in range(n):
        d = START + timedelta(days=i)
        strain = rng.uniform(4, 16)
        p = (0.75 if strain > 10 else 0.25) if confound else 0.5
        b = 1.0 if rng.random() < p else 0.0
        sleep = rng.gauss(7.2, 0.7)
        e = rho * e + rng.gauss(0, noise)
        y = 60 + effect * b - 1.5 * (strain - 10) + 4 * (sleep - 7.2) + (3 if d.weekday() >= 5 else 0) + e
        out.append({"date": d, "behavior": b, "y": y, "strain": strain, "sleep_h": sleep})
    return out


def naive_diff(obs):
    yes = [o["y"] for o in obs if o["behavior"]]
    no = [o["y"] for o in obs if not o["behavior"]]
    return sum(yes) / len(yes) - sum(no) / len(no)


# ---------------------------------------------------------------- pure functions
def test_ols_recovers_exact_coefficients_without_noise():
    days = [START + timedelta(days=i) for i in range(40)]
    x = [[i % 2, (i * 7) % 11, math.sin(i)] for i in range(40)]
    y = [2.0 + 3.0 * a - 0.5 * b + 1.25 * c for a, b, c in x]
    fit = RG.ols_hac(days, y, x, ["a", "b", "c"])
    for name, want in (("intercept", 2.0), ("a", 3.0), ("b", -0.5), ("c", 1.25)):
        assert fit["coef"][name]["estimate"] == pytest.approx(want, abs=1e-9)
    assert fit["n"] == 40 and fit["k"] == 4


def test_hac_with_lag_zero_is_white_hc1():
    """lag 0 = White's heteroskedasticity-robust covariance × n / (n − k) (HC1); checked against the closed form for
    a regression on one dummy: var(b) = n/(n−k) · (Σ_yes e² / n_yes² + Σ_no e² / n_no²)."""
    obs = person(60, -5.0, seed=3, confound=False)
    days, y = [o["date"] for o in obs], [o["y"] for o in obs]
    b = [o["behavior"] for o in obs]
    fit = RG.ols_hac(days, y, [[v] for v in b], ["behavior"], lag_days=0)
    yes = [v for v, t in zip(y, b) if t]
    no = [v for v, t in zip(y, b) if not t]
    my, mn = sum(yes) / len(yes), sum(no) / len(no)
    hc = (sum((v - my) ** 2 for v in yes) / len(yes) ** 2 + sum((v - mn) ** 2 for v in no) / len(no) ** 2) * 60 / 58
    assert fit["coef"]["behavior"]["estimate"] == pytest.approx(my - mn)
    assert fit["coef"]["behavior"]["se"] == pytest.approx(math.sqrt(hc))


def test_hac_widens_the_interval_under_autocorrelation():
    """Positively autocorrelated errors and a slowly changing regressor: Newey-West SE > the lag-0 SE."""
    rng = random.Random(11)
    days = [START + timedelta(days=i) for i in range(200)]
    x = [[1.0 if (i // 10) % 2 else 0.0] for i in range(200)]   # 10-day blocks
    e, y = 0.0, []
    for row in x:
        e = 0.8 * e + rng.gauss(0, 1)
        y.append(row[0] + e)
    se7 = RG.ols_hac(days, y, x, ["b"])["coef"]["b"]["se"]
    se0 = RG.ols_hac(days, y, x, ["b"], lag_days=0)["coef"]["b"]["se"]
    assert se7 > 1.5 * se0


def test_lags_are_calendar_days():
    """Dropping every other day: neighbours in the list are 2 days apart, so lag 1 has nothing to pair."""
    obs = person(80, -5.0, seed=5)[::2]
    days, y, x = [o["date"] for o in obs], [o["y"] for o in obs], [[o["behavior"]] for o in obs]
    lag1 = RG.ols_hac(days, y, x, ["b"], lag_days=1)["coef"]["b"]["se"]
    lag0 = RG.ols_hac(days, y, x, ["b"], lag_days=0)["coef"]["b"]["se"]
    assert lag1 == pytest.approx(lag0)


def test_singular_design():
    days = [START + timedelta(days=i) for i in range(30)]
    x = [[i % 2, 2 * (i % 2)] for i in range(30)]
    with pytest.raises(RG.Singular):
        RG.ols_hac(days, [float(i) for i in range(30)], x, ["a", "b"])


def test_bh_matches_hand_computed():
    # p sorted: 0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205 (m = 8); q = min over j >= i of p_j · 8 / j
    p = [0.041, 0.001, 0.205, 0.008, 0.06, 0.039, 0.074, 0.042]
    q = RG.bh(p)
    want = {0.001: 0.008, 0.008: 0.032, 0.039: 0.0672, 0.041: 0.0672, 0.042: 0.0672, 0.06: 0.08, 0.074: 0.0846,
            0.205: 0.205}
    assert [round(v, 4) for v in q] == [round(want[v], 4) for v in p]
    assert RG.bh([]) == [] and RG.bh([0.3]) == [0.3]


def test_regression_removes_confounding():
    """Known effect −6 on recovery: the raw group difference is pulled up by strain (hard days → behavior, and hard
    days lower next-day recovery); the adjusted estimate is close to −6 and its interval covers it."""
    obs = person(180, -6.0, seed=7)
    raw = naive_diff(obs)
    r = analysis.regress(obs, higher_is_better=True)
    assert r["status"] == "ok" and r["n"] == 180
    assert raw < -7.5                       # confounded: strain's effect adds to the behavior's
    assert r["estimate"] == pytest.approx(-6.0, abs=1.5)
    assert r["ci95"][0] < -6.0 < r["ci95"][1]
    assert r["covariates"]["prev_strain"] == pytest.approx(-1.5, abs=0.3)
    assert r["covariates"]["sleep_hours"] == pytest.approx(4.0, abs=1.5)
    assert r["direction"] == "worse" and r["significant"] and r["p_adj"] == r["p"] and r["bh_tests"] == 1
    assert r["reliable"] and r["controls"] == ["prev_strain", "sleep_hours", "weekday"]
    assert "_higher_is_better" in r          # internal, removed by analyze() / analyze_all()


def test_null_behavior_is_unclear():
    r = analysis.regress(person(180, 0.0, seed=8), higher_is_better=True)
    assert r["status"] == "ok" and r["direction"] == "unclear" and not r["significant"]
    assert r["ci95"][0] < 0 < r["ci95"][1]


def test_false_positive_rate_under_the_null():
    """100 made-up people with no behavior effect, 90 days each: the share flagged at p ≤ 0.05 stays near 5%
    (HAC is asymptotic, so a few points above is expected at n = 90; the bound only catches a broken SE)."""
    hits = sum(analysis.regress(person(90, 0.0, seed=100 + s), True)["p"] <= 0.05 for s in range(100))
    assert hits <= 15


def test_insufficient_and_collinear():
    short = analysis.regress(person(25, -6.0, seed=9), True)
    assert short["status"] == "insufficient" and "自由度" in short["message"]
    few_yes = [dict(o, behavior=1.0 if i < 3 else 0.0) for i, o in enumerate(person(90, 0.0, seed=10))]
    assert analysis.regress(few_yes, True)["status"] == "insufficient"
    mondays = [dict(o, behavior=1.0 if o["date"].weekday() == 0 else 0.0) for o in person(90, 0.0, seed=12)]
    # the Monday dummy is the reference, so "behavior = Monday" equals intercept − all six weekday dummies
    assert analysis.regress(mondays, True)["status"] == "collinear"


def test_bh_across_behaviors():
    """Three behaviors for one outcome: a real effect stays significant, nulls stay unclear, q >= p."""
    results = []
    for key, eff, seed in (("real", -6.0, 21), ("null1", 0.0, 22), ("null2", 0.0, 23)):
        reg = analysis.regress(person(180, eff, seed), True)
        results.append({"item": {"key": key}, "outcomes": {"next_recovery": {"regression": reg}}})
    analysis.apply_bh(results)
    regs = {a["item"]["key"]: a["outcomes"]["next_recovery"]["regression"] for a in results}
    assert all(r["bh_tests"] == 3 and r["p_adj"] >= r["p"] - 1e-4 for r in regs.values())
    assert regs["real"]["significant"] and regs["real"]["direction"] == "worse"
    assert not regs["null1"]["significant"] and not regs["null2"]["significant"]


# ---------------------------------------------------------------- through the database and the API
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_JOURNAL_REGRESSION", "body_test_journal_regression")


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def world(dsn):
    """A user with 120 days of journal + scores: 'sauna' has effect −6, 'tea' none; 'alcohol' is logged only 3 times."""
    obs = {"sauna": person(120, -6.0, seed=31), "tea": person(120, 0.0, seed=32)}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("INSERT INTO users (display_name) VALUES ('example') RETURNING id").fetchone()["id"]
        token = auth.create_token(conn, uid, "test", ["read"])
        conn.execute("""INSERT INTO journal_items (user_id, key, label_zh, kind) VALUES
                        (%s, 'sauna', '桑拿', 'bool'), (%s, 'tea', '喝茶', 'bool'), (%s, 'alcohol', '喝酒', 'bool')""",
                     (uid, uid, uid))
        # one daily_scores row per date: the strain of that day (as a behavior day D) and the recovery and sleep of
        # that morning (as the outcome day D+1), all from the 'sauna' person; 'tea' only adds its behavior column, so
        # its true effect is 0
        rows: dict[date, dict] = {}
        for o in obs["sauna"]:
            rows.setdefault(o["date"], {})["strain"] = round(o["strain"], 2)
            rows.setdefault(o["date"] + timedelta(days=1), {}).update(recovery=round(o["y"]),
                                                                      sleep=round(o["sleep_h"] * 60))
            conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, 'sauna', %s)",
                         (uid, o["date"], o["behavior"]))
        for d, r in rows.items():
            conn.execute("""INSERT INTO daily_scores (user_id, date, strain, recovery, sleep_actual_min, algo_version)
                            VALUES (%s, %s, %s, %s, %s, 'test')""",
                         (uid, d, r.get("strain"), r.get("recovery"), r.get("sleep")))
        for o in obs["tea"]:
            conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, 'tea', %s)",
                         (uid, o["date"], o["behavior"]))
        for i in range(3):
            conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, 'alcohol', 1)",
                         (uid, START + timedelta(days=i)))
    return uid, token, START + timedelta(days=119)


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def test_first_version_unchanged(dsn, world):
    """regression=True only adds fields: removing them gives exactly the first version's result."""
    uid, _, end = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        v1 = analysis.analyze(conn, uid, "sauna", end=end, days=120)
        v2 = analysis.analyze(conn, uid, "sauna", end=end, days=120, regression=True)
        all1 = analysis.analyze_all(conn, uid, end=end, days=120)
        all2 = analysis.analyze_all(conn, uid, end=end, days=120, regression=True)
    assert "regression_method" not in v1 and all("regression" not in o for o in v1["outcomes"].values())
    strip = lambda a: {**{k: v for k, v in a.items() if k != "regression_method"},
                       "outcomes": {n: {k: v for k, v in o.items() if k != "regression"} for n, o in a["outcomes"].items()}}
    assert strip(v2) == v1 and [strip(a) for a in all2] == all1


def test_regression_through_the_database(dsn, world):
    uid, _, end = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        res = {a["item"]["key"]: a for a in analysis.analyze_all(conn, uid, end=end, days=120, regression=True)}
    sauna = res["sauna"]["outcomes"]["next_recovery"]["regression"]
    # every behavior day has its next-day row (the last outcome is on end + 1, which is stored too)
    assert sauna["status"] == "ok" and sauna["n"] == 120
    assert sauna["estimate"] == pytest.approx(-6.0, abs=2.0) and sauna["direction"] == "worse"
    assert sauna["bh_tests"] == 2 and "_higher_is_better" not in sauna            # sauna and tea; alcohol has too few
    assert res["tea"]["outcomes"]["next_recovery"]["regression"]["direction"] == "unclear"
    assert res["alcohol"]["outcomes"]["next_recovery"]["regression"]["status"] == "insufficient"
    # no resting heart rate / HRV rows: those outcomes have no observations at all
    assert res["sauna"]["outcomes"]["next_rhr"]["regression"]["status"] == "insufficient"
    assert "Newey-West" in res["sauna"]["regression_method"]


def test_api_parameter(client, world):
    uid, token, end = world
    hdr = {"Authorization": f"Bearer {token}"}
    plain = client.get(f"/v1/users/me/journal/analysis?key=sauna&end={end}&days=120", headers=hdr).json()
    assert "regression" not in plain["outcomes"]["next_recovery"]
    one = client.get(f"/v1/users/me/journal/analysis?key=sauna&end={end}&days=120&regression=true", headers=hdr).json()
    assert one["outcomes"]["next_recovery"]["regression"]["status"] == "ok"
    every = client.get(f"/v1/users/me/journal/analysis?end={end}&days=120&regression=true", headers=hdr).json()
    assert {a["item"]["key"]: a["outcomes"]["next_recovery"]["regression"]["bh_tests"] for a in every
            if a["outcomes"]["next_recovery"]["regression"]["status"] == "ok"} == {"sauna": 2, "tea": 2}
