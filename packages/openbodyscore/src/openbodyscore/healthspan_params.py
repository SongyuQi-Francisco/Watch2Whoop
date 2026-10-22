"""Parameters and literature sources for body age (each source checked against the full text / full tables on 2026-10-17). Bump ALGO_VERSION whenever a parameter that affects results changes.

Anchor: most hazard ratios in the literature use "the worst bracket" as reference (least walking, least regular...); using them directly would make almost everyone "younger".
Here every item is converted to a hazard ratio relative to a "typical adult": HR_rel = HR(x) / HR(anchor), anchor = 0 years. The anchor choice is noted next to each item.
Curve knots are [(x, HR)], interpolated linearly in ln(HR) between knots, held flat (no extrapolation) beyond the ends.
Every function in healthspan.py takes `params=` (default: this module); `override(**changes)` builds a modified copy.
"""
import sys

from . import _override

ALGO_VERSION = "hs-v1.1-2026-10-17"   # v1.1: pace of aging switched to 26 weeks + 4-week moving average

# ---------------------------------------------------------------- window and conversion
WINDOW_DAYS = 180
RECENT_DAYS, RECENT_WEIGHT = 30, 2.0      # day weight of the last 30 days x 2
MIN_DAYS = 30                             # at least 30 days per item (regularity = 30 pairs of adjacent nights)
# Gompertz: adult (after age 40) all-cause mortality roughly doubles every 8 years. Kirkwood TBL. Deciphering death: a commentary on Gompertz (1825).
# Philos Trans R Soc B 2015;370:20140379(doi:10.1098/rstb.2014.0379), citing Jones HB 1956 "doubling time consistently around 8 years".
MORTALITY_DOUBLING_YEARS = 8.0
ITEM_CAP_YEARS = 4.0
SHRINKAGE = 0.6                           # items are correlated (e.g. steps, exercise, VO₂max, resting heart rate), so the total is scaled to 60% to avoid double counting
TOTAL_CAP_YEARS = 10.0
# Pace of aging (decided 2026-10-17 by the maintainer): each week's Δ (body age - chronological age) is first smoothed with a 4-week moving average, then least-squares fitted over the last 26 weeks,
# pace = 1 + dΔ/dt (years / year). Before 26 weeks exist, all available weeks are used, and at least 8 weeks are needed. v1.0 used 8 weeks without smoothing, and the owner's value was pinned at -1.
PACE_WEEKS, PACE_MIN_WEEKS, PACE_SMOOTH_WEEKS = 26, 8, 4
PACE_RANGE = (-1.0, 3.0)

# ---------------------------------------------------------------- sleep duration
# Yin J et al. J Am Heart Assoc 2017;6:e005947(doi:10.1161/JAHA.117.005947) dose-response meta-analysis: nadir around 7h,
# below 7h each 1h less gives RR 1.06 (1.04–1.07), above 7h each 1h more gives RR 1.13 (1.11–1.15). The U shape is also seen in Cappuccio 2010 Sleep 33:585
# (short sleep RR 1.12, long sleep 1.30) and Shen 2016 Sci Rep 6:21480 (nadir 7h).
SLEEP_NADIR_H = 7.0                       # anchor = nadir (self-reported 7h is also the most common duration in the population)
SLEEP_RR_PER_H_SHORT, SLEEP_RR_PER_H_LONG = 1.06, 1.13
# Durations in the literature are self-reported in questionnaires, while the watch measures actual time asleep. Lauderdale DS et al. Epidemiology 2008;19:838 (doi:10.1097/EDE.0b013e318187a7b0):
# self-report averages 0.8h more than actigraphy and correlates with the measured value: each extra 1h measured gives 34 more minutes self-reported; measured 5h -> self-report +1.2h, 7h -> +0.4h.
# Conversion: self-report ≈ 3.365 + 0.567 × measured (the line through those two points).
SLEEP_SELF_REPORT = (3.365, 0.567)

# ---------------------------------------------------------------- sleep regularity (SRI)
# Windred DP et al. Sleep 2024;47:zsad253 (doi:10.1093/sleep/zsad253), UK Biobank, 60,000 people, about 1 week of accelerometry:
# SRI quintile medians 65.1 / 75.6 / 81.0 / 85.2 / 89.8, all-cause mortality HR (vs the most irregular Q1, full model) 1 / 0.80 / 0.75 / 0.72 / 0.70.
SRI_KNOTS = [(65.1, 1.00), (75.6, 0.80), (81.0, 0.75), (85.2, 0.72), (89.8, 0.70)]
SRI_ANCHOR = 81.0                         # anchor = cohort median (Q3)

