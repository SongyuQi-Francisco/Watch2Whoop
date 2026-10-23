"""Validation metrics over scored days (rows of scores.engine.score_days). Everything here only reads.

- stability: lag-1 correlation of consecutive days (pairs of calendar-adjacent days that both have a value);
- validity: relations the scores should show (recovery vs the previous day's strain, nightly ln HRV vs resting heart rate,
  recovery vs a known truth on synthetic data);
- sensitivity: each parameter scaled by ±delta (default 20%), all days re-scored, mean absolute change per score and the
  share of days whose recovery zone changes;
- agreement: two versions of the recovery score (e.g. SDNN vs RMSSD): correlation, zone agreement, Bland-Altman.
"""
import statistics
from contextlib import contextmanager
from datetime import date, timedelta

from ..scores import engine as SE
from ..scores import params as P
from ..scores import recovery as R
from ..scores import sleep as S
from ..scores import strain as L

GREEN, RED = 67, 34                    # recovery zones, same as insights/coach.py RECOVERY_ZONES
SLEEP_WEIGHTS = ("DURATION_IMPACT", "STAGES_IMPACT", "CONSISTENCY_IMPACT", "INTERRUPTIONS_IMPACT")

# (label, how to perturb, delta). kinds: "scalar" = params.NAME × (1 ± delta) (ints rounded, timedelta scaled);
# "weight:<key>" = one recovery weight (the engine renormalises); "sleep:<NAME>" = one sleep-score weight, the other three
# rescaled so the four still sum to 1; "zones" = every Edwards zone lower bound × (1 ± delta).
SPECS = [
    ("RECOVERY_WEIGHTS.hrv", "weight:hrv", 0.2), ("RECOVERY_WEIGHTS.rhr", "weight:rhr", 0.2),
    ("RECOVERY_WEIGHTS.sleep_performance", "weight:sleep_performance", 0.2),
    ("RECOVERY_WEIGHTS.resp_rate", "weight:resp_rate", 0.2),
    ("RECOVERY_SLOPE", "scalar", 0.2), ("RECOVERY_OFFSET", "scalar", 0.2), ("BASELINE_DAYS", "scalar", 0.2),
    ("MIN_BASELINE_NIGHTS", "scalar", 0.2), ("Z_CLIP", "scalar", 0.2), ("RHR_WINDOW", "scalar", 0.2),
    ("DURATION_IMPACT", "sleep:DURATION_IMPACT", 0.2), ("STAGES_IMPACT", "sleep:STAGES_IMPACT", 0.2),
    ("OPTIMAL_MIN_HOURS", "scalar", 0.2), ("DEEP_TARGET_MIN", "scalar", 0.2), ("REM_TARGET_MIN", "scalar", 0.2),
    ("CONSISTENCY_GRACE_MIN", "scalar", 0.2), ("INTERRUPTIONS_GRACE_MIN", "scalar", 0.2),
    ("SLEEP_NEED_BASELINE_MIN", "scalar", 0.2), ("DEBT_DECAY", "scalar", 0.2), ("DEBT_CAP_MIN", "scalar", 0.2),
    ("STRAIN_NEED_MIN_PER_POINT", "scalar", 0.2),
    ("STRAIN_K", "scalar", 0.2), ("EDWARDS_ZONES", "zones", 0.05), ("HR_SAMPLE_CAP", "scalar", 0.2),
]


def score(inp, first: date, last: date) -> list[dict]:
    """All days first..last, scored as if the last day were over."""
    return SE.score_days(inp, first, last, today=last + timedelta(days=1))


def corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None
    try:
        return round(statistics.correlation(xs, ys), 3)
    except statistics.StatisticsError:
        return None


def zone(r: int) -> str:
    return "green" if r >= GREEN else "yellow" if r >= RED else "red"


def _by_date(rows: list[dict], key: str) -> dict[date, float]:
    return {r["date"]: r[key] for r in rows if r.get(key) is not None}


def lag1(rows: list[dict], key: str) -> tuple[float | None, int]:
    v = _by_date(rows, key)
    pairs = [(v[d], v[d + timedelta(days=1)]) for d in v if d + timedelta(days=1) in v]
    return corr([a for a, _ in pairs], [b for _, b in pairs]), len(pairs)


