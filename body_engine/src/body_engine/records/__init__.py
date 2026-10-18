"""Health records: supplements, lab results, journal (with journal analysis), body events, exercise extra info.

- store.py: read/write functions, called by both the HTTP API (api.py) and AI assistant tools (tools.py)
- analysis.py: journal analysis (a behavior -> next-day outcome, comparing the two group means)
- defaults.py: default journal items every user has
- md_seed.py: historical records transcribed from body-management.md (supplements, labs, events, banned exercises, journal backfill)
- importer.py: idempotent migration command body-engine import-records
"""
