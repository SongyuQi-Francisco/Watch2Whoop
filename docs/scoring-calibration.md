# Scoring calibration: method and decision rules

> Issue #118. Three questions are settled here: how to calibrate, RMSSD against SDNN together with the 20% artifact threshold,
> and what counts as passing. Metrics and scoring: [scoring-validation.md](scoring-validation.md) and the
> [methodology](../packages/openbodyscore/METHODOLOGY.md). Code: `body_engine/src/body_engine/validation/calibration.py`.
>
> **This file holds the method and the rules, never a result on real data.** Real-data output goes to the maintainer privately
> and stays out of git. Nothing here changes a parameter.

## 1. Scope
- **Data**: one person (the project maintainer) plus synthetic data. Other users' data is not read for this, and the consent
  text is unchanged. The multi-user, research-consent and WHOOP-comparison parts of the older plan (scoring-validation
  sections 5 and 6) are dropped.
- **What a result can mean**: anything fitted or checked here is a **personal calibration (n = 1)**. It says how a parameter behaves
  on one person's data with no external reference (no WHOOP, ECG or polysomnography). It is not evidence about other people, and it is
  never written up as a general finding.
- **What the analysis may do**: read and report. A rule below can only make a change *eligible*; the maintainer decides, in a
  separate pull request (section 6).

## 2. What is being decided
| # | Question | Candidates |
|---|---|---|
| D2 | HRV kind | Apple's SDNN (library default and fallback) vs RMSSD from beat times (the per-user default since #117) |
| D3 | Artifact threshold `MAX_DEVIATION` | 25% (`rr-v1`) vs 20%; 15% and 30% only to see the trend, they cannot be adopted |
| D4 | Recovery logistic (`RECOVERY_SLOPE`, `RECOVERY_OFFSET`) | Fixed values vs a single fit on the development window vs a per-person rolling mapping |
| D5 | Recovery weights | Current vs first principal component on the development window vs equal weights |
| D6 | Strain `STRAIN_K` | Fixed constant checked against scale limits vs a reference-session definition |
| D7 | `SLEEP_NEED_BASELINE_MIN` | Report only, cannot change from one person's data |
| D8 | Mixed beat sources in one baseline | Is the 10 ms export resolution a problem for this person's RMSSD level? |

## 3. Calibration method
**Replay, then gates. The current value wins ties.**

1. **Replay.** Stored nightly inputs are replayed through the library's own pure functions (`baseline`, `zscore`, `recovery_score`,
   `sleep_need`, `strain`, `hrv.analyze`) with `params.override(...)`. Nothing is written, and the phone and server code are untouched.
   The replay must first reproduce the stored scores (rule A).
2. **Split by time.** The first 2/3 of the paired nights form the *development* window, the last 1/3 the *hold-out*. Whatever is fitted
   is fitted on development only. Hold-out nights are never used for fitting, and a rule that looks at them decides only once.
3. **Uncertainty.** A paired moving-block bootstrap (Künsch 1989): blocks of 14 consecutive calendar days, 2,000 resamples, fixed
   seed, percentile 95% interval of *candidate − current*. Fourteen days spans two weekly training cycles and more than the short-range
   autocorrelation of nightly values. Whole blocks keep the pairing between candidates and the order within a block.
4. **Why not fit everything.** Three of the parameters cannot be identified from one person with no external reference:
   - The logistic is a monotone map of the weighted z, so it cannot change any rank-based relation with anything. Slope and offset
     only decide how days fall into colours, which makes them a display choice: they get scale checks (D4), not a fit to a criterion.
   - Each recovery input is also a part of the score, so a criterion built from those inputs favours the weight of the input it
     resembles. Independent criteria are few (D5).
   - `STRAIN_K` is a scale constant. It rescales strain without reordering days, so it needs a definition, not a fit (D6).
5. **Calibration methods considered:**

| Method | Verdict | Why |
|---|---|---|
| Fixed parameters + n = 1 gates | **Chosen** | No new runtime state, same formula on phone and server, scores comparable over time and between people |
| One global fit on the maintainer's data | Only under D4's rule | Would write one person's distribution into a default, which the project rules forbid unless it is relabelled and kept configurable |
| Per-person rolling mapping | Only under D4's rule | Flattens real long-term differences ("this person is often yellow") and adds state the phone has to carry |
| Multi-user fit | Not available | Decision of 2026-11-02: only the maintainer's data and synthetic data |
| Fit to WHOOP | Not available | Same decision; no comparison data |

## 4. Metrics
Nightly HRV is computed as in scoring: the median of ln readings inside the night's main watch sleep. All three primary metrics are
oriented so that **higher is better**, and all are computed on nights where both candidates have a value.

| | Metric | Definition | Why it can compare HRV series |
|---|---|---|---|
| P1 | Stability | Lag-1 Pearson of nightly ln HRV, calendar-adjacent nights only | Physiology moves slowly compared with single-reading noise |
| P2 | Agreement with resting HR | −r(nightly ln HRV, same-night resting HR) | A separate measurement of the same night, with an expected negative relation |
| P3 | Reading consistency | ICC(1) of ln readings within nights having ≥ 2 readings (one-way random effects, Shrout & Fleiss 1979; unequal counts use the usual n₀) | Share of reading variance that is between nights |

