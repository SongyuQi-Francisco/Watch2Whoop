"""Parameters of 「我的饮食档案」 (assistant/diet_profile.py), with sources. Configurable; nothing here is one person's data."""

# Energy averages shown under 「为什么是这个数」: the last 28 complete days (yesterday back), from daily_metrics
# (basal_energy_burned + active_energy_burned, already resolved to one clean source per day, importers/common.py).
ENERGY_WINDOW_DAYS = 28
# Fewer days with both values than this -> no average (a week of data is too noisy for a maintenance estimate).
ENERGY_MIN_DAYS = 14
# A day whose basal energy is below this is a partial sync (the watch was off / the upload missed hours) and is skipped;
# same floor as training/importer.py uses for "a real basal day".
BASAL_MIN_KCAL = 800

# kcal per kg of body-weight change, the classic "3500 kcal per pound" rule (Wishnofsky M. Am J Clin Nutr 1958;6:542-6).
# It overestimates long-term loss because expenditure adapts (Hall KD et al. Lancet 2011;378:826-37); used here only for a
# first-weeks estimate, labelled as such.
KCAL_PER_KG = 7700

# Weekly loss above this share of body weight is flagged as fast: ACSM position stand recommends 0.5-1 % per week
# (Donnelly JE et al. Med Sci Sports Exerc 2009;41:459-71).
FAST_LOSS_SHARE_PER_WEEK = 0.01

# Goals over the next days (SparkyFitness fallback): the weekly goal plan can switch training / rest-day presets, so the
# average daily target is taken over one week.
GOAL_LOOKAHEAD_DAYS = 7

# Size limits for what the AI writes (the page shows short labels; longer text stays short too).
MAX_ITEMS = 20
MAX_TEXT = 300
