"""Data status (「数据状态」, issue #163, data_status.py): which health data types reached the server, and when a type counts
as "stale". Change here, not in the code.

These are data-freshness heuristics for a person who wears an Apple Watch every day (no clinical source; the cadences
are Apple's documented sampling behaviour):
- Heart rate, steps and active energy are recorded all day by the watch and iPhone: more than 2 days without any means
  the data is not being read (or the watch was not worn).
- HRV (SDNN, a few background readings a day / during sleep), resting heart rate (one value a day), sleep (one night),
  blood oxygen and respiratory rate (background readings, mostly during sleep), sleeping wrist temperature (each night
  with sleep tracking) and the beat-to-beat series behind each HRV reading: 3 days allows a night without the watch.
- Water: only if the user logs drinks in Health (or an app that writes there); 7 days.
- Workouts: rest weeks are normal; 14 days.
- Weight needs a smart scale or a manual entry; VO₂ max is estimated only after outdoor walks / runs / hikes with the
  watch: 30 days.
- ECG is recorded only when the user opens the ECG app: never "stale", only received or never received.

STALE_AFTER_DAYS: days since the latest data before the type counts as stale (None = never stale).
WINDOW_DAYS: the "last N days" of the counts (today included).
SOURCES_DAYS: how far back the list of sources looks.
"""
WINDOW_DAYS = 7
SPARKY_TIMEOUT_S = 8   # the strength-training row (#210) asks SparkyFitness; a slow server must not hold the page for its 60 s default
SOURCES_DAYS = 30

# key -> days; the order is the order on the page and in the app
STALE_AFTER_DAYS: dict[str, int | None] = {
    "heart_rate": 2,
    "hrv": 3,
    "resting_heart_rate": 3,
    "sleep": 3,
    "steps": 2,
    "active_energy": 2,
    "workouts": 14,
    "weight": 30,
    "blood_oxygen": 3,
    "respiratory_rate": 3,
    "wrist_temperature": 3,
    "vo2max": 30,
    "heartbeats": 3,
    "ecg": None,
    "water": 7,
}
