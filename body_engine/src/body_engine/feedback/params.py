"""Limits of the AI feedback queue (docs/ai-feedback-plan.md §2). Product limits, not health thresholds: chosen so one
user's AI (or a prompt injection in a screenshot) cannot flood the maintainer, while a real user hitting several bugs in a
day still gets them through. Change here only."""

PER_USER_DAY = 5           # new reports per user in a rolling 24 h (votes on an existing report do not count)
PER_USER_MONTH = 30        # rolling 30 days
GLOBAL_DAY = 100           # whole server, rolling 24 h

MAX_TITLE = 80             # characters, after sanitising (the root rule asks for ≤ 60)
MAX_FIELD = {"what": 1000, "expected": 500, "steps": 800, "error_text": 500, "area": 60}
MAX_BODY = 2000            # body_public as a whole

REVIEW_RATIO = 0.3         # placeholders make up more than this share of the text -> needs_review (stays on the server)
DIAG_WINDOW_MIN = 10       # failing tool calls of this user in the last N minutes are attached (names and counts only)
UPDATE_DAYS = 14           # status changes newer than this are offered to the reporter's AI once (briefing feedback_updates)
LIST_LIMIT = 30

OPEN_STATUSES = ("new", "triaged", "filed", "public")
WITHDRAWABLE = ("new",)    # the reporter can withdraw (delete) a report until the maintainer has looked at it
TELL_USER_STATUSES = ("public", "fixed", "declined", "duplicate")   # worth telling the reporter about

# What a status means for the reporter (Me → 反馈记录, list_feedback)
STATUS_LABEL = {"new": "已收到", "triaged": "开发者已看", "filed": "已记入待办", "public": "已公开",
                "fixed": "已修复", "declined": "暂不做", "duplicate": "重复"}
STATUS_LABEL_EN = {"new": "Received", "triaged": "Seen by the developers", "filed": "On the to-do list", "public": "Public",
                   "fixed": "Fixed", "declined": "Not planned", "duplicate": "Duplicate"}
KIND_LABEL = {"bug": "问题", "feature": "需求"}
MODES = ("auto", "ask", "off")