def stability(rows: list[dict]) -> dict:
    return {k: dict(zip(("r", "n"), lag1(rows, k))) for k in ("recovery", "hrv_night_ln", "rhr_night", "sleep_score", "strain")}


def _paired(a: dict[date, float], b: dict[date, float], shift: int = 0) -> tuple[list[float], list[float]]:
    days = [d for d in a if d + timedelta(days=shift) in b]
    return [a[d] for d in days], [b[d + timedelta(days=shift)] for d in days]


def validity(rows: list[dict], truth: dict[date, float] | None = None, illness: set[date] | None = None) -> dict:
    """Expected signs: recovery vs previous-day strain −, nightly ln HRV vs resting heart rate − (two separate measurements),
    recovery vs sleep score +, recovery vs the latent truth + (synthetic only)."""
    rec, strain = _by_date(rows, "recovery"), _by_date(rows, "strain")
    out = {"recovery_vs_prev_strain": corr(*_paired(strain, rec, 1)),
           "hrv_vs_rhr_same_night": corr(*_paired(_by_date(rows, "hrv_night_ln"), _by_date(rows, "rhr_night"))),
           "recovery_vs_sleep_score": corr(*_paired(rec, _by_date(rows, "sleep_score"))),
           "illness_flag_days": sum("illness_warning" in (r["flags"] or {}) for r in rows)}
    if truth:
        out["recovery_vs_truth"] = corr(*_paired(rec, truth))
        out["hrv_vs_truth"] = corr(*_paired(_by_date(rows, "hrv_night_ln"), truth))
        out["rhr_vs_truth"] = corr(*_paired(_by_date(rows, "rhr_night"), truth))
    if illness is not None:
        flagged = {r["date"] for r in rows if "illness_warning" in (r["flags"] or {})}
        out["illness_days_flagged"] = f"{len(flagged & illness)} / {len(illness)}"
        out["illness_false_flags"] = len(flagged - illness)
    return out


LOAD_OUTCOMES = ("recovery", "hrv_night_ln", "rhr_night")


def load_regression(rows: list[dict], weekday: bool = False, outcomes=LOAD_OUTCOMES) -> dict:
    """Construct validity, regression version (docs/scoring-validation.md 2.4): for each outcome on day D+1, OLS on the
    strain of day D and the sleep hours of the night before D+1 (+ weekday of D when `weekday`), Newey-West standard errors
    (7-day lag, records/regression.py), Benjamini-Hochberg over every strain and sleep coefficient of the call.
    Returns {outcome: {"n", "strain": {...}, "sleep_h": {...}}} with estimate, ci95, p, q per coefficient."""
    from ..records import analysis_params as AP
    from ..records import regression as RG

    by = {r["date"]: r for r in rows}
    out, tests = {}, []
    for key in outcomes:
        days, y, x = [], [], []
        for d, r in by.items():
            nxt = by.get(d + timedelta(days=1))
            if nxt is None or r.get("strain") is None or nxt.get(key) is None or nxt.get("sleep_actual_min") is None:
                continue
            row = [r["strain"], nxt["sleep_actual_min"] / 60]
            if weekday:
                row += [1.0 if d.weekday() == k else 0.0 for k in range(1, 7)]
            days.append(d)
            y.append(nxt[key])
            x.append(row)
        names = ["strain", "sleep_h"] + ([f"wd{k}" for k in range(1, 7)] if weekday else [])
        if len(y) - len(names) - 1 < AP.REG_MIN_DF:
            out[key] = {"n": len(y), "insufficient": True}
            continue
        try:
            fit = RG.ols_hac(days, y, x, names)
        except RG.Singular:
            out[key] = {"n": len(y), "collinear": True}
            continue
        out[key] = {"n": fit["n"]}
        for name in ("strain", "sleep_h"):
            c = fit["coef"][name]
            out[key][name] = {"estimate": c["estimate"], "ci95": c["ci95"], "p": c["p"]}
            tests.append(out[key][name])
    for t, q in zip(tests, RG.bh([t["p"] for t in tests])):
        t["q"] = q
    return out


