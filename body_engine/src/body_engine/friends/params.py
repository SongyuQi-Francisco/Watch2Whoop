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

# How long a new invite code / link works: the owner picks one of INVITE_DAYS_CHOICES (days) or "never expires" (#156).
# An invite can always be revoked early (one by one, or all at once with reset). Wrong values → 400.
INVITE_DAYS_CHOICES = (1, 3, 7, 30)
INVITE_DEFAULT_DAYS = 3
REFERRAL_INVITE_DAYS = 30     # invite to the inviter's circles after signing up with their referral code (#57)
MAX_LIVE_CODES = 5            # live codes per circle; minting one more revokes the oldest
MAX_CIRCLES_PER_USER = 10
MAX_MEMBERS = 30
NAME_MAX = 40

# Wrong invite codes per client IP before a pause (same sliding window idea as web/login_limit.py)
JOIN_MAX_FAILURES = 10
JOIN_WINDOW_SECONDS = 900

# ---------------------------------------------------------------- leaderboards (docs/friends-social-plan.md §3.4)
# Every board scores 0–100 against the person's OWN baseline / goals, never absolute values, so people of very different
# fitness, age or weight can share a board. A board needs every scope in `scopes` from that person (to the viewer);
# otherwise the row says "not shared" and gets no rank (never last).
BOARDS = ("body", "activity", "lifestyle", "recomp", "longevity")
WINDOW_DAYS = 7                     # rolling window ending today (no weekly reset)
LONGEVITY_WINDOW_DAYS = 28          # body age / pace of aging move slowly: 4 weeks
BOARD_SCOPES = {
    "body": ("scores",),
    "activity": ("scores", "training"),
    "lifestyle": ("sleep", "nutrition_summary"),
    "recomp": ("weight", "training", "nutrition_summary"),
    "longevity": ("scores",),
}
CACHE_SECONDS = 600                 # a person's board numbers are reused for 10 minutes (they call SparkyFitness)

# Body status: recovery is already relative to the person's own HRV / resting-HR baseline (scores/), sleep performance
# relative to their own sleep need (actual / need, capped at 100 %).
BODY_W_RECOVERY, BODY_W_SLEEP = 0.6, 0.4
BODY_MIN_DAYS = 4                   # at least 4 of 7 days with a recovery score

# Activity: acute:chronic workload ratio = last 7 days' strain ÷ the person's weekly mean over the 28 days before.
# 0.8–1.3 is the "sweet spot", above 1.5 the injury risk rises (Gabbett TJ. Br J Sports Med 2016;50:273-80,
# doi:10.1136/bjsports-2015-095788). Full marks 0.8–1.5, linear down to 0 at 2.0; below 0.8 linear from 0.
ACT_W_LOAD, ACT_W_DAYS = 0.7, 0.3
ACWR_FULL_LOW, ACWR_PENALTY_FROM, ACWR_ZERO_AT = 0.8, 1.5, 2.0
ACT_FULL_DAYS = 4                   # WHO 2020 guidelines: activity spread over most days + muscle strengthening ≥ 2 days
ACT_MIN_BASELINE_DAYS = 14          # days with strain in the 28-day baseline
ACT_MIN_DAYS = 4                    # days with strain in the last 7

# Lifestyle: 50 % on-time bedtime + 50 % nutrition goals met (n / 7 each; a day not logged is not met).
LIFE_W_SLEEP, LIFE_W_FOOD = 0.5, 0.5
BEDTIME_TOL_MIN = 45                # without a bedtime goal: within ± 45 min of the person's own 28-day median bedtime
                                    # (a regularity measure; cf. sleep regularity, Phillips AJK et al. Sci Rep 2017;7:3216)
BEDTIME_BASELINE_NIGHTS = 28
BEDTIME_MIN_BASELINE = 7            # nights needed for the median

# Recomposition (lean bulk / cut): 40 % weight toward the goal, 40 % training volume progress, 20 % protein met.
RECOMP_W_WEIGHT, RECOMP_W_TRAIN, RECOMP_W_PROTEIN = 0.4, 0.4, 0.2
# Weekly change in % of body weight: losing 0.5–1.0 %/week keeps lean mass (Helms ER et al. JISSN 2014;11:20,
# doi:10.1186/1550-2783-11-20); gaining 0.25–0.5 %/week limits fat gain (Iraki J et al. Sports 2019;7:154,
# doi:10.3390/sports7070154). Within ± GOAL_NEAR_KG of the goal the person is maintaining: |change| ≤ 0.5 %/week.
LOSS_BAND, GAIN_BAND, MAINTAIN_MAX = (0.5, 1.0), (0.25, 0.5), 0.5
RATE_ZERO_AT = 2.0                  # %/week at which a too fast change (or maintenance drift ≥ 1.5 + 0.5) scores 0
GOAL_NEAR_KG = 1.0
WEIGH_INS_MIN = 2                   # weigh-ins needed in each of the two 7-day windows
# Body fat (when the person logs it in both windows): the composition part is the mean of the weight and body-fat scores;
# a drop of FAT_PP_FULL percentage points a week = 100, unchanged = 50, a rise of FAT_PP_FULL = 0 (when gaining weight
# on purpose, unchanged body fat already counts as 100).
FAT_PP_FULL = 0.2
TRAIN_VOLUME_FULL = 1.0             # last 7 days' strength volume ≥ the 28-day weekly mean → full marks

# Longevity (4 weeks): 60 % change of the body-age gap (body age − age), 40 % current pace of aging.
LONG_W_GAP, LONG_W_PACE = 0.6, 0.4
GAP_YEARS_FULL = 1.0                # 1 year younger over 4 weeks = 100, unchanged = 50, 1 year older = 0
PACE_BEST, PACE_MID, PACE_WORST = 0.5, 1.0, 1.5   # pace of aging (years per year): 0.5 → 100, 1.0 → 50, 1.5 → 0

# ---------------------------------------------------------------- training feed (rollout flag circle_feed, friends/feed.py)
FEED_FLAG = "circle_feed"
FEED_SCOPE = "training"             # a member's posts go to a circle only when they ticked this scope for it
FEED_PAGE = 10                      # posts per page (the page loads more when scrolling down)
FEED_LOOKBACK_DAYS = 14             # days looked at when posts are made on the fly (a post follows the day's training)
FEED_PREVIEW_EXERCISES = 3          # exercises shown before 「show all」
COMMENT_MAX = 300                   # characters; text only
COMMENTS_PER_HOUR = 30              # per person, against floods (429)
COMMENTS_SHOWN = 100                # newest comments returned for one post
REPORT_REASON_MAX = 200
