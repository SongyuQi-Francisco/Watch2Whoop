"""Circles and sharing on the Friends page (docs/friends-social-plan.md §3.2, decided 2026-10-22). Configurable here."""

# What a member may show the others of a circle, in the order the join sheet lists them. `ecg` is never one of them
# (consent §2 "圈子共享": ECGs are never shared through circles; also a CHECK in migration 470).
CIRCLE_SCOPES = ("scores", "sleep", "training", "nutrition_summary",
                 "nutrition", "weight", "metrics", "labs", "supplements", "journal", "events", "memory")
# Ticked by default when joining: scores (recovery, sleep, strain), training and nutrition adherence only.
# Detailed food, weight, labs, supplements, journal and long-term memory stay off unless the member ticks them.
DEFAULT_SCOPES = ("scores", "sleep", "training", "nutrition_summary")
NEVER_SHARED = ("ecg",)

# Circle sharing needs the consent version that has the circle clause (consent §2 "圈子共享", the owner-approved text of 2026-10-22.2)
CONSENT_VERSION = "2026-10-22.2"

INVITE_HOURS = 72             # an invite code / link works for 72 hours; the owner can reset it any time
REFERRAL_INVITE_DAYS = 30     # invite to the inviter's circles after signing up with their referral code (#57)
MAX_LIVE_CODES = 5            # live codes per circle; minting one more revokes the oldest
MAX_CIRCLES_PER_USER = 10
MAX_MEMBERS = 30
NAME_MAX = 40

# Wrong invite codes per client IP before a pause (same sliding window idea as web/login_limit.py)
JOIN_MAX_FAILURES = 10
JOIN_WINDOW_SECONDS = 900
