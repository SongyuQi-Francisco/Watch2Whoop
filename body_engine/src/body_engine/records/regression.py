"""Journal analysis, second version (docs/scoring-validation.md section 2.2): OLS with Newey-West (HAC) standard errors and
Benjamini-Hochberg across behaviors. Standard library only (a model has about 10 coefficients and at most 730 days, so
plain Python is fast enough and adds no dependency).

- ols_hac(rows): y = b0 + b1·x1 + … by ordinary least squares; the covariance of b is the Newey-West sandwich
  (X'X)⁻¹ S (X'X)⁻¹ with S = Σ_i Σ_j w(|t_i − t_j|) e_i e_j x_i x_j', Bartlett weights w(l) = 1 − l / (L + 1) for
  l ≤ L calendar days (Newey & West, Econometrica 1987, doi:10.2307/1913610), times n / (n − k) as the small-sample
  correction. Lags are measured in days, not rows, so a gap in the journal does not pair days a week apart as neighbours.
- Inference is the normal approximation (two-sided p from z = b / se), as usual for HAC standard errors.
- bh(pvalues): Benjamini-Hochberg adjusted p-values (q); a test is significant when q <= the FDR level.
"""
import math
from datetime import date

from . import analysis_params as AP


class Singular(ValueError):
    """The design matrix is collinear (e.g. a behavior always logged on the same weekday)."""


def _inverse(a: list[list[float]], tol: float) -> list[list[float]]:
    """Gauss-Jordan inverse with partial pivoting of a symmetric positive (semi)definite matrix; raises Singular."""
    n = len(a)
    scale = max(abs(a[i][i]) for i in range(n)) or 1.0
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) <= tol * scale:
            raise Singular(c)
        m[c], m[p] = m[p], m[c]
        piv = m[c][c]
        m[c] = [v / piv for v in m[c]]
        for r in range(n):
            if r != c and m[r][c] != 0.0:
                f = m[r][c]
                m[r] = [v - f * w for v, w in zip(m[r], m[c])]
    return [row[n:] for row in m]


def _norm_p(z: float) -> float:
    """Two-sided p-value of a standard normal z."""
    return math.erfc(abs(z) / math.sqrt(2))


def ols_hac(days: list[date], y: list[float], x: list[list[float]], names: list[str],
            lag_days: int = AP.HAC_LAG_DAYS, tol: float = AP.SINGULAR_TOL) -> dict:
    """OLS of y on x (each row of x has len(names) regressors; an intercept is added) with Newey-West standard errors.

    days: the date of each observation (for the lag weights; sorted or not). Returns {"n", "k", "coef": {name: {...}}}
    where each coefficient has estimate, se, z, p, ci95; "intercept" is included. Raises Singular for a collinear design."""
    n = len(y)
    names = ["intercept", *names]
    xs = [[1.0, *row] for row in x]
    k = len(names)
    xtx = [[sum(r[i] * r[j] for r in xs) for j in range(k)] for i in range(k)]
    inv = _inverse(xtx, tol)
    xty = [sum(r[i] * v for r, v in zip(xs, y)) for i in range(k)]
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    resid = [v - sum(b * c for b, c in zip(beta, r)) for r, v in zip(xs, y)]

    # S = Σ_i Σ_j w(|t_i - t_j|) u_i u_j' with u_i = x_i e_i (only pairs within lag_days have weight)
    order = sorted(range(n), key=lambda i: days[i])
    ords = [days[i].toordinal() for i in order]
    u = [[c * resid[i] for c in xs[i]] for i in order]
    s = [[0.0] * k for _ in range(k)]
    for a in range(n):
        for b in range(a, n):
            lag = ords[b] - ords[a]
            if lag > lag_days:
                break
            w = 1.0 if lag == 0 and a == b else 1.0 - lag / (lag_days + 1)
            if w <= 0:
                continue
            ua, ub = u[a], u[b]
            for i in range(k):
                for j in range(k):
                    v = ua[i] * ub[j]
                    s[i][j] += w * (v if a == b else v + ub[i] * ua[j])
    corr = n / (n - k) if n > k else float("inf")
    tmp = [[sum(inv[i][t] * s[t][j] for t in range(k)) for j in range(k)] for i in range(k)]
    cov = [[corr * sum(tmp[i][t] * inv[t][j] for t in range(k)) for j in range(k)] for i in range(k)]

    coef = {}
    for i, name in enumerate(names):
        se = math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0
        z = beta[i] / se if se > 0 else None
        coef[name] = {"estimate": beta[i], "se": se, "z": z, "p": _norm_p(z) if z is not None else None,
                      "ci95": [beta[i] - 1.96 * se, beta[i] + 1.96 * se] if se > 0 else None}
    return {"n": n, "k": k, "coef": coef}


def bh(pvalues: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values, in the order given: q_(i) = min_{j >= i} p_(j) · m / j, capped at 1."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        q[i] = running
    return q
