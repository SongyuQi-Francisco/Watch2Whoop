"""Water intake parameters (issue #125). Change here, not in the code; bump rules/params.RULES_VERSION when a value that
affects reminders changes.

Default daily goal (drinks only, ml) when the diet profile has no water_ml target:
  NHMRC (Australia / New Zealand) Nutrient Reference Values, "Water" (2006): adequate intake of fluids from beverages
  for adults 19+ = 2.6 L/day for men and 2.1 L/day for women (about 10 and 8 cups; total water including food 3.4 / 2.8 L).
  Unknown sex: the lower value. EFSA's 2010 opinion on water (EFSA Journal 8(3):1459: total water 2.5 / 2.0 L, about
  70–80 % from drinks) lands in the same range.
Extra on a training day: ACSM position stand "Exercise and fluid replacement" (Sawka et al., Med Sci Sports Exerc 2007;
  39(2):377–390): sweat rates of 0.3–2.4 L/h, drink to replace what is lost. A flat +500 ml for a day with a workout of
  at least TRAINING_MIN_MINUTES is a modest default, not a prescription.
Extra on a sauna day: a sauna session costs about 0.5 kg of sweat (Hannuksela & Ellahham, Am J Med 2001;110:118–126);
  the journal item 「蒸桑拿」 already advises 300–500 ml before and after.
Reminder: product rule from the issue (no clinical source): from REMIND_FROM_HOUR local time, if less than REMIND_SHARE
  of the day's goal is in, remind once that day.
"""
FEATURE = "water"                 # key for gradual rollout (feature flags; see water/__init__.enabled)

DEFAULT_GOAL_ML = {"male": 2600, "female": 2100}
DEFAULT_GOAL_UNKNOWN_ML = 2100
TRAINING_EXTRA_ML = 500
TRAINING_MIN_MINUTES = 30
SAUNA_EXTRA_ML = 500
SAUNA_JOURNAL_KEY = "sauna"

QUICK_ML = (250, 500)             # Today page buttons
MAX_ML = 5000                     # one entry (also the database check)
SOURCES = ("ai", "manual", "button", "apple_health")

REMIND_FROM_HOUR = 15
REMIND_SHARE = 0.5
