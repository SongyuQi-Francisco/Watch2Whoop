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

# Open sign-up (account/open_signup.py, feature flag `open_signup`; on = state `all`, any other state = off). While it is on
# and fewer than OPEN_SIGNUP_MAX_ACTIVE accounts count as active, a new sign-in account is active at once (no invite code)
# and accounts still waiting for a code are activated the next time they reach the server. At the cap new accounts are
# `pending` again and need a code, by themselves: nothing has to be switched. Below the cap again (accounts deleted or
# disabled, or the cap raised here) it opens again.
#   Counted: users.status = 'active', the deployer and admin-created accounts included.
#   Not counted: `pending` and `disabled` accounts, deleted ones, and members of the cohorts in OPEN_SIGNUP_EXCLUDE_COHORTS
#   (the App Review demo account: `body-engine cohort add --user-id N --cohort demo`).
OPEN_SIGNUP_FLAG = "open_signup"
OPEN_SIGNUP_MAX_ACTIVE = 200
OPEN_SIGNUP_EXCLUDE_COHORTS = ("demo",)

# Abuse limits. Per IP (the address the server sees, as for the login failure limit): at most OPEN_SIGNUP_PER_IP_DAY new
# accounts are opened without a code in any 24 hours; a further new account from that address stays `pending` (an invite
# code still works) and is never activated by open sign-up later. The IP is stored only as a sha256 and cleared after
# OPEN_SIGNUP_IP_KEEP_HOURS. Accounts that were already waiting before sign-up opened are not counted per IP.
OPEN_SIGNUP_PER_IP_DAY = 5
OPEN_SIGNUP_IP_KEEP_HOURS = 48
# Identities: an Apple ID / e-mail that already opened an account (with a code, or by open sign-up) is not opened again
# without a code (the same rule as invite codes; the record outlives a deleted account), so deleting and signing up again
# gets no fresh fallback-AI allowance.
# Codes still count while sign-up is open: an account opened by open sign-up may enter one invite / referral code within
# OPEN_SIGNUP_CODE_DAYS days (POST /v1/account/redeem-invite: records the inviter, the circle invite and the reward, as at
# sign-up). Circle codes work as for any existing account (Friends page, or the same endpoint).
OPEN_SIGNUP_CODE_DAYS = 14


def referrals_enabled() -> bool:
    import os
    return (os.environ.get(REFERRALS_ENV) or "").strip().lower() in ("1", "true", "yes", "on")
