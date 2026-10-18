"""Cardio metrics from raw heart rate (docs/raw-data-plan.md §5).

- hrr.py: heart rate recovery 60 s after each workout (HRR60) -> workout_hrr, written by compute-scores
- views.py / api.py: GET /v1/users/{user}/cardio (HRR per workout + 30-day trend, recovery index per night)
The recovery index itself (minutes after falling asleep until the lowest 5-minute heart rate) is computed by the scoring
engine (daily_scores.details.recovery.rhr_low_offset_min); this module only presents it.
"""
