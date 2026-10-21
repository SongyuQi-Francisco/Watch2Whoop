"""Thresholds for the Trends tab analyses (docs/wearable-gap-analysis.md). First-version values; each says where it comes from.
All of these are derived views over data already in daily_scores / daily_metrics / stress_daily: nothing here changes the recovery or strain scores."""

TREND_VERSION = "trends-1"

RANGES = (7, 30, 90, 180, 365)        # Trends tab ranges in days (WHOOP 1W / 1M / 6M plus 3M and 1Y)
BASELINE_DAYS = 60                    # personal baseline window, same as the scores module
MIN_BASELINE_DAYS = 14

# ---- HRV status (Garmin "HRV Status"): 7-day mean of ln HRV against the 60-day personal band
HRV_STATUS_WINDOW = 7
HRV_STATUS_MIN_DAYS = 3               # need at least 3 nights in the 7-day window
HRV_BAND_SD = 1.0                     # band = baseline mean ± 1 SD of the daily values (Garmin shows a "balanced" band of similar width)
HRV_POOR_SD = 2.0                     # 7-day mean below mean − 2 SD = "很低"

# ---- training status (Garmin Training Status / Polar cardio load status / Apple Training Load 7 vs 28 days)
ACUTE_DAYS, CHRONIC_DAYS = 7, 28      # acute:chronic workload ratio windows (Gabbett 2016, rolling averages)
ACWR_HIGH = 1.5                       # > 1.5: injury / overreaching risk rises sharply (Gabbett 2016)
ACWR_LOW = 0.8                        # < 0.8: load below what the body is used to ("sweet spot" 0.8–1.3)
DETRAIN_ACWR = 0.6                    # far below usual load ...
DETRAIN_CTL_DROP = 0.85               # ... and CTL fell to < 85% of 28 days ago = detraining
VO2_SLOPE_DAYS = 60                   # VO₂max trend window
VO2_SLOPE_UP = 0.5                    # mL/kg·min per 60 days counted as rising / falling (Apple's estimate moves ~±1 on noise)
MIN_TRIMP_DAYS = 21                   # days with TRIMP in the last 28 needed for a status

# ---- load focus (Garmin Load Focus): share of TRIMP by zone group over the last 28 days, Edwards zones 50/60/70/80/90 %HRmax
LOAD_FOCUS = (("low_aerobic", "低有氧", (0, 1)), ("high_aerobic", "高有氧", (2, 3)), ("anaerobic", "无氧", (4,)))

# ---- aerobic minutes (Fitbit Active Zone Minutes / WHO 150 minutes of moderate activity a week)
AZM_MODERATE_ZONES = (1, 2)           # 60–80 %HRmax counts × 1
AZM_VIGOROUS_ZONES = (3, 4)           # ≥ 80 %HRmax counts × 2 (WHO: 1 vigorous minute ≈ 2 moderate minutes)
AZM_WEEK_TARGET = 150

# ---- resilience (Oura Resilience: balance of daytime stress and recovery over 14 days), our own first-version mapping
RESILIENCE_DAYS = 14
RESILIENCE_MIN_DAYS = 10
RESILIENCE_LEVELS = ((0.62, "exceptional", "极强"), (0.52, "strong", "强"), (0.42, "solid", "稳固"),
                     (0.32, "adequate", "一般"), (-9, "limited", "受限"))

# ---- WHOOP-style sleep performance (2024 WHOOP: hours vs needed, consistency, efficiency, high sleep stress).
# No stress during sleep here, so its weight goes to the other three.
SLEEP_PERF_WEIGHTS = {"sufficiency": 0.6, "consistency": 0.2, "efficiency": 0.2}

# ---- combined strain (WHOOP Strength Trainer adds muscular load into Strain): optional view, the stored strain is unchanged
STRAIN_K = 170.0                      # same as scores.params
MUSCLE_TRIMP_AT_MEDIAN = 60.0         # a median strength session (by volume, last 90 days) counts as 60 TRIMP (≈ 1 hour of moderate cardio)

# ---- illness risk (Oura Symptom Radar style): weighted signs from the recovery z-scores and the wrist temperature
ILLNESS_WEIGHTS = {"rhr": 1.0, "hrv": 1.0, "resp_rate": 0.8}
ILLNESS_DEAD_Z = 0.5                  # only the part of an adverse z-score beyond 0.5 SD counts (normal night-to-night noise)
ILLNESS_TEMP_C = 0.5                  # wrist temperature ≥ +0.5 °C above baseline adds one sign
ILLNESS_SPO2 = 93.0                   # night SpO₂ median below 93 % adds half a sign
ILLNESS_LEVELS = ((60, "high", "高"), (30, "watch", "留意"), (0, "low", "低"))

