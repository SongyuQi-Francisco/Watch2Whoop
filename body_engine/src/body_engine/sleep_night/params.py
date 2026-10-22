"""Thresholds of the rule-based notes on the sleep detail page (sleep_night/view.py). First-version values, each with its source.
Nothing here changes a score: the notes only read the night, the scores row and the owner's own averages."""

BASELINE_SHORT, BASELINE_LONG = 7, 30   # "vs my own average" windows in nights (the Trends tab's 1W / 1M)
MIN_BASELINE_NIGHTS = 3                 # an average needs at least 3 nights with data

# Score bands, the same as the explain sheet of sleep_score (explain/personal.py) so the page and the sheet agree
SCORE_GOOD, SCORE_MID = 80, 60
PART_WEAK = 80                          # a score part below this gets a note (explain/personal.py uses the same cut)

# Duration against the night's sleep need (scores: 8 h baseline + debt + strain, openbodyscore/params.py)
SHORT_OF_NEED_MIN = 30                  # >= 30 min below need -> "short"
SHORT_OF_NEED_BAD_MIN = 90              # >= 90 min below need -> "clearly short"

# Stage shares of the time asleep. Healthy adults: deep (N3) ~13–23 %, REM ~20–25 % (Ohayon et al. 2004, Sleep 27:1255,
# meta-analysis of 65 polysomnography studies; Carskadon & Dement, Principles and Practice of Sleep Medicine). Apple Watch
# under-reads deep sleep a little against PSG (Apple Sleep Stages validation paper, 2023), so the low cuts sit under the ranges.
DEEP_LOW_PCT = 10
REM_LOW_PCT = 15

# Sleep efficiency (asleep / in bed) and latency: NSF sleep quality recommendations (Ohayon et al. 2017, Sleep Health 3:6):
# efficiency >= 85 % good, <= 74 % poor; latency <= 30 min good, >= 46 min poor; WASO <= 20 min good, >= 51 min poor.
EFFICIENCY_GOOD, EFFICIENCY_POOR = 85, 75
LATENCY_LONG_MIN, LATENCY_POOR_MIN = 30, 46
WASO_LONG_MIN, WASO_POOR_MIN = 20, 51

# Bedtime against the owner's 30-night circular mean: social-jet-lag scale shifts (Wittmann et al. 2006, Chronobiol Int 23:497)
BEDTIME_SHIFT_MIN = 60

# Overnight vitals against the owner's own 30-night mean (same directions as the recovery score and the health monitor)
HRV_LOW_PCT = 15                        # HRV >= 15 % under the mean (openbodyscore ILLNESS_HRV_RATIO 0.85)
RHR_HIGH_BPM = 5                        # lowest sleeping HR >= 5 bpm over the mean (openbodyscore ILLNESS_RHR_DELTA)
RESP_HIGH = 1.0                         # respiration >= 1 breath/min over the mean (WHOOP / Oura flag ~1 rpm)
TEMP_DELTA_C = 0.6                      # wrist temperature: openbodyscore WRIST_TEMP_DELTA
SPO2_LOW = 94.0                         # median SpO2 < 94 %: openbodyscore SPO2_LOW_MEDIAN

MAX_NOTES = 6                           # notes shown, worst first
