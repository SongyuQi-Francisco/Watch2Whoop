"""Goals (the first one: bedtime goal, docs/system-design.md §13 "bedtime goal").

- params.py: parameters such as default pace and on-target tolerance, with their basis
- sleep.py: pure functions: conversion of times across midnight into a "sleep day", the step-by-step earlier plan, tonight's target, progress
- store.py: reads and writes the sleep_goals table, assembles the view shared by the API / assistant / rules
- api.py / tools.py: GET / PUT /v1/users/{user}/goals/sleep, assistant tools get_sleep_goal / set_sleep_goal
Rule reminders (tonight's target and pre-sleep arrangements, consecutive deviations, milestone reached) are in rules/sleep_goal.py.
"""
