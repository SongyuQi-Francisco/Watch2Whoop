"""Explanations behind the Today page items: what a metric / status card item means, how to read it, and advice from the user's own data.

- glossary.py: static Chinese entries (metrics, lab items, status items, conditions matched by keyword).
- personal.py: rule-based advice computed from the owner's data (scores, metrics, labs, exercise rules, profile card). No AI call:
  the page hands `ai_prompt` to the viewer's own AI (in-page chat with their own key, or their butler), never someone else's.
- api.py: GET /v1/users/{user}/explain/metric/{key} and /explain/status/{key}, shares-checked per data block.
"""
