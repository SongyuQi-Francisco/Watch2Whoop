"""Limits for in-page chat turns that run on without their connection (ai/runs.py, issue #114).

Not health rules; plain operating limits. Change them here, nothing else hard-codes them.
"""
MAX_RUN_SECONDS = 600     # one turn (all model calls and tools of one message or one confirmation) is stopped after this long
KEEP_SECONDS = 900        # a finished turn's events stay resumable this long (from the moment it ended), then only the database has it
MAX_RUNNING_PER_USER = 2  # turns of one person running at the same time (each new conversation is a turn of its own)
MAX_RUNNING = 6           # turns running at the same time on the server: each holds a pool connection (api.py max_size=10)
                          # until it ends, so a few connections stay free for everything else
MAX_KEPT_RUNS = 200       # at most this many finished turns are kept in memory (oldest dropped first), whatever their age
POLL_SECONDS = 0.1        # how often a stream checks the buffer for new events
HEARTBEAT_SECONDS = 15    # an SSE comment line when nothing happened this long, so proxies keep an idle stream open
