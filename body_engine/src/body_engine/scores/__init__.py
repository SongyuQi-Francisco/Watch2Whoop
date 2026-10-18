"""WHOOP-style scores (docs/system-design.md §7): sleep, recovery, strain.
params.py holds parameters; sleep / recovery / strain are pure functions; inputs reads data; engine computes day by day and writes daily_scores; store reads the scores."""
