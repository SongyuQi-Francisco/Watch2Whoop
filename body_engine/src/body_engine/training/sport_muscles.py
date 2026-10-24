"""Which muscles a workout type trains (muscle load map, sports layer; issue: every workout must credit some muscle).

SPORT_MUSCLES: type -> (primary muscles, secondary muscles, RESISTANCE). The muscle keys are the catalog's (catalog_data.MUSCLE_ZH).
RESISTANCE is the share of the workout's minutes that load its primary muscles like resistance training does, on a 0..1 scale where
lifting = 1 (see params.SPORT_SETS_PER_MIN and muscle_load.sport_load for the formula). It is a coarse rating of the movement pattern,
not a measurement; change it when better data is at hand.

Coverage rule (tests/test_muscle_coverage.py, `body-engine muscle-coverage-check`): every type the watch / Apple Health / Open Wearables /
the Shortcut / SparkyFitness / Hevy can produce has an entry here, and `resolve()` NEVER returns an empty mapping: a type that is not
listed (or "other" / "generic" / "unknown") gets FALLBACK, a whole-body low-intensity mapping.

Basis per group (the first block of cited works is from issue #189; entries without a citation are rated from the movement itself):
- running / treadmill: stance-phase work of the hip and knee extensors and the plantar flexors, hamstrings decelerate the swing
  (Novacheck 1998, Gait Posture 7:77, "The biomechanics of running").
- walking / hiking / stair climbing (incline walking is recorded as walking): glutes, hamstrings, quadriceps and calves, their
  activation rising with the grade (Franz & Kram 2012, Gait Posture 36:381, grade and speed vs leg muscle activation).
- cycling / indoor cycling / elliptical: quadriceps and glutes produce most of the power, calves transfer it, hamstrings help
  in the upstroke (Hug & Dorel 2009, J Electromyogr Kinesiol 19:182, EMG of pedalling: a review).
- rowing machine: legs drive (quadriceps, glutes), then the back and arms pull (Turpin et al. 2011, Eur J Appl Physiol 111:3017,
  muscle coordination during rowing).
- swimming: propulsion mainly from the latissimus dorsi, shoulders and triceps, the legs and trunk stabilise and kick
  (Martens, Figueiredo & Daly 2015, J Electromyogr Kinesiol 25:273, EMG in the four competitive strokes: a systematic review).
- basketball / soccer / volleyball / court and field sports: repeated sprints, jumps and changes of direction load the lower body
  (Stojanovic et al. 2018, Sports Med 48:111, activity demands of basketball match-play: a systematic review).
- tennis / badminton / squash / pickleball / table tennis: footwork as above plus the hitting arm and trunk rotation
  (Kovacs 2007, Sports Med 37:189, tennis physiology).
- boxing / martial arts: punches from the shoulders and triceps driven by the legs and trunk; climbing: grip and pulling
  (forearms, lats, biceps); jump rope: plantar flexors.
- Strength training with no logged sets (a watch workout only): the whole-body mapping; muscle_load spreads the estimated working sets
  over the day's planned muscles, else over this mapping (params.STRENGTH_* constants).
- Everything else (skiing, skating, paddling, dance, yoga, golf, ...): the muscles that move the body in that pattern, rated from the
  movement. Light activities (golf, bowling, fishing, gaming, stretching) get a small RESISTANCE so they register as weak, not as zero.
"""
from __future__ import annotations

_ALL_BIG = ("chest", "lats", "middle back", "shoulders", "quadriceps", "glutes", "hamstrings")
_ALL_REST = ("biceps", "triceps", "abdominals", "lower back", "calves", "forearms", "traps")

