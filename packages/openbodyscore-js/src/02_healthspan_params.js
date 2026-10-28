/* healthspan_params (v1.1) and healthspan_params_v12 (v1.2): body-age parameters and their literature sources. Port of
packages/openbodyscore/src/openbodyscore/healthspan_params.py and healthspan_params_v12.py, plus the product texts kept in
body_engine/insights/healthspan_params.py (TIPS, strength-record coverage) that the snapshot needs. Each source was checked
against the full text / tables (2026-10-17 for v1.1, 2026-10-23 for v1.2); see the Python files for the long notes.

Anchor: most hazard ratios in the literature use "the worst bracket" as reference; here every item is a hazard ratio relative
to a "typical adult": HR_rel = HR(x) / HR(anchor). Curve knots are [x, HR], interpolated linearly in ln(HR), flat beyond the ends.
*/
OBS.healthspan_params = (function (U) {
  "use strict";
  var H = {
    ALGO_VERSION: "hs-v1.1-2026-10-17",   // v1.1: pace of aging switched to 26 weeks + 4-week moving average

    // ------------------------------------------------ window and conversion
    WINDOW_DAYS: 180,
    RECENT_DAYS: 30, RECENT_WEIGHT: 2.0,      // day weight of the last 30 days x 2
    MIN_DAYS: 30,                             // at least 30 days per item (regularity = 30 pairs of adjacent nights)
    // Gompertz: adult all-cause mortality roughly doubles every 8 years. Kirkwood TBL. Philos Trans R Soc B
    // 2015;370:20140379 (doi:10.1098/rstb.2014.0379), citing Jones HB 1956.
    MORTALITY_DOUBLING_YEARS: 8.0,
    ITEM_CAP_YEARS: 4.0,
    SHRINKAGE: 0.6,                           // items are correlated, so the total is scaled to 60% to avoid double counting
    TOTAL_CAP_YEARS: 10.0,
    // Pace of aging (maintainer's decision 2026-10-17): weekly Δ, 4-week moving average, least squares over the last 26
    // weeks (all available before that, at least 8).
    PACE_WEEKS: 26, PACE_MIN_WEEKS: 8, PACE_SMOOTH_WEEKS: 4,
    PACE_RANGE: [-1.0, 3.0],

    // ------------------------------------------------ sleep duration
    // Yin J et al. J Am Heart Assoc 2017;6:e005947 (doi:10.1161/JAHA.117.005947): nadir ~7h, below 7h RR 1.06 per hour,
    // above 7h RR 1.13 per hour (U shape also in Cappuccio 2010 Sleep 33:585, Shen 2016 Sci Rep 6:21480).
    SLEEP_NADIR_H: 7.0,
    SLEEP_RR_PER_H_SHORT: 1.06, SLEEP_RR_PER_H_LONG: 1.13,
    // Lauderdale DS et al. Epidemiology 2008;19:838 (doi:10.1097/EDE.0b013e318187a7b0): self-report ≈ 3.365 + 0.567 × measured.
    SLEEP_SELF_REPORT: [3.365, 0.567],

    // ------------------------------------------------ sleep regularity (SRI)
    // Windred DP et al. Sleep 2024;47:zsad253 (doi:10.1093/sleep/zsad253), UK Biobank quintiles.
    SRI_KNOTS: [[65.1, 1.00], [75.6, 0.80], [81.0, 0.75], [85.2, 0.72], [89.8, 0.70]],
    SRI_ANCHOR: 81.0,                         // anchor = cohort median (Q3)

    // ------------------------------------------------ steps
    // Paluch AE et al. Lancet Public Health 2022;7:e219 (doi:10.1016/S2468-2667(21)00302-9), 15 cohorts, model 2.
    STEPS_KNOTS_UNDER60: [[4849, 1.00], [7245, 0.59], [8911, 0.51]],
    STEPS_KNOTS_60PLUS: [[2841, 1.00], [5217, 0.62], [7116, 0.52], [10501, 0.43]],
    STEPS_ANCHOR_UNDER60: (7245 + 8911) / 2,  // cohort median of this age group ≈ 8078
    STEPS_ANCHOR_60PLUS: (5217 + 7116) / 2,   // ≈ 6167

    // ------------------------------------------------ moderate-to-vigorous exercise (moderate-equivalent minutes / week)
    // Lee DH et al. Circulation 2022;146:523 (doi:10.1161/CIRCULATIONAHA.121.058162), bracket midpoints.
    MVPA_KNOTS: [[10, 1.00], [47, 0.91], [112, 0.84], [187, 0.80], [262, 0.79], [337, 0.74], [412, 0.74], [525, 0.69],
      [700, 0.68]],
    MVPA_ANCHOR: 150,                         // guideline minimum of 150 min / week
    VIGOROUS_FACTOR: 2.0,

    // ------------------------------------------------ strength training (minutes / week)
    // Momma H et al. Br J Sports Med 2022;56:755 (doi:10.1136/bjsports-2021-105061): J-shaped, lowest RR 0.83 at 40 min/week;
    // 0.92 at 140 read off the curve plot (inferred).
    STRENGTH_KNOTS: [[0, 1.00], [40, 0.83], [140, 0.92]],
    STRENGTH_ANCHOR: 0,
    STRENGTH_MIN_PER_SET: 3.0,                // no duration recorded: 3 minutes per working set

    // ------------------------------------------------ VO₂max
    // Kodama S et al. JAMA 2009;301:2024 (doi:10.1001/jama.2009.681): RR 0.87 per 1 MET (3.5 mL/kg/min).
    VO2_RR_PER_MET: 0.87,
    VO2_MAX_DELTA_MET: 3.0,
    VO2_MIN_READINGS: 2,                      // >= 2 readings in 180 days,
    VO2_MAX_AGE_DAYS: 120,                    // and the latest within 120 days
    // FRIEND 2022 (Kaminsky LA et al. Mayo Clin Proc 2022;97:285, doi:10.1016/j.mayocp.2021.08.020) treadmill VO₂peak
    // 50th percentile at the midpoint of each age band.
    VO2_MEDIAN: {
      male: { 25: 46.5, 35: 39.7, 45: 35.3, 55: 29.2, 65: 24.6, 75: 20.6, 85: 17.6 },
      female: { 25: 36.6, 35: 28.3, 45: 25.7, 55: 22.9, 65: 19.6, 75: 17.2, 85: 15.4 }
    },
    VO2_DECILES: {   // P10 … P90, same table
      male: { 25: [28.6, 35.2, 40.0, 43.6, 46.5, 49.0, 51.9, 54.5, 58.6],
        35: [24.9, 29.8, 33.5, 37.0, 39.7, 43.4, 46.4, 50.0, 55.5],
        45: [22.1, 26.7, 29.7, 32.4, 35.3, 37.9, 40.9, 45.2, 50.8],
        55: [18.6, 22.2, 24.5, 26.9, 29.2, 31.8, 34.3, 38.3, 43.4],
        65: [15.8, 18.5, 20.7, 22.8, 24.6, 26.5, 28.7, 32.0, 37.1],
        75: [13.6, 15.9, 17.3, 19.1, 20.6, 22.2, 23.8, 25.9, 29.4],
        85: [12.9, 14.8, 16.1, 16.6, 17.6, 18.4, 20.0, 21.4, 22.8] },
      female: { 25: [22.5, 27.2, 30.8, 34.0, 36.6, 39.0, 41.8, 44.8, 49.0],
        35: [18.6, 21.9, 24.2, 26.4, 28.3, 31.0, 33.6, 37.0, 42.1],
        45: [17.2, 19.7, 21.8, 23.9, 25.7, 27.7, 30.0, 33.0, 37.8],
        55: [16.5, 18.5, 20.1, 21.5, 22.9, 24.6, 26.3, 28.4, 32.4],
        65: [13.4, 15.4, 17.0, 18.3, 19.6, 20.9, 22.4, 24.3, 27.3],
        75: [12.3, 14.0, 15.2, 16.2, 17.2, 18.3, 19.6, 20.8, 22.8],
        85: [11.4, 12.6, 13.7, 14.7, 15.4, 16.0, 17.3, 18.4, 20.8] }
    },

    // ------------------------------------------------ resting heart rate
    // Jensen MT et al. Heart 2013;99:882 (doi:10.1136/heartjnl-2012-303375): HR 1.16 per 10 bpm, adjusted for VO₂max;
    // shape (flat below 45): Zhang D et al. CMAJ 2016;188:E53.
    RHR_RR_PER_10: 1.16,
    RHR_FLOOR: 45.0,
    RHR_ANCHOR: 65.0,                         // Quer G et al. PLoS One 2020;15:e0227709 (wearable users)

    // ------------------------------------------------ body composition
    // BMI: Global BMI Mortality Collaboration. Lancet 2016;388:776 (doi:10.1016/S0140-6736(16)30175-1), bracket midpoints.
    BMI_KNOTS: [[17.0, 1.51], [19.25, 1.13], [21.25, 1.00], [23.75, 1.00], [26.25, 1.07], [28.75, 1.20], [32.5, 1.45],
      [37.5, 1.94], [45.0, 2.76]],
    BMI_ANCHOR: 25.0,
    // Waist-to-height: Jayedi A et al. BMJ 2020;370:m3324 (doi:10.1136/bmj.m3324): HR 1.24 per 0.1, nadir 0.50.
    WHTR_KNOTS: [[0.50, 1.00], [0.60, 1.24], [0.70, Math.pow(1.24, 2)], [0.80, Math.pow(1.24, 3)]],
    WHTR_ANCHOR: 0.50,
    WEIGHT_MAX_AGE_DAYS: 90,

    // ------------------------------------------------ product texts and record coverage (body_engine insights/healthspan_params.py)
    // Data-quality heuristics (issue #210), design choices, not part of the score: they only label the strength item.
    STRENGTH_COVERAGE_WEEKS: 4,
    STRENGTH_JOURNAL_KEYS: ["training_upper", "training_legs"],
    STRENGTH_JOURNAL_MISSED_MIN: 2,
    STRENGTH_PLAN_MIN_SHARE: 0.5,
    TIPS: {
      sleep_short: "每晚多睡 30–60 分钟",
      sleep_long: "睡太久，固定起床时间",
      sleep_ok: "睡眠时长合适，保持",
      sleep_regularity: ["作息规律，保持", "固定起床时间，误差 ≤ 1 小时"],
      steps: ["步数够了，保持", "每天多走 2000 步"],
      mvpa: ["运动量达标，保持", "每周加 2 次 30 分钟快走或爬坡"],
      strength: ["力量训练规律，保持", "每周力量训练 2 次以上"],
      strength_incomplete: "记录可能不全，先补记力量训练",
      vo2max: ["心肺能力好，保持", "加 Z2 有氧和每周 1 次间歇"],
      rhr: ["静息心率低，保持", "多做有氧、少熬夜"],
      body_comp: ["体重合适，保持", "按计划减脂"],
      body_comp_goal: "按计划减到目标体重 {kg} kg",
      activity: ["活动量充足，保持", "每天多走 2000 步或多练一次"]
    }
  };
  H.override = function (changes) { return U.override(H, changes, "healthspan_params"); };

  // ---------------------------------------------------------------- v1.2: activity domain (issue #118)
  // Steps, exercise and strength training become one domain with one cap; everything else is inherited from v1.1.
  var H12 = {};
  Object.keys(H).forEach(function (k) { if (k !== "override") H12[k] = H[k]; });
  var v12 = {
    ALGO_VERSION: "hs-v1.2-2026-10-23",
    ACTIVITY_MODEL: "domain",
    ACTIVITY_CAP_YEARS: 4.0,                  // [design choice] one cap for the domain = one item's cap in v1.1
    // Ding D et al. Lancet Public Health 2025;10:e668–e681 (doi:10.1016/S2468-2667(25)00164-1), Table 2, all-cause mortality,
    // HR vs 2000 steps/day [literature]. Flat beyond the ends.
    STEPS_KNOTS_UNDER60: [[2000, 1.00], [3000, 0.77], [4000, 0.64], [5000, 0.57], [6000, 0.54], [7000, 0.53], [8000, 0.52],
      [9000, 0.52], [10000, 0.52], [11000, 0.49], [12000, 0.45]],
    STEPS_ANCHOR_UNDER60: 7803,               // median of the under-60 cohorts in Paluch 2022 [literature]
    // Apple Watch vs hip / waist devices: no consistent bias (Breteler 2019 doi:10.2196/11489; Spartano 2024 doi:10.2196/54631).
    WRIST_STEPS_FACTOR: 1.0,
    // CADENCE-Adults (Tudor-Locke 2019 doi:10.1186/s12966-019-0769-6; 2020 doi:10.1186/s12966-020-01045-z): 100 steps/min ≈
    // 3 MET, 130 ≈ 6 MET [literature]; linear between, flat above [design choice]; < 3 MET = light, 0.
    MODERATE_MET: 3.0, VIGOROUS_MET: 6.0,
    CADENCE_MET_KNOTS: [[3.0, 100.0], [6.0, 130.0]],
    // 2024 Adult Compendium of Physical Activities (Herrmann SD et al. J Sport Health Sci 2024;13:6, doi:10.1016/j.jshs.2023.10.010)
    WORKOUT_MET: { swimming: 5.8, pool_swimming: 5.8, open_water_swimming: 5.8, cycling: 7.0, indoor_cycling: 6.8,
      rowing_machine: 5.0 },
    STRENGTH_IN_VOLUME: false,                // [design choice] strength stays on its own curve (Momma 2022)
    STRENGTH_MET: 3.5                         // resistance training [02054], 2024 Compendium
  };
  Object.keys(v12).forEach(function (k) { H12[k] = v12[k]; });
  H12.NONSTEP_TYPES = Object.keys(H12.WORKOUT_MET);   // only these types are converted to step equivalents
  H12.override = function (changes) { return U.override(H12, changes, "healthspan_params_v12"); };
  OBS.healthspan_params_v12 = H12;
  return H;
})(OBS.util);
