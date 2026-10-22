"""Parameters of the lab trend view (status card spark / trend, the trend page, get_lab_history), with sources.

GOOD_DIRECTION: which way a change counts as better ("down" = lower is better, "up" = higher is better).
Tests not listed are "in range is what matters" (e.g. sodium, potassium, haemoglobin): their trend arrow is shown neutral.
"""

GOOD_DIRECTION: dict[str, str] = {
    # Uric acid: ACR 2020 gout guideline treats to a serum urate target < 6 mg/dL (≈ 360 μmol/L); lower is better within that range.
    "uric_acid": "down",
    # Liver enzymes: raised ALT / AST / GGT reflect liver injury (fatty liver here); falling toward the range = improving
    # (AASLD 2023 MASLD practice guidance; ACG 2017 clinical guideline on abnormal liver chemistries).
    "alt": "down", "ast": "down", "ggt": "down",
    # Lipids: ESC/EAS 2019 dyslipidaemia guidelines - LDL-C / non-HDL-C "lower is better", TG < 1.7 mmol/L desirable;
    # HDL-C is inversely related to cardiovascular risk (same guideline), so higher is better.
    "triglycerides": "down", "total_cholesterol": "down", "ldl": "down", "non_hdl": "down", "chol_hdl_ratio": "down",
    "hdl": "up",
    # Glucose metabolism: ADA Standards of Care 2025 (fasting glucose, HbA1c, insulin resistance) - lower is better above normal.
    "glucose_fasting": "down", "hba1c": "down", "insulin_fasting": "down",
    # Kidney: KDIGO 2012 CKD guideline - eGFR higher is better; serum creatinine moves the opposite way.
    # Note: creatine supplementation raises creatinine without kidney harm (the owner takes 5 g/day, CLAUDE.md).
    "creatinine": "down", "egfr": "up",
    # Inflammation: AHA/CDC 2003 statement on hs-CRP - lower is better.
    "crp": "down",
    # Vitamin D: Endocrine Society 2011 - deficiency < 50 nmol/L, higher is better up to the sufficient range.
    "vitamin_d": "up",
    # Body measurements / blood pressure: the owner's goal is fat loss; ACC/AHA 2017 BP guideline - lower is better above 120/80.
    "body_weight": "down", "bmi": "down", "blood_pressure_systolic": "down", "blood_pressure_diastolic": "down",
}

# Trend of the last two readings: a change smaller than this share of the previous value counts as flat.
# 1% only absorbs rounding (e.g. 2.05 vs 2.04); it is not a claim about measurement error.
TREND_FLAT_FRACTION = 0.01

# Status card spark: at most this many readings (old -> new). Lab tests are months apart, 12 covers several years.
SPARK_POINTS = 12

# Status card tone when above the upper limit: more than this share above it is "bad", otherwise "warn"
# (same split as the old status card; e.g. uric acid 520 vs 420 = 24% -> bad, 480 = 14% -> warn).
HIGH_BAD_FRACTION = 0.20

SOURCES = ("lab", "home_device")
SOURCE_LABEL = {"lab": "化验", "home_device": "家测"}
