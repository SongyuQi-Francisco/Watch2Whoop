"""Which AI tools go out with every model call, and which groups each Today page adds (issue #99, docs/ai-assistant-plan.md §9.17).

Every tool has a domain `group` (the `group=` on its spec in */tools.py). What the model sees on each call:
- MCP tools/list: CORE_TOOLS + the ALWAYS_CONFIRM tools (so the client's own approval rules by tool name keep working,
  e.g. the "ask" list in assistant/.claude/settings.json) + the three meta tools;
- in-page chat (ai/chat.py): CORE_TOOLS + the groups of the page the chat was opened on (PAGE_GROUPS) + the meta tools.
Everything else is one find_tools call away and runs through run_read_tool / run_write_tool (assistant/toolsets.py).
Tool definitions are sent on every call, so the budgets below are enforced by tests/test_tool_groups.py.
Change the lists here; keep the core list ordered as in assistant/tools.TOOLS (the sent order follows TOOLS anyway).
"""

# Resident core: the opening briefing, the scene context tools and playbooks, the everyday logging tools and their undo.
CORE_TOOLS = (
    "get_briefing", "list_metrics", "get_daily_metrics", "get_sleep", "get_scores", "list_events", "add_event",
    "get_training_log", "get_nutrition", "get_weight",
    "get_journal", "log_journal",
    "get_reminders",
    "get_training_calendar", "get_week_plan", "get_exercise_rules",
    "search_exercises", "log_food", "log_workout", "log_weight", "undo_sparky_entry", "update_food_entry",
    "get_write_log", "rollback_write", "get_memory", "update_profile_card", "add_observation",
    "get_meal_templates", "log_meal_template",
    "get_shopping_context", "record_store_prices",
    "report_feedback",
    "get_training_context", "get_week_review_context", "get_playbook",   # scene context + playbooks (#100)
)

# Group -> one line for find_tools' index (AI-facing text, Chinese).
GROUPS = {
    "body": "身体数据和评分：资料、每日指标、睡眠、恢复 / 负荷、压力、身体年龄、心率区间、VO₂max、周报月报、心肺、心电图、提醒、睡眠目标、体重、截图指标、圈子成员概况",
    "training": "训练：日历、某天详情、周计划（排 / 改 / 换动作 / 挪日子）、动作库、禁用动作、记训练、改 / 删训练",
    "nutrition": "饮食：每日摄入、记饮食、改 / 合并饮食记录、营养目标、快捷记餐模板、饮食档案、饮食约束、喝水",
    "recipes": "食谱：查 / 推荐 / 新建 / 修改食谱，按份数记一餐",
    "grocery": "采购：商品库、条码、超市、特价、查价写回、价格记录、采购清单、家里存货",
    "records": "健康档案：补剂、化验单和历次趋势、日记项和日记、身体事件（伤病、疼痛…）",
    "memory": "长期记忆：档案卡、观察笔记、状态卡；AI 写入记录和回滚",
    "feedback": "给开发者报 bug / 提需求，查看已报的",
    "help": "操作说明（playbook）：规划采购、排一周训练、复盘一周、照片记餐",
}

# Today page id -> extra groups for the in-page chat. Ids are the tab ids in web/static/app.js (TABS) and its sub-pages
# as "<tab>.<sub>" (record: S.recSub, food: S.foodMode / S.foodSub, trends: S.trendsMode). An unknown "<tab>.<sub>" falls
# back to "<tab>"; an unknown tab or no page = core only (everything else via find_tools).
PAGE_GROUPS = {
    "today": ("body", "training"),
    "trends": ("body",),
    "food": ("nutrition", "recipes"),
    "food.diet": ("nutrition", "recipes"),
    "food.recipes": ("recipes", "nutrition"),
    "food.shopping": ("grocery", "nutrition"),
    "record": ("records", "training"),
    "record.journal": ("records",),
    "record.training": ("training",),
    "me": ("memory", "records", "feedback"),
}
PAGE_MAX_LEN = 40

# Owner's decision (2026-10-18): logging tools (food, training, weight, journal, labs, templates, recipes, events, supplements,
# memory ...) run directly; only deleting / undoing asks first, so a mistaken call cannot destroy data. Same list as the
# "ask" rules in the butler's assistant/.claude/settings.json. Judged by the real tool name, also behind run_write_tool.
ALWAYS_CONFIRM = {"undo_sparky_entry", "undo_meal_log", "undo_day_changes", "delete_journal_entry", "delete_lab_results", "delete_observation",
                  "delete_supplement", "remove_product", "remove_shopping_items", "remove_staple", "delete_workout", "rollback_write",
                  "delete_custom_exercise"}

# Size budgets in characters of {name, description, relaxed input_schema} JSON (what tools/list sends).
# Before #99 all 106 tools were sent every time: 58,539 chars.
CORE_BUDGET_CHARS = 30000
META_BUDGET_CHARS = 2500
MCP_LIST_BUDGET_CHARS = 35000     # core + ALWAYS_CONFIRM + meta
GROUP_BUDGET_CHARS = {"body": 12000, "training": 13000, "nutrition": 14000, "recipes": 7000, "grocery": 9000,
                      "records": 10000, "memory": 4000, "feedback": 1500, "help": 1500}
PAGE_BUDGET_CHARS = 36500         # core + the page's groups + meta, for any page in PAGE_GROUPS; also the cap for a conversation
                                  # (36000 -> 36500 for the 5 nutrient fields of update_food_entry, #183)
                                  # that has been on several pages (the oldest pages' groups are dropped beyond it)
FALLBACK_PAGE_BUDGET_CHARS = 25000  # the same cap for the fallback AI (core and pages cut to its whitelist)

FIND_TOOLS_MAX = 12               # find_tools(query) returns at most this many full schemas

# Summary-mode (detail=false) result of every read tool on the synthetic data in tests/test_tool_groups.py, in characters
# of compact.dumps (what the model reads). Generous on purpose: it catches a tool that dumps raw rows, not normal growth.
READ_RESULT_MAX_CHARS = 8000
READ_RESULT_MAX_CHARS_BY_TOOL = {
    "propose_plan": 14000,       # a whole week's draft with sets, loads and the questions to ask first (~10k)
    "search_exercises": 12000,   # up to its own result limit, each with muscles, equipment and safe alternatives (~9k)
}

# Scene context tools (assistant/scenes.py, issue #100)
TRAINING_RECENT_DAYS = 14         # get_training_context: training and load looked back on
EQUIPMENT_CARD_KEY = "equipment"  # profile card item where the user's equipment is kept (asked once, then remembered)
REVIEW_MIN_DAYS = 4               # get_week_review_context: fewer scored / food-logged days than this -> a `needs` line
REVIEW_NOTES_PER_ITEM = 3         # journal notes shown per item in the week review
REVIEW_NOTE_CHARS = 60