# ---------------------------------------------------------------- steps
# Paluch AE et al. Lancet Public Health 2022;7:e219 (doi:10.1016/S2468-2667(21)00302-9), 15 cohorts: quartile medians and HR (model 2, reference Q1).
# < 60 years: 4849 / 7245 / 8911 / 11482 steps -> 1 / 0.59 / 0.51 / 0.60 (the Q4 rebound is noise; the authors conclude a plateau after 8000–10000 steps) -> flat after Q3.
# ≥ 60 years: 2841 / 5217 / 7116 / 10501 -> 1 / 0.62 / 0.52 / 0.43 (plateau at 6000–8000).
STEPS_KNOTS_UNDER60 = [(4849, 1.00), (7245, 0.59), (8911, 0.51)]
STEPS_KNOTS_60PLUS = [(2841, 1.00), (5217, 0.62), (7116, 0.52), (10501, 0.43)]
STEPS_ANCHOR_UNDER60 = (7245 + 8911) / 2  # anchor = cohort median of this age group (midpoint of the Q2 and Q3 medians) ≈ 8078
STEPS_ANCHOR_60PLUS = (5217 + 7116) / 2   # ≈ 6167

# ---------------------------------------------------------------- moderate-to-vigorous exercise (moderate-equivalent minutes / week)
# Lee DH et al. Circulation 2022;146:523 (doi:10.1161/CIRCULATIONAHA.121.058162), NHS / HPFS, 116,000 people:
# moderate intensity (reference 0–19 min / week) 20–74: 0.91, 75–149: 0.84, 150–224: 0.80, 225–299: 0.79, 300–374: 0.74, 375–449: 0.74,
# 450–599: 0.69, ≥ 600: 0.68 (bracket midpoints; ≥ 600 taken as 700). 1 vigorous minute counts as 2 moderate minutes (the guideline equivalence; Lee's vigorous curve is roughly
# the moderate curve at half the minutes: vigorous 150–224 = 0.79 ≈ moderate 300–374 = 0.74-0.79).
MVPA_KNOTS = [(10, 1.00), (47, 0.91), (112, 0.84), (187, 0.80), (262, 0.79), (337, 0.74), (412, 0.74), (525, 0.69),
              (700, 0.68)]
MVPA_ANCHOR = 150                         # anchor = guideline minimum of 150 min / week
VIGOROUS_FACTOR = 2.0
# Our minutes = minutes of all-day heart rate in Z2–Z3 (60–80% HRmax) + 2 × (Z4–Z5, ≥ 80%) (daily_scores.details.load.zone_minutes),
# not only the workout windows; the questionnaire covers leisure exercise, so the definitions do not match exactly.

# ---------------------------------------------------------------- strength training (minutes / week)
# Momma H et al. Br J Sports Med 2022;56:755 (doi:10.1136/bjsports-2021-105061) meta-analysis: J-shaped, lowest RR 0.83 (0.79–0.86) at 40 min / week,
# RR stays < 1 up to about 140 min / week, the effect of more is unclear (not penalized, treated as 140). The paper gives no point estimate at 140; 0.92 is used (read off the curve plot, flagged as inferred).
STRENGTH_KNOTS = [(0, 1.00), (40, 0.83), (140, 0.92)]
STRENGTH_ANCHOR = 0                       # anchor = no training (about 70% of adults do not reach 2 strength sessions a week)
STRENGTH_MIN_PER_SET = 3.0                # when SparkyFitness has no duration: estimate 3 minutes per working set (about 45 s of work + 2 min rest)

