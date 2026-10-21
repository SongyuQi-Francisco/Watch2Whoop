"""Rule reminder engine (docs/system-design.md, "rule engine" section; requirement 4).

- params.py: all thresholds and their sources (which section of body-management.md), RULES_VERSION
- context.py: data the rules use (SparkyFitness training / plans / food / weight; Body Engine exercise restrictions, scores, journal)
- progression.py: double-progression weight increase / reduce weight after consecutive failures (4.5)
- nutrition.py: adjust calories by weekly average weight, pre-workout eating, the user's own red meat / per-food weekly limits (nutrition/constraints.py, issue #76)
- safety.py: banned / caution exercises (4.3), combined with recovery score / sleep debt (section 7)
- engine.py: run all rules, idempotently write reminders / rule_runs (body-engine evaluate-rules)
- store.py / api.py / tools.py: read reminders, change status (shared by the HTTP API and the AI assistant)
Each rule is a pure function (Ctx) -> RuleResult; it only gives advice and never changes any SparkyFitness data.
"""
