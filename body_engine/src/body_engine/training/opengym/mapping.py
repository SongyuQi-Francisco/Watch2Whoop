"""openGym vocabulary -> the exercise library's (training/catalog_data.py: MUSCLE_ZH, EQUIPMENT_ZH; category enum of exercise_catalog).

Nothing here is guessed: a name that has no clear counterpart is NOT in these tables, and the importer lists it in its report
(`unmapped`). Muscles: a finer muscle maps to the coarser catalog muscle that contains it (rhomboids -> middle back). Equipment that
is not mapped keeps openGym's own lowercase name in `equipment` (so it is still searchable and filterable) until someone decides.
"""

# target (primary muscle) -> catalog muscle. "cardiovascular system" is not a muscle: such entries get no primary muscle and
# category cardio (see CARDIO_TARGET); it is not counted as unmapped.
TARGET = {
    "abs": "abdominals", "glutes": "glutes", "quads": "quadriceps", "delts": "shoulders", "pectorals": "chest", "lats": "lats",
    "biceps": "biceps", "triceps": "triceps", "calves": "calves", "upper back": "middle back", "hamstrings": "hamstrings",
    "spine": "lower back", "forearms": "forearms", "abductors": "abductors", "adductors": "adductors", "traps": "traps",
}
CARDIO_TARGET = "cardiovascular system"

# secondaryMuscles -> catalog muscle
SECONDARY = {
    "core": "abdominals", "abdominals": "abdominals", "obliques": "abdominals", "lower abs": "abdominals",
    "shoulders": "shoulders", "deltoids": "shoulders", "rear deltoids": "shoulders", "rotator cuff": "shoulders",
    "glutes": "glutes", "hamstrings": "hamstrings", "quadriceps": "quadriceps", "triceps": "triceps", "biceps": "biceps",
    "brachialis": "biceps", "forearms": "forearms", "grip muscles": "forearms", "wrist flexors": "forearms",
    "wrist extensors": "forearms", "wrists": "forearms", "upper back": "middle back", "rhomboids": "middle back",
    "lower back": "lower back", "calves": "calves", "soleus": "calves", "chest": "chest", "upper chest": "chest",
    "adductors": "adductors", "inner thighs": "adductors", "groin": "adductors", "lats": "lats", "latissimus dorsi": "lats",
    "traps": "traps", "trapezius": "traps", "sternocleidomastoid": "neck",
}

# equipment -> catalog equipment key (EQUIPMENT_ZH keys, plus the two the free-exercise-db converter already emits)
EQUIPMENT = {
    "body weight": "body only", "dumbbell": "dumbbell", "cable": "cable", "barbell": "barbell", "olympic barbell": "barbell",
    "leverage machine": "machine", "sled machine": "machine", "trap bar": "barbell",   # a trap (hex) bar is loaded like a barbell
    "band": "bands", "resistance band": "bands",
    "kettlebell": "kettlebell", "smith machine": "smith machine", "ez barbell": "ez curl bar", "roller": "foam roll",
    "medicine ball": "medicine ball", "stability ball": "exercise ball",
}

# openGym category -> exercise_catalog.category (strength / stretching / cardio / mobility). Decisions, not guesses:
# only cardio / stretching / mobility have their own treatment downstream (not counted as strength sets), so the types that
# are loaded or held movements stay strength (rehab included: external-rotation work is strength work), yoga poses are mobility.
# The original category is kept in text_sources, so a later split into more categories loses nothing.
CATEGORY = {
    "strength": "strength", "calisthenics": "strength", "plyometrics": "strength", "isometric": "strength",
    "olympic": "strength", "pilates": "strength", "rehab": "strength", "combat": "strength",
    "stretching": "stretching", "mobility": "mobility", "yoga": "mobility", "cardio": "cardio",
}
# held (timed) work: logged as duration instead of reps
DURATION_CATEGORIES = {"isometric", "yoga", "cardio", "stretching", "mobility"}

# Words an openGym name starts with for its equipment ("cable lat pulldown"). Used only to find the library's own name for the same
# exercise ("lat pulldown" on a cable machine): the stripped name must equal an existing name AND the row's equipment must match.
NAME_PREFIX = {
    "barbell": ("barbell",), "olympic barbell": ("barbell", "olympic"), "dumbbell": ("dumbbell",), "cable": ("cable",),
    "leverage machine": ("lever", "leverage", "machine"), "sled machine": ("sled",), "band": ("band",),
    "resistance band": ("band", "resistance band"), "ez barbell": ("ez barbell", "ez bar", "ez-bar"), "smith machine": ("smith",),
    "kettlebell": ("kettlebell",), "trap bar": ("trap bar",),
}

LICENSES = {"exercisedb": ("exercisedb", "MIT"), "opengym": ("opengym", "AGPL-3.0")}   # textSource -> (catalog source, licence)
IMPORTED_SOURCES = tuple(src for src, _ in LICENSES.values())   # catalog sources of the rows the import creates
SMALL_WORDS = {"a", "an", "and", "of", "on", "in", "the", "to", "with", "for", "at", "by", "or", "vs"}
