"""Parameters of the fallback AI (ai/fallback.py). Change prices here when DeepSeek changes them.

Prices: DeepSeek's Chinese pricing page https://api-docs.deepseek.com/zh-cn/quick_start/pricing, checked 2026-10-18,
model deepseek-flash (= DeepSeek-V4.1-Flash-1017), CNY per million tokens:
  peak (Beijing time Mon–Fri 9:00–12:00, 14:00–18:00, excluding Chinese public holidays): cache hit 0.04, cache miss 2, output 8;
  off-peak = half price.
The page lists CNY directly, so no exchange rate is needed. Every call is charged at the PEAK price (conservative):
the real bill is never higher than what we count, and public holidays need no calendar. The quota is therefore at least ¥3 of real spend.
"""

# What it may reach (dl_app.py registers these as views). Read tools for everything the Today page shows + the everyday write
# tools. No ECG / raw heart data (get_cardio, get_ecg_list: excluded by the consent, ai/fallback.CONSENT_POINTS), no family
# data, no recipe editing.
TOOL_NAMES = (
    "get_briefing", "get_profile", "get_scores", "get_sleep", "get_daily_metrics", "get_reminders", "get_sleep_goal",
    "get_strain_coach", "get_sleep_coach", "get_training_calendar", "get_training_day", "get_muscle_load", "get_exercise_rules",
    "search_exercises", "get_exercise", "get_week_plan", "propose_plan", "save_plan", "swap_exercise",
    "reschedule_day", "swap_days", "replan_week", "undo_day_changes",
    "get_training_log", "get_nutrition", "get_weight", "get_lab_results", "list_supplements",
    "list_events", "list_journal_items", "get_journal", "get_memory",
    "log_food", "log_workout", "log_weight", "log_water", "undo_sparky_entry", "log_journal", "add_event",
    "add_observation", "update_profile_card",
    "add_lab_results", "log_metric_snapshot", "add_supplement",   # lab report photos, watch / InBody screenshots, supplement bottles
    "add_custom_exercise",   # 「让 Jarvis 补进动作库」 on the muscle load map (#189)
    "report_feedback", "list_feedback",   # root rules (ai/root_rules.md): writes only the feedback queue
    # The read tool behind every Today page card (issue #58; tests/test_fallback_coverage.py maps cards -> tools):
    # computed metrics, the 饮食 page and 采购.
    "get_healthspan", "get_stress", "get_health_monitor", "get_hr_zones", "get_vo2max", "get_report",
    "get_sleep_details", "get_lab_history", "get_metric_snapshots", "analyze_journal",
    "get_diet_profile", "get_nutrition_goals", "get_meal_templates", "list_recipes", "get_recipe", "suggest_meals",
    "get_shopping_list", "get_weekly_specials", "find_products", "get_price_history",
    "get_diet_constraints", "set_diet_constraint", "end_diet_constraint",   # the user's own diet constraints (#76)
    "get_training_context", "get_week_review_context", "get_playbook",      # scene context + playbooks (#100); no ECG in them
    "get_memory_review", "apply_memory_changes",                            # tidying the memory (playbook memory_review, #113)
)
# Behind the rollout flag ai_parity (ai/parity.py): the fallback gets every tool the other channels have for the user's own
# data, except what its consent (CONSENT_POINTS: 「不发：心电图、逐拍心跳、家人的数据」) keeps from DeepSeek. Nothing else may
# be left out (tests/test_ai_parity.py); a tool that ought to be is a consent question for the owner, not a whitelist edit.
CONSENT_EXCLUDED = {
    "get_cardio": "逐拍心跳",   # raw heart data (cardio/: recovery after workouts, nightly recovery index); out since the first consent
    "get_ecg_list": "心电图",
    "get_friends_overview": "家人的数据",   # circle members' shared data
}

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
DISPLAY_NAME = "DeepSeek Flash"
KEY_FILE_NAME = "deepseek_api_key"        # secrets/deepseek_api_key (Ali), or BODY_DEEPSEEK_API_KEY_FILE

PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing（2026-10-18 核对，按高峰价）"
PRICE_CACHE_HIT_CNY = 0.04                # per million input tokens served from DeepSeek's context cache
PRICE_CACHE_MISS_CNY = 2.0                # per million input tokens not in the cache
PRICE_OUTPUT_CNY = 8.0                    # per million output tokens

MONTHLY_QUOTA_CNY = 3.0                   # per person per natural month (user's time zone); admin can override per person
MAX_OUTPUT_TOKENS = 4096                  # per model call: answers are short, but propose_plan / save_plan arguments for a whole
                                          # week reached ~2,000 output tokens on Ali (2026-10-22), right at the old 2048 (issue #184)
MAX_ROUNDS = 12                           # model calls chained for one user message (the general chat allows 8). A shopping plan or
                                          # a week plan with find_tools / run_read_tool takes 7–10 (issue #184: 6 stopped them
                                          # half way). The last one is always a tools-off answer (ai/chat.py _loop)
MAX_TURN_CNY = 0.3                        # what one user message may cost (10% of the default quota): past it the next call is the
                                          # tools-off answer. Observed 2026-10-22: a 6-round message ≈ ¥0.02 (mostly cache hits)
# Context (ai/context.py): history is resent on every call, so past this budget the older turns are folded into a summary
CONTEXT_MAX_CHARS = 24_000                # history after the summary, as JSON text (~8–12k tokens, ≈ ¥0.02 per call at cache-miss price)
CONTEXT_MAX_MESSAGES = 40                 # or this many messages (tool calls and results count)
KEEP_TURNS = 4                            # recent turns kept verbatim at most
CHARS_PER_TOKEN = 2.0                     # pre-call estimate of prompt tokens (Chinese ~1–1.5 chars/token, JSON ~3–4): errs high
RATE_PER_MINUTE = 20                      # model calls per person per minute (one message is usually 1–3 calls, at most
                                          # MAX_ROUNDS + 1 history fold, all within ~20 s)
RATE_PER_DAY = 150                        # model calls per person per day (local day)
VISION = True                             # deepseek-flash reads images (https://api-docs.deepseek.com/guides/vision; tested 2026-10-18:
                                          # a 900x600 JPEG ~350 prompt tokens). Images may only be in user messages (ours are).
IMAGE_TOKENS = 1024                       # DeepSeek bills at most 1024 tokens per image: used for the pre-call worst case

BALANCE_STOP_CNY = 2.0                    # account balance below this: fallback pauses for everyone (doctor ✗)
BALANCE_WARN_CNY = 3.0                    # below this and only then: doctor ⚠ "top up" (owner 2026-11-04; one yuan above the stop line)
BALANCE_MAX_AGE_S = 600                   # re-check the balance at most every 10 minutes (on chat or doctor)
TOPUP_URL = "https://platform.deepseek.com/top_up"
