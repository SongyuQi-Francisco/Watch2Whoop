"""Invite and referral parameters (account/invites.py). Product choices, not health thresholds; keep them configurable."""

# User referral codes (「我」→「邀请好友」, GET /v1/users/{user}/invite-code). Off unless the environment variable
# BODY_REFERRALS_ENABLED is set (1 / true / yes): self-hosted servers keep sign-up admin-gated by default.
REFERRALS_ENV = "BODY_REFERRALS_ENABLED"
REFERRAL_MAX_USES = 10           # friends one user can bring in (an existing code follows this value the next time it is shown)
REFERRAL_DAILY_REDEEMS = 3       # redemptions per referral code in any 24 hours (abuse cap; admin codes are not capped)
REFERRAL_DAYS = None             # referral codes do not expire; the admin can revoke them (`invite-code revoke`)

# What an inviter earns per friend who redeems their code. Written to invite_rewards as `pending`; nothing grants it yet
# (placeholder until the reward is decided, e.g. fallback-AI credit).
REFERRAL_REWARD = {"type": "ai_fallback_credit", "amount": 1, "unit": "CNY"}


def referrals_enabled() -> bool:
    import os
    return (os.environ.get(REFERRALS_ENV) or "").strip().lower() in ("1", "true", "yes", "on")
