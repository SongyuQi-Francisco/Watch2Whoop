"""Settings of the weekly what's-new message (whatsnew/). The entries themselves are in entries.py."""
from datetime import time

KIND = "updates.weekly"        # notify() kind; the category `updates` is declared in notify/params.py
URL = "/app/?whatsnew=1"       # opens the 「本周更新」 sheet (web/static/whatsnew.js; notify.js announces it as "body:link")

SEND_WEEKDAY = 6               # Monday = 0 … Sunday = 6, in the user's own time zone
SEND_TIME = time(19, 0)        # the message is due from here on …
CATCHUP_HOURS = 48             # … and until this long after, so an hourly job that was down on Sunday evening still sends it
LOOKBACK_DAYS = 14             # a user with no earlier message hears about at most this many days (never before the account existed)

# Limits tests/test_whats_new.py holds every entry to: one short sentence, said the way you would say it.
MAX_ZH = 40                    # characters
MAX_EN = 110
LIST_WEEKS = 8                 # weeks the sheet shows by default
LIST_WEEKS_MAX = 26
# Words that do not belong in an entry (readers never see the code). PR / issue numbers, snake_case words and every key of
# rollout/registry.py are checked separately.
FORBIDDEN = ("API", "MCP", "token", "Token", "JSON", "SDK", "webhook", "flag", "PR", "OW", "HealthKit", "Postgres",
             "SparkyFitness", "Grafana", "Open Wearables", "接口", "迁移", "数据库", "服务器", "开关", "灰度", "后端", "前端",
             "缓存", "commit", "bug", "Bug")