_RUN = (("quadriceps", "glutes", "calves"), ("hamstrings", "abdominals"), 0.30)
_CYCLE = (("quadriceps", "glutes"), ("calves", "hamstrings", "abdominals"), 0.25)
_SWIM = (("lats", "shoulders", "triceps"), ("chest", "abdominals", "quadriceps", "glutes"), 0.30)
_PADDLE = (("lats", "shoulders", "middle back"), ("triceps", "biceps", "abdominals", "forearms"), 0.30)
_COURT = (("quadriceps", "glutes", "calves"), ("shoulders", "forearms", "abdominals"), 0.25)
_TEAM = (("quadriceps", "glutes", "calves"), ("hamstrings", "adductors", "abdominals", "shoulders"), 0.28)
_STRENGTH = (_ALL_BIG, _ALL_REST, 1.0)
_CARDIO_MIX = (("quadriceps", "glutes", "abdominals"), ("calves", "hamstrings", "shoulders", "chest", "lats"), 0.28)
_SNOW = (("quadriceps", "glutes", "hamstrings"), ("abdominals", "calves", "adductors", "abductors", "lower back"), 0.30)
_SKATE = (("quadriceps", "glutes", "abductors"), ("adductors", "hamstrings", "calves", "abdominals"), 0.25)
_FIGHT = (("shoulders", "triceps", "quadriceps"), ("chest", "abdominals", "glutes", "calves", "lats"), 0.30)
_YOGA = (("abdominals", "quadriceps", "glutes"), ("shoulders", "lower back", "hamstrings", "triceps"), 0.15)
_STRETCH = (("hamstrings", "quadriceps", "lower back"), ("glutes", "calves", "shoulders"), 0.05)
_DANCE = (("quadriceps", "glutes", "calves"), ("hamstrings", "abdominals", "adductors"), 0.22)
_LIGHT = (("forearms", "abdominals", "quadriceps"), ("shoulders", "lower back", "glutes"), 0.06)

