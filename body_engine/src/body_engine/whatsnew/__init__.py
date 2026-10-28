"""Weekly "what's new": tell every user what shipped this week.

entries.py  the hand-kept list of user-facing changes (one line each, Chinese + English, optional link into the page)
service.py  who is due, what to say, the idempotent send (`body-engine whats-new send [--user-id N] [--dry-run]`)
api.py      GET /v1/whats-new: the list the Today page shows in its 「本周更新」 sheet
"""
