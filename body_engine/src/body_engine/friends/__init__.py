"""Friends page (docs/friends-social-plan.md): circles, invites and the sharing they create.

- params.py: circle scopes (never ecg), defaults, invite lifetime, limits
- store.py: create / invite / join / change scopes / leave / remove / dissolve; keeps shares.circle_scopes in sync
- codes.py: circle activation codes (#199): sign up and join one circle with a readable code; uses, expiry, disable
- admin.py: circles for groups made by the admin (owner, member cap, default scopes) and circle avatars (images/)
- cards.py: Friends tab member cards (today + 7 days, per-scope) and one-tap cheers
- leaderboard.py: five boards scored against each person's own baseline / goals (weights in params.py)
- tools.py: get_friends_overview for the AI
- api.py: /v1/friends/* (only ever acts for the caller)
- moderation.py / moderation_api.py / moderation_cli.py: block, report, the admins' review queue and bans (App Store 1.2);
  blocks.py: what blocks / reports / moderation hide on every read path; rules.py: community rules acceptance
  (community_rules*.md = docs/legal/community-rules*.md); keyword filter on write: ../wordfilter.py
"""