# ---------------------------------------------------------------- VO₂max
# Kodama S et al. JAMA 2009;301:2024 (doi:10.1001/jama.2009.681): each 1 MET (3.5 mL/kg/min) higher cardiorespiratory fitness gives all-cause mortality RR 0.87 (0.84–0.90).
# Mandsager 2018 JAMA Netw Open 1:e183605: the elite group has the lowest risk with no ceiling effect (low vs elite HR 5.04).
VO2_RR_PER_MET = 0.87
VO2_MAX_DELTA_MET = 3.0                   # gap to the norm counted at most ±3 MET (Kodama's linear relation holds in this range)
VO2_MIN_READINGS = 2                      # VO₂max readings are sporadic (estimated only on outdoor walks / runs), so the "30 days" rule does not apply: >= 2 readings in 180 days,
VO2_MAX_AGE_DAYS = 120                    # and the latest within 120 days
# Anchor = same-age same-sex median: FRIEND 2022 (Kaminsky LA et al. Mayo Clin Proc 2022;97:285, doi:10.1016/j.mayocp.2021.08.020)
# treadmill VO₂peak 50th percentile; placed at the midpoint of each age band (20–29 -> age 25), linearly interpolated between. The Apple Watch estimate runs a few units low, with no correction.
VO2_MEDIAN = {
    "male": {25: 46.5, 35: 39.7, 45: 35.3, 55: 29.2, 65: 24.6, 75: 20.6, 85: 17.6},
    "female": {25: 36.6, 35: 28.3, 45: 25.7, 55: 22.9, 65: 19.6, 75: 17.2, 85: 15.4},
}
# Deciles used for the fitness level (fitness.py) (same table): P10 … P90
VO2_DECILES = {
    "male": {25: [28.6, 35.2, 40.0, 43.6, 46.5, 49.0, 51.9, 54.5, 58.6],
             35: [24.9, 29.8, 33.5, 37.0, 39.7, 43.4, 46.4, 50.0, 55.5],
             45: [22.1, 26.7, 29.7, 32.4, 35.3, 37.9, 40.9, 45.2, 50.8],
             55: [18.6, 22.2, 24.5, 26.9, 29.2, 31.8, 34.3, 38.3, 43.4],
             65: [15.8, 18.5, 20.7, 22.8, 24.6, 26.5, 28.7, 32.0, 37.1],
             75: [13.6, 15.9, 17.3, 19.1, 20.6, 22.2, 23.8, 25.9, 29.4],
             85: [12.9, 14.8, 16.1, 16.6, 17.6, 18.4, 20.0, 21.4, 22.8]},
    "female": {25: [22.5, 27.2, 30.8, 34.0, 36.6, 39.0, 41.8, 44.8, 49.0],
               35: [18.6, 21.9, 24.2, 26.4, 28.3, 31.0, 33.6, 37.0, 42.1],
               45: [17.2, 19.7, 21.8, 23.9, 25.7, 27.7, 30.0, 33.0, 37.8],
               55: [16.5, 18.5, 20.1, 21.5, 22.9, 24.6, 26.3, 28.4, 32.4],
               65: [13.4, 15.4, 17.0, 18.3, 19.6, 20.9, 22.4, 24.3, 27.3],
               75: [12.3, 14.0, 15.2, 16.2, 17.2, 18.3, 19.6, 20.8, 22.8],
               85: [11.4, 12.6, 13.7, 14.7, 15.4, 16.0, 17.3, 18.4, 20.8]},
}

# ---------------------------------------------------------------- resting heart rate
# Jensen MT et al. Heart 2013;99:882 (doi:10.1136/heartjnl-2012-303375), Copenhagen Male Study: HR 1.16 (1.10–1.22) per 10 bpm higher,
# adjusted for VO₂max (so it overlaps less with the VO₂max item; chosen over Zhang 2016 CMAJ's 1.09).
# Shape: Zhang D et al. CMAJ 2016;188:E53 meta-analysis, all-cause mortality rises linearly from 45 bpm with no threshold -> flat below 45.
RHR_RR_PER_10 = 1.16
RHR_FLOOR = 45.0
RHR_ANCHOR = 65.0                         # anchor = average resting heart rate of wearable users, about 65 (Quer G et al. PLoS One 2020;15:e0227709, 90,000 Fitbit users)

# ---------------------------------------------------------------- body composition
# BMI: Global BMI Mortality Collaboration. Lancet 2016;388:776 (doi:10.1016/S0140-6736(16)30175-1), never-smokers without chronic disease:
# 15–18.5: 1.51, 18.5–20: 1.13, 20–22.5: 1.00, 22.5–25: reference, 25–27.5: 1.07, 27.5–30: 1.20, 30–35: 1.45, 35–40: 1.94, 40–60: 2.76 (bracket midpoints).
BMI_KNOTS = [(17.0, 1.51), (19.25, 1.13), (21.25, 1.00), (23.75, 1.00), (26.25, 1.07), (28.75, 1.20), (32.5, 1.45),
             (37.5, 1.94), (45.0, 2.76)]
BMI_ANCHOR = 25.0                         # anchor = healthy / overweight boundary (upper edge of the reference bracket)
# Waist-to-height ratio: Jayedi A et al. BMJ 2020;370:m3324 (doi:10.1136/bmj.m3324): HR 1.24 (1.12–1.36) per 0.1 higher, J-shaped, nadir 0.50.
WHTR_KNOTS = [(0.50, 1.00), (0.60, 1.24), (0.70, 1.24 ** 2), (0.80, 1.24 ** 3)]
WHTR_ANCHOR = 0.50
WEIGHT_MAX_AGE_DAYS = 90                  # weight / waist are sporadic measurements: one within the last 90 days counts (the "30 days" rule does not apply)


def override(**changes):
    """These parameters with some replaced, for the `params=` argument of the healthspan functions, e.g.
    `hr_rhr(58, params=healthspan_params.override(RHR_ANCHOR=60.0))`. The module itself stays unchanged."""
    return _override.override(sys.modules[__name__], changes)
