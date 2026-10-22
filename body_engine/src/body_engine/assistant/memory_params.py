"""Parameters of the long-term memory (assistant/memory.py, ai/prompt.py), issue #112. Configurable; nothing here is one person's data."""

# A recent note ("this week I'm travelling", "day 3 of a cold") given without an end date expires after this many days.
# Two weeks covers a typical cold, a trip or an exam period; a judgement call, not from a study.
RECENT_DEFAULT_DAYS = 14
# Upper bound for an explicit end date: anything that lasts longer is a lasting fact or a profile card item.
RECENT_MAX_DAYS = 180

# Profile card items the in-page and fallback AI always get, in this order, however long the memory is
# (ai/prompt.py _memory_block): how to talk to the user, then the two items that keep advice safe.
PRIORITY_KEYS = ("communication", "health_constraints", "injuries")
# Observation notes (lasting facts) put into the per-conversation memory, newest first.
PROMPT_FACTS_MAX = 60
