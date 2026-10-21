"""Friends page (docs/friends-social-plan.md): circles, invites and the sharing they create.

- params.py: circle scopes (never ecg), defaults, invite lifetime, limits
- store.py: create / invite / join / change scopes / leave / remove / dissolve; keeps shares.circle_scopes in sync
- api.py: /v1/friends/* (only ever acts for the caller)
"""
