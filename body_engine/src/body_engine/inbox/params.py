"""Settings of the inbox (inbox/, issue #359 P1)."""

FLAG = "inbox"          # rollout flag (rollout/registry.py): every endpoint answers 404 while it is off for the person

# Items older than this are not listed: the inbox is about what is new, the source tables keep their own retention
# (notifications are pruned after notify/params.KEEP_DAYS, the same 90 days). inbox_item_state rows older than this refer to
# items that have left the list already; they are removed whenever the person writes new state.
KEEP_DAYS = 90

PAGE_LIMIT = 50         # items per page of GET /inbox …
PAGE_LIMIT_MAX = 200    # … and the most a client may ask for
SOURCE_MAX = 500        # newest rows read from one source per request, so a very busy account stays cheap
READ_IDS_MAX = 500      # ids in one POST /inbox/read

# What an invite card says the person would share, by circle scope (friends/params.CIRCLE_SCOPES; a test checks they match).
# The same words as the Friends page's join sheet (web/static/circles.js).
SCOPE_LABELS = {
    "scores": ("评分", "Scores"),
    "sleep": ("睡眠", "Sleep"),
    "training": ("训练", "Workouts"),
    "nutrition_summary": ("饮食达标率", "Nutrition adherence"),
    "nutrition": ("具体饮食", "Food details"),
    "weight": ("体重", "Weight"),
    "metrics": ("每日指标", "Daily metrics"),
    "labs": ("化验", "Lab results"),
    "supplements": ("补剂", "Supplements"),
    "journal": ("日记", "Journal"),
    "events": ("身体事件", "Body events"),
    "memory": ("长期记忆", "Long-term memory"),
}
