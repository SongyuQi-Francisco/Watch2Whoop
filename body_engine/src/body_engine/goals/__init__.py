"""Goals: bedtime goal (docs/system-design.md §13 "bedtime goal") and nutrition goals (issue #31).

- params.py: parameters such as default pace and on-target tolerance, nutrition goal ranges, with their basis
- sleep.py: pure functions: conversion of times across midnight into a "sleep day", the step-by-step earlier plan, tonight's target, progress
- store.py: reads and writes the sleep_goals table, assembles the view shared by the API / assistant / rules
- nutrition.py: versioned nutrition goals (table nutrition_goal_versions: training / rest day sets with caps, effective
  date, who / why), resolved per date for rules, status cards, reports and adherence; SparkyFitness goals are only the
  fallback before the first version, and import_sparky_plans backfills versions from SparkyFitness weekly goal plans
- api.py / tools.py: GET / PUT / DELETE /v1/users/{user}/goals/sleep (GET / PUT / DELETE carry `settings`, the stored row
  the app caches) and GET / PUT /goals/nutrition, assistant tools get_sleep_goal /
  set_sleep_goal / get_nutrition_goals / set_nutrition_goals
Rule reminders (tonight's target and pre-sleep arrangements, consecutive deviations, milestone reached) are in rules/sleep_goal.py.
"""