def agreement(a: list[dict], b: list[dict], key: str = "recovery") -> dict:
    """Two scorings of the same days: coverage, correlation, zone agreement and 3×3 matrix (rows = a, columns = b),
    Bland-Altman mean difference (b − a) and 95% limits of agreement."""
    va, vb = _by_date(a, key), _by_date(b, key)
    both = sorted(set(va) & set(vb))
    out = {"a_only": len(set(va) - set(vb)), "b_only": len(set(vb) - set(va)), "both": len(both)}
    if len(both) < 5:
        return out
    xs, ys = [va[d] for d in both], [vb[d] for d in both]
    diffs = [y - x for x, y in zip(xs, ys)]
    md, sd = statistics.fmean(diffs), statistics.stdev(diffs)
    matrix = {za: {zb: 0 for zb in ("green", "yellow", "red")} for za in ("green", "yellow", "red")}
    for x, y in zip(xs, ys):
        matrix[zone(x)][zone(y)] += 1
    out.update(corr=corr(xs, ys), zone_agree=round(sum(zone(x) == zone(y) for x, y in zip(xs, ys)) / len(both), 3),
               mean_abs_diff=round(statistics.fmean(abs(d) for d in diffs), 1), mean_diff=round(md, 1),
               loa=(round(md - 1.96 * sd, 1), round(md + 1.96 * sd, 1)), matrix=matrix,
               a_mean=round(statistics.fmean(xs), 1), b_mean=round(statistics.fmean(ys), 1),
               a_sd=round(statistics.stdev(xs), 1), b_sd=round(statistics.stdev(ys), 1))
    return out


# ---------------------------------------------------------------- sensitivity
def _scaled(value, f: float):
    if isinstance(value, bool):
        raise TypeError("cannot scale a bool")
    if isinstance(value, int):
        return max(1, round(value * f))
    return value * f


def _new_values(kind: str, f: float) -> dict:
    """Parameter name -> new value for one perturbation."""
    if kind.startswith("weight:"):
        k = kind.split(":", 1)[1]
        return {"RECOVERY_WEIGHTS": {**P.RECOVERY_WEIGHTS, k: P.RECOVERY_WEIGHTS[k] * f}}
    if kind.startswith("sleep:"):
        name = kind.split(":", 1)[1]
        new = getattr(P, name) * f
        rest = sum(getattr(P, n) for n in SLEEP_WEIGHTS if n != name)
        scale = (1 - new) / rest
        return {n: (new if n == name else getattr(P, n) * scale) for n in SLEEP_WEIGHTS}
    if kind == "zones":
        return {"EDWARDS_ZONES": tuple((min(lo * f, 0.99), w) for lo, w in P.EDWARDS_ZONES)}
    raise ValueError(kind)


@contextmanager
def patched(values: dict):
    """Set scores.params attributes for the duration of the block. Functions whose default arguments captured the old
    value at import time (e.g. strain(trimp, k=STRAIN_K)) get the new default too; everything is restored afterwards."""
    old = {n: getattr(P, n) for n in values}
    funcs = [f for m in (R, S, L) for f in vars(m).values() if callable(f) and getattr(f, "__defaults__", None)]
    saved = {f: f.__defaults__ for f in funcs}
    try:
        for n, v in values.items():
            setattr(P, n, v)
            for f in funcs:
                if any(d is old[n] for d in f.__defaults__):
                    f.__defaults__ = tuple(v if d is old[n] else d for d in f.__defaults__)
        yield
    finally:
        for n, v in old.items():
            setattr(P, n, v)
        for f, d in saved.items():
            f.__defaults__ = d


def _delta(base: list[dict], new: list[dict], key: str) -> float | None:
    va, vb = _by_date(base, key), _by_date(new, key)
    both = set(va) & set(vb)
    return round(statistics.fmean(abs(vb[d] - va[d]) for d in both), 2) if both else None


def compare_rows(base: list[dict], new: list[dict]) -> dict:
    ra, rb = _by_date(base, "recovery"), _by_date(new, "recovery")
    both = set(ra) & set(rb)
    return {"recovery": _delta(base, new, "recovery"),
            "zone_change": round(sum(zone(ra[d]) != zone(rb[d]) for d in both) / len(both), 3) if both else None,
            "recovery_days": len(rb) - len(ra), "sleep_score": _delta(base, new, "sleep_score"),
            "sleep_need_min": _delta(base, new, "sleep_need_min"), "strain": _delta(base, new, "strain"),
            "tsb": _delta(base, new, "tsb")}


