"""Parameters of the journal analysis (records/analysis.py), with sources.

First version (two-group means): MIN_N / RELIABLE_N, from docs/system-design.md section 7 (Journal): "show only with >= 5
yes and >= 5 no days in 90 days, trustworthy from 15-20 each". Design choices, not validated.

Second version (regression): the method was written down before looking at any result, in docs/scoring-validation.md
section 2.2 and docs/system-design.md section 7: OLS of the next-day outcome on the behavior, controlling for the
previous day's strain, the sleep duration of the night before the outcome and the weekday; Newey-West (HAC) standard
errors with a 7-day lag; Benjamini-Hochberg at a 5% false discovery rate when several behaviors are tested together.
"""

# ---------- both versions ----------
MIN_N = 5          # each group (yes / no) needs at least this many days for a result (system design section 7)
RELIABLE_N = 15    # each group needs at least this many days to be marked reliable (system design section 7)

# ---------- second version: OLS + Newey-West + Benjamini-Hochberg ----------
# Newey-West lag in days (scoring-validation 2.2: "HAC, lag 7 days" = one week, the longest regular cycle in the data:
# weekly training plans and weekday routines). Lags are calendar days, so gaps in the journal are respected.
HAC_LAG_DAYS = 7
# Benjamini-Hochberg false discovery rate (scoring-validation 2.2: "FDR 5%"); Benjamini & Hochberg, J R Stat Soc B 1995,
# doi:10.1111/j.2517-6161.1995.tb02031.x. The family is all behaviors tested together for one outcome.
FDR_Q = 0.05
# Residual degrees of freedom (days − coefficients) needed for a regression result. With the intercept, the behavior,
# strain, sleep and 6 weekday dummies (10 coefficients) that is >= 30 complete days. Design choice: HAC standard
# errors are asymptotic and badly biased downward in very short series (Andrews, Econometrica 1991,
# doi:10.2307/2938229), so we refuse rather than show an overconfident interval.
REG_MIN_DF = 20
# A pivot smaller than this share of the largest diagonal element of X'X counts as zero (collinear design, e.g. a
# behavior logged only on Mondays is indistinguishable from the Monday dummy). Numerical choice.
SINGULAR_TOL = 1e-9
