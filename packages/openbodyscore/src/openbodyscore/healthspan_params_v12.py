"""Body-age parameters, version 1.2: steps, exercise and strength training become one "activity" domain (issue #118).
Everything not set here is inherited unchanged from healthspan_params (v1.1): sleep, regularity, VO₂max, resting heart
rate, body composition, the window, the 8-year doubling time, × 0.6 and the ±10-year total cap.
Sources checked against the full texts / tables on 2026-10-23. Labels as in docs/scoring-methodology.md 0.1:
[literature] / [design choice] / [inferred] (a number derived from a paper, not printed in it).

Why (v1.1 → v1.2):
1. v1.1 scored steps, MVPA and strength as three items, each capped at ±4 years, and relied on × 0.6 to undo the overlap.
   Lee 2022 counts walking and weightlifting inside its MVPA, and steps are mostly walking, so the same activity was counted
   up to three times. v1.2 scores one hazard ratio for the domain and caps it once.
2. The under-60 step curve (Paluch 2022 quartiles) falls from HR 1.00 to 0.59 between 4849 and 7245 steps — the least
   precise segment (Q2 CI 0.39–0.88) and the one most open to reverse causation (people who walk little include the ill).
   The larger 2025 meta-analysis is almost flat between 5000 and 10000 steps; v1.2 uses it for adults under 60.
3. Strength training, swimming, cycling and rowing give the wrist few steps; v1.2 converts the non-walking aerobic ones to
   step equivalents and keeps strength on its own curve.
"""
import sys

from . import _override
from .healthspan_params import *  # noqa: F401,F403  (v1.1 values for everything not set below)

ALGO_VERSION = "hs-v1.2-2026-10-23"   # v1.2: activity domain (steps + non-step exercise + strength, one cap)
ACTIVITY_MODEL = "domain"              # healthspan_store: "items" (v1.1, default when absent) or "domain"

# ---------------------------------------------------------------- domain cap [design choice]
# One cap for the whole domain = what one item could reach in v1.1, so activity can no longer add up to ±12 years before × 0.6.
ACTIVITY_CAP_YEARS = 4.0

# ---------------------------------------------------------------- steps, adults under 60 [literature]
# Ding D et al. Daily steps and health outcomes in adults: a systematic review and dose-response meta-analysis.
# Lancet Public Health 2025;10:e668–e681 (doi:10.1016/S2468-2667(25)00164-1), Table 2, all-cause mortality (14 studies,
# 161,176 people), HR vs 2000 steps/day: 3000 0.77, 4000 0.64, 5000 0.57, 6000 0.54, 7000 0.53 (0.46–0.60), 8000 0.52,
# 9000 0.52, 10000 0.52 (0.45–0.59), 11000 0.49, 12000 0.45 (0.39–0.53). Inflection ~5400 steps (also in the younger
# subgroup); the curve was "very similar" when studies without adequate confounder adjustment were excluded.
# Flat beyond the table's ends (no extrapolation), as for every curve here.
# Reverse causation: Hansen BH et al. Scand J Med Sci Sports 2020;30:1705 (doi:10.1111/sms.13726), waist accelerometer,
# quartiles 4651 / 6862 / 8670 / 11467 steps: HR 0.45 / 0.49 / 0.42 vs Q1, 0.52 / 0.50 / 0.43 after excluding the first
# 2 years — the lowest segment shrinks most. Lee IM et al. JAMA Intern Med 2019;179:1105 (doi:10.1001/jamainternmed.2019.0899):
# Q4 0.42 → 0.50 after excluding year 1 and baseline CVD / cancer / diabetes. Ding's flatter curve already sits where
# these corrected estimates point; the ±20% slope sensitivity covers the rest.
STEPS_KNOTS_UNDER60 = [(2000, 1.00), (3000, 0.77), (4000, 0.64), (5000, 0.57), (6000, 0.54), (7000, 0.53), (8000, 0.52),
                       (9000, 0.52), (10000, 0.52), (11000, 0.49), (12000, 0.45)]
# Anchor = median of the under-60 cohorts in Paluch 2022 (text: 7803, IQR 5377–10352) [literature]; v1.1 used 8078, the
# midpoint of the Q2 / Q3 medians.
STEPS_ANCHOR_UNDER60 = 7803
# Adults 60+: Paluch 2022's 60+ curve and anchor stay as in v1.1 (STEPS_KNOTS_60PLUS, STEPS_ANCHOR_60PLUS). Ding reports a
# linear curve in its older subgroup (HRs only in the appendix, not checked), so the 60+ curve is not flattened.