Secondary metrics are reported but gate nothing, unless a rule names them:
−r(nightly ln HRV, Apple's daily resting HR on the same date); r(recovery, 60-second heart-rate recovery of that day's workouts, when ≥ 15 pairs);
the strain coefficient of scoring-validation model B (OLS + Newey-West + weekday; low power for one person); recovery lag-1; share of
days whose colour differs from the previous day; number of illness flags.

**History impact** is reported for every candidate, whether or not it passes: days compared, mean absolute difference, Bland-Altman mean
difference and 95% limits of agreement (Bland & Altman 1986), colour agreement and the 3 × 3 colour table, days moved by ≥ 5 and ≥ 10 points,
and days gained or lost.

## 5. Decision rules
Every threshold is a design choice, recorded in `validation/calibration_params.py`. They were written before any real data was analysed
(only row counts were looked at, to see which criteria were available). A later change has to be marked as made afterwards: section 5.7 lists them.

### 5.1 Rules for any change (D2, D3)
| Rule | Requirement | If not met |
|---|---|---|
| **A. Replay fidelity** | Replayed recovery within ±1 point of the stored value on ≥ 95% of days and within ±3 on all; replayed nightly ln HRV within 0.005 on ≥ 99% of nights | No decisions at all: the what-if numbers cannot be trusted |
| **B. Enough data** | ≥ 90 paired nights, ≥ 30 of them in the hold-out | `insufficient data` |
| **C. Coverage** | Candidate keeps ≥ 98% of the current recovery days | `keep` |
| **D. No harm** | For P1, P2 and P3: point difference ≥ −0.05 **and** the 95% interval is not entirely below 0 | `keep` |
| **E. Improvement** | At least one primary metric has its 95% interval entirely above 0, over all paired nights, **and** a positive point difference in the development window, the hold-out and the newest source era (when that era has ≥ 30 nights) | `keep` |
| **F. Practical effect** | Mean absolute difference ≥ 1 point or colour changes on ≥ 2% of days | `keep (no practical effect)`: not worth a new algorithm version |
| **G. Churn** | If colours change on > 25% of days, at least two primary metrics must meet E | `keep` |
| **H. Synthetic checks** | The candidate passes scoring-validation 2.5's criteria on synthetic series | `keep` |

All of them must hold, which makes this an intersection-union test: no multiplicity correction is needed for the gates, at the cost of being conservative.
Rule D is weaker than a formal non-inferiority test, which would have almost no power at this sample size; a pass means "no detectable harm",
not "proven no harm". The outcome is `eligible` or `keep`.

