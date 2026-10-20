"""Invite and referral parameters (account/invites.py). Product choices, not health thresholds; keep them configurable."""

# Referral codes (GET /v1/users/{user}/invite-code): off unless the environment variable BODY_REFERRALS_ENABLED is set.
REFERRALS_ENV = "BODY_REFERRALS_ENABLED"
REFERRAL_MAX_USES = 5            # friends one user's referral code can bring in
REFERRAL_DAYS = None             # validity in days; None = until revoked

# What an inviter earns per friend who redeems their code. Written to invite_rewards as `pending`; nothing grants it yet
# (placeholder until the reward is decided, e.g. fallback-AI credit).
REFERRAL_REWARD = {"type": "ai_fallback_credit", "amount": 1, "unit": "CNY"}