def sensitivity(inp, first: date, last: date, specs=SPECS, base: list[dict] | None = None) -> list[dict]:
    """One row per (parameter, direction): the mean absolute change of each score against the unperturbed run."""
    base = base if base is not None else score(inp, first, last)
    out = []
    for label, kind, delta in specs:
        for sign in (-1, 1):
            f = 1 + sign * delta
            values = {label: _scaled(getattr(P, label), f)} if kind == "scalar" else _new_values(kind, f)
            with patched(values):
                rows = score(inp, first, last)
            out.append({"param": label, "change": f"{sign * delta:+.0%}", **compare_rows(base, rows)})
    return out


# ---------------------------------------------------------------- Markdown
def _f(x) -> str:
    return "—" if x is None else str(x)


def markdown(title: str, rows: list[dict], truth: dict | None = None, sens: list[dict] | None = None,
             agree: dict | None = None, agree_labels: tuple[str, str] = ("A", "B"), illness: set | None = None) -> str:
    st, va = stability(rows), validity(rows, truth, illness)
    rec = [r["recovery"] for r in rows if r["recovery"] is not None]
    out = [f"# {title}", "", f"Days: {len(rows)}; with recovery {len(rec)}; with sleep score "
           f"{sum(r['sleep_score'] is not None for r in rows)}; with strain {sum(r['strain'] is not None for r in rows)}.",
           f"Recovery mean ± SD {round(statistics.fmean(rec), 1) if rec else '—'} ± "
           f"{round(statistics.stdev(rec), 1) if len(rec) > 1 else '—'}; green / yellow / red "
           f"{sum(zone(x) == 'green' for x in rec)} / {sum(zone(x) == 'yellow' for x in rec)} / {sum(zone(x) == 'red' for x in rec)}.",
           "", "## Stability (lag-1 correlation of adjacent days)", "", "| score | r | pairs |", "|---|---|---|"]
    out += [f"| {k} | {_f(v['r'])} | {v['n']} |" for k, v in st.items()]
    out += ["", "## Construct validity (expected sign in brackets)", "", "| relation | r |", "|---|---|"]
    signs = {"recovery_vs_prev_strain": "−", "hrv_vs_rhr_same_night": "−", "recovery_vs_sleep_score": "+",
             "recovery_vs_truth": "+", "hrv_vs_truth": "+", "rhr_vs_truth": "−"}
    out += [f"| {k} ({signs.get(k, 'count')}) | {_f(v)} |" for k, v in va.items()]
    if agree:
        a, b = agree_labels
        out += ["", f"## Agreement of two recovery versions ({a} vs {b})", "",
                f"- days with both {agree['both']}, only {a} {agree['a_only']}, only {b} {agree['b_only']}"]
        if "corr" in agree:
            m = agree["matrix"]
            out += [f"- mean ± SD: {a} {agree['a_mean']} ± {agree['a_sd']}, {b} {agree['b_mean']} ± {agree['b_sd']}",
                    f"- r = {agree['corr']}, zone agreement {agree['zone_agree']:.0%}, mean |difference| {agree['mean_abs_diff']}",
                    f"- Bland-Altman ({b} − {a}): mean {agree['mean_diff']}, 95% limits {agree['loa'][0]} to {agree['loa'][1]}",
                    "", f"| {a} \\ {b} | green | yellow | red |", "|---|---|---|---|"]
            out += [f"| {za} | {m[za]['green']} | {m[za]['yellow']} | {m[za]['red']} |" for za in m]
    if sens:
        out += ["", "## Sensitivity (each parameter alone; mean absolute change over all days)", "",
                "| parameter | change | recovery | zone changed | Δ days with recovery | sleep score | sleep need (min) | strain | TSB |",
                "|---|---|---|---|---|---|---|---|---|"]
        for s in sens:
            zc = "—" if s["zone_change"] is None else f"{s['zone_change']:.0%}"
            out.append(f"| {s['param']} | {s['change']} | {_f(s['recovery'])} | {zc} | {s['recovery_days']:+d} | {_f(s['sleep_score'])} | "
                       f"{_f(s['sleep_need_min'])} | {_f(s['strain'])} | {_f(s['tsb'])} |")
    return "\n".join(out) + "\n"
