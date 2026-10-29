"""Parameters of the weekly / monthly report (insights/reports.py), with sources. Mirrored in the algorithm bundle
(packages/openbodyscore-js/src/35_reports.js `OBS.reports.params`, compared by tests/test_js_golden_reports.py).

None of these is tuned to anyone's data: they are the bands the rest of the product already uses.
"""

# Version of the report and journal-analysis algorithms on the phone (docs/ios-native-2.0.md section 3.3, key `reports`
# of OBS.ALGO_VERSIONS). Bump it with any change of reports.py / records/analysis.py / records/regression.py / these
# parameters, together with src/35_reports.js and the goldens.
REPORTS_VERSION = "reports-1"

# Week = the 7 days back from `end`, month = the 30 days back; the previous period is the span of the same length right
# before (module docstring of reports.py; WHOOP's weekly / monthly Performance Assessment).
PERIOD_DAYS = {"week": 7, "month": 30}

# Recovery colour bands: >= 67 green, 34–66 yellow, < 34 red (WHOOP's recovery bands, docs/system-design.md section 7;
# the same values as web/views.RECOVERY_GREEN and rules/params.RECOVERY_RED).
RECOVERY_GREEN = 67
RECOVERY_RED = 34

# Nights with less than 6 hours asleep are counted (the short-sleep threshold of the sleep literature: Watson et al.,
# "Recommended amount of sleep for a healthy adult", Sleep 2015, doi:10.5665/sleep.4716 — 7 h recommended, < 6 h short).
SHORT_SLEEP_MIN = 360

# Days with strain >= 14 are counted ("strenuous" in WHOOP's strain bands: 14–17.9 strenuous, 18+ all out).
HIGH_STRAIN = 14

# Journal findings: the last 90 days of the journal (the window of the 行为影响 card, records/analysis.py default), at most
# the 5 largest effects by |Cohen's d| (a summary for the AI; the full list is GET /journal/analysis).
JOURNAL_DAYS = 90
JOURNAL_TOP = 5
