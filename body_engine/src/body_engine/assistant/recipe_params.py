"""Health-constraint thresholds and recommendation parameters for recipes (used by recipes.py). Changing a threshold needs no migration, it is recomputed on read; writes also refresh the stored constraint_flags.

Sources: health constraints in CLAUDE.md / body-management.md (uric acid 550, fatty liver, ALT, triglycerides -> low saturated fat, low-to-medium purine;
red meat <= 250g/week, beef short rib <= 1 per week, see rules/params.py). Purine grams are estimated from common food purine tables (mg per 100g) and are only a grading reference.
"""
# Purine per serving (mg): < LOW is low, <= HIGH is medium, > HIGH is high. Gout diets often use "<= 400mg per day"; a 180g serving of chicken is itself about 230mg,
# so an ordinary meat dish counts as "medium" and only above 350mg (big portion of meat + mushrooms / legumes / rich soup stacked) counts as "high".
PURINE_LOW_MG = 150
PURINE_HIGH_MG = 350
# Saturated fat per serving (g): <= LOW is low, <= HIGH is medium, > HIGH is high (daily saturated fat < 7-10% of total calories, about 18-25g; no more than 7g in one meal)
SAT_FAT_LOW_G = 4
SAT_FAT_HIGH_G = 7
# automatic tags
HIGH_PROTEIN_G = 30            # Protein per serving >= 30g -> high protein
QUICK_MIN = 20                 # Prep + cook <= 20 minutes -> quick

# recommendations
STAPLE = {"food": "糙白混合米（生）", "grams": 90, "kcal": 320, "protein_g": 7, "carbs_g": 69, "fat_g": 1.6}  # body-management.md 5.3.0
SLOT_ORDER = ["breakfast", "lunch", "dinner"]          # Order of main meals in a day
# Usual share of the day's target for each meal (inferred: the common 25 / 35 / 35 + snack 5-10). When earlier main meals are not logged, they are assumed eaten in this proportion,
# and what is left is shared by proportion between this meal and the main meals not yet eaten; a snack gets at most SLOT_SHARE["snack"] of the day.
SLOT_SHARE = {"breakfast": 0.25, "lunch": 0.35, "dinner": 0.35, "snack": 0.10}
RECENT_DAYS = 2                # Dishes eaten in the last 2 days get lower priority (variety)
SUGGEST_COUNT = 3
# Scoring weights (larger = more important)
W_KCAL = 1.0                   # Share by which calories deviate from this meal's target
W_PROTEIN = 0.8                # Share by which protein falls short of this meal's target
W_PURINE = {"low": 0, "medium": 0.15, "high": 0.6}
W_SAT_FAT = {"low": 0, "medium": 0.1, "high": 0.4}
W_RECENT = 0.5
BONUS_FAVORITE = 0.15
BONUS_CAN_MAKE = 0.3