SPORT_MUSCLES: dict[str, tuple[tuple[str, ...], tuple[str, ...], float]] = {
    # --- walking / running / stairs
    "running": _RUN, "treadmill": _RUN, "trail_running": _RUN, "track_and_field": (("quadriceps", "glutes", "calves", "hamstrings"), ("abdominals",), 0.32),
    "walking": (("glutes", "calves"), ("quadriceps", "hamstrings"), 0.15),
    "hiking": (("glutes", "quadriceps", "calves"), ("hamstrings", "lower back"), 0.25),
    "stair_climbing": (("quadriceps", "glutes"), ("calves", "hamstrings"), 0.35),
    "stairs": (("quadriceps", "glutes"), ("calves", "hamstrings"), 0.35),
    "step_training": (("quadriceps", "glutes"), ("calves", "hamstrings", "abdominals"), 0.30),
    "snowshoeing": (("quadriceps", "glutes", "calves"), ("hamstrings", "adductors", "abductors"), 0.28),
    # --- wheels
    "cycling": _CYCLE, "indoor_cycling": (*_CYCLE[:2], 0.30), "mountain_biking": (*_CYCLE[:2], 0.30), "e_biking": (*_CYCLE[:2], 0.15),
    "hand_cycling": (("shoulders", "triceps", "biceps"), ("lats", "chest", "abdominals"), 0.25),
    "wheelchair_walk": (("lats", "shoulders", "triceps"), ("biceps", "forearms", "chest"), 0.20),
    "wheelchair_run": (("lats", "shoulders", "triceps"), ("biceps", "forearms", "chest"), 0.30),
    "wheelchair": (("lats", "shoulders", "triceps"), ("biceps", "forearms", "chest"), 0.20),
    "elliptical": (("quadriceps", "glutes"), ("hamstrings", "calves", "shoulders"), 0.20),
    "rowing_machine": (("quadriceps", "glutes", "lats", "middle back"), ("hamstrings", "biceps", "lower back", "shoulders"), 0.45),
    "rowing": (("quadriceps", "glutes", "lats", "middle back"), ("hamstrings", "biceps", "lower back", "shoulders"), 0.45),
    "jump_rope": (("calves",), ("quadriceps", "shoulders", "forearms"), 0.30),
    "skateboarding": _SKATE, "inline_skating": _SKATE,
    # --- water
    "swimming": _SWIM, "pool_swimming": _SWIM, "open_water_swimming": _SWIM,
    "water_fitness": (("quadriceps", "glutes", "shoulders"), ("abdominals", "hamstrings", "chest"), 0.25),
    "water_polo": (("shoulders", "quadriceps", "lats"), ("triceps", "abdominals", "glutes"), 0.30),
    "paddling": _PADDLE, "paddle_sports": _PADDLE, "kayaking": _PADDLE, "canoeing": _PADDLE, "water_sports": _PADDLE,
    "sailing": (("forearms", "abdominals", "shoulders"), ("lats", "lower back", "quadriceps"), 0.10),
    "surfing": (("lats", "shoulders", "triceps"), ("chest", "abdominals", "quadriceps", "lower back"), 0.28),
    "diving": (("quadriceps", "glutes", "calves"), ("abdominals", "shoulders"), 0.10),
    "underwater_diving": (("quadriceps", "glutes", "calves"), ("abdominals", "shoulders"), 0.10),
    "triathlon": (("quadriceps", "glutes", "lats", "shoulders"), ("calves", "hamstrings", "triceps", "abdominals"), 0.28),
    "swim_bike_run": (("quadriceps", "glutes", "lats", "shoulders"), ("calves", "hamstrings", "triceps", "abdominals"), 0.28),
    "transition": (("quadriceps", "glutes"), ("calves", "hamstrings"), 0.05),
    # --- ball, court and field sports
    "basketball": _TEAM, "volleyball": (("quadriceps", "glutes", "calves"), ("shoulders", "abdominals"), 0.28),
    "soccer": (("quadriceps", "hamstrings", "glutes", "calves"), ("adductors", "abductors", "abdominals"), 0.28),
    "football": (("quadriceps", "hamstrings", "glutes", "calves"), ("adductors", "abdominals", "shoulders"), 0.28),
    "australian_football": (("quadriceps", "hamstrings", "glutes", "calves"), ("adductors", "abdominals", "shoulders"), 0.28),
    "american_football": (("quadriceps", "glutes", "hamstrings"), ("calves", "shoulders", "chest", "traps", "abdominals"), 0.30),
    "rugby": (("quadriceps", "glutes", "hamstrings"), ("calves", "shoulders", "traps", "abdominals"), 0.30),
    "handball": (("quadriceps", "glutes", "shoulders"), ("calves", "triceps", "abdominals", "hamstrings"), 0.28),
    "lacrosse": (("quadriceps", "glutes", "calves"), ("hamstrings", "shoulders", "forearms", "abdominals"), 0.28),
    "hockey": (("quadriceps", "glutes", "abductors"), ("adductors", "hamstrings", "abdominals", "forearms"), 0.26),
    "ice_skating": _SKATE, "skating": _SKATE,
    "disc_sports": (("quadriceps", "glutes", "calves"), ("shoulders", "forearms", "abdominals"), 0.18),
    "baseball": (("abdominals", "shoulders", "forearms"), ("quadriceps", "glutes", "lower back"), 0.12),
    "softball": (("abdominals", "shoulders", "forearms"), ("quadriceps", "glutes", "lower back"), 0.12),
    "cricket": (("shoulders", "abdominals", "quadriceps"), ("forearms", "glutes", "calves"), 0.12),
    "tennis": _COURT, "badminton": _COURT, "squash": _COURT, "racquetball": _COURT,
    "pickleball": (("quadriceps", "calves"), ("glutes", "shoulders", "forearms"), 0.18),
    "table_tennis": (("calves",), ("quadriceps", "shoulders", "forearms"), 0.12),
    "golf": (("abdominals", "shoulders", "forearms"), ("glutes", "lower back", "quadriceps"), 0.08),
    "bowling": _LIGHT, "archery": (("shoulders", "traps", "middle back"), ("forearms", "biceps", "abdominals"), 0.10),
    "fencing": (("quadriceps", "glutes", "calves"), ("shoulders", "forearms", "abdominals"), 0.25),
    "curling": (("quadriceps", "abdominals", "glutes"), ("shoulders", "forearms"), 0.10),
    "horseback_riding": (("adductors", "abdominals", "glutes"), ("quadriceps", "lower back", "forearms"), 0.12),
    "equestrian": (("adductors", "abdominals", "glutes"), ("quadriceps", "lower back", "forearms"), 0.12),
    "fishing": _LIGHT, "hunting": (("quadriceps", "glutes", "calves"), ("lower back", "shoulders", "forearms"), 0.10),
    # --- snow
    "skiing": _SNOW, "alpine_skiing": _SNOW, "snowboarding": _SNOW, "snow_sports": _SNOW, "downhill_skiing": _SNOW,
    "cross_country_skiing": (("quadriceps", "glutes", "lats", "triceps"), ("hamstrings", "calves", "shoulders", "abdominals"), 0.35),
    # --- combat, climbing, gymnastics
    "boxing": (("shoulders", "triceps"), ("chest", "abdominals", "calves", "quadriceps"), 0.30), "kickboxing": _FIGHT,
    "martial_arts": (("quadriceps", "glutes"), ("shoulders", "abdominals", "hamstrings"), 0.30), "wrestling": _FIGHT,
    "tai_chi": (("quadriceps", "glutes", "calves"), ("abdominals", "shoulders"), 0.12),
    "climbing": (("forearms", "lats", "biceps"), ("middle back", "shoulders", "abdominals"), 0.40),
    "rock_climbing": (("forearms", "lats", "biceps"), ("middle back", "shoulders", "abdominals"), 0.40),
    "gymnastics": (("shoulders", "abdominals", "chest", "lats"), ("triceps", "quadriceps", "forearms"), 0.40),
    # --- gym and mixed training
    "strength_training": _STRENGTH, "fitness_equipment": _STRENGTH, "functional_strength_training": _STRENGTH,
    "weightlifting": _STRENGTH, "crossfit": _STRENGTH, "calisthenics": _STRENGTH, "bodyweight": _STRENGTH,
    "core_training": (("abdominals",), ("lower back", "glutes", "shoulders"), 0.60),
    "cardio_training": _CARDIO_MIX, "mixed_cardio": _CARDIO_MIX, "cross_training": _CARDIO_MIX, "aerobics": _CARDIO_MIX,
    "mixed_metabolic_cardio_training": _CARDIO_MIX, "barre": (("quadriceps", "glutes", "abdominals"), ("calves", "shoulders", "adductors"), 0.25),
    "hiit": (*_CARDIO_MIX[:2], 0.40),
    "dance": _DANCE, "social_dance": _DANCE, "cardio_dance": _DANCE, "dance_inspired_training": _DANCE,
    "yoga": _YOGA, "mind_and_body": _YOGA, "pilates": (("abdominals", "glutes"), ("lower back", "adductors", "quadriceps", "shoulders"), 0.25),
    "stretching": _STRETCH, "flexibility": _STRETCH, "cooldown": _STRETCH, "preparation_and_recovery": _STRETCH,
    "play": (("quadriceps", "glutes", "calves"), ("abdominals", "shoulders"), 0.10),
    "gaming": (("forearms",), ("shoulders", "neck"), 0.03), "fitness_gaming": (("quadriceps", "shoulders"), ("glutes", "abdominals", "forearms"), 0.10),
}