# ---------------------------------------------------------------- wrist steps [literature, no correction]
# The step literature uses waist / hip devices; the Apple Watch counts at the wrist. A raw ActiGraph moved from waist to wrist
# counts ~2500 more steps/day (Tudor-Locke C et al. Med Sci Sports Exerc 2015;47:839, doi:10.1249/MSS.0000000000000476),
# but that is one device's raw signal, not the Apple Watch's algorithm. Apple Watch vs hip / waist research devices in free
# living: about 4–5% fewer over 72 h (Breteler MJM et al. JMIR Form Res 2019, doi:10.2196/11489, n = 30);
# medians 7060 vs 7064 steps/day (Spartano NL et al. JMIR Biomed Eng 2024, doi:10.2196/54631, n = 523), with
# wide day-to-day limits of agreement. No consistent bias → factor 1.0 (the sensitivity analysis varies it ±20%).
WRIST_STEPS_FACTOR = 1.0

# ---------------------------------------------------------------- exercise without steps → step equivalents
# Minutes × the steps a walk / run of the same MET takes. CADENCE-Adults (Tudor-Locke C et al. Int J Behav Nutr Phys Act
# 2019;16:8, doi:10.1186/s12966-019-0769-6, ages 21–40; 2020;17:137, doi:10.1186/s12966-020-01045-z, ages 41–60):
# 100 steps/min ≈ 3 MET (moderate), 130 steps/min ≈ 6 MET (vigorous) [literature]; linear in between and flat above 6 MET
# [design choice — no extra credit for intensity beyond the step count, which is what Lee 2019 and Saint-Maurice 2020 found
# for cadence once steps are adjusted for]. Below 3 MET (yoga, pilates, stretching) = light activity, 0.
# Tudor-Locke C et al. How many steps/day are enough? for adults. IJBNPA 2011;8:79 (doi:10.1186/1479-5868-8-79): 30 min of
# MVPA ≈ 3000–4000 steps, the same 100 steps / moderate minute.
MODERATE_MET, VIGOROUS_MET = 3.0, 6.0
CADENCE_MET_KNOTS = [(3.0, 100.0), (6.0, 130.0)]   # (MET, steps per minute)
# MET per workout type: 2024 Adult Compendium of Physical Activities (Herrmann SD et al. J Sport Health Sci 2024;13:6,
# doi:10.1016/j.jshs.2023.10.010; pacompendium.com codes in brackets), the general / moderate entry of each [literature]:
# swimming laps freestyle, slow, recreational 5.8 [18240]; bicycling, general 7.0 [01014]; stationary bicycling, general
# 6.8 [01200]; rowing ergometer, < 100 W, moderate 5.0 [02071].
WORKOUT_MET = {"swimming": 5.8, "pool_swimming": 5.8, "open_water_swimming": 5.8, "cycling": 7.0, "indoor_cycling": 6.8,
               "rowing_machine": 5.0}
# Only these types are converted [design choice]: the wrist barely counts steps for them. Walking, running, hiking, stairs,
# basketball, tennis, football, dance, jump rope, elliptical (arms move with the handles) already show up as steps; adding
# them would count them twice. Unrecorded exercise is not converted (record a workout).
NONSTEP_TYPES = frozenset(WORKOUT_MET)

# ---------------------------------------------------------------- strength training inside the domain
# Momma 2022's curve (STRENGTH_KNOTS, unchanged from v1.1) multiplies the volume hazard ratio. Its included studies all
# considered aerobic or other activity, so the muscle-strengthening association is the part beyond aerobic activity
# [literature; GRADE "very low", heterogeneous]; combined with aerobic activity it was RR 0.60 (0.54–0.67) vs neither.
# Step studies are not adjusted for strength training, so multiplying the two is an approximation [design choice].
# Strength minutes are NOT also converted to step equivalents [design choice]: Momma's association already includes what the
# sessions themselves burn, so doing both would count strength twice. Lee 2022 instead puts weightlifting inside moderate
# activity; True gives that alternative (shown in the comparison report, not the default).
STRENGTH_IN_VOLUME = False
STRENGTH_MET = 3.5   # resistance (weight) training, multiple exercises, 8–15 reps at varied resistance [02054], 2024 Compendium

# ---------------------------------------------------------------- MVPA (reference only, not counted)
# The heart-rate MVPA of v1.1 (Z2–Z3 + 2 × Z4–Z5, ≥ 60% HRmax) reads low for strength training: at a given %HRmax
# resistance exercise uses less oxygen than running (Collins MA et al. Med Sci Sports Exerc 1991;23:636,
# doi:10.1249/00005768-199105000-00018), and much of a session (rests) sits below 60% HRmax. Questionnaires such as Lee 2022
# class activities by absolute MET (weightlifting = moderate, < 6 MET). The reference part therefore counts strength and
# non-step workouts at their MET (moderate × 1, ≥ 6 MET × 2) and everything else by heart rate. Apple's exercise minutes are
# not used: their ring starts at about 30% VO₂ reserve, below the 40% moderate threshold (Abt G et al. JMIR Cardio
# 2018;2:e6, doi:10.2196/cardio.8574), so they overestimate.


def override(**changes):
    """These parameters with some replaced (healthspan_params.override for v1.1)."""
    return _override.override(sys.modules[__name__], changes)
