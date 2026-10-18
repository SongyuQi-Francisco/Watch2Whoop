"""Rule reminder engine (docs/system-design.md, "rule engine" section; requirement 4).

- params.py: all thresholds and their sources (which section of body-management.md), RULES_VERSION
- context.py: data the rules use (SparkyFitness training / plans / food / weight; Body Engine exercise restrictions, scores, journal)
- progression.py: double-progression weight increase / reduce weight after consecutive failures (4.5)
- nutrition.py: adjust calories by weekly average weight (5.2), pre-workout eating (4.2.1), red meat / beef short rib frequency caps (5.3.1, 5.3.2)
- safety.py: banned / caution exercises (4.3), combined with recovery score / sleep debt (section 7)
- engine.py: run all rules, idempotently write reminders / rule_runs (body-engine evaluate-rules)
- store.py / api.py / tools.py: read reminders, change status (shared by the HTTP API and the AI assistant)
Each rule is a pure function (Ctx) -> RuleResult; it only gives advice and never changes any SparkyFitness data.
"""