# Unlisted types and the unknown ones ("other" / "generic" / "unknown"): the whole body at a low rating. Large muscle groups as
# primary (so the map shows something even for a one-hour unknown workout), everything else secondary.
FALLBACK: tuple[tuple[str, ...], tuple[str, ...], float] = (
    ("quadriceps", "glutes", "lats", "chest", "abdominals"),
    ("shoulders", "middle back", "lower back", "traps", "hamstrings", "calves", "biceps", "triceps", "forearms", "adductors", "abductors", "neck"),
    0.10,
)
FALLBACK_TYPES = {"other", "generic", "unknown"}

# Types where the watch cannot say which muscles: the estimated working sets are spread over the planned / whole-body muscles
STRENGTH_LIKE = {"strength_training", "fitness_equipment", "functional_strength_training", "weightlifting", "crossfit", "calisthenics", "bodyweight"}


def normalize(t: str | None) -> str:
    """'Traditional Strength Training' / 'cross-training' -> 'traditional_strength_training' / 'cross_training'; the Shortcut's labels
    and the Swift names are mapped to the unified type through params.SHORTCUT_TYPES."""
    from . import params as P   # params imports this module for its constants
    key = (t or "").strip().lower().replace("-", " ")
    if key in P.SHORTCUT_TYPES:
        return P.SHORTCUT_TYPES[key]
    return "_".join(key.split()) or "unknown"


def resolve(t: str | None) -> dict:
    """Mapping of a workout type, never empty: {"type", "primary", "secondary", "resistance", "known"}.
    known=False means the fallback was used (type not listed or the source did not know the activity)."""
    key = normalize(t)
    row = None if key in FALLBACK_TYPES else SPORT_MUSCLES.get(key)
    prim, sec, res = row or FALLBACK
    return {"type": key, "primary": prim, "secondary": tuple(m for m in sec if m not in prim), "resistance": res, "known": row is not None}


def all_known_types() -> set[str]:
    """Every workout type name our parsers can produce (the coverage test and the check command run each one through resolve())."""
    from . import params as P
    hk = {P.hk_workout_type(a) for a in P.HK_WORKOUT_ACTIVITIES}   # every HKWorkoutActivityType, through the Open Wearables mapping
    return (set(P.TYPE_NAMES) | set(P.OW_SDK_WORKOUT_TYPES.values()) | set(P.SHORTCUT_TYPES.values()) | hk
            | set(P.STRENGTH_TYPES) | set(P.CARDIO_TYPES) | set(P.SPORT_TYPES) | set(SPORT_MUSCLES) | FALLBACK_TYPES)