**D2 (RMSSD vs SDNN).** SDNN is the reference and RMSSD the candidate. RMSSD is already the per-user default (#117), so the possible
outcomes are *RMSSD supported* or *RMSSD not supported on this person's data*. The second only reports to the maintainer; nothing switches
back by itself. `HRV_KIND = "sdnn"` stays the library default either way, as the fallback for people with no beat data. Comparison is
also repeated on the same readings only (each reading's stored Apple SDNN against its RMSSD) as a sensitivity check.

**D3 (artifact threshold).** 25% is the reference and only 20% can become eligible. On top of the rules above:
**reading loss**: the share of readings with a usable RMSSD drops by at most 2 percentage points; **no over-filtering**: over readings usable under
both thresholds, the median change in ln RMSSD does not fall below −0.05, because a tighter filter that mostly trims real variability would lower RMSSD across
the board. The other thresholds show whether any effect has a sensible trend. A local-median filter (each interval against its neighbours) is run only as an exploratory
row, labelled as such; it cannot become eligible here.

### 5.2 Recovery logistic (D4)
- **Scale check of the fixed mapping**: in both development and hold-out, green, yellow and red each cover 15–60% of days, and the mean weighted z
  stays within ±0.30. Pass = `keep`.
- If the check fails in both windows, two alternatives are evaluated:
  a single fit on development (the slope and offset placing the window's terciles of weighted z on the 34 and 67 boundaries), and a rolling
  per-person mapping (the same calculation on the trailing 90 nights, slope limited to 0.5–4, falling back to fixed with fewer than 30).
  Either is `eligible` only if it passes the scale check on the hold-out and its noise flip rate is at most 2 points worse.
  Equal thirds is itself a design choice.
- **Noise flip rate** (reported always): among nights with ≥ 2 readings, the share where leaving out one reading can change the colour.
  Reported for slope × 0.8 / 1 / 1.2.

### 5.3 Recovery weights (D5)
Candidates fitted on development: non-negative loadings of the first principal component of the four z-scores, normalised to sum to 1. Equal weights are a
bracket. On the hold-out: (i) recovery lag-1; (ii) −r(recovery, Apple's daily resting HR, same date); (iii) r(recovery, same-day workout heart-rate
recovery), when ≥ 15 pairs. `eligible` only if at least two have a 95% interval entirely above 0 and none is entirely below 0. Criterion (ii) leans towards
weighting resting HR, so it cannot carry the decision alone.

### 5.4 Strain K (D6)
K is not fitted. Checks on the current value: the median strain of training days (TRIMP ≥ 40) lies within 8–16, and ≤ 5% of days reach strain ≥ 19. If both hold,
`keep`, and the parameter can be relabelled from "personal calibration" to a design choice anchored on a reference session: 60 minutes entirely at 70–80% HRmax
(Edwards TRIMP = 180). If either fails, K is redefined from that session alone, with no person's data: strain 14, so K = 180 / ln 3 ≈ 164.
K calibrated per person is rejected in any case, because it makes strain incomparable between people (scoring-validation 3.7). The old definition (median training day → 12)
is reported for the development and hold-out windows to show how unstable it is.

### 5.5 Sleep-need baseline (D7)
Report only: median actual sleep on nights with low debt (≤ 30 minutes) and low previous-day strain (≤ 7). How long someone sleeps is shaped by habit and schedule, so
this does not measure need. The value cannot change from one person's data.

### 5.6 Mixed sources in one baseline (D8)
Newest-source nights are re-processed as an older export would give them (beat times rounded to 10 ms, no gap flags), then compared with their own exact form.
A mixed baseline is acceptable when the median absolute difference in nightly ln RMSSD is ≤ 0.05 and the mean signed shift is at most 0.25 of the baseline SD.
Otherwise the maintainer is told, since the baseline would need its sources separated. This covers a known systematic bias: rounding pushes RMSSD up, most at low RMSSD
(scoring-validation 3.6).

### 5.7 Changes after the rules were written
No threshold, candidate or metric changed. Two things did, both found while checking the data preparation, and both are stated here because they came after a first look at real data:
1. **A reading counted twice.** The same SDNN reading can arrive from two sources with different time precision (microseconds and whole seconds), and the first version of the export
   merge kept both. A night's duplicated reading looks perfectly consistent, which flatters SDNN on P3, so this could change a D2 outcome. Readings are now matched on the whole second.
   The check that found it does not depend on the outcome: the merged reading count has to equal what the scoring code's own report counts.
2. **Wording of the over-filtering rule** in D3: "unchanged-outcome readings" was vague and became "readings usable under both thresholds". The threshold is the same.

## 6. From a rule to a change
A rule saying `eligible` is not a decision:
1. The maintainer sees the comparison and the history impact (section 4) and decides.
2. A separate pull request changes the parameter in **both** `packages/openbodyscore` and `packages/openbodyscore-js`, bumps `ALGO_VERSION` (and the filter's `ALGO`,
   with every stored reading re-derived, if D3 changes), and updates both sets of golden files.
3. The parameter's category in the methodology becomes **personal calibration (n = 1)**, with the rules it passed and no personal values.
4. All history is recomputed on the next full run, and old and new versions are shown side by side first.

Public wording: "checked on one person's data, with no external reference". Never "validated".

## 7. Limits
- One person, one watch, one period. Results can reflect this person's habits and may not hold for them later either.
- There is no ground truth. P1–P3 measure consistency, not accuracy: a stable bias scores well.
- With a few hundred nights the intervals are wide, so most candidates will end in `keep`. That is intended: it means "no evidence to change", not "proven optimal".
- Rule thresholds are design choices. They make the decision explicit and repeatable; they do not make it objective.
- Everything runs on one chosen block length and seed. When an interval's bound sits near 0, the private report says so and repeats it with other block lengths and seeds.

## 8. How to run it
```bash
cd body_engine
uv run python -m body_engine.validation calibration export-sql --user-id N   # one read-only COPY per file, for one user
uv run python -m body_engine.validation calibration run --data DIR --json OUT.json   # files only, no database; holds personal values, keep out of git
uv run python -m body_engine.validation calibration synthetic                # the same report on a made-up person
uv run python -m body_engine.validation calibration rules                    # the table below
```
Run the export where the database is, save each COPY as the file named above it in a private directory outside the repository, and delete that directory afterwards.
Tests: `body_engine/tests/test_calibration.py`, synthetic data only. Among them, the replay has to reproduce the real scoring engine on a synthetic person.

### Rules on synthetic persons
The generator (`validation/calibration_synth.py`) has a known answer, so this measures the rules and says nothing about real physiology. Each row uses 340 days, like the
data the rules were written for.

<!-- RULES-TABLE -->

## References
DOIs checked against Crossref on 2026-11-03 (title, first author, journal, year, volume, pages).
- Künsch HR. The jackknife and the bootstrap for general stationary observations. *Ann Stat* 1989;17:1217–1241. doi:10.1214/aos/1176347265
- Shrout PE, Fleiss JL. Intraclass correlations: uses in assessing rater reliability. *Psychol Bull* 1979;86:420–428. doi:10.1037/0033-2909.86.2.420
- Bland JM, Altman DG. Statistical methods for assessing agreement between two methods of clinical measurement. *Lancet* 1986;327:307–310. doi:10.1016/S0140-6736(86)90837-8

Other references (Edwards TRIMP, Task Force 1996, Peltola 2012, Lipponen & Tarvainen 2019): see the methodology.
