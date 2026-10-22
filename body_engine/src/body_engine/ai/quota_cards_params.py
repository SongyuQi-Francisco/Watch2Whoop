"""Parameters of the Jarvis quota cards (ai/quota_cards.py, issue #182, docs/ai-assistant-plan.md §9.10).

Limits are the owner's product choices (no outside source): they keep a typo in a CLI call or a plugin from handing out
an unbounded allowance. Periodic rules live in the database (table ai_quota_card_rules, `body-engine ai-cards rule-set`).
"""

FLAG = "ai_cards"                     # rollout flag: the Today page shows cards and can use them; periodic rules grant only to people it is on for
KINDS = ("reset", "extend")           # reset = clear this month's used allowance; extend = add amount_cny to this month's allowance
SOURCES = ("admin", "periodic", "reward", "other")

DEFAULT_EXPIRES_DAYS = 30             # a card without an explicit expiry lasts this long (CLI --expires never = no expiry)
MAX_EXTEND_CNY = 50.0                 # one extend card adds at most this much (the monthly quota is ¥3 by default)
MAX_COUNT = 10                        # cards per person in one grant
MAX_REASON = 200                      # characters; the reason is shown to the person on the card and in the notification

NOTIFY_KIND = "ai_card"               # notify/params.py CATEGORIES["ai_card"]
NOTIFY_URL = "/app/?tab=me&ai_cards=1"   # notify.js turns ai_cards into the "body:link" event; ai.js opens the card list
HISTORY_DAYS = 90                     # used / expired / revoked cards the person still sees in the list