# ---- chronotype (Munich ChronoType Questionnaire: mid-sleep on free days, corrected for sleep debt)
CHRONO_DAYS = 60
CHRONO_MIN_NIGHTS = 10
CHRONO_FREE_WAKE_DAYS = (5, 6)        # Saturday / Sunday wake dates count as free days
CHRONO_BINS = ((3 * 60, "early", "早型"), (4 * 60 + 30, "intermediate", "中间型"), (6 * 60, "late", "晚型"),
               (24 * 60, "very_late", "极晚型"))   # MSFsc after midnight, minutes

# ---- body battery (Garmin Body Battery), rough estimate: Apple Watch has no continuous daytime HRV
BB_WAKE_BASE, BB_WAKE_SPAN = 20.0, 75.0      # value at wake-up = 20 + 75 × recovery / 100
BB_DRAIN_PER_HOUR = 2.0                       # awake baseline drain
BB_STRESS_DRAIN = {"low": 0.0, "medium": 2.0, "high": 4.0}
BB_STRAIN_DRAIN = 2.5                         # per strain point, spread over the waking hours

# ---- recovery time (Garmin Recovery Time), rough estimate
RT_STRAIN_FREE = 8.0                  # strain ≤ 8 needs no extra recovery
RT_HOURS_PER_STRAIN = 5.0             # each strain point above 8 adds 5 hours (strain 21 → 65 h)
RT_MAX_HOURS = 96
RT_TSB_FATIGUE = 50.0                 # factor 1 + max(0, −TSB) / 50
RT_ZONE_FACTOR = {"green": 0.8, "yellow": 1.0, "red": 1.25}
RT_WORKOUT_HOUR = 18                  # the hard day's load is assumed to end at 18:00
RT_AS_OF_HOUR = 7                     # recovery time is reported as of 07:00 of the day

# ---- recovery index (Oura): when during the night the lowest 5-minute heart rate happened
RI_GOOD, RI_LATE = 0.5, 0.75          # lowest point in the first half = good, after 75 % of the night = late

# ---- heart-rate recovery one minute after exercise (Cole et al. 1999: ≤ 12 bpm abnormal)
HRR_LOW, HRR_GOOD = 12.0, 20.0

# ---- English twins of every Chinese label / unit the Trends tab shows (series.py SERIES, the status labels above and in
# calc.py), looked up with i18n.tl() when the request is in English (issue #42). Chinese stays the stored / AI-facing text.
EN = {
    # series labels
    "恢复分": "Recovery", "HRV（SDNN 日均）": "HRV (SDNN, daily mean)", "静息心率（Apple）": "Resting HR (Apple)",
    "呼吸频率": "Respiratory rate", "血氧": "Blood oxygen (SpO₂)", "手腕温度偏差": "Wrist temperature deviation",
    "生病风险": "Illness risk", "最低心率出现": "Lowest HR timing", "实际睡眠": "Sleep", "睡眠需求": "Sleep need",
    "睡眠债": "Sleep debt", "睡眠表现": "Sleep performance", "睡眠表现（综合）": "Sleep performance (overall)",
    "恢复性睡眠": "Restorative sleep", "恢复性睡眠占比": "Restorative sleep share", "睡眠效率": "Sleep efficiency",
    "睡眠分": "Sleep score", "睡眠规律性": "Sleep consistency", "夜间醒来": "Awakenings", "日照时间": "Time in daylight",
    "负荷 strain": "Strain", "负荷（含肌肉）": "Strain (with muscle load)", "CTL 体能": "Fitness (CTL)",
    "ATL 疲劳": "Fatigue (ATL)", "TSB 状态": "Form (TSB)", "急慢性负荷比": "Acute:chronic load ratio",
    "有氧分钟": "Aerobic minutes", "肌肉负荷": "Muscle load", "步数": "Steps", "活动消耗": "Active energy",
    "总消耗": "Total energy", "运动后心率恢复": "Heart rate recovery", "压力": "Stress", "韧性": "Resilience",
    "体重": "Weight", "身体年龄": "Body age",
    # units
    "次/分": "/min", "次": "times", "分钟": "min", "步": "steps", "岁": "yrs",
    # status labels (calc.py and LOAD_FOCUS / RESILIENCE_LEVELS / ILLNESS_LEVELS / CHRONO_BINS above)
    "平衡": "Balanced", "偏高": "High", "偏低": "Low", "很低": "Very low",
    "过度": "Overreaching", "去适应": "Detraining", "恢复中": "Recovering", "疲劳": "Strained", "有效": "Productive",
    "低效": "Unproductive", "保持": "Maintaining",
    "低有氧": "Low aerobic", "高有氧": "High aerobic", "无氧": "Anaerobic",
    "极强": "Exceptional", "强": "Strong", "稳固": "Solid", "一般": "Adequate", "受限": "Limited",
    "高": "High", "留意": "Watch", "低": "Low",
    "早型": "Early", "中间型": "Intermediate", "晚型": "Late", "极晚型": "Very late",
    "前半夜": "First half", "后半夜": "Second half", "很晚": "Very late",
}
